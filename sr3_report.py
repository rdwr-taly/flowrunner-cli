"""SR3 report writer — emit ``/report/report.json`` for ShowRunner to pull.

ShowRunner v3.0 pulls this file out of the container at window close (via the
Docker API) and projects its ``measures`` into the demo report + runbook. The
app declares this contract in its ``.showrunner/appspec.json`` ``sdk`` block, so
ShowRunner knows the path and what measures to expect.

For FlowRunner the natural, first-class outcome signal is per-flow pass/fail:
``flow_runner.Metrics`` tracks ``flows_passed`` / ``flows_failed`` (a flow passes
when it completes with no ``flow_error``) and a per-status-code tally of every
HTTP response its steps received. This module turns those into report measures.

Fully optional and non-fatal: if the path is not writable the run is unaffected
(ShowRunner simply degrades to Tier-0, i.e. Prometheus metrics + logs). The file
is written atomically (tmp + rename) with ``status: "final"`` so ShowRunner never
observes a half-written report.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

LOGGER = logging.getLogger("flowrunner.sr3_report")

DEFAULT_REPORT_PATH = "/report/report.json"


def responses_by_code(metrics: Any) -> dict[str, int]:
    """Return the per-status-code response counts as string-keyed ints.

    Reads ``metrics.status_code_counts`` (``{int: int}``) which the flow runner
    populates for every request its steps receive. Keys are stringified so the
    report JSON round-trips cleanly (JSON object keys are strings) and matches
    the ``count_by_enum`` measure the appspec declares.
    """
    counts: dict[str, int] = {}
    try:
        raw = dict(getattr(metrics, "status_code_counts", {}) or {})
    except Exception:  # pragma: no cover - defensive; never break the run
        return counts
    for code, count in raw.items():
        try:
            counts[str(int(code))] = counts.get(str(int(code)), 0) + int(count)
        except (TypeError, ValueError):
            continue
    return counts


def build_report(metrics: Any) -> dict[str, Any]:
    """Build the SR3 report document from the current FlowRunner metrics."""
    passed = _safe_int(getattr(metrics, "flows_passed", 0))
    failed = _safe_int(getattr(metrics, "flows_failed", 0))
    total_flows = passed + failed
    success_ratio = round(passed / total_flows, 4) if total_flows else 0.0

    by_code = responses_by_code(metrics)
    total_requests = sum(by_code.values())

    if total_flows:
        summary = (
            f"Ran {total_flows} flow(s): {passed} passed, {failed} failed "
            f"({success_ratio:.0%} success) across {total_requests} request(s)."
        )
    else:
        summary = "No flows completed."

    findings: list[dict[str, Any]] = []
    if failed:
        findings.append(
            {
                "severity": "warning" if passed else "critical",
                "title": f"{failed} flow(s) failed",
                "category": "flow_failure",
                "detail": {
                    "flows_passed": passed,
                    "flows_failed": failed,
                    "flow_success_ratio": success_ratio,
                },
            }
        )

    return {
        "schema_version": 1,
        "status": "final",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "measures": {
            "flows_passed": passed,
            "flows_failed": failed,
            "flow_success_ratio": success_ratio,
            "steps.by_code": by_code,
        },
        "summary": summary,
        "findings": findings,
    }


def write_report(metrics: Any, path: str | None = None) -> bool:
    """Atomically write the SR3 report. Returns True on success, never raises."""
    target = Path(path or os.getenv("SR_REPORT_PATH", DEFAULT_REPORT_PATH))
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_name(target.name + ".tmp")
        tmp.write_text(json.dumps(build_report(metrics), indent=2), encoding="utf-8")
        tmp.replace(target)  # atomic rename on the same filesystem
        LOGGER.info("SR3 report written to %s", target)
        return True
    except Exception:  # pragma: no cover - degrade to Tier-0, never affect the run
        LOGGER.debug(
            "SR3 report write failed; ShowRunner will degrade to Tier-0", exc_info=True
        )
        return False


def _safe_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


__all__ = ["build_report", "responses_by_code", "write_report", "DEFAULT_REPORT_PATH"]
