import threading
import time
import logging
import signal
import os
import psutil
from datetime import datetime
import resource
from typing import Any, Dict, Optional, List

# Import all flow runner functionality
from flow_runner import (
    StartRequest,
    FlowRunner,
    Metrics,
    asyncio,
    logger as fr_logger
)

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, ValidationError
from typing import Dict, Optional

# ------------------------------------------------------
# Logging in UTC
# ------------------------------------------------------
# Force the formatter to use UTC/gmtime for timestamps:
logging.Formatter.converter = time.gmtime

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)sZ - %(levelname)s - %(name)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger("flow_container_control")

app = FastAPI()

# ------------------------------------------------------
# Global Runtime State
# ------------------------------------------------------
current_settings = {
    'app_status': 'initializing',  # 'initializing' | 'running' | 'stopped' | 'error'
    'container_status': 'running'
}

flow_runner_instance = None  # type: Optional[FlowRunner]
event_loop = None               # type: Optional[asyncio.AbstractEventLoop]
background_thread = None        # type: Optional[threading.Thread]

# ------------------------------------------------------
# Memory Limits
# ------------------------------------------------------
MEMORY_SOFT_LIMIT = 4096  # in MB (4GB)
MEMORY_HARD_LIMIT = 4608  # in MB (4.5GB)

def set_memory_limits():
    """Set memory limits for the container process, if desired."""
    MB = 1024 * 1024
    try:
        soft, hard = resource.getrlimit(resource.RLIMIT_AS)
        new_soft = min(MEMORY_SOFT_LIMIT * MB, hard)
        new_hard = max(MEMORY_HARD_LIMIT * MB, new_soft)

        resource.setrlimit(resource.RLIMIT_AS, (new_soft, hard))
        logger.info(f"Memory soft limit set: {new_soft / MB:.0f}MB")

        try:
            resource.setrlimit(resource.RLIMIT_AS, (new_soft, new_hard))
            logger.info(f"Memory hard limit set: {new_hard / MB:.0f}MB")
        except ValueError:
            logger.warning(f"Could not raise hard memory limit to {new_hard / MB:.0f}MB. Keeping hard limit at {hard / MB:.0f}MB.")
            resource.setrlimit(resource.RLIMIT_AS, (new_soft, hard))

    except ValueError as e:
        logger.warning(f"Could not set memory limits (maybe invalid values or permissions): {e}")
    except Exception as e:
        logger.error(f"Failed to set memory limits due to unexpected error: {e}")

# ---------------------------------------------------------------------
# HELPER: Restructure incoming data to have { "config": {...}, "flowmap": {...} }.
# ---------------------------------------------------------------------
def _ensure_config_flowmap_structure(data: dict) -> dict:
    flowmap = data.pop("flowmap", None)
    config = data.get("config", {})

    for key in list(data.keys()):
        if key not in ("config", "flowmap"):
            config[key] = data.pop(key)

    data["config"] = config
    data["flowmap"] = flowmap if flowmap is not None else {}
    return data

# ---------------------------------------------------------------------
# FORCE-STOP HELPER
# ---------------------------------------------------------------------
def _force_stop_flow_runner():
    """
    Immediately stop the flow runner if it exists and is running,
    then set status to 'stopped' and clear references.
    """
    global flow_runner_instance, event_loop

    if not flow_runner_instance or not flow_runner_instance.running:
        logger.info("No running flow runner instance to stop forcibly.")
        current_settings['app_status'] = 'stopped'
        flow_runner_instance = None
        return

    logger.info("Forcibly stopping existing flow runner...")

    instance = flow_runner_instance
    loop = event_loop

    try:
        if loop and not loop.is_closed() and loop.is_running():
            async def stopper():
                await instance.stop_generating()

            future = asyncio.run_coroutine_threadsafe(stopper(), loop)
            future.result(timeout=10)
        else:
            logger.warning("Event loop unavailable or not running; forcing instance.running = False.")
            instance.running = False
    except Exception as e:
        logger.error(f"Unexpected error forcibly stopping flow runner: {e}", exc_info=True)
    finally:
        current_settings['app_status'] = 'stopped'
        flow_runner_instance = None
        logger.info("Flow runner forcibly stopped and marked as 'stopped'.")

