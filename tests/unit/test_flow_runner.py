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
from pydantic import ValidationError

from flow_runner import (
    FlowRunner,
    ContainerConfig,
    FlowMap,
    RequestStep,
    LoopStep,
    ConditionStep,
    TransformStep,
    TransformOp,
    ConditionData,
    Metrics,
    StartRequest,
    get_value_from_context,
    _MISSING,
    set_value_in_context,
    execute_transform_ops,
    _normalize_transform_op,
    logger as fr_logger,
)


@pytest.fixture
def base_config() -> ContainerConfig:
    return ContainerConfig(flow_target_url="http://example.com", sim_users=1)


@pytest.fixture
def empty_flow() -> FlowMap:
    return FlowMap(name="test", steps=[], staticVars={"static": "val"})


def test_configure_logging_debug(empty_flow):
    cfg = ContainerConfig(flow_target_url="http://example.com", sim_users=1, debug=True)
    make_runner(cfg, empty_flow)
    assert fr_logger.level == logging.DEBUG
    assert all(h.level == logging.DEBUG for h in fr_logger.handlers)


def test_configure_logging_info(empty_flow):
    cfg = ContainerConfig(flow_target_url="http://example.com", sim_users=1, debug=False)
    make_runner(cfg, empty_flow)
    assert fr_logger.level == logging.INFO
    assert all(h.level == logging.INFO for h in fr_logger.handlers)


def make_runner(config: ContainerConfig, flow: FlowMap) -> FlowRunner:
    metrics = Metrics()
    metrics.increment = AsyncMock()
    metrics.record_flow_duration = AsyncMock()
    runner = FlowRunner(config, flow, metrics)
    runner.metrics = metrics
    return runner


def test_flowmap_accepts_numeric_id():
    fm = FlowMap(id=12345, name="test", steps=[], staticVars={})
    assert fm.id == 12345


def test_start_request_with_flowmaps(base_config, empty_flow):
    sr = StartRequest(config=base_config, flowmaps=[empty_flow])
    assert sr.flowmaps is not None and sr.flowmap is None


def test_start_request_requires_flow(base_config):
    with pytest.raises(ValidationError):
        StartRequest(config=base_config)


def test_init_override_step_url_host_default(base_config, empty_flow):
    runner = make_runner(base_config, empty_flow)
    assert runner.config.override_step_url_host is True


def test_init_override_step_url_host_false(empty_flow):
    cfg = ContainerConfig(flow_target_url="http://example.com", sim_users=1, override_step_url_host=False)
    runner = make_runner(cfg, empty_flow)
    assert runner.config.override_step_url_host is False


@pytest.mark.asyncio
async def test_metrics_resets_after_threshold():
    metrics = Metrics()
    metrics.MAX_FLOW_COUNT = 3
    for _ in range(3):
        await metrics.record_flow_duration(1.0)
    assert metrics.flow_count == 0
    assert metrics.flow_duration_sum == 0.0
    assert await metrics.get_average_flow_duration_ms() == 0.0


@pytest.mark.asyncio
async def test_metrics_increment_updates_rps():
    metrics = Metrics()
    await metrics.increment()
    await metrics.increment()
    assert metrics.last_rps_value >= 1
    await asyncio.sleep(1.1)
    await metrics.get_rps()
    assert metrics.last_rps_value == 0.0


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


def test_substitute_random_variables_cached(base_config, empty_flow):
    runner = make_runner(base_config, empty_flow)
    context = {}
    first_int = runner._substitute_variables("{{RANDOM_INT}}", context)
    second_int = runner._substitute_variables("{{RANDOM_INT}}", context)
    assert first_int == second_int
    assert int(first_int) >= 0

    first_range = runner._substitute_variables("{{RANDOM_INT(5, 5)}}", context)
    second_range = runner._substitute_variables("{{RANDOM_INT(5, 5)}}", context)
    assert first_range == "5"
    assert first_range == second_range

    first_str = runner._substitute_variables("{{RANDOM_STRING(8)}}", context)
    second_str = runner._substitute_variables("{{RANDOM_STRING(8)}}", context)
    assert first_str == second_str
    assert len(first_str) == 8


def test_substitute_random_unquoted_marker(base_config, empty_flow):
    runner = make_runner(base_config, empty_flow)
    context = {}
    value = runner._substitute_variables("##VAR:unquoted:RANDOM_INT(1, 1)##", context)
    assert value == 1


