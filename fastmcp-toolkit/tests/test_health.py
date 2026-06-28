"""register_health_endpointsのテスト。"""

import pytest
from fastmcp import FastMCP
from httpx import ASGITransport, AsyncClient

from fastmcp_toolkit.health import register_health_endpoints


async def _make_client(app: FastMCP) -> AsyncClient:
    starlette_app = app.http_app()
    transport = ASGITransport(app=starlette_app)
    return AsyncClient(transport=transport, base_url="http://test")


@pytest.mark.asyncio
async def test_liveness_registered_at_default_path():
    """デフォルトパス /livez にエンドポイントが登録される。"""
    app = FastMCP("test")
    register_health_endpoints(app)
    async with await _make_client(app) as client:
        resp = await client.get("/livez")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_readiness_registered_at_default_path():
    """デフォルトパス /readyz にエンドポイントが登録される。"""
    app = FastMCP("test")
    register_health_endpoints(app)
    async with await _make_client(app) as client:
        resp = await client.get("/readyz")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_custom_paths():
    """カスタムパスにエンドポイントが登録される。"""
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
async def test_readiness_checks_wired():
    """readiness_checksが正しく動作する（ロジックはcore-toolkitでテスト済み）。"""

    async def check_fail() -> tuple[bool, str]:
        return False, "down"

    app = FastMCP("test")
    register_health_endpoints(app, readiness_checks=[check_fail])
    async with await _make_client(app) as client:
        resp = await client.get("/readyz")
    assert resp.status_code == 503
