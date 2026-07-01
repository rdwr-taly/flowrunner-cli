"""Tests for additive declarative ``step.assertions`` on request steps.

Assertions reuse the frozen ``conditionData`` operator vocabulary and are
evaluated against the request result (status/headers/body/extracted vars) after
the request completes. Pass/fail is recorded into the execution context; unknown
operators or missing targets degrade to a FAILED assertion with a warning and
never crash the run.
"""

import logging
from typing import Any, Dict
from unittest.mock import AsyncMock, MagicMock

import pytest

from flow_runner import (
    Assertion,
    ContainerConfig,
    FlowMap,
    FlowRunner,
    Metrics,
    RequestStep,
    get_value_from_context,
)


@pytest.fixture
def empty_flow() -> FlowMap:
    return FlowMap(name="test", steps=[], staticVars={})


def make_runner(config: ContainerConfig, flow: FlowMap) -> FlowRunner:
    metrics = Metrics()
    metrics.increment = AsyncMock()
    metrics.record_flow_duration = AsyncMock()
    runner = FlowRunner(config, flow, metrics)
    runner.metrics = metrics
    runner.running = True
    return runner


def _resp(status: int, body):
    r = AsyncMock()
    r.status = status
    r.headers = {"Content-Type": "application/json"}
    r.json = AsyncMock(return_value=body)
    r.text = AsyncMock(return_value="{}")
    r.read = AsyncMock(return_value=b"{}")
    return r


def _session(resp):
    session = MagicMock()
    cm = AsyncMock()
    cm.__aenter__.return_value = resp
    cm.__aexit__.return_value = AsyncMock()
    session.request.return_value = cm
    return session


# --- model parsing ----------------------------------------------------------

def test_request_step_assertions_parsed():
    step = RequestStep.model_validate({
        "id": "s1", "type": "request", "method": "GET", "url": "/a",
        "onFailure": "continue",
        "assertions": [
            {"name": "ok", "variable": "response_s1_status", "operator": "equals", "value": "200"},
        ],
    })
    assert step.assertions is not None
    assert isinstance(step.assertions[0], Assertion)
    assert step.assertions[0].operator == "equals"


def test_request_step_assertions_absent_defaults_none():
    step = RequestStep.model_validate({
        "id": "s1", "type": "request", "method": "GET", "url": "/a",
        "onFailure": "continue",
    })
    assert step.assertions is None


# --- evaluation: pass / fail recorded --------------------------------------

@pytest.mark.asyncio
async def test_assertions_all_pass_recorded(empty_flow):
    cfg = ContainerConfig(flow_target_url="http://base.com", sim_users=1)
    runner = make_runner(cfg, empty_flow)
    session = _session(_resp(200, {"data": {"ok": True, "count": 5}}))

    step = RequestStep(
        id="s1", type="request", method="GET", url="/a", onFailure="continue",
        assertions=[
            Assertion(name="status ok", variable="response_s1_status", operator="equals", value="200"),
            Assertion(name="flag true", variable="response_s1_body.data.ok", operator="is_true"),
            Assertion(name="count > 3", variable="response_s1_body.data.count", operator="greater_than", value="3"),
        ],
    )
    ctx: Dict[str, Any] = {}
    await runner._execute_request_step(step, session, {}, {}, ctx)

    results = get_value_from_context(ctx, "response_s1_assertions")
    assert isinstance(results, list) and len(results) == 3
    assert all(r["passed"] for r in results)
    assert get_value_from_context(ctx, "response_s1_assertions_passed") is True


@pytest.mark.asyncio
async def test_assertions_failure_recorded(empty_flow):
    cfg = ContainerConfig(flow_target_url="http://base.com", sim_users=1)
    runner = make_runner(cfg, empty_flow)
    session = _session(_resp(500, {"data": {"ok": False}}))

    step = RequestStep(
        id="s1", type="request", method="GET", url="/a", onFailure="continue",
        assertions=[
            Assertion(name="expects 200", variable="response_s1_status", operator="equals", value="200"),
        ],
    )
    ctx: Dict[str, Any] = {}
    await runner._execute_request_step(step, session, {}, {}, ctx)

    results = get_value_from_context(ctx, "response_s1_assertions")
    assert len(results) == 1
    assert results[0]["passed"] is False
    assert get_value_from_context(ctx, "response_s1_assertions_passed") is False


# --- degrade gracefully -----------------------------------------------------

@pytest.mark.asyncio
async def test_unknown_operator_degrades_without_crash(empty_flow, caplog):
    cfg = ContainerConfig(flow_target_url="http://base.com", sim_users=1)
    runner = make_runner(cfg, empty_flow)
    session = _session(_resp(200, {}))

    step = RequestStep(
        id="s1", type="request", method="GET", url="/a", onFailure="continue",
        assertions=[
            Assertion(name="weird", variable="response_s1_status", operator="frobnicate", value="x"),
        ],
    )
    ctx: Dict[str, Any] = {}
    with caplog.at_level(logging.WARNING):
        # Must not raise.
        await runner._execute_request_step(step, session, {}, {}, ctx)

    results = get_value_from_context(ctx, "response_s1_assertions")
    assert len(results) == 1
    # Unknown operator => failed assertion, flagged, run continues.
    assert results[0]["passed"] is False
    assert get_value_from_context(ctx, "response_s1_assertions_passed") is False


@pytest.mark.asyncio
async def test_unknown_target_missing_variable_degrades(empty_flow):
    cfg = ContainerConfig(flow_target_url="http://base.com", sim_users=1)
    runner = make_runner(cfg, empty_flow)
    session = _session(_resp(200, {}))

    step = RequestStep(
        id="s1", type="request", method="GET", url="/a", onFailure="continue",
        assertions=[
            Assertion(name="missing exists", variable="response_s1_body.nope.deep", operator="exists"),
        ],
    )
    ctx: Dict[str, Any] = {}
    await runner._execute_request_step(step, session, {}, {}, ctx)
    results = get_value_from_context(ctx, "response_s1_assertions")
    assert results[0]["passed"] is False  # missing target => 'exists' is False


@pytest.mark.asyncio
async def test_no_assertions_records_nothing(empty_flow):
    cfg = ContainerConfig(flow_target_url="http://base.com", sim_users=1)
    runner = make_runner(cfg, empty_flow)
    session = _session(_resp(200, {}))

    step = RequestStep(id="s1", type="request", method="GET", url="/a", onFailure="continue")
    ctx: Dict[str, Any] = {}
    await runner._execute_request_step(step, session, {}, {}, ctx)

    from flow_runner import _MISSING
    assert get_value_from_context(ctx, "response_s1_assertions") is _MISSING
    assert get_value_from_context(ctx, "response_s1_assertions_passed") is _MISSING