# ---------------------------------------------------------------------
# BACKGROUND THREAD ROUTINE
# ---------------------------------------------------------------------
def run_flow_runner_in_loop(start_request_data: StartRequest):
    """
    Dedicated background thread: creates an asyncio loop,
    instantiates the flow runner, and runs until done.
    """
    global event_loop, flow_runner_instance

    try:
        event_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(event_loop)

        flow_runner_instance = FlowRunner(
            config=start_request_data.config,
            flowmap=start_request_data.flowmap,
            metrics=Metrics()
        )
        logger.info("Starting flow generation based on flowmap...")
        event_loop.run_until_complete(flow_runner_instance.start_generating())
    except asyncio.CancelledError:
        logger.info("Flow generation cancelled.")
    except Exception as e:
        logger.error(f"Background flow runner error: {e}", exc_info=True)
        current_settings['app_status'] = 'error'
    finally:
        logger.info("Background flow runner thread exiting.")
        if current_settings['app_status'] not in ('error', 'stopped'):
            current_settings['app_status'] = 'stopped'

        if event_loop and not event_loop.is_closed():
            tasks = asyncio.all_tasks(event_loop)
            logger.debug(f"Cleaning up {len(tasks)} tasks in event loop.")
            for task in tasks:
                if not task.done():
                    task.cancel()
            try:
                event_loop.run_until_complete(asyncio.gather(*tasks, return_exceptions=True))
                logger.debug("Gathered cancelled tasks.")
            except RuntimeError as e:
                logger.warning(f"Error during loop shutdown gather: {e}")
            except asyncio.CancelledError:
                logger.info("Loop shutdown gather was cancelled.")
            finally:
                if not event_loop.is_closed():
                    event_loop.close()
                    logger.info("Asyncio event loop closed.")

# ---------------------------------------------------------------------
# FASTAPI ENDPOINTS
# ---------------------------------------------------------------------
@app.get('/api/health')
async def health_check():
    """Basic health check endpoint."""
    return JSONResponse({
        "status": "healthy",
        "app_status": current_settings['app_status']
    })

class StartRequestWrapper(BaseModel):
    config: Dict[str, Any]
    flowmap: Dict[str, Any]

@app.post('/api/start')
async def start_flow_runner(data: dict):
    """Start the flow runner with the given configuration and flowmap.
    The run is continuous and will only stop when `/api/stop` is called or on
    container shutdown. Supports the `override_step_url_host` config option.
    If a runner is already active it is stopped before the new one begins.
    """
    global background_thread

    # Restructure to ensure { "config": {...}, "flowmap": {...} }
    structured_data = _ensure_config_flowmap_structure(data)

    # If currently running, forcibly stop.
    if current_settings['app_status'] == 'running':
        logger.info("Received /api/start while flow runner is already running. Forcing stop...")
        _force_stop_flow_runner()

    # Validate request
    try:
        start_req_obj = StartRequest(**structured_data)
        logger.info("Start request validated successfully.")
        log_level = logging.DEBUG if start_req_obj.config.debug else logging.INFO
        fr_logger.setLevel(log_level)
        for handler in fr_logger.handlers:
            handler.setLevel(log_level)
        logger.info(f"Flow Runner log level set to {logging.getLevelName(log_level)}.")
    except ValidationError as ve:
        logger.error(f"Request validation failed: {ve}", exc_info=True)
        current_settings['app_status'] = 'stopped'
        raise HTTPException(status_code=400, detail=ve.errors())
    except Exception as e:
        logger.error(f"Invalid request body: {e}", exc_info=True)
        current_settings['app_status'] = 'stopped'
        raise HTTPException(status_code=400, detail=str(e))

    # Now we proceed to start fresh
    current_settings['app_status'] = 'running'
    background_thread = threading.Thread(
        target=run_flow_runner_in_loop,
        args=(start_req_obj,),
        daemon=True
    )
    background_thread.start()

    logger.info("FlowRunner started (continuous mode)")

    return JSONResponse({"message": "Flow runner started with the provided flowmap"})

