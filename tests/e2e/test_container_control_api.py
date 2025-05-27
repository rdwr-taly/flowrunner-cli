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

import container_control
from tests.e2e.mock_server import create_mock_server, shutdown_mock_server


@pytest.fixture
async def mock_server():
    runner, base_url, hits = await create_mock_server()
    yield {'base_url': base_url, 'hits': hits}
    await shutdown_mock_server(runner)


@pytest.fixture
async def api_client():
    async with httpx.AsyncClient(app=container_control.app, base_url="http://testserver") as client:
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
