import sys
import os
import types
sys.modules.setdefault("psutil", types.ModuleType("psutil"))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

pydantic = types.ModuleType("pydantic")
class BaseModel:
    def __init__(self, **data):
        for k,v in data.items():
            setattr(self, k, v)
    @classmethod
    def model_rebuild(cls):
        pass

def Field(default=None, *args, **kwargs):
    return default

def validator(*args, **kwargs):
    def decorator(fn):
        return fn
    return decorator

RootModel = BaseModel
field_validator = validator
model_validator = validator
ConfigDict = dict
setattr(pydantic, "BaseModel", BaseModel)
setattr(pydantic, "Field", Field)
setattr(pydantic, "validator", validator)
setattr(pydantic, "RootModel", RootModel)
setattr(pydantic, "field_validator", field_validator)
setattr(pydantic, "model_validator", model_validator)
setattr(pydantic, "ConfigDict", ConfigDict)
sys.modules.setdefault("pydantic", pydantic)
import importlib
del sys.modules["pydantic"]
sys.modules["pydantic"] = importlib.import_module("pydantic")
import asyncio
from typing import Any, Dict
from unittest.mock import AsyncMock, MagicMock

import pytest
import logging
import aiohttp
import copy

from flow_runner import (
    FlowRunner,
    ContainerConfig,
    FlowMap,
    RequestStep,
    LoopStep,
    ConditionStep,
    ConditionData,
    Metrics,
    get_value_from_context, _MISSING, set_value_in_context,
)


@pytest.fixture
def base_config() -> ContainerConfig:
    return ContainerConfig(flow_target_url="http://example.com", sim_users=1)


@pytest.fixture
def empty_flow() -> FlowMap:
    return FlowMap(name="test", steps=[], staticVars={"static": "val"})


def make_runner(config: ContainerConfig, flow: FlowMap) -> FlowRunner:
    metrics = Metrics()
    metrics.increment = AsyncMock()
    metrics.record_flow_duration = AsyncMock()
    runner = FlowRunner(config, flow, metrics)
    runner.metrics = metrics
    return runner


def test_init_override_step_url_host_default(base_config, empty_flow):
    runner = make_runner(base_config, empty_flow)
    assert runner.config.override_step_url_host is True


def test_init_override_step_url_host_false(empty_flow):
    cfg = ContainerConfig(flow_target_url="http://example.com", sim_users=1, override_step_url_host=False)
    runner = make_runner(cfg, empty_flow)
    assert runner.config.override_step_url_host is False


def test_get_value_from_context_basic():
    ctx = {"a": {"b": [1, {"c": 2}]}}
    assert get_value_from_context(ctx, "a.b[1].c") == 2
    assert get_value_from_context(ctx, "a.b[0]") == 1
    assert get_value_from_context(ctx, "missing") is _MISSING

@pytest.mark.asyncio
async def test_substitute_variables_string_and_markers(base_config, empty_flow):
    runner = make_runner(base_config, empty_flow)
    context = {"foo": "BAR", "data": {"num": 5}, "obj": {"k": "v"}}
    assert runner._substitute_variables("Value {{foo}}", context) == "Value BAR"
    assert runner._substitute_variables("##VAR:string:foo##", context) == "BAR"
    assert runner._substitute_variables("##VAR:unquoted:obj##", context) == {"k": "v"}
    assert runner._substitute_variables("Missing {{none}}", context) == "Missing "


def test_extract_data_status_headers_and_body(base_config, empty_flow):
    runner = make_runner(base_config, empty_flow)
    ctx: Dict[str, Any] = {}
    body = {"user": {"id": 1}}
    headers = {"Content-Type": "application/json"}
    rules = {
        "status_code": ".status",
        "ctype": "headers.Content-Type",
        "user_id": "body.user.id",
        "user_id2": "user.id",
    }
    runner._extract_data(body, rules, ctx, 201, headers)
    assert ctx["status_code"] == 201
    assert ctx["ctype"] == "application/json"
    assert ctx["user_id"] == 1
    assert ctx["user_id2"] == 1


