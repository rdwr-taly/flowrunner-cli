"""Focused tests for the SR3 report writer (sr3_report.py).

Covers the report builder in isolation (against the metrics attribute contract)
plus an end-to-end pass through the real ``flow_runner.Metrics`` recorders so we
prove the fields the runner populates line up with what the report reads.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

import sr3_report


class _FakeMetrics:
    """Minimal stand-in mirroring the public attributes of flow_runner.Metrics."""

    def __init__(self, passed=0, failed=0, status_code_counts=None):
        self.flows_passed = passed
        self.flows_failed = failed
        self.status_code_counts = status_code_counts or {}


def test_responses_by_code_stringifies_keys():
    metrics = _FakeMetrics(status_code_counts={200: 40, 403: 3, 429: 1})
    assert sr3_report.responses_by_code(metrics) == {"200": 40, "403": 3, "429": 1}


def test_build_report_measures_and_ratio():
    metrics = _FakeMetrics(passed=9, failed=1, status_code_counts={200: 90, 500: 5})
    report = sr3_report.build_report(metrics)

    assert report["schema_version"] == 1
    assert report["status"] == "final"
    assert report["generated_at"]  # iso timestamp present

    m = report["measures"]
    assert m["flows_passed"] == 9
    assert m["flows_failed"] == 1
    assert m["flow_success_ratio"] == 0.9  # 9 / (9+1)
    assert m["steps.by_code"] == {"200": 90, "500": 5}
    assert "9 passed" in report["summary"]

    # A failure produces a finding.
    assert report["findings"]
    assert report["findings"][0]["category"] == "flow_failure"
    assert report["findings"][0]["severity"] == "warning"


def test_build_report_no_flows():
    report = sr3_report.build_report(_FakeMetrics())
    assert report["measures"]["flows_passed"] == 0
    assert report["measures"]["flows_failed"] == 0
    assert report["measures"]["flow_success_ratio"] == 0.0
    assert report["measures"]["steps.by_code"] == {}
    assert report["status"] == "final"
    assert report["findings"] == []


def test_build_report_all_failed_is_critical():
    report = sr3_report.build_report(_FakeMetrics(passed=0, failed=3))
    assert report["measures"]["flow_success_ratio"] == 0.0
    assert report["findings"][0]["severity"] == "critical"


def test_build_report_handles_none_metrics():
    # Runner never started -> _runner_metrics is None; must still seal a report.
    report = sr3_report.build_report(None)
    assert report["status"] == "final"
    assert report["measures"]["flows_passed"] == 0
    assert report["measures"]["steps.by_code"] == {}


def test_write_report_atomic_and_sealed(tmp_path):
    target = tmp_path / "report" / "report.json"
    metrics = _FakeMetrics(passed=5, failed=0, status_code_counts={200: 50})
    ok = sr3_report.write_report(metrics, str(target))

    assert ok is True
    assert target.exists()
    assert not (tmp_path / "report" / "report.json.tmp").exists()  # no leftover tmp

    data = json.loads(target.read_text())
    assert data["status"] == "final"
    assert data["measures"]["flows_passed"] == 5
    assert data["measures"]["steps.by_code"]["200"] == 50


def test_write_report_unwritable_path_degrades():
    # Path under a file (not a dir) can't be created -> returns False, never raises.
    assert sr3_report.write_report(_FakeMetrics(), "/dev/null/nope/report.json") is False


def test_sr_report_path_env_override(tmp_path, monkeypatch):
    target = tmp_path / "custom.json"
    monkeypatch.setenv("SR_REPORT_PATH", str(target))
    assert sr3_report.write_report(_FakeMetrics(passed=1)) is True
    assert json.loads(target.read_text())["measures"]["flows_passed"] == 1


def test_real_metrics_recorders_feed_report():
    """End-to-end: the real flow_runner.Metrics recorders populate the fields
    the report reads (pass/fail tallies + per-status-code counts)."""
    from flow_runner import Metrics

    async def _drive():
        metrics = Metrics()
        # Two passing flows, one failing flow.
        await metrics.record_flow_result(True)
        await metrics.record_flow_result(True)
        await metrics.record_flow_result(False)
        # A spread of response codes.
        for _ in range(4):
            await metrics.record_status_code(200)
        await metrics.record_status_code(403)
        await metrics.record_status_code(500)
        await metrics.record_status_code(598)  # synthetic connection-error code
        return metrics

    metrics = asyncio.run(_drive())
    assert metrics.flows_passed == 2
    assert metrics.flows_failed == 1

    report = sr3_report.build_report(metrics)
    m = report["measures"]
    assert m["flows_passed"] == 2
    assert m["flows_failed"] == 1
    assert m["flow_success_ratio"] == round(2 / 3, 4)
    assert m["steps.by_code"] == {"200": 4, "403": 1, "500": 1, "598": 1}
