# Test-only container control stub used by E2E tests.

import asyncio
import threading
import time
from typing import Any, Dict, Optional, Tuple

import psutil
from fastapi import FastAPI, HTTPException
from fastapi.responses import PlainTextResponse
from pydantic import ValidationError

from flow_runner import FlowRunner, Metrics, StartRequest, logger as flow_logger

app = FastAPI()

flow_runner: Optional[FlowRunner] = None
background_thread: Optional[threading.Thread] = None
event_loop: Optional[asyncio.AbstractEventLoop] = None
metrics: Optional[Metrics] = None

current_settings: Dict[str, Any] = {"app_status": "initializing"}
_state_lock = threading.Lock()


def _app_status_to_gauge(status: str) -> int:
    mapping = {
        "initializing": 0,
        "running": 1,
        "stopped": 2,
        "error": 3,
    }
    return mapping.get(status, 3)


def _run_flow_runner_in_thread(start_request: StartRequest) -> None:
    global flow_runner, event_loop, metrics
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    event_loop = loop
    metrics = Metrics()

    flowmaps = start_request.flowmaps or ([start_request.flowmap] if start_request.flowmap else [])
    flow_runner = FlowRunner(
        start_request.config,
        start_request.flowmap,
        metrics,
        flowmaps=flowmaps,
    )

    try:
        current_settings["app_status"] = "running"
        loop.run_until_complete(flow_runner.start_generating())
    except Exception as exc:
        flow_logger.error(f"FlowRunner thread error: {exc}")
        current_settings["app_status"] = "error"
    finally:
        try:
            loop.run_until_complete(loop.shutdown_asyncgens())
        except Exception:
            pass
        loop.close()
        event_loop = None
        flow_runner = None
        if current_settings.get("app_status") == "running":
            current_settings["app_status"] = "stopped"


def _force_stop_flow_runner() -> None:
    global flow_runner, background_thread, event_loop
    if flow_runner and event_loop and event_loop.is_running():
        try:
            future = asyncio.run_coroutine_threadsafe(flow_runner.stop_generating(), event_loop)
            future.result(timeout=5)
        except Exception as exc:
            flow_logger.warning(f"Failed to stop FlowRunner: {exc}")

    if background_thread and background_thread.is_alive():
        background_thread.join(timeout=5)

    flow_runner = None
    event_loop = None
    background_thread = None
    current_settings["app_status"] = "stopped"


def _get_metrics_snapshot() -> Tuple[float, float, int]:
    rps_value = 0.0
    avg_flow_ms = 0.0
    active_users = 0
    if flow_runner:
        try:
            active_users = flow_runner.get_active_user_count()
        except Exception:
            active_users = 0
    if metrics:
        if event_loop and event_loop.is_running():
            try:
                rps_future = asyncio.run_coroutine_threadsafe(metrics.get_rps(), event_loop)
                rps_value = float(rps_future.result(timeout=1))
            except Exception:
                rps_value = float(getattr(metrics, "last_rps_value", 0.0) or 0.0)
            try:
                avg_future = asyncio.run_coroutine_threadsafe(metrics.get_average_flow_duration_ms(), event_loop)
                avg_flow_ms = float(avg_future.result(timeout=1))
            except Exception:
                avg_flow_ms = 0.0
        else:
            rps_value = float(getattr(metrics, "last_rps_value", 0.0) or 0.0)
    return rps_value, avg_flow_ms, active_users


def _sanitize_validation_errors(errors: list[Dict[str, Any]]) -> list[Dict[str, Any]]:
    sanitized: list[Dict[str, Any]] = []
    for err in errors:
        entry = dict(err)
        ctx = entry.get("ctx")
        if isinstance(ctx, dict):
            entry["ctx"] = {key: str(value) for key, value in ctx.items()}
        sanitized.append(entry)
    return sanitized


@app.post("/api/start")
async def start_flow_runner(payload: Dict[str, Any]) -> Dict[str, str]:
    global background_thread
    try:
        start_request = StartRequest.model_validate(payload)
    except ValidationError as exc:
        raise HTTPException(status_code=400, detail=_sanitize_validation_errors(exc.errors())) from exc
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    with _state_lock:
        if background_thread and background_thread.is_alive():
            _force_stop_flow_runner()

        background_thread = threading.Thread(
            target=_run_flow_runner_in_thread,
            args=(start_request,),
            daemon=True,
        )
        background_thread.start()

    return {"message": "Flow runner started with the provided flowmap"}


@app.post("/api/stop")
async def stop_flow_runner() -> Dict[str, str]:
    if not background_thread or not background_thread.is_alive():
        if current_settings.get("app_status") != "stopped":
            current_settings["app_status"] = "stopped"
        return {"message": "Flow runner is already stopped."}

    _force_stop_flow_runner()
    return {"message": "Flow runner forcibly stopped."}


@app.get("/api/health")
async def health() -> Dict[str, str]:
    return {"status": "healthy", "app_status": current_settings.get("app_status", "error")}


@app.get("/api/metrics")
async def metrics_endpoint() -> Dict[str, Any]:
    cpu_percent = float(psutil.cpu_percent(interval=None))
    mem = psutil.virtual_memory()
    net = psutil.net_io_counters()
    rps_value, avg_flow_ms, active_users = _get_metrics_snapshot()

    return {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "app_status": current_settings.get("app_status", "error"),
        "container_status": "running",
        "network": {
            "bytes_sent": int(getattr(net, "bytes_sent", 0)),
            "bytes_recv": int(getattr(net, "bytes_recv", 0)),
            "packets_sent": int(getattr(net, "packets_sent", 0)),
            "packets_recv": int(getattr(net, "packets_recv", 0)),
        },
        "system": {
            "cpu_percent": cpu_percent,
            "memory_percent": float(getattr(mem, "percent", 0.0)),
            "memory_available_mb": float(getattr(mem, "available", 0.0)) / (1024 * 1024),
            "memory_used_mb": float(getattr(mem, "used", 0.0)) / (1024 * 1024),
        },
        "metrics": {
            "rps": rps_value,
            "active_simulated_users": active_users,
            "average_flow_duration_ms": avg_flow_ms,
        },
    }


@app.get("/metrics")
async def prometheus_metrics() -> PlainTextResponse:
    rps_value, avg_flow_ms, active_users = _get_metrics_snapshot()
    status_value = _app_status_to_gauge(current_settings.get("app_status", "error"))
    lines = [
        "# HELP app_status Application status (initializing=0, running=1, stopped=2, error=3).",
        "# TYPE app_status gauge",
        f"app_status {status_value}",
        "# HELP flow_runner_rps Current requests-per-second generated by flows.",
        "# TYPE flow_runner_rps gauge",
        f"flow_runner_rps {rps_value}",
        "# HELP flow_runner_active_users Number of active simulated users.",
        "# TYPE flow_runner_active_users gauge",
        f"flow_runner_active_users {active_users}",
        "# HELP flow_runner_avg_flow_duration_ms Average flow duration in milliseconds.",
        "# TYPE flow_runner_avg_flow_duration_ms gauge",
        f"flow_runner_avg_flow_duration_ms {avg_flow_ms}",
    ]
    return PlainTextResponse("\n".join(lines))


__all__ = ["app", "background_thread", "current_settings", "_force_stop_flow_runner"]