def test_extract_data_non_dict_body(base_config, empty_flow):
    runner = make_runner(base_config, empty_flow)
    ctx: Dict[str, Any] = {}
    body = "plain text"
    headers = {}
    runner._extract_data(body, {"user": "user.id"}, ctx, 200, headers)
    assert ctx["user"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "operator,left,right,expected",
    [
        ("equals", 5, "5", True),
        ("not_equals", 5, "6", True),
        ("greater_than", 5, "4", True),
        ("less_than", 5, "6", True),
        ("contains", ["a", "b"], "a", True),
        ("starts_with", "abc", "a", True),
        ("ends_with", "abc", "c", True),
        ("matches_regex", "abc123", r"\d+", True),
        ("exists", "x", "", True),
        ("not_exists", None, "", True),
        ("is_number", 3, "", True),
        ("is_text", "t", "", True),
        ("is_boolean", True, "", True),
        ("is_array", [1], "", True),
        ("is_true", True, "", True),
        ("is_false", False, "", True),
    ],
)
async def test_evaluate_structured_condition(operator, left, right, expected, base_config, empty_flow):
    runner = make_runner(base_config, empty_flow)
    ctx = {"val": left}
    data = ConditionData(variable="val", operator=operator, value=right)
    assert runner._evaluate_structured_condition(data, ctx) is expected


@pytest.mark.asyncio
async def test_evaluate_structured_condition_edge_cases(base_config, empty_flow, caplog):
    runner = make_runner(base_config, empty_flow)

    ctx = {"val": "abc"}
    data = ConditionData(variable="val", operator="matches_regex", value="(")
    with caplog.at_level(logging.ERROR, logger="FlowRunner"):
        assert runner._evaluate_structured_condition(data, ctx) is False

    ctx_nan = {"val": float('nan')}
    data_nan = ConditionData(variable="val", operator="greater_than", value="1")
    assert runner._evaluate_structured_condition(data_nan, ctx_nan) is False

    ctx_num = {"val": 5}
    data_bad = ConditionData(variable="val", operator="less_than", value="abc")
    assert runner._evaluate_structured_condition(data_bad, ctx_num) is False

    ctx_bool = {"val": "true"}
    data_bool = ConditionData(variable="val", operator="is_true", value="")
    assert runner._evaluate_structured_condition(data_bool, ctx_bool) is False


@pytest.mark.asyncio
async def test_execute_loop_step_iterates_and_isolates_context(monkeypatch, base_config, empty_flow):
    runner = make_runner(base_config, empty_flow)
    step = LoopStep(id="l1", type="loop", source="{{items}}", loopVariable="item", steps=[])
    ctx = {"items": [1, 2]}
    calls = []

    async def fake_execute_steps(steps, session, base_h, flow_h, loop_ctx, depth):
        calls.append(loop_ctx["item"])
    monkeypatch.setattr(runner, "_execute_steps", fake_execute_steps)

    session = AsyncMock()
    runner.running = True
    await runner._execute_loop_step(step, session, {}, {}, ctx, 0, "u1")
    assert calls == [1, 2]


@pytest.mark.asyncio
async def test_execute_request_step_url_override(empty_flow):
    cfg = ContainerConfig(flow_target_url="http://base.com", sim_users=1)
    runner = make_runner(cfg, empty_flow)

    resp = AsyncMock()
    resp.status = 200
    resp.headers = {"Content-Type": "application/json"}
    resp.json = AsyncMock(return_value={})
    resp.text = AsyncMock(return_value="{}")
    resp.read = AsyncMock(return_value=b"{}")
    from unittest.mock import MagicMock
    session = MagicMock()
    cm = AsyncMock()
    cm.__aenter__.return_value = resp
    cm.__aexit__.return_value = AsyncMock()
    session.request.return_value = cm

    step = RequestStep(id="s1", type="request", method="GET", url="http://other.com/path", onFailure="continue")
    context: Dict[str, Any] = {}
    await runner._execute_request_step(step, session, {}, {}, context)
    called_url = session.request.call_args.args[1]
    assert called_url == "http://base.com/path"

    cfg2 = ContainerConfig(flow_target_url="http://base.com", sim_users=1, override_step_url_host=False)
    runner2 = make_runner(cfg2, empty_flow)
    session2 = MagicMock()
    cm2 = AsyncMock()
    cm2.__aenter__.return_value = resp
    cm2.__aexit__.return_value = AsyncMock()
    session2.request.return_value = cm2
    await runner2._execute_request_step(step, session2, {}, {}, context)
    assert session2.request.call_args.args[1] == "http://other.com/path"