@pytest.mark.asyncio
async def test_transform_step_updates_context(base_config, empty_flow):
    runner = make_runner(base_config, empty_flow)
    context = {"payload": {"exp": 100}}
    step = TransformStep(
        id="t1",
        name="Transform",
        type="transform",
        ops=[
            {"op": "math_add", "set": "sum", "args": [1, 2]},
            {"op": "json_set", "set": "payload", "args": [{"ref": "payload"}, "exp", 110]},
        ],
    )
    await runner._execute_transform_step(step, context, depth=0, user_id_log="test")
    assert context["sum"] == 3
    assert context["payload"]["exp"] == 110


def test_execute_transform_ops_skips_unknown_op_without_downgrade():
    # An unknown/newer transform op must NOT be silently rewritten to base64_decode.
    # It is skipped with a machine-readable warning; later known ops still run.
    context: Dict[str, Any] = {}
    ops = [
        # "SGVsbG8" base64url-decodes to "Hello". If the old bug downgrades this to
        # base64_decode, context["decoded"] would become "Hello". It must stay unset.
        {"op": "totally_unknown_future_op", "set": "decoded", "args": ["SGVsbG8"]},
        {"op": "math_add", "set": "sum", "args": [1, 2]},
    ]
    output = execute_transform_ops(ops, context)
    # unknown op skipped: variable never set (definitely not base64-decoded to "Hello")
    assert "decoded" not in context
    # subsequent known op still executed
    assert context["sum"] == 3
    assert "sum" in output["updatedVars"]
    assert "decoded" not in output["updatedVars"]
    # a machine-readable warning was recorded for the skipped op
    warnings = output["warnings"]
    assert len(warnings) == 1
    w = warnings[0]
    assert w["op"] == "totally_unknown_future_op"
    assert w["set"] == "decoded"
    assert w["status"] == "skipped"


def test_normalize_transform_op_raises_on_unknown_op():
    # Defense in depth: normalization must never silently substitute base64_decode.
    with pytest.raises(ValueError):
        _normalize_transform_op({"op": "nope_not_real", "set": "x", "args": ["SGVsbG8"]})


@pytest.mark.asyncio
async def test_transform_step_skips_unknown_op_without_halting(base_config, empty_flow):
    # Graceful degradation: an unknown op does not halt the flow (no flow_error is set)
    # and known ops in the same step still apply.
    runner = make_runner(base_config, empty_flow)
    context: Dict[str, Any] = {}
    step = TransformStep(
        id="t2",
        name="Transform",
        type="transform",
        ops=[
            {"op": "totally_unknown_future_op", "set": "decoded", "args": ["SGVsbG8"]},
            {"op": "math_add", "set": "sum", "args": [2, 3]},
        ],
    )
    await runner._execute_transform_step(step, context, depth=0, user_id_log="test")
    # no crash / no halt
    assert get_value_from_context(context, "flow_error") is _MISSING
    # unknown op did not run (not downgraded to base64_decode)
    assert "decoded" not in context
    # known op still ran
    assert context["sum"] == 5


def test_execute_transform_ops_skips_unknown_transformop_instance():
    # Exercises the model_dump() branch of the guard directly: an unknown op passed as a
    # TransformOp model instance (the shape ops actually take after step validation).
    context: Dict[str, Any] = {}
    op = TransformOp.model_validate({"op": "unknown_model_op", "set": "decoded", "args": ["SGVsbG8"]})
    output = execute_transform_ops([op], context)
    assert "decoded" not in context
    assert output["updatedVars"] == []
    assert len(output["warnings"]) == 1
    assert output["warnings"][0]["op"] == "unknown_model_op"
    assert output["warnings"][0]["status"] == "skipped"


def test_execute_transform_ops_handles_missing_op_name_and_set():
    # op is None (missing) and set is missing -> each skipped with a warning, no crash.
    context: Dict[str, Any] = {}
    output = execute_transform_ops([{"args": []}, {"op": "still_unknown"}], context)
    assert output["updatedVars"] == []
    assert len(output["warnings"]) == 2
    assert output["warnings"][0]["op"] is None
    assert output["warnings"][0]["set"] is None


