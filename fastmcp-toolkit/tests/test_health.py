"""ヘルスチェックエンドポイントのテスト。"""

import pytest
from fastmcp import FastMCP
from httpx import ASGITransport, AsyncClient

from fastmcp_toolkit.health import register_health_endpoints


async def _make_client(app: FastMCP) -> AsyncClient:
    starlette_app = app.http_app()
    transport = ASGITransport(app=starlette_app)
    return AsyncClient(transport=transport, base_url="http://test")


@pytest.mark.asyncio
async def test_liveness_returns_ok():
    app = FastMCP("test")
    register_health_endpoints(app)
    async with await _make_client(app) as client:
        resp = await client.get("/livez")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


@pytest.mark.asyncio
async def test_readiness_without_checks_returns_ok():
    app = FastMCP("test")
    register_health_endpoints(app)
    async with await _make_client(app) as client:
        resp = await client.get("/readyz")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


@pytest.mark.asyncio
async def test_readiness_all_checks_pass():
    async def check_db() -> tuple[bool, str]:
        return True, "connected"

    async def check_cache() -> tuple[bool, str]:
        return True, "reachable"

    app = FastMCP("test")
    register_health_endpoints(app, readiness_checks=[check_db, check_cache])
    async with await _make_client(app) as client:
        resp = await client.get("/readyz")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["checks"]["check_db"] == {"ok": True, "detail": "connected"}
    assert body["checks"]["check_cache"] == {"ok": True, "detail": "reachable"}


@pytest.mark.asyncio
async def test_readiness_with_failing_check_returns_503():
    async def check_ok() -> tuple[bool, str]:
        return True, "fine"

    async def check_fail() -> tuple[bool, str]:
        return False, "timeout"

    app = FastMCP("test")
    register_health_endpoints(app, readiness_checks=[check_ok, check_fail])
    async with await _make_client(app) as client:
        resp = await client.get("/readyz")
    assert resp.status_code == 503
    body = resp.json()
    assert body["status"] == "unavailable"
    assert body["checks"]["check_ok"] == {"ok": True, "detail": "fine"}
    assert body["checks"]["check_fail"] == {"ok": False, "detail": "timeout"}


@pytest.mark.asyncio
async def test_custom_paths():
    app = FastMCP("test")
    register_health_endpoints(
        app, liveness_path="/health/live", readiness_path="/health/ready"
    )
    async with await _make_client(app) as client:
        live_resp = await client.get("/health/live")
        ready_resp = await client.get("/health/ready")
    assert live_resp.status_code == 200
    assert ready_resp.status_code == 200


@pytest.mark.asyncio
async def test_check_function_name_used_as_key():
    async def my_custom_check() -> tuple[bool, str]:
        return True, "ok"

    app = FastMCP("test")
    register_health_endpoints(app, readiness_checks=[my_custom_check])
    async with await _make_client(app) as client:
        resp = await client.get("/readyz")
    assert "my_custom_check" in resp.json()["checks"]