@app.post('/api/stop')
async def stop_flow_runner():
    """
    Immediately stops the running flow runner (if any) and sets status to 'stopped'.
    If already stopped, returns a message that it's stopped.
    """
    if current_settings['app_status'] != 'running':
        # If we are 'stopped' or 'error' or 'initializing', there's nothing to do
        if current_settings['app_status'] == 'stopped':
            return JSONResponse({"message": "Flow runner is already stopped."})
        return JSONResponse({"message": f"No running flow runner to stop (status={current_settings['app_status']})."})

    _force_stop_flow_runner()
    return JSONResponse({"message": "Flow runner forcibly stopped."})

@app.get('/api/metrics')
async def api_metrics():
    """
    Return combined container + flow runner stats.
    Generator metrics are placed under the top-level 'metrics' key.
    """
    container_cpu_percent = psutil.cpu_percent(interval=0.1)
    container_mem = psutil.virtual_memory()
    net_io = psutil.net_io_counters()

    rps_val = 0.0
    active_users = 0
    avg_flow_duration = 0.0

    instance = flow_runner_instance
    loop = event_loop

    if instance and instance.running:
        # Fetch RPS
        if loop and not loop.is_closed():
            async def get_rps_async():
                if instance and hasattr(instance, 'metrics') and instance.metrics:
                    try:
                        return await instance.metrics.get_rps()
                    except Exception as metrics_err:
                        logger.warning(f"Error getting RPS from metrics object: {metrics_err}")
                        return 0.0
                return 0.0
            future = asyncio.run_coroutine_threadsafe(get_rps_async(), loop)
            try:
                rps_val = future.result(timeout=0.5)
            except Exception as e:
                logger.error(f"Error getting RPS from generator: {e}")
                rps_val = 0.0

        # Active users
        try:
            active_users = instance.get_active_user_count()
        except Exception as e:
            logger.warning(f"Failed to get active user count: {e}")
            active_users = -1

        # Average flow duration
        if loop and not loop.is_closed():
            async def get_avg_flow_duration_async():
                if instance and hasattr(instance, 'metrics') and instance.metrics:
                    try:
                        return await instance.metrics.get_average_flow_duration_ms()
                    except Exception as metrics_err:
                        logger.warning(f"Error getting avg flow duration: {metrics_err}")
                        return 0.0
                return 0.0
            future2 = asyncio.run_coroutine_threadsafe(get_avg_flow_duration_async(), loop)
            try:
                avg_flow_duration = future2.result(timeout=0.5)
            except Exception as e:
                logger.error(f"Error getting average flow duration: {e}")
                avg_flow_duration = 0.0

    resp_body = {
        "timestamp": datetime.utcnow().isoformat() + 'Z',
        "app_status": current_settings['app_status'],
        "container_status": current_settings['container_status'],
        "network": {
            "bytes_sent": net_io.bytes_sent,
            "bytes_recv": net_io.bytes_recv,
            "packets_sent": net_io.packets_sent,
            "packets_recv": net_io.packets_recv
        },
        "system": {
            "cpu_percent": round(container_cpu_percent, 1),
            "memory_percent": round(container_mem.percent, 1),
            "memory_available_mb": round(container_mem.available / (1024 * 1024), 2),
            "memory_used_mb": round(container_mem.used / (1024 * 1024), 2)
        },
        "metrics": {
            "rps": float(rps_val),
            "active_simulated_users": active_users,
            "average_flow_duration_ms": avg_flow_duration
        }
    }
    return JSONResponse(resp_body)