@pytest.mark.asyncio
async def test_transform_step_all_ops_unknown_does_not_halt(base_config, empty_flow):
    # Every op unknown: the whole step degrades to a no-op and the flow is not halted.
    runner = make_runner(base_config, empty_flow)
    context: Dict[str, Any] = {}
    step = TransformStep(
        id="t3",
        name="Transform",
        type="transform",
        ops=[
            {"op": "unknown_a", "set": "a", "args": []},
            {"op": "unknown_b", "set": "b", "args": []},
        ],
    )
    await runner._execute_transform_step(step, context, depth=0, user_id_log="test")
    assert get_value_from_context(context, "flow_error") is _MISSING
    assert "a" not in context
    assert "b" not in context


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


def test_extract_data_root_list(base_config, empty_flow):
    runner = make_runner(base_config, empty_flow)
    ctx: Dict[str, Any] = {}
    body = [{"order_id": 1}, {"order_id": 2}]
    headers = {}
    rules = {
        "first": "body.[0].order_id",
        "second": "body[1].order_id",
    }
    runner._extract_data(body, rules, ctx, 200, headers)
    assert ctx["first"] == 1
    assert ctx["second"] == 2


@pytest.mark.asyncio
async def test_url_substitution_plain_encodes(base_config, empty_flow):
    runner = make_runner(base_config, empty_flow)
    context = {"pwd": "p@ss word!"}
    out = runner._substitute_variables("{{pwd}}", context, for_url=True)
    assert out == "p%40ss%20word%21"


@pytest.mark.asyncio
async def test_url_substitution_preserves_already_encoded(base_config, empty_flow):
    runner = make_runner(base_config, empty_flow)
    context = {"pwd": "p%40ss%21"}
    out = runner._substitute_variables("{{pwd}}", context, for_url=True)
    assert out == "p%40ss%21"


@pytest.mark.asyncio
async def test_url_substitution_normalizes_partial_encoding(base_config, empty_flow):
    runner = make_runner(base_config, empty_flow)
    context = {"pwd": "p@ss%21word"}
    out = runner._substitute_variables("{{pwd}}", context, for_url=True)
    assert out == "p%40ss%21word"


@pytest.mark.asyncio
async def test_url_substitution_handles_malformed_percent(base_config, empty_flow):
    runner = make_runner(base_config, empty_flow)
    context = {"pwd": "abc%zz"}
    out = runner._substitute_variables("{{pwd}}", context, for_url=True)
    assert out == "abc%25zz"


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
async def test_execute_request_step_query_param_plus_encoding(empty_flow):
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

    step = RequestStep(id="s1", type="request", method="GET", url="/p?query={{val}}", onFailure="continue")
    ctx = {"val": "value with+plus"}
    await runner._execute_request_step(step, session, {}, {}, ctx)
    called_url = session.request.call_args.args[1]
    assert called_url == "http://base.com/p?query=value%20with%2Bplus"


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
async def test_simulate_user_lifecycle_run_once(monkeypatch, empty_flow):
    cfg = ContainerConfig(
        flow_target_url="http://example.com",
        sim_users=1,
        min_sleep_ms=0,
        max_sleep_ms=0,
        run_once=True,
    )
    metrics = Metrics()
    metrics.increment = AsyncMock()
    metrics.record_flow_duration = AsyncMock()
    runner = FlowRunner(cfg, empty_flow, metrics)

    connector = MagicMock(closed=False, close=AsyncMock())
    monkeypatch.setattr(runner, "create_aiohttp_connector", lambda: connector)

    session = MagicMock(closed=False, close=AsyncMock())
    monkeypatch.setattr(runner, "create_session", lambda conn: session)

    contexts = []

    async def fake_execute_steps(steps, session=None, base_headers=None, flow_headers=None, context=None, depth=0, **kwargs):
        contexts.append(context.copy())

    monkeypatch.setattr(runner, "_execute_steps", fake_execute_steps)
    monkeypatch.setattr(asyncio, "sleep", AsyncMock())

    runner.running = True
    await runner.simulate_user_lifecycle(0)

    assert len(contexts) == 1
    assert contexts[0].get("flowInstance") == 1
    assert runner.running is False


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