@pytest.mark.asyncio
async def test_execute_request_step_url_override_preserves_query_and_fragment(empty_flow):
    cfg = ContainerConfig(flow_target_url="http://base.com", sim_users=1)
    runner = make_runner(cfg, empty_flow)

    resp = AsyncMock()
    resp.status = 200
    resp.headers = {"Content-Type": "application/json"}
    resp.json = AsyncMock(return_value={})
    resp.text = AsyncMock(return_value="{}")
    resp.read = AsyncMock(return_value=b"{}")
    session = MagicMock()
    cm = AsyncMock()
    cm.__aenter__.return_value = resp
    cm.__aexit__.return_value = AsyncMock()
    session.request.return_value = cm

    step = RequestStep(id="s1", type="request", method="GET", url="http://other.com/p?a=1#frag", onFailure="continue")
    await runner._execute_request_step(step, session, {}, {}, {})
    assert session.request.call_args.args[1] == "http://base.com/p?a=1#frag"


@pytest.mark.asyncio
async def test_execute_request_step_dns_override_host_header(empty_flow):
    cfg = ContainerConfig(flow_target_url="http://base.com", sim_users=1, flow_target_dns_override="1.2.3.4")
    runner = make_runner(cfg, empty_flow)

    resp = AsyncMock()
    resp.status = 200
    resp.headers = {"Content-Type": "application/json"}
    resp.json = AsyncMock(return_value={})
    resp.text = AsyncMock(return_value="{}")
    resp.read = AsyncMock(return_value=b"{}")
    session = MagicMock()
    cm = AsyncMock()
    cm.__aenter__.return_value = resp
    cm.__aexit__.return_value = AsyncMock()
    session.request.return_value = cm

    step = RequestStep(id="s1", type="request", method="GET", url="http://other.com/path", onFailure="continue")
    await runner._execute_request_step(step, session, {}, {}, {})
    called_url = session.request.call_args.args[1]
    called_headers = session.request.call_args.kwargs["headers"]
    assert called_url == "http://1.2.3.4/path"
    assert called_headers["Host"] == "base.com"


@pytest.mark.asyncio
async def test_execute_request_step_dns_override_absolute_url(empty_flow):
    cfg = ContainerConfig(flow_target_url="http://base.com", sim_users=1, flow_target_dns_override="1.2.3.4", override_step_url_host=False)
    runner = make_runner(cfg, empty_flow)

    resp = AsyncMock()
    resp.status = 200
    resp.headers = {"Content-Type": "application/json"}
    resp.json = AsyncMock(return_value={})
    resp.text = AsyncMock(return_value="{}")
    resp.read = AsyncMock(return_value=b"{}")
    session = MagicMock()
    cm = AsyncMock()
    cm.__aenter__.return_value = resp
    cm.__aexit__.return_value = AsyncMock()
    session.request.return_value = cm

    step_same = RequestStep(id="s1", type="request", method="GET", url="http://base.com/a", onFailure="continue")
    await runner._execute_request_step(step_same, session, {}, {}, {})
    called_url = session.request.call_args.args[1]
    called_headers = session.request.call_args.kwargs["headers"]
    assert called_url == "http://1.2.3.4/a"
    assert called_headers["Host"] == "base.com"

    step_diff = RequestStep(id="s2", type="request", method="GET", url="http://other.com/a", onFailure="continue")
    await runner._execute_request_step(step_diff, session, {}, {}, {})
    second_url = session.request.call_args.args[1]
    second_headers = session.request.call_args.kwargs["headers"]
    assert second_url == "http://other.com/a"
    assert "Host" not in second_headers


@pytest.mark.asyncio
async def test_execute_request_step_on_failure(empty_flow):
    cfg = ContainerConfig(flow_target_url="http://base.com", sim_users=1)
    runner = make_runner(cfg, empty_flow)

    resp = AsyncMock()
    resp.status = 404
    resp.headers = {"Content-Type": "text/plain"}
    resp.text = AsyncMock(return_value="notfound")
    resp.read = AsyncMock(return_value=b"notfound")
    session = MagicMock()
    cm = AsyncMock()
    cm.__aenter__.return_value = resp
    cm.__aexit__.return_value = AsyncMock()
    session.request.return_value = cm

    step = RequestStep(id="s1", type="request", method="GET", url="/missing", onFailure="stop")
    ctx: Dict[str, Any] = {}
    await runner._execute_request_step(step, session, {}, {}, ctx)
    assert ctx["flow_error"]

    step2 = RequestStep(id="s1", type="request", method="GET", url="/missing", onFailure="continue")
    ctx2: Dict[str, Any] = {}
    await runner._execute_request_step(step2, session, {}, {}, ctx2)
    assert ctx2.get("flow_error") is None


