"""ShowRunner-managed entry point for FlowRunner.

Replaces the Container Control adapter pattern.  FlowRunner now owns its
own process; the showrunner-sdk provides config injection, Prometheus
metrics, and a health endpoint.
"""

import asyncio
import logging
import os
import signal
import sys
import threading
from typing import Any, Dict, Optional

from showrunner_sdk import config, metrics, health
from flow_runner import (
    FlowRunner,
    FlowMap,
    ContainerConfig,
    Metrics,
    StartRequest,
    FLOWRUNNER_VERSION,
)
import sr3_report

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logger = logging.getLogger("flowrunner")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)sZ - %(levelname)s - %(name)s - %(message)s",
)

# ---------------------------------------------------------------------------
# SDK: Prometheus metrics (exposed on :9090/metrics)
# ---------------------------------------------------------------------------
prom_rps = metrics.gauge("flowrunner_requests_per_second", "Current requests per second")
prom_total_requests = metrics.counter("flowrunner_total_requests", "Total HTTP requests sent")
prom_flows_completed = metrics.gauge("flowrunner_flows_completed", "Total flows completed")
prom_active_users = metrics.gauge("flowrunner_active_users", "Currently active simulated users")
prom_avg_duration = metrics.gauge(
    "flowrunner_avg_flow_duration_milliseconds",
    "Average flow duration in milliseconds",
)
prom_running = metrics.gauge("flowrunner_running", "Runner status (1=running, 0=stopped)")

metrics.set_app_info(name="flowrunner", version=FLOWRUNNER_VERSION)
metrics.start_server()  # :9090

# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------
_runner: Optional[FlowRunner] = None
_runner_metrics: Optional[Metrics] = None
_event_loop: Optional[asyncio.AbstractEventLoop] = None
_bg_thread: Optional[threading.Thread] = None
_shutdown_event = threading.Event()
_run_once_completed = False


# ---------------------------------------------------------------------------
# Metrics bridge — push FlowRunner internal metrics to Prometheus gauges
# ---------------------------------------------------------------------------
def _push_metrics_snapshot() -> None:
    """Copy current FlowRunner metrics into the Prometheus gauges."""
    if _runner_metrics is None:
        return

    # RPS — try the async path, fall back to the cached value
    rps_value = 0.0
    if _event_loop and _event_loop.is_running():
        try:
            future = asyncio.run_coroutine_threadsafe(
                _runner_metrics.get_rps(), _event_loop,
            )
            rps_value = float(future.result(timeout=1.0))
        except Exception:
            rps_value = float(_runner_metrics.last_rps_value)
    else:
        rps_value = float(_runner_metrics.last_rps_value)

    prom_rps.set(rps_value)
    prom_total_requests._value.set(  # counter monotonic — set to current total
        _runner_metrics.total_requests
    )
    prom_flows_completed.set(_runner_metrics.flow_count)
    prom_active_users.set(
        getattr(_runner, "_active_users_count", 0) if _runner else 0
    )

    avg_ms = 0.0
    if _runner_metrics.flow_count > 0:
        avg_ms = _runner_metrics.flow_duration_sum / _runner_metrics.flow_count * 1000
    prom_avg_duration.set(avg_ms)

    prom_running.set(1 if (_runner and getattr(_runner, "running", False)) else 0)


def _metrics_push_loop() -> None:
    """Background thread that periodically pushes metrics to Prometheus."""
    while not _shutdown_event.is_set():
        try:
            _push_metrics_snapshot()
        except Exception as exc:
            logger.debug("Metrics push error: %s", exc)
        _shutdown_event.wait(timeout=1.0)


# ---------------------------------------------------------------------------
# FlowRunner lifecycle (runs in a background thread with its own event loop)
# ---------------------------------------------------------------------------
def _build_start_request(raw_cfg: Dict[str, Any]) -> StartRequest:
    """Build a validated StartRequest from the raw config dict."""
    return StartRequest.model_validate(raw_cfg)


async def _run_flow_runner_lifecycle(runner: FlowRunner) -> None:
    """Start the runner, wait for shutdown, then stop it."""
    global _run_once_completed
    try:
        logger.info("Starting FlowRunner generation")
        await runner.start_generating()

        # If run_once mode, start_generating returns after a single pass
        if bool(getattr(runner, "run_once", False)) and not _shutdown_event.is_set():
            logger.info("FlowRunner completed run-once execution; initiating shutdown.")
            _run_once_completed = True
            _shutdown_event.set()

        # Wait for external shutdown signal
        while not _shutdown_event.is_set():
            await asyncio.sleep(0.1)

        logger.info("Shutdown signal received, stopping FlowRunner")
    except Exception as exc:
        logger.error("Error in FlowRunner lifecycle: %s", exc)
    finally:
        try:
            await runner.stop_generating()
        except Exception as exc:
            logger.warning("Error stopping FlowRunner: %s", exc)