@pytest.mark.asyncio
async def test_simulate_user_flow_cycle_delay(monkeypatch, empty_flow):
    cfg = ContainerConfig(flow_target_url="http://example.com", sim_users=1, flow_cycle_delay_ms=200)
    runner = make_runner(cfg, empty_flow)

    monkeypatch.setattr(runner, "create_aiohttp_connector", lambda: MagicMock(closed=False, close=AsyncMock()))
    monkeypatch.setattr(runner, "create_session", lambda conn: MagicMock(closed=False, close=AsyncMock()))
    monkeypatch.setattr(runner, "_execute_steps", AsyncMock())

    sleep_calls = []
    original_sleep = asyncio.sleep

    async def fake_sleep(d):
        sleep_calls.append(d)
        runner.running = False
        await original_sleep(0)

    monkeypatch.setattr(asyncio, "sleep", AsyncMock(side_effect=fake_sleep))

    runner.running = True
    await runner.simulate_user_lifecycle(1)
    assert sleep_calls and sleep_calls[0] == 0.2


@pytest.mark.asyncio
async def test_simulate_user_flow_cycle_delay_min(monkeypatch, empty_flow):
    cfg = ContainerConfig(flow_target_url="http://example.com", sim_users=1, flow_cycle_delay_ms=0)
    runner = make_runner(cfg, empty_flow)

    monkeypatch.setattr(runner, "create_aiohttp_connector", lambda: MagicMock(closed=False, close=AsyncMock()))
    monkeypatch.setattr(runner, "create_session", lambda conn: MagicMock(closed=False, close=AsyncMock()))
    monkeypatch.setattr(runner, "_execute_steps", AsyncMock())

    sleep_calls = []
    original_sleep = asyncio.sleep

    async def fake_sleep(d):
        sleep_calls.append(d)
        runner.running = False
        await original_sleep(0)

    monkeypatch.setattr(asyncio, "sleep", AsyncMock(side_effect=fake_sleep))

    runner.running = True
    await runner.simulate_user_lifecycle(1)
    assert sleep_calls and sleep_calls[0] == 0.001





def test_container_config_alias_override_step_url_host():
    cfg = ContainerConfig(
        flow_target_url="http://example.com",
        sim_users=1,
        **{"Override Step URL Host": False},
    )
    assert cfg.override_step_url_host is False


def test_container_config_alias_flow_cycle_delay_ms():
    cfg = ContainerConfig(
        flow_target_url="http://example.com",
        sim_users=1,
        **{"Flow Cycle Delay MS": 1500},
    )
    assert cfg.flow_cycle_delay_ms == 1500


def test_container_config_alias_run_once():
    cfg = ContainerConfig(
        flow_target_url="http://example.com",
        sim_users=1,
        **{"Run Once": True},
    )
    assert cfg.run_once is True


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


@pytest.mark.asyncio
async def test_simulate_user_lifecycle_moves_to_next_flow_on_failure(base_config):
    flow1 = FlowMap(name="flow1", steps=[])
    flow2 = FlowMap(name="flow2", steps=[])
    metrics = Metrics()
    metrics.increment = AsyncMock()
    metrics.record_flow_duration = AsyncMock()
    runner = FlowRunner(base_config, flow1, metrics, flowmaps=[flow1, flow2])
    runner.config.min_sleep_ms = runner.config.max_sleep_ms = 0
    runner.running = True

    class DummyConnector:
        closed = False

        async def close(self):
            self.closed = True

    connector = DummyConnector()
    runner.create_aiohttp_connector = MagicMock(return_value=connector)

    dummy_session = MagicMock()
    dummy_session.closed = False

    async def close_session():
        dummy_session.closed = True

    dummy_session.close = AsyncMock(side_effect=close_session)
    runner.create_session = MagicMock(return_value=dummy_session)

    call_errors = []

    async def fake_execute_steps(steps, session, base_headers, flow_headers, context, depth=0):
        if not call_errors:
            set_value_in_context(context, "flow_error", "boom")
        else:
            runner.running = False
        call_errors.append(get_value_from_context(context, "flow_error"))

    runner._execute_steps = AsyncMock(side_effect=fake_execute_steps)

    await runner.simulate_user_lifecycle(user_id=0)

    assert call_errors[0] == "boom"
    assert call_errors[1] is None