@pytest.mark.asyncio
async def test_execute_request_step_retries_server_error(monkeypatch, empty_flow):
    cfg = ContainerConfig(flow_target_url="http://base.com", sim_users=1)
    runner = make_runner(cfg, empty_flow)

    resp1 = AsyncMock()
    resp1.status = 503
    resp1.headers = {"Content-Type": "application/json"}
    resp1.json = AsyncMock(return_value={})
    resp1.text = AsyncMock(return_value="{}")
    resp1.read = AsyncMock(return_value=b"{}")

    resp2 = AsyncMock()
    resp2.status = 200
    resp2.headers = {"Content-Type": "application/json"}
    resp2.json = AsyncMock(return_value={})
    resp2.text = AsyncMock(return_value="{}")
    resp2.read = AsyncMock(return_value=b"{}")

    session = MagicMock()
    cm1 = AsyncMock(); cm1.__aenter__.return_value = resp1; cm1.__aexit__.return_value = AsyncMock()
    cm2 = AsyncMock(); cm2.__aenter__.return_value = resp2; cm2.__aexit__.return_value = AsyncMock()
    session.request.side_effect = [cm1, cm2]

    monkeypatch.setattr(asyncio, "sleep", AsyncMock())

    step = RequestStep(id="s1", type="request", method="GET", url="/a", onFailure="continue")
    await runner._execute_request_step(step, session, {}, {}, {})
    assert session.request.call_count == 2
    assert runner.metrics.increment.await_count == 1


@pytest.mark.asyncio
async def test_execute_request_step_retries_connection_error(monkeypatch, empty_flow):
    cfg = ContainerConfig(flow_target_url="http://base.com", sim_users=1)
    runner = make_runner(cfg, empty_flow)

    resp = AsyncMock()
    resp.status = 200
    resp.headers = {"Content-Type": "application/json"}
    resp.json = AsyncMock(return_value={})
    resp.text = AsyncMock(return_value="{}")
    resp.read = AsyncMock(return_value=b"{}")

    cm_success = AsyncMock(); cm_success.__aenter__.return_value = resp; cm_success.__aexit__.return_value = AsyncMock()
    session = MagicMock()
    session.request.side_effect = [aiohttp.ClientConnectionError(), cm_success]

    monkeypatch.setattr(asyncio, "sleep", AsyncMock())

    step = RequestStep(id="s1", type="request", method="GET", url="/a", onFailure="continue")
    await runner._execute_request_step(step, session, {}, {}, {})
    assert session.request.call_count == 2
    assert runner.metrics.increment.await_count == 1


@pytest.mark.asyncio
async def test_execute_request_step_metrics_not_incremented_on_failure(monkeypatch, empty_flow):
    cfg = ContainerConfig(flow_target_url="http://base.com", sim_users=1)
    runner = make_runner(cfg, empty_flow)

    session = MagicMock()
    session.request.side_effect = aiohttp.ClientConnectionError()

    monkeypatch.setattr(asyncio, "sleep", AsyncMock())

    step = RequestStep(id="s1", type="request", method="GET", url="/a", onFailure="continue")
    await runner._execute_request_step(step, session, {}, {}, {})
    assert runner.metrics.increment.await_count == 0


@pytest.mark.asyncio
async def test_run_stop_continuous(monkeypatch, base_config, empty_flow):
    cfg = ContainerConfig(flow_target_url="http://example.com", sim_users=1, min_sleep_ms=1, max_sleep_ms=1)
    runner = make_runner(cfg, empty_flow)

    contexts = []
    async def fake_execute_steps(steps, session, base_headers=None, flow_headers=None, context=None, depth=0):
        contexts.append(context.copy())
        if len(contexts) >= 2:
            runner.running = False
    monkeypatch.setattr(runner, "_execute_steps", fake_execute_steps)
    monkeypatch.setattr(runner, "create_aiohttp_connector", lambda: MagicMock())
    monkeypatch.setattr(runner, "create_session", lambda conn: MagicMock())

    sleep_calls = []
    original_sleep = asyncio.sleep
    async def fake_sleep(d):
        sleep_calls.append(d)
        await original_sleep(0)
    monkeypatch.setattr(asyncio, "sleep", fake_sleep)

    task = asyncio.create_task(runner.run())
    await original_sleep(0.01)
    await runner.stop()
    await task

    assert len(contexts) >= 1
    assert sleep_calls
    if len(contexts) >= 2:
        assert contexts[0]["flowInstance"] == 1
        assert contexts[1]["flowInstance"] == 2


