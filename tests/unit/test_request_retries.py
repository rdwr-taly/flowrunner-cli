"""Tests for the additive per-request ``step.retries={count, delayMs}`` policy.

Mirrors the FlowRunner UI JS engine (flowRunner.js ``_executeRequestStep``):

- ``count`` defaults to 0 => single attempt, IDENTICAL to prior behavior.
- A retry fires on a non-2xx HTTP status OR a network/fetch error.
- ``delayMs`` is slept between attempts.
- A user-requested stop (``self.running == False``) is NEVER retried.
- Each attempt issues a fresh request (the CLI's analogue of a fresh
  AbortController per attempt in the browser).
"""

from typing import Any, Dict
from unittest.mock import AsyncMock, MagicMock

import aiohttp
import asyncio
import pytest

from flow_runner import (
    ContainerConfig,
    FlowMap,
    FlowRunner,
    Metrics,
    RequestStep,
    RetryConfig,
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
    # A request step only ever executes while the runner is actively running;
    # the executor loop runs inside `while self.running`. Reflect that here so
    # the user-retry policy (which must not fire past a user-stop) is exercised.
    runner.running = True
    return runner


def _resp(status: int):
    r = AsyncMock()
    r.status = status
    r.headers = {"Content-Type": "application/json"}
    r.json = AsyncMock(return_value={})
    r.text = AsyncMock(return_value="{}")
    r.read = AsyncMock(return_value=b"{}")
    return r


def _cm(resp):
    cm = AsyncMock()
    cm.__aenter__.return_value = resp
    cm.__aexit__.return_value = AsyncMock()
    return cm


def _session_from(side_effect):
    session = MagicMock()
    session.request.side_effect = side_effect
    return session


# --- retries model on RequestStep ------------------------------------------

def test_request_step_retries_field_parsed():
    step = RequestStep.model_validate({
        "id": "s1", "type": "request", "method": "GET", "url": "/a",
        "onFailure": "continue", "retries": {"count": 2, "delayMs": 50},
    })
    assert isinstance(step.retries, RetryConfig)
    assert step.retries.count == 2
    assert step.retries.delayMs == 50


def test_request_step_retries_absent_defaults_none():
    step = RequestStep.model_validate({
        "id": "s1", "type": "request", "method": "GET", "url": "/a",
        "onFailure": "continue",
    })
    assert step.retries is None


# --- default (count 0) is a single attempt ---------------------------------

@pytest.mark.asyncio
async def test_no_retries_single_attempt_on_non_2xx(monkeypatch, empty_flow):
    cfg = ContainerConfig(flow_target_url="http://base.com", sim_users=1)
    runner = make_runner(cfg, empty_flow)
    session = _session_from([_cm(_resp(404))])
    monkeypatch.setattr(asyncio, "sleep", AsyncMock())

    step = RequestStep(id="s1", type="request", method="GET", url="/a", onFailure="continue")
    await runner._execute_request_step(step, session, {}, {}, {})
    assert session.request.call_count == 1  # no user-retry on 4xx by default


# --- retry on non-2xx ------------------------------------------------------

@pytest.mark.asyncio
async def test_retries_on_non_2xx_then_success(monkeypatch, empty_flow):
    cfg = ContainerConfig(flow_target_url="http://base.com", sim_users=1)
    runner = make_runner(cfg, empty_flow)
    session = _session_from([_cm(_resp(404)), _cm(_resp(200))])
    sleep_mock = AsyncMock()
    monkeypatch.setattr(asyncio, "sleep", sleep_mock)

    step = RequestStep(
        id="s1", type="request", method="GET", url="/a", onFailure="continue",
        retries=RetryConfig(count=2, delayMs=25),
    )
    ctx: Dict[str, Any] = {}
    await runner._execute_request_step(step, session, {}, {}, ctx)
    assert session.request.call_count == 2  # 404 then 200
    from flow_runner import get_value_from_context
    assert get_value_from_context(ctx, "response_s1_status") == 200
    # delayMs was slept at least once
    assert any(call.args and abs(call.args[0] - 0.025) < 1e-9 for call in sleep_mock.await_args_list)


@pytest.mark.asyncio
async def test_retries_exhausted_on_persistent_non_2xx(monkeypatch, empty_flow):
    cfg = ContainerConfig(flow_target_url="http://base.com", sim_users=1)
    runner = make_runner(cfg, empty_flow)
    # Use 404 (a non-2xx the built-in 5xx resilience loop does NOT retry) so
    # the attempt count reflects ONLY the user retry policy.
    session = _session_from([_cm(_resp(404)), _cm(_resp(404)), _cm(_resp(404))])
    monkeypatch.setattr(asyncio, "sleep", AsyncMock())

    step = RequestStep(
        id="s1", type="request", method="GET", url="/a", onFailure="continue",
        retries=RetryConfig(count=2, delayMs=0),
    )
    ctx: Dict[str, Any] = {}
    await runner._execute_request_step(step, session, {}, {}, ctx)
    # 1 initial + 2 retries = 3 total attempts, all 404.
    assert session.request.call_count == 3
    from flow_runner import get_value_from_context
    assert get_value_from_context(ctx, "response_s1_status") == 404


# --- retry on network error -------------------------------------------------

@pytest.mark.asyncio
async def test_retries_on_network_error_then_success(monkeypatch, empty_flow):
    cfg = ContainerConfig(flow_target_url="http://base.com", sim_users=1)
    runner = make_runner(cfg, empty_flow)
    session = _session_from([aiohttp.ClientConnectionError(), _cm(_resp(200))])
    monkeypatch.setattr(asyncio, "sleep", AsyncMock())

    step = RequestStep(
        id="s1", type="request", method="GET", url="/a", onFailure="continue",
        retries=RetryConfig(count=3, delayMs=0),
    )
    ctx: Dict[str, Any] = {}
    await runner._execute_request_step(step, session, {}, {}, ctx)
    assert session.request.call_count == 2
    from flow_runner import get_value_from_context
    assert get_value_from_context(ctx, "response_s1_status") == 200


# --- user-stop is never retried --------------------------------------------

@pytest.mark.asyncio
async def test_user_stop_not_retried(monkeypatch, empty_flow):
    cfg = ContainerConfig(flow_target_url="http://base.com", sim_users=1)
    runner = make_runner(cfg, empty_flow)
    runner.running = False  # simulate a user-requested stop
    # 404 (not retried by the built-in 5xx loop) isolates the user-retry path.
    session = _session_from([_cm(_resp(404)), _cm(_resp(200))])
    monkeypatch.setattr(asyncio, "sleep", AsyncMock())

    step = RequestStep(
        id="s1", type="request", method="GET", url="/a", onFailure="continue",
        retries=RetryConfig(count=5, delayMs=0),
    )
    await runner._execute_request_step(step, session, {}, {}, {})
    # Stop signal => no user-retry, single attempt only.
    assert session.request.call_count == 1


# --- a 2xx never triggers user-retry ---------------------------------------

@pytest.mark.asyncio
async def test_success_2xx_no_retry(monkeypatch, empty_flow):
    cfg = ContainerConfig(flow_target_url="http://base.com", sim_users=1)
    runner = make_runner(cfg, empty_flow)
    session = _session_from([_cm(_resp(200)), _cm(_resp(200))])
    monkeypatch.setattr(asyncio, "sleep", AsyncMock())

    step = RequestStep(
        id="s1", type="request", method="GET", url="/a", onFailure="continue",
        retries=RetryConfig(count=3, delayMs=0),
    )
    await runner._execute_request_step(step, session, {}, {}, {})
    assert session.request.call_count == 1
