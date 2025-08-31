"""
FlowRunner Adapter for Container Control Core v2.0.

This adapter integrates FlowRunner with the Container Control Core system,
allowing FlowRunner to be managed as a containerized workload.
"""

from __future__ import annotations
import asyncio
import threading
import logging
from typing import Any, Dict, Optional

from app_adapter import ApplicationAdapter
from flow_runner import FlowRunner, FlowMap, ContainerConfig, Metrics, StartRequest

logger = logging.getLogger("flowrunner_adapter")


class FlowRunnerAdapter(ApplicationAdapter):
    """
    Adapter that manages FlowRunner lifecycle within Container Control Core.
    
    This adapter handles:
    - Starting/stopping FlowRunner instances
    - Managing the async event loop in a background thread
    - Exposing FlowRunner metrics
    - Graceful shutdown and cleanup
    """

    def __init__(self, static_cfg: Dict[str, Any] | None = None) -> None:
        super().__init__(static_cfg)
        self.flow_runner: Optional[FlowRunner] = None
        self.event_loop: Optional[asyncio.AbstractEventLoop] = None
        self.background_thread: Optional[threading.Thread] = None
        self.metrics: Optional[Metrics] = None
        self._shutdown_event = threading.Event()

    def start(self, start_payload: Dict[str, Any], *, ensure_user) -> Any:
        """
        Start FlowRunner with the provided configuration and flowmap(s).

        Expected payload structure:
        {
            "config": { ... },
            "flowmap": { ... } | "flowmaps": [ {...}, {...} ]
        }
        """
        logger.info("Starting FlowRunner with payload")
        
        # Stop any existing instance first
        if self.flow_runner or self.background_thread:
            logger.info("Stopping existing FlowRunner instance before starting new one")
            self.stop()

        try:
            # Parse and validate the payload
            start_request = StartRequest.model_validate(start_payload)
            
            # Create metrics instance
            self.metrics = Metrics()
            
            # Start FlowRunner in background thread
            self._shutdown_event.clear()
            self.background_thread = threading.Thread(
                target=self._run_flow_runner_in_thread,
                args=(start_request,),
                daemon=True
            )
            self.background_thread.start()
            
            logger.info("FlowRunner started successfully")
            return self.background_thread
            
        except Exception as e:
            logger.error(f"Failed to start FlowRunner: {e}")
            raise

    def stop(self) -> None:
        """Stop FlowRunner gracefully."""
        logger.info("Stopping FlowRunner")
        
        # Signal shutdown
        self._shutdown_event.set()
        
        # Stop the flow runner if it exists
        if self.flow_runner and self.event_loop:
            try:
                if self.event_loop.is_running():
                    # Schedule the stop coroutine in the event loop
                    future = asyncio.run_coroutine_threadsafe(
                        self.flow_runner.stop_generating(),
                        self.event_loop
                    )
                    # Wait for completion with timeout
                    future.result(timeout=10.0)
            except Exception as e:
                logger.warning(f"Error during FlowRunner stop: {e}")
        
        # Wait for background thread to finish
        if self.background_thread and self.background_thread.is_alive():
            self.background_thread.join(timeout=15.0)
            if self.background_thread.is_alive():
                logger.warning("Background thread did not stop within timeout")
        
        # Clean up references
        self.flow_runner = None
        self.event_loop = None
        self.background_thread = None
        
        logger.info("FlowRunner stopped")

    def update(self, update_payload: Dict[str, Any]) -> bool:
        """
        Update FlowRunner configuration at runtime.
        
        For now, this is not supported and requires a restart.
        """
        logger.info("Update requested - FlowRunner does not support live updates")
        return False

    def get_metrics(self) -> Dict[str, Any]:
        """Return current FlowRunner metrics."""
        if not self.metrics:
            return {}
        
        try:
            # Get metrics synchronously (the metrics methods are not async)
            return {
                "flow_runner": {
                    "running": self.flow_runner is not None and getattr(self.flow_runner, 'running', False),
                    "rps": self.metrics.last_rps_value,
                    "total_requests": len(self.metrics.request_timestamps),
                    "flow_count": self.metrics.flow_count,
                    "avg_flow_duration_ms": (
                        self.metrics.flow_duration_sum / self.metrics.flow_count * 1000
                        if self.metrics.flow_count > 0 else 0
                    ),
                    "active_users": getattr(self.flow_runner, '_active_users_count', 0) if self.flow_runner else 0,
                }
            }
        except Exception as e:
            logger.warning(f"Error collecting metrics: {e}")
            return {"flow_runner": {"error": str(e)}}

    def prometheus_metrics(self) -> list[str]:
        """Return Prometheus-formatted metrics."""
        metrics = self.get_metrics().get("flow_runner", {})
        
        if "error" in metrics:
            return []
        
        lines = []
        
        # Add Prometheus metrics
        lines.append("# HELP flowrunner_running Whether FlowRunner is currently running")
        lines.append("# TYPE flowrunner_running gauge")
        lines.append(f"flowrunner_running {int(metrics.get('running', False))}")
        
        lines.append("# HELP flowrunner_requests_per_second Current requests per second")
        lines.append("# TYPE flowrunner_requests_per_second gauge")
        lines.append(f"flowrunner_requests_per_second {metrics.get('rps', 0)}")
        
        lines.append("# HELP flowrunner_total_requests_total Total number of requests made")
        lines.append("# TYPE flowrunner_total_requests_total counter")
        lines.append(f"flowrunner_total_requests_total {metrics.get('total_requests', 0)}")
        
        lines.append("# HELP flowrunner_flows_completed_total Total number of flows completed")
        lines.append("# TYPE flowrunner_flows_completed_total counter")
        lines.append(f"flowrunner_flows_completed_total {metrics.get('flow_count', 0)}")
        
        lines.append("# HELP flowrunner_avg_flow_duration_milliseconds Average flow duration in milliseconds")
        lines.append("# TYPE flowrunner_avg_flow_duration_milliseconds gauge")
        lines.append(f"flowrunner_avg_flow_duration_milliseconds {metrics.get('avg_flow_duration_ms', 0)}")
        
        lines.append("# HELP flowrunner_active_users Number of active simulated users")
        lines.append("# TYPE flowrunner_active_users gauge")
        lines.append(f"flowrunner_active_users {metrics.get('active_users', 0)}")
        
        return lines

    def _run_flow_runner_in_thread(self, start_request: StartRequest) -> None:
        """
        Run FlowRunner in a dedicated thread with its own event loop.
        """
        logger.info("Starting FlowRunner background thread")
        
        try:
            # Create new event loop for this thread
            self.event_loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self.event_loop)
            
            # Create FlowRunner instance
            self.flow_runner = FlowRunner(
                config=start_request.config,
                flowmap=start_request.flowmap,
                flowmaps=start_request.flowmaps,
                metrics=self.metrics
            )
            
            # Run until shutdown is requested
            self.event_loop.run_until_complete(self._run_flow_runner_lifecycle())
            
        except asyncio.CancelledError:
            logger.info("FlowRunner background thread cancelled")
        except Exception as e:
            logger.error(f"Error in FlowRunner background thread: {e}")
        finally:
            # Clean up event loop
            if self.event_loop:
                try:
                    # Cancel any remaining tasks
                    pending = asyncio.all_tasks(self.event_loop)
                    for task in pending:
                        task.cancel()
                    
                    if pending:
                        self.event_loop.run_until_complete(
                            asyncio.gather(*pending, return_exceptions=True)
                        )
                    
                    self.event_loop.close()
                except Exception as e:
                    logger.warning(f"Error during event loop cleanup: {e}")
            
            logger.info("FlowRunner background thread finished")

    async def _run_flow_runner_lifecycle(self) -> None:
        """
        Manage the FlowRunner lifecycle within the async event loop.
        """
        try:
            logger.info("Starting FlowRunner generation")
            await self.flow_runner.start_generating()
            
            # Wait for shutdown signal
            while not self._shutdown_event.is_set():
                await asyncio.sleep(0.1)
            
            logger.info("Shutdown signal received, stopping FlowRunner")
            
        except Exception as e:
            logger.error(f"Error in FlowRunner lifecycle: {e}")
        finally:
            if self.flow_runner:
                try:
                    await self.flow_runner.stop_generating()
                except Exception as e:
                    logger.warning(f"Error stopping FlowRunner: {e}")

    def pre_start_hooks(self, start_payload: Dict[str, Any]) -> None:
        """Optional hook called before starting FlowRunner."""
        logger.info("FlowRunner pre-start hooks executed")

    def post_stop_hooks(self) -> None:
        """Optional hook called after stopping FlowRunner."""
        logger.info("FlowRunner post-stop hooks executed")