@pytest.mark.asyncio
async def test_condition_branch_passes_copied_context(monkeypatch, base_config):
    cond_step = ConditionStep(
        id="c1",
        type="condition",
        conditionData=ConditionData(variable="v", operator="equals", value="1"),
        then=[{"id": "t1", "type": "request", "method": "GET", "url": "/", "onFailure": "continue"}],
        else_=[],
    )
    flow = FlowMap(name="f", steps=[cond_step], staticVars={})
    runner = make_runner(base_config, flow)

    monkeypatch.setattr(runner, "_evaluate_condition", lambda *args, **kw: True)
    branch_contexts = []
    orig_execute = runner._execute_steps

    async def patched(steps, session, base_h, flow_h, ctx, depth=0):
        if depth > 0:
            branch_contexts.append(ctx)
            return
        return await orig_execute(steps, session, base_h, flow_h, ctx, depth)

    monkeypatch.setattr(runner, "_execute_steps", patched)

    runner.running = True
    session = AsyncMock()
    ctx = {"v": "1"}
    await runner._execute_steps([cond_step], session, {}, {}, ctx)
    assert branch_contexts
    assert branch_contexts[0]["v"] == "1"


@pytest.mark.asyncio
async def test_condition_evaluation_error_sets_error(monkeypatch, base_config):
    cond_step = ConditionStep(
        id="c1",
        type="condition",
        conditionData=ConditionData(variable="v", operator="equals", value="1"),
        then=[{"id": "t1", "type": "request", "method": "GET", "url": "/", "onFailure": "continue"}],
        else_=[{"id": "e1", "type": "request", "method": "GET", "url": "/", "onFailure": "continue"}],
    )
    flow = FlowMap(name="f", steps=[cond_step], staticVars={})
    runner = make_runner(base_config, flow)

    monkeypatch.setattr(runner, "_evaluate_condition", MagicMock(side_effect=Exception("boom")))
    branch_called = False
    orig_exec = runner._execute_steps

    async def patched_exec(steps, session, b, f, ctx, depth=0):
        nonlocal branch_called
        if depth > 0:
            branch_called = True
            return
        return await orig_exec(steps, session, b, f, ctx, depth)

    monkeypatch.setattr(runner, "_execute_steps", patched_exec)
    runner.running = True
    session = AsyncMock()
    ctx = {"v": "1"}
    await runner._execute_steps([cond_step], session, {}, {}, ctx)
    assert branch_called is False
    assert ctx.get("flow_error")


@pytest.mark.asyncio
@pytest.mark.parametrize("val", ["str", 1, {"a": 1}, None])
async def test_execute_loop_step_invalid_sources(monkeypatch, base_config, val, caplog):
    flow = FlowMap(name="f", steps=[], staticVars={})
    runner = make_runner(base_config, flow)
    step = LoopStep(id="l1", type="loop", source="{{items}}", loopVariable="i", steps=[{}])
    session = AsyncMock()
    monkeypatch.setattr(runner, "_execute_steps", AsyncMock())
    ctx = {"items": val}
    runner.running = True
    with caplog.at_level(logging.WARNING):
        await runner._execute_loop_step(step, session, {}, {}, ctx, 0, "u")
    runner._execute_steps.assert_not_called()


