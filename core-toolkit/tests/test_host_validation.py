"""HostValidationMiddlewareのテスト。"""

import pytest
from httpx import ASGITransport, AsyncClient
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from core_toolkit.middleware.host_validation import HostValidationMiddleware


async def _ok(request: Request) -> JSONResponse:
    return JSONResponse({"ok": True})


def _create_app(allowed_hosts: tuple[str, ...] = ("localhost:8000",)) -> Starlette:
    app = Starlette(routes=[Route("/", _ok, methods=["GET", "POST"])])
    app.add_middleware(HostValidationMiddleware, allowed_hosts=allowed_hosts)
    return app


async def _get(app: Starlette, host: str = "localhost:8000") -> JSONResponse:
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://localhost:8000"
    ) as client:
        return await client.get("/", headers={"host": host})


@pytest.mark.asyncio
async def test_valid_host_passes():
    """許可リストのHostヘッダーは通過する。"""
    resp = await _get(_create_app())
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_invalid_host_rejected():
    """許可リストにないHostヘッダーは421で拒否される。"""
    resp = await _get(_create_app(), host="evil.com")
    assert resp.status_code == 421


@pytest.mark.asyncio
async def test_missing_host_rejected():
    """空のHostヘッダーは421で拒否される。"""
    resp = await _get(_create_app(), host="")
    assert resp.status_code == 421


@pytest.mark.asyncio
async def test_wildcard_port_passes():
    """ワイルドカードポート（localhost:*）は任意ポートを許可する。"""
    app = _create_app(allowed_hosts=("localhost:*",))
    resp = await _get(app, host="localhost:9999")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_wildcard_port_wrong_host_rejected():
    """ワイルドカードポートでもホスト名が異なれば拒否される。"""
    resp = await _get(_create_app(allowed_hosts=("localhost:*",)), host="evil.com:9999")
    assert resp.status_code == 421


@pytest.mark.asyncio
async def test_empty_allowed_hosts_rejects_all():
    """allowed_hosts=()は全てのHostヘッダーを拒否する。"""
    resp = await _get(_create_app(allowed_hosts=()))
    assert resp.status_code == 421


@pytest.mark.asyncio
async def test_multiple_allowed_hosts():
    """複数の許可ホストをいずれも通過させる。"""
    app = _create_app(allowed_hosts=("localhost:8000", "api.example.com"))
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://localhost:8000"
    ) as client:
        r1 = await client.get("/", headers={"host": "localhost:8000"})
        r2 = await client.get("/", headers={"host": "api.example.com"})
    assert r1.status_code == 200
    assert r2.status_code == 200


@pytest.mark.asyncio
async def test_websocket_scope_passthrough():
    """websocketスコープはスルーされる。"""
    received: list[str] = []

    async def bare_app(scope: object, receive: object, send: object) -> None:
        received.append(scope["type"])  # type: ignore[index]

    await HostValidationMiddleware(bare_app)({"type": "websocket"}, None, None)
    assert received == ["websocket"]
