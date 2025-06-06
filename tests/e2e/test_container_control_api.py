import types
import sys, os
pydantic = types.ModuleType("pydantic")
class BaseModel:
    def __init__(self, **data):
        for k, v in data.items():
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
sys.modules.setdefault("psutil", types.SimpleNamespace(
    cpu_percent=lambda interval=None: 0.0,
    virtual_memory=lambda: types.SimpleNamespace(percent=0.0, available=0, used=0),
    net_io_counters=lambda: types.SimpleNamespace(bytes_sent=0, bytes_recv=0, packets_sent=0, packets_recv=0)
))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))
import asyncio
import httpx
import pytest
import pytest_asyncio
import json

import container_control
from tests.e2e.mock_server import create_mock_server, shutdown_mock_server


@pytest_asyncio.fixture
async def mock_server():
    runner, base_url, hits, requests = await create_mock_server()
    yield {'base_url': base_url, 'hits': hits, 'requests': requests}
    await shutdown_mock_server(runner)


@pytest_asyncio.fixture
async def api_client():
    transport = httpx.ASGITransport(app=container_control.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client
    container_control._force_stop_flow_runner()
    if container_control.background_thread:
        container_control.background_thread.join(timeout=1)
        container_control.background_thread = None
    container_control.current_settings['app_status'] = 'initializing'


@pytest.mark.asyncio
async def test_health_endpoint(api_client):
    resp = await api_client.get("/api/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "healthy"
    assert data["app_status"] == "initializing"


@pytest.mark.asyncio
async def test_start_stop_continuous_metrics(api_client, mock_server):
    flowmap = {
        "name": "complex",
        "staticVars": {},
        "steps": [
            {
                "id": "loop",
                "type": "loop",
                "source": "{{items}}",
                "loopVariable": "item",
                "steps": [
                    {
                        "id": "req",
                        "type": "request",
                        "method": "GET",
                        "url": "/{{item}}",
                        "onFailure": "continue",
                    }
                ],
            }
        ],
    }
    # Provide items list for loop
    flowmap["staticVars"] = {"items": ["ping", "ping2"]}

    config = {
        "flow_target_url": mock_server["base_url"],
        "sim_users": 1,
        "min_sleep_ms": 10,
        "max_sleep_ms": 20,
        "override_step_url_host": True,
    }

    res = await api_client.post("/api/start", json={"config": config, "flowmap": flowmap})
    assert res.status_code == 200

    await asyncio.sleep(0.3)
    total_hits = sum(mock_server["hits"].values())
    assert total_hits >= 2

    metrics = await api_client.get("/api/metrics")
    data = metrics.json()
    assert data["app_status"] == "running"
    assert data["metrics"]["active_simulated_users"] == 1
    assert data["metrics"]["rps"] > 0

    prom = await api_client.get("/metrics")
    assert "flow_runner_rps" in prom.text

    await api_client.post("/api/stop")
    await asyncio.sleep(0.1)

    hits_after_stop = sum(mock_server["hits"].values())
    await asyncio.sleep(0.2)
    assert sum(mock_server["hits"].values()) == hits_after_stop

    stopped = await api_client.get("/api/metrics")
    assert stopped.json()["app_status"] == "stopped"


@pytest.mark.asyncio
async def test_start_with_override_disabled(api_client, mock_server):
    flowmap = {
        "name": "simple",
        "steps": [
            {
                "id": "r1",
                "type": "request",
                "method": "GET",
                "url": "/ping",
                "onFailure": "continue",
            }
        ],
    }
    config = {
        "flow_target_url": mock_server["base_url"],
        "sim_users": 1,
        "min_sleep_ms": 10,
        "max_sleep_ms": 10,
        "override_step_url_host": False,
    }

    res = await api_client.post("/api/start", json={"config": config, "flowmap": flowmap})
    assert res.status_code == 200
    await asyncio.sleep(0.2)
    assert mock_server["hits"].get("/ping", 0) >= 1
    await api_client.post("/api/stop")


@pytest.mark.asyncio
async def test_start_missing_flowmap(api_client):
    config = {"flow_target_url": "http://example.com", "sim_users": 1}
    res = await api_client.post("/api/start", json={"config": config})
    assert res.status_code == 400
    detail = res.json().get("detail")
    assert detail and "flowmap" in str(detail)


@pytest.mark.asyncio
async def test_start_invalid_config(api_client):
    flowmap = {"name": "bad", "steps": []}
    config = {
        "flow_target_url": "http://example.com",
        "sim_users": 0,
        "override_step_url_host": "yes",
    }
    res = await api_client.post("/api/start", json={"config": config, "flowmap": flowmap})
    assert res.status_code == 400
    detail = res.json()["detail"]
    assert any("sim_users" in str(d) or "override_step_url_host" in str(d) for d in detail)


@pytest.mark.asyncio
async def test_start_invalid_step_definition(api_client):
    flowmap = {"name": "invalid", "steps": [{"id": "no_type"}]}
    config = {"flow_target_url": "http://example.com", "sim_users": 1}
    res = await api_client.post("/api/start", json={"config": config, "flowmap": flowmap})
    assert res.status_code == 400
    assert "type" in str(res.json().get("detail"))


@pytest.mark.asyncio
async def test_start_while_running_then_stop_noop(api_client, mock_server):
    flow1 = {
        "name": "f1",
        "steps": [{"id": "r1", "type": "request", "method": "GET", "url": "/ping", "onFailure": "continue"}],
    }
    flow2 = {
        "name": "f2",
        "steps": [{"id": "r2", "type": "request", "method": "GET", "url": "/ping2", "onFailure": "continue"}],
    }
    config = {"flow_target_url": mock_server["base_url"], "sim_users": 1, "min_sleep_ms": 10, "max_sleep_ms": 10}

    res1 = await api_client.post("/api/start", json={"config": config, "flowmap": flow1})
    assert res1.status_code == 200
    await asyncio.sleep(0.2)
    res2 = await api_client.post("/api/start", json={"config": config, "flowmap": flow2})
    assert res2.status_code == 200
    await asyncio.sleep(0.3)
    assert mock_server["hits"].get("/ping2", 0) >= 1

    stop_resp = await api_client.post("/api/stop")
    assert stop_resp.status_code == 200
    await asyncio.sleep(0.1)

    # Calling stop again when already stopped
    stop2 = await api_client.post("/api/stop")
    assert stop2.status_code == 200
    assert "already stopped" in stop2.json()["message"].lower() or "no running" in stop2.json()["message"].lower()


@pytest.mark.asyncio
async def test_start_with_debug_true(api_client, mock_server):
    flowmap = {"name": "dbg", "steps": [{"id": "r1", "type": "request", "method": "GET", "url": "/ping", "onFailure": "continue"}]}
    config = {
        "flow_target_url": mock_server["base_url"],
        "sim_users": 1,
        "min_sleep_ms": 10,
        "max_sleep_ms": 10,
        "debug": True,
    }
    res = await api_client.post("/api/start", json={"config": config, "flowmap": flowmap})
    assert res.status_code == 200
    await asyncio.sleep(0.2)
    assert mock_server["hits"].get("/ping", 0) >= 1
    await api_client.post("/api/stop")


@pytest.mark.asyncio
async def test_dns_override_and_host_header(api_client, mock_server, monkeypatch):
    import aiohttp
    import socket

    class DummyResolver(aiohttp.abc.AbstractResolver):
        def __init__(self):
            self.records = {}

        def add_override(self, host: str, port: int, ip: str) -> None:
            self.records[(host, port)] = ip

        async def resolve(self, host, port=0, family=socket.AF_INET):
            ip = self.records.get((host, port), host)
            return [{"hostname": host, "host": ip, "port": port, "family": family, "proto": 0, "flags": socket.AI_NUMERICHOST}]

        async def close(self):
            pass

    monkeypatch.setattr(aiohttp.resolver, "AsyncResolver", DummyResolver)

    base_url = mock_server["base_url"]
    port = int(base_url.rsplit(":", 1)[1])
    flowmap = {"name": "dns", "steps": [{"id": "r1", "type": "request", "method": "GET", "url": "/ping", "onFailure": "continue"}]}
    config = {
        "flow_target_url": f"http://example.com:{port}",
        "sim_users": 1,
        "min_sleep_ms": 10,
        "max_sleep_ms": 10,
        "flow_target_dns_override": "127.0.0.1",
    }
    res = await api_client.post("/api/start", json={"config": config, "flowmap": flowmap})
    assert res.status_code == 200
    await asyncio.sleep(0.2)
    req_headers = mock_server["requests"][-1]["headers"]
    assert req_headers.get("Host") == "example.com"
    await api_client.post("/api/stop")


def _parse_prom_metrics(text: str) -> dict:
    metrics = {}
    for line in text.splitlines():
        if line.startswith("#") or not line.strip():
            continue
        key, val = line.split()
        metrics[key] = float(val)
    return metrics


@pytest.mark.asyncio
async def test_prometheus_metrics_states(api_client, mock_server):
    prom_idle = await api_client.get("/metrics")
    metrics = _parse_prom_metrics(prom_idle.text)
    assert metrics.get("app_status") == 0.0

    flowmap = {"name": "m", "steps": [{"id": "r1", "type": "request", "method": "GET", "url": "/ping", "onFailure": "continue"}]}
    config = {"flow_target_url": mock_server["base_url"], "sim_users": 1, "min_sleep_ms": 10, "max_sleep_ms": 10}
    await api_client.post("/api/start", json={"config": config, "flowmap": flowmap})
    await asyncio.sleep(0.3)
    prom_running = await api_client.get("/metrics")
    metrics_run = _parse_prom_metrics(prom_running.text)
    assert metrics_run.get("app_status") == 1.0
    assert metrics_run.get("flow_runner_active_users", 0) >= 1.0

    await api_client.post("/api/stop")
    await asyncio.sleep(0.2)
    prom_stopped = await api_client.get("/metrics")
    metrics_stop = _parse_prom_metrics(prom_stopped.text)
    assert metrics_stop.get("app_status") == 2.0


@pytest.mark.asyncio
async def test_complex_flow_unquoted_variables(api_client, mock_server):
    flowmap = {
        "name": "complex_unquoted",
        "staticVars": {"nums": [1, 2], "flag": True},
        "steps": [
            {
                "id": "loop",
                "type": "loop",
                "source": "{{nums}}",
                "loopVariable": "num",
                "steps": [
                    {
                        "id": "cond",
                        "type": "condition",
                        "conditionData": {"variable": "num", "operator": "greater_than", "value": "0"},
                        "then": [
                            {
                                "id": "req",
                                "type": "request",
                                "method": "POST",
                                "url": "/echo",
                                "headers": {"Content-Type": "application/json"},
                                "body": {"number": "##VAR:unquoted:num##", "flag": "##VAR:unquoted:flag##"},
                                "onFailure": "continue",
                            }
                        ],
                    }
                ],
            }
        ],
    }
    config = {"flow_target_url": mock_server["base_url"], "sim_users": 1, "min_sleep_ms": 10, "max_sleep_ms": 10}
    res = await api_client.post("/api/start", json={"config": config, "flowmap": flowmap})
    assert res.status_code == 200
    await asyncio.sleep(0.4)
    bodies = [
        json.loads(r["body"] or "null")
        for r in mock_server["requests"]
        if r["path"] == "/echo"
    ]
    assert {"number": 1, "flag": True} in bodies
    assert {"number": 2, "flag": True} in bodies
    await api_client.post("/api/stop")


@pytest.mark.asyncio
async def test_flow_cycle_delay(api_client, mock_server):
    flowmap = {
        "name": "cycle_delay",
        "steps": [
            {
                "id": "r1",
                "type": "request",
                "method": "GET",
                "url": "/ping",
                "onFailure": "continue",
            }
        ],
    }
    config = {
        "flow_target_url": mock_server["base_url"],
        "sim_users": 1,
        "min_sleep_ms": 10,
        "max_sleep_ms": 10,
        "flow_cycle_delay_ms": 300,
    }

    resp = await api_client.post("/api/start", json={"config": config, "flowmap": flowmap})
    assert resp.status_code == 200

    await asyncio.sleep(0.7)
    reqs = [r for r in mock_server["requests"] if r["path"] == "/ping"]
    assert len(reqs) >= 2
    delay = reqs[1]["timestamp"] - reqs[0]["timestamp"]
    assert 0.25 <= delay <= 0.45

    await api_client.post("/api/stop")
    hits_after_stop = len(reqs)
    await asyncio.sleep(0.4)
    reqs_after = [r for r in mock_server["requests"] if r["path"] == "/ping"]
    assert len(reqs_after) == hits_after_stop

# Manual signal handling test (SIGTERM) should be performed via Docker: `docker kill -s TERM <container_id>` to verify graceful shutdown.