@pytest.mark.asyncio
async def test_on_iteration_start_called_with_context(monkeypatch, base_config):
    callback_calls = []
    def on_iter(n, ctx):
        callback_calls.append((n, ctx.copy()))

    flow = FlowMap(name="f", steps=[], staticVars={"x": 1})
    runner = make_runner(base_config, flow)
    runner.on_iteration_start = on_iter

    monkeypatch.setattr(runner, "create_aiohttp_connector", lambda: MagicMock(closed=False, close=AsyncMock()))
    monkeypatch.setattr(runner, "create_session", lambda conn: MagicMock(closed=False, close=AsyncMock()))

    async def fake_steps(steps, session, base_headers=None, flow_headers=None, context=None, depth=0):
        if context["flowInstance"] >= 2:
            runner.running = False

    monkeypatch.setattr(runner, "_execute_steps", fake_steps)
    monkeypatch.setattr(asyncio, "sleep", AsyncMock())

    runner.running = True
    await runner.simulate_user_lifecycle(1)
    assert callback_calls and callback_calls[0][0] == 2
    assert callback_calls[0][1]["flowInstance"] == 2


@pytest.mark.asyncio
async def test_start_and_stop_generating_updates_active_count(monkeypatch, base_config, empty_flow):
    runner = make_runner(base_config, empty_flow)

    async def fake_user(user_id):
        async with runner.lock:
            runner._active_users_count += 1
        try:
            while runner.running:
                await original_sleep(0)
        except asyncio.CancelledError:
            pass
        finally:
            async with runner.lock:
                runner._active_users_count -= 1

    monkeypatch.setattr(runner, "simulate_user_lifecycle", fake_user)
    original_sleep = asyncio.sleep
    monkeypatch.setattr(asyncio, "sleep", AsyncMock())

    task = asyncio.create_task(runner.start_generating())
    await original_sleep(0)
    await original_sleep(0)
    assert runner.get_active_user_count() == 1
    await runner.stop_generating()
    await task
    assert runner.get_active_user_count() == 0





def test_container_config_alias_override_step_url_host():
    cfg = ContainerConfig(
        flow_target_url="http://example.com",
        sim_users=1,
        **{"Override Step URL Host": False},
    )
    assert cfg.override_step_url_host is False


def test_container_config_validation_errors():
    pydantic = sys.modules["pydantic"]
    with pytest.raises(pydantic.ValidationError):
        ContainerConfig(
            flow_target_url="http://example.com",
            sim_users=0,
        )

    with pytest.raises(pydantic.ValidationError):
        ContainerConfig(
            flow_target_url="http://example.com",
            sim_users=1,
            min_sleep_ms=10,
            max_sleep_ms=5,
        )


def test_get_value_from_context_edge_cases():
    ctx = {
        "a": {"b": [1, {"c": 2}]},
        "zero": 0,
        "none": None,
        "false": False,
    }

    assert get_value_from_context(ctx, "") is _MISSING
    assert get_value_from_context(ctx, "a.b[1].missing") is _MISSING
    assert get_value_from_context(ctx, "a.b[2]") is _MISSING
    assert get_value_from_context(ctx, "a.b.key") is _MISSING
    assert get_value_from_context(ctx, "a[0]") is _MISSING
    assert get_value_from_context(ctx, "a.b[0].c") is _MISSING
    assert get_value_from_context(ctx, "zero") == 0
    assert get_value_from_context(ctx, "none") is None
    assert get_value_from_context(ctx, "false") is False
    assert get_value_from_context(None, "a") is _MISSING


def test_set_value_in_context_nested_creation():
    ctx: Dict[str, Any] = {}
    set_value_in_context(ctx, "x.y.z", 5)
    assert ctx == {"x": {"y": {"z": 5}}}


def test_set_value_in_context_invalid_indices():
    ctx = {"arr": [0]}
    set_value_in_context(ctx, "arr[2]", 9)
    assert ctx["arr"] == [0]
    set_value_in_context(ctx, "arr[0].a", 1)  # type mismatch should not raise
    assert ctx["arr"] == [0]


def test_set_value_in_context_invalid_context():
    set_value_in_context(None, "a", 1)  # Should not raise


def test_substitute_variables_unquoted_and_malformed(base_config, empty_flow):
    runner = make_runner(base_config, empty_flow)
    context = {"none": None, "lst": [], "d": {}}

    assert runner._substitute_variables("##VAR:unquoted:none##", context) is None
    assert runner._substitute_variables("##VAR:unquoted:lst##", context) == []
    assert runner._substitute_variables("##VAR:unquoted:d##", context) == {}

    assert (
        runner._substitute_variables("##VAR:name##", context)
        == "##VAR:name##"
    )
    assert (
        runner._substitute_variables("##VAR:unquoted:name:extra##", context)
        is None
    )
