import argparse
import asyncio
import json
import logging
from pathlib import Path

from flow_runner import FlowRunner, FlowMap, ContainerConfig, Metrics


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run FlowRunner locally for a flow file")
    parser.add_argument("flow_file", help="Path to flow definition JSON file")
    parser.add_argument("flow_url", nargs="?", default="http://localhost:8000", help="Base URL for flow target")
    parser.add_argument("sim_users", nargs="?", type=int, default=1, help="Number of simulated users")
    parser.add_argument("debug_level", nargs="?", default="INFO", help="Logging level (DEBUG, INFO, WARNING)")
    parser.add_argument(
        "--cycle-delay-ms",
        dest="cycle_delay_ms",
        type=int,
        default=None,
        help="Fixed delay between flow iterations in milliseconds",
    )
    parser.add_argument(
        "--min-step-ms",
        dest="min_step_ms",
        type=int,
        default=None,
        help="Override min_sleep_ms for step delay",
    )
    parser.add_argument(
        "--max-step-ms",
        dest="max_step_ms",
        type=int,
        default=None,
        help="Override max_sleep_ms for step delay",
    )
    parser.add_argument(
        "--run-once",
        dest="run_once",
        action="store_true",
        help="Execute the flow only once and then exit",
    )
    return parser.parse_args()


async def run_flow(cfg: ContainerConfig, fmap: FlowMap, run_once: bool) -> None:
    metrics = Metrics()
    runner = FlowRunner(cfg, fmap, metrics, run_once=run_once)
    try:
        await runner.start_generating()
    finally:
        await runner.stop_generating()


def main() -> None:
    args = parse_args()
    logging.basicConfig(level=getattr(logging, args.debug_level.upper(), logging.INFO))

    flow_path = Path(args.flow_file)
    with flow_path.open("r", encoding="utf-8") as f:
        flow_data = json.load(f)
    fmap = FlowMap.model_validate(flow_data)

    cfg = ContainerConfig(
        flow_target_url=args.flow_url,
        sim_users=args.sim_users,
        debug=args.debug_level.upper() == "DEBUG",
        flow_cycle_delay_ms=args.cycle_delay_ms,
        **{
            k: v
            for k, v in {
                "min_sleep_ms": args.min_step_ms,
                "max_sleep_ms": args.max_step_ms,
            }.items()
            if v is not None
        },
    )

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(run_flow(cfg, fmap, args.run_once))
    except KeyboardInterrupt:
        print("Stopping FlowRunner...")
        loop.run_until_complete(asyncio.sleep(0))
    finally:
        loop.run_until_complete(loop.shutdown_asyncgens())
        loop.close()


if __name__ == "__main__":
    main()
