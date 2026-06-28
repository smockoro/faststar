"""register_metrics_endpointのテスト。"""

import pytest
from fastmcp import FastMCP
from httpx import ASGITransport, AsyncClient

from fastmcp_toolkit.metrics import register_metrics_endpoint


@pytest.mark.asyncio
async def test_register_at_default_path():
    """デフォルトパス /metrics にエンドポイントが登録される。"""
    app = FastMCP("test")
    register_metrics_endpoint(app)
    starlette_app = app.http_app()
    async with AsyncClient(
        transport=ASGITransport(app=starlette_app), base_url="http://test"
    ) as client:
        resp = await client.get("/metrics")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_register_at_custom_path():
    """カスタムパスにエンドポイントが登録される。"""
    app = FastMCP("test")
    register_metrics_endpoint(app, path="/prom/metrics")
    starlette_app = app.http_app()
    async with AsyncClient(
        transport=ASGITransport(app=starlette_app), base_url="http://test"
    ) as client:
        resp = await client.get("/prom/metrics")
    assert resp.status_code == 200