def _runner_thread(start_request: StartRequest) -> None:
    """Run FlowRunner in a dedicated thread with its own asyncio event loop."""
    global _runner, _runner_metrics, _event_loop

    logger.info("Starting FlowRunner background thread")
    try:
        _event_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(_event_loop)

        _runner_metrics = Metrics()
        _runner = FlowRunner(
            config=start_request.config,
            flowmap=start_request.flowmap,
            flowmaps=start_request.flowmaps,
            metrics=_runner_metrics,
        )

        health.set_status("running")
        _event_loop.run_until_complete(_run_flow_runner_lifecycle(_runner))

    except Exception as exc:
        logger.error("Error in FlowRunner thread: %s", exc)
        health.set_status("error", reason=str(exc))
    finally:
        if _event_loop:
            try:
                pending = asyncio.all_tasks(_event_loop)
                for task in pending:
                    task.cancel()
                if pending:
                    _event_loop.run_until_complete(
                        asyncio.gather(*pending, return_exceptions=True)
                    )
                _event_loop.close()
            except Exception as exc:
                logger.warning("Error during event loop cleanup: %s", exc)

        health.set_status("stopped")
        logger.info("FlowRunner background thread finished")


def _stop_runner() -> None:
    """Signal the runner to stop and wait for the background thread."""
    global _runner, _event_loop, _bg_thread

    _shutdown_event.set()

    if _runner and _event_loop and _event_loop.is_running():
        try:
            future = asyncio.run_coroutine_threadsafe(
                _runner.stop_generating(), _event_loop,
            )
            future.result(timeout=10.0)
        except Exception as exc:
            logger.warning("Error during FlowRunner stop: %s", exc)

    if _bg_thread and _bg_thread.is_alive():
        _bg_thread.join(timeout=15.0)
        if _bg_thread.is_alive():
            logger.warning("Background thread did not stop within timeout")

    _runner = None
    _event_loop = None
    _bg_thread = None


def _start_runner(raw_cfg: Dict[str, Any]) -> None:
    """Parse config and launch the FlowRunner in a background thread."""
    global _bg_thread, _run_once_completed

    _run_once_completed = False
    _shutdown_event.clear()

    start_request = _build_start_request(raw_cfg)

    _bg_thread = threading.Thread(
        target=_runner_thread,
        args=(start_request,),
        daemon=True,
    )
    _bg_thread.start()
    logger.info("FlowRunner thread started")


# ---------------------------------------------------------------------------
# Config reload callback — restart the runner with new config
# ---------------------------------------------------------------------------
def _on_config_reload(new_cfg: Dict[str, Any]) -> None:
    """Called by the SDK when SIGHUP triggers a config re-read."""
    logger.info("Config reloaded — restarting FlowRunner with new configuration")
    _stop_runner()
    try:
        _start_runner(new_cfg)
    except Exception as exc:
        logger.error("Failed to restart FlowRunner after config reload: %s", exc)
        health.set_status("error", reason=str(exc))


# ---------------------------------------------------------------------------
# Signal handlers
# ---------------------------------------------------------------------------
def _handle_sigterm(signum: int, frame: Any) -> None:
    logger.info("SIGTERM received — shutting down")
    _shutdown_event.set()


def _handle_sigint(signum: int, frame: Any) -> None:
    logger.info("SIGINT received — shutting down")
    _shutdown_event.set()


# ---------------------------------------------------------------------------
# SR3: write /report/report.json from the last runner's metrics
# ---------------------------------------------------------------------------
def _write_sr3_report() -> None:
    """Emit the SR3 report from the current metrics. Best-effort, never raises.

    ``_runner_metrics`` holds the flow runner's lifetime pass/fail + status-code
    tallies for the run window (a config reload replaces it with a fresh Metrics,
    so this reflects the most recent window). ShowRunner pulls the sealed file at
    window close; a missing/failed write simply degrades to Tier-0.
    """
    try:
        sr3_report.write_report(_runner_metrics)
    except Exception as exc:  # defensive — must never block shutdown
        logger.debug("SR3 report write failed: %s", exc)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> None:
    global _bg_thread

    signal.signal(signal.SIGTERM, _handle_sigterm)
    signal.signal(signal.SIGINT, _handle_sigint)

    # ── SDK: Load config ──
    cfg = config.load()
    config.on_reload(_on_config_reload)

    if not cfg:
        logger.warning(
            "No config found at startup — waiting for config via SIGHUP. "
            "Mount a config file to %s or set SHOWRUNNER_CONFIG_PATH.",
            os.environ.get("SHOWRUNNER_CONFIG_PATH", "/config/app.json"),
        )
        health.set_status("waiting", reason="no config at startup")
        # Block until config arrives via SIGHUP or we are told to shut down
        _shutdown_event.wait()
        if not config.data:
            logger.info("Shutting down without ever receiving config")
            return

    # ── Run the FlowRunner, always sealing an SR3 report on exit ──
    # The finally covers every exit path — normal completion, run-once, a
    # SIGTERM/SIGINT stop, or a start/runtime error — so ShowRunner can pull a
    # sealed report whenever the container is stopped.
    try:
        # ── Start the FlowRunner ──
        _start_runner(cfg)

        # ── Start metrics bridge ──
        metrics_thread = threading.Thread(
            target=_metrics_push_loop, daemon=True, name="metrics-push",
        )
        metrics_thread.start()

        # ── Block until shutdown ──
        logger.info("FlowRunner is running. Waiting for shutdown signal...")
        _shutdown_event.wait()

        logger.info("Shutting down...")
        _stop_runner()

        if _run_once_completed:
            logger.info("Run-once execution complete — exiting")
        else:
            logger.info("FlowRunner stopped")
    except Exception as exc:
        logger.error("Failed to start/run FlowRunner: %s", exc)
        health.set_status("error", reason=str(exc))
        sys.exit(1)
    finally:
        # Always seal a report — the finally runs on normal exit, on the
        # sys.exit(1) above (SystemExit still triggers finally), and on stop.
        _write_sr3_report()


if __name__ == "__main__":
    main()