@app.get('/metrics')
async def metrics_prometheus():
    """
    Prometheus /metrics endpoint with combined container + flow runner stats.
    """
    container_cpu_percent = psutil.cpu_percent(interval=0.1)
    container_mem = psutil.virtual_memory()
    net_io = psutil.net_io_counters()

    rps_val = 0.0
    active_users = 0
    avg_flow_ms = 0.0

    instance = flow_runner_instance
    loop = event_loop

    if instance and instance.running:
        # RPS
        if loop and not loop.is_closed():
            async def get_rps_async():
                if instance and hasattr(instance, 'metrics') and instance.metrics:
                    try:
                        return await instance.metrics.get_rps()
                    except Exception:
                        return 0.0
                return 0.0
            future = asyncio.run_coroutine_threadsafe(get_rps_async(), loop)
            try:
                rps_val = future.result(timeout=0.5)
            except Exception:
                rps_val = 0.0

        # Active users
        try:
            active_users = instance.get_active_user_count()
        except Exception:
            active_users = -1

        # Average flow duration
        if loop and not loop.is_closed():
            async def get_avg_flow_duration_async():
                if instance and hasattr(instance, 'metrics') and instance.metrics:
                    try:
                        return await instance.metrics.get_average_flow_duration_ms()
                    except Exception:
                        return 0.0
                return 0.0
            future2 = asyncio.run_coroutine_threadsafe(get_avg_flow_duration_async(), loop)
            try:
                avg_flow_ms = future2.result(timeout=0.5)
            except Exception:
                avg_flow_ms = 0.0

    status_map = {
        "initializing": 0,
        "running": 1,
        "stopped": 2,
        "error": 3
    }
    app_status_val = status_map.get(current_settings['app_status'], 3)  # default to 3='error' if unexpected

    lines = [
        "# HELP container_cpu_percent CPU usage percent.",
        "# TYPE container_cpu_percent gauge",
        f"container_cpu_percent {round(container_cpu_percent, 1)}",
        "# HELP container_memory_percent Memory usage percent.",
        "# TYPE container_memory_percent gauge",
        f"container_memory_percent {round(container_mem.percent, 1)}",
        "# HELP container_memory_available_bytes Memory available in bytes.",
        "# TYPE container_memory_available_bytes gauge",
        f"container_memory_available_bytes {container_mem.available}",
        "# HELP container_memory_used_bytes Memory used in bytes.",
        "# TYPE container_memory_used_bytes gauge",
        f"container_memory_used_bytes {container_mem.used}",
        "# HELP container_network_bytes_sent_total Bytes sent.",
        "# TYPE container_network_bytes_sent_total counter",
        f"container_network_bytes_sent_total {net_io.bytes_sent}",
        "# HELP container_network_bytes_recv_total Bytes received.",
        "# TYPE container_network_bytes_recv_total counter",
        f"container_network_bytes_recv_total {net_io.bytes_recv}",
        "# HELP container_network_packets_sent_total Packets sent.",
        "# TYPE container_network_packets_sent_total counter",
        f"container_network_packets_sent_total {net_io.packets_sent}",
        "# HELP container_network_packets_recv_total Packets received.",
        "# TYPE container_network_packets_recv_total counter",
        f"container_network_packets_recv_total {net_io.packets_recv}",
        "# HELP flow_runner_rps Current requests-per-second generated by flows.",
        "# TYPE flow_runner_rps gauge",
        f"flow_runner_rps {float(rps_val)}",
        "# HELP flow_runner_active_users Current number of active simulated users executing flows.",
        "# TYPE flow_runner_active_users gauge",
        f"flow_runner_active_users {active_users}",
        "# HELP app_status Application status (initializing=0, running=1, stopped=2, error=3).",
        "# TYPE app_status gauge",
        f"app_status {app_status_val}",
        "# HELP flow_runner_average_duration_ms Average user flow duration in milliseconds.",
        "# TYPE flow_runner_average_duration_ms gauge",
        f"flow_runner_average_duration_ms {avg_flow_ms}",
    ]
    return Response("\n".join(lines) + "\n", media_type="text/plain; version=0.0.4")

# ---------------------------------------------------------------------
# SIGNAL HANDLER (SIGTERM, SIGINT)
# ---------------------------------------------------------------------
def handle_signal(signum, frame):
    """
    Handle SIGTERM/SIGINT to gracefully stop the flow runner.
    """
    signal_name = signal.Signals(signum).name
    logger.info(f"Received signal {signal_name} ({signum}); initiating immediate shutdown.")
    _force_stop_flow_runner()

    logger.info("Exiting flow_container_control due to signal.")
    os._exit(0)

# Register signal handlers
signal.signal(signal.SIGTERM, handle_signal)
signal.signal(signal.SIGINT, handle_signal)

# ---------------------------------------------------------------------
# MAIN ENTRY POINT (for dev usage)
# ---------------------------------------------------------------------
if __name__ == '__main__':
    set_memory_limits()
    logger.info("Starting flow_container_control API server...")
    current_settings['app_status'] = 'initializing'

    import uvicorn
    uvicorn.run(
        "container_control:app",
        host='0.0.0.0',
        port=8080,
        log_level="info",
        reload=os.environ.get("DEV_RELOAD", "false").lower() == "true"
    )
