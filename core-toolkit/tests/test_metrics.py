"""メトリクスハンドラファクトリのテスト。"""

import pytest
from httpx import ASGITransport, AsyncClient
from starlette.applications import Starlette
from starlette.routing import Route

from core_toolkit.metrics import make_metrics_handler


def _create_app(path: str = "/metrics") -> Starlette:
    return Starlette(routes=[Route(path, make_metrics_handler(), methods=["GET"])])


@pytest.mark.asyncio
async def test_handler_returns_200():
    """メトリクスハンドラは200を返す。"""
    async with AsyncClient(
        transport=ASGITransport(app=_create_app()), base_url="http://test"
    ) as client:
        resp = await client.get("/metrics")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_handler_content_type_is_prometheus():
    """レスポンスのContent-TypeはPrometheus形式である。"""
    async with AsyncClient(
        transport=ASGITransport(app=_create_app()), base_url="http://test"
    ) as client:
        resp = await client.get("/metrics")
    assert "text/plain" in resp.headers["content-type"]


@pytest.mark.asyncio
async def test_handler_has_nosniff_header():
    """メトリクスレスポンスにnosniffヘッダーが付与される。"""
    async with AsyncClient(
        transport=ASGITransport(app=_create_app()), base_url="http://test"
    ) as client:
        resp = await client.get("/metrics")
    assert resp.headers["x-content-type-options"] == "nosniff"