@pytest.mark.asyncio
async def test_simulate_user_lifecycle_restarts_single_flow_after_failure(base_config):
    flow = FlowMap(name="solo", steps=[])
    metrics = Metrics()
    metrics.increment = AsyncMock()
    metrics.record_flow_duration = AsyncMock()
    runner = FlowRunner(base_config, flow, metrics)
    runner.config.min_sleep_ms = runner.config.max_sleep_ms = 0
    runner.running = True

    class DummyConnector:
        closed = False

        async def close(self):
            self.closed = True

    connector = DummyConnector()
    runner.create_aiohttp_connector = MagicMock(return_value=connector)

    dummy_session = MagicMock()
    dummy_session.closed = False

    async def close_session():
        dummy_session.closed = True

    dummy_session.close = AsyncMock(side_effect=close_session)
    runner.create_session = MagicMock(return_value=dummy_session)

    call_errors = []

    async def fake_execute_steps(steps, session, base_headers, flow_headers, context, depth=0):
        if not call_errors:
            set_value_in_context(context, "flow_error", "boom")
        else:
            runner.running = False
        call_errors.append(get_value_from_context(context, "flow_error"))

    runner._execute_steps = AsyncMock(side_effect=fake_execute_steps)

    await runner.simulate_user_lifecycle(user_id=0)

    assert call_errors[0] == "boom"
    assert call_errors[1] is None


@pytest.mark.asyncio
async def test_flow_concurrency_disabled(monkeypatch, empty_flow):
    cfg = ContainerConfig(
        flow_target_url="http://example.com",
        sim_users=2,
        allow_flow_concurrency=False,
        min_sleep_ms=0,
        max_sleep_ms=0,
    )
    metrics = Metrics()
    metrics.increment = AsyncMock()
    metrics.record_flow_duration = AsyncMock()
    runner = FlowRunner(cfg, empty_flow, metrics, flowmaps=[empty_flow])
    runner.running = True

    concurrent = {"count": 0, "max": 0}

    async def fake_execute_steps(*args, **kwargs):
        concurrent["count"] += 1
        concurrent["max"] = max(concurrent["max"], concurrent["count"])
        await asyncio.sleep(0.05)
        concurrent["count"] -= 1

    monkeypatch.setattr(runner, "_execute_steps", fake_execute_steps)
    monkeypatch.setattr(runner, "create_aiohttp_connector", lambda: MagicMock(closed=False, close=AsyncMock()))
    monkeypatch.setattr(runner, "create_session", lambda conn: MagicMock(closed=False, close=AsyncMock()))

    async def stop_later():
        await asyncio.sleep(0.2)
        runner.running = False

    task1 = asyncio.create_task(runner.simulate_user_lifecycle(0))
    task2 = asyncio.create_task(runner.simulate_user_lifecycle(1))
    stopper = asyncio.create_task(stop_later())
    await asyncio.gather(task1, task2, stopper)

    assert concurrent["max"] == 1


@pytest.mark.asyncio
async def test_flow_distribution_without_concurrency(monkeypatch):
    flow1 = FlowMap(name="flow1", description=None, headers=None, steps=[], staticVars={"fname": "flow1"})
    flow2 = FlowMap(name="flow2", description=None, headers=None, steps=[], staticVars={"fname": "flow2"})
    cfg = ContainerConfig(
        flow_target_url="http://example.com",
        sim_users=2,
        allow_flow_concurrency=False,
        min_sleep_ms=0,
        max_sleep_ms=0,
    )
    metrics = Metrics()
    metrics.increment = AsyncMock()
    metrics.record_flow_duration = AsyncMock()
    runner = FlowRunner(cfg, flow1, metrics, flowmaps=[flow1, flow2])
    runner.running = True

    concurrent = {"running": set(), "max": 0, "duplicate": False}

    async def fake_execute_steps(steps, session, base_headers, flow_headers, context, depth=0):
        fname = context.get("fname")
        if fname in concurrent["running"]:
            concurrent["duplicate"] = True
        concurrent["running"].add(fname)
        concurrent["max"] = max(concurrent["max"], len(concurrent["running"]))
        await asyncio.sleep(0.05)
        concurrent["running"].remove(fname)

    monkeypatch.setattr(runner, "_execute_steps", fake_execute_steps)
    monkeypatch.setattr(runner, "create_aiohttp_connector", lambda: MagicMock(closed=False, close=AsyncMock()))
    monkeypatch.setattr(runner, "create_session", lambda conn: MagicMock(closed=False, close=AsyncMock()))

    async def stop_later():
        await asyncio.sleep(0.2)
        runner.running = False

    task1 = asyncio.create_task(runner.simulate_user_lifecycle(0))
    task2 = asyncio.create_task(runner.simulate_user_lifecycle(1))
    stopper = asyncio.create_task(stop_later())
    await asyncio.gather(task1, task2, stopper)

    assert not concurrent["duplicate"]
    assert concurrent["max"] == 2
