"""OriginValidationMiddlewareのテスト。"""

import pytest
from httpx import ASGITransport, AsyncClient
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from core_toolkit.middleware.origin_validation import OriginValidationMiddleware


async def _ok(request: Request) -> JSONResponse:
    return JSONResponse({"ok": True})


def _create_app(
    allowed_origins: tuple[str, ...] = ("http://localhost:3000",),
) -> Starlette:
    app = Starlette(routes=[Route("/", _ok)])
    app.add_middleware(OriginValidationMiddleware, allowed_origins=allowed_origins)
    return app


async def _get(app: Starlette, origin: str | None = None) -> JSONResponse:
    headers = {}
    if origin is not None:
        headers["origin"] = origin
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://localhost:8000"
    ) as client:
        return await client.get("/", headers=headers)


@pytest.mark.asyncio
async def test_valid_origin_passes():
    """許可リストのOriginヘッダーは通過する。"""
    resp = await _get(_create_app(), origin="http://localhost:3000")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_invalid_origin_rejected():
    """許可リストにないOriginヘッダーは403で拒否される。"""
    resp = await _get(_create_app(), origin="http://evil.com")
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_no_origin_header_passes():
    """Originヘッダーなし（直接アクセス等）は通過する。"""
    resp = await _get(_create_app(), origin=None)
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_wildcard_port_passes():
    """ワイルドカードポートは任意ポートを許可する。"""
    resp = await _get(
        _create_app(allowed_origins=("http://localhost:*",)),
        origin="http://localhost:5173",
    )
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_wildcard_port_wrong_host_rejected():
    """ワイルドカードポートでもホスト名が異なれば拒否される。"""
    resp = await _get(
        _create_app(allowed_origins=("http://localhost:*",)),
        origin="http://evil.com:5173",
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_empty_allowed_origins_rejects_any_origin():
    """allowed_origins=()はOriginヘッダーありのリクエストを全て拒否する。"""
    resp = await _get(_create_app(allowed_origins=()), origin="http://localhost:3000")
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_empty_allowed_origins_no_origin_passes():
    """allowed_origins=()でもOriginヘッダーなしは通過する。"""
    resp = await _get(_create_app(allowed_origins=()), origin=None)
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_multiple_allowed_origins():
    """複数の許可オリジンをいずれも通過させる。"""
    app = _create_app(
        allowed_origins=("http://localhost:3000", "https://app.example.com")
    )
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://localhost:8000"
    ) as client:
        r1 = await client.get("/", headers={"origin": "http://localhost:3000"})
        r2 = await client.get("/", headers={"origin": "https://app.example.com"})
    assert r1.status_code == 200
    assert r2.status_code == 200


@pytest.mark.asyncio
async def test_websocket_scope_passthrough():
    """websocketスコープはスルーされる。"""
    received: list[str] = []

    async def bare_app(scope: object, receive: object, send: object) -> None:
        received.append(scope["type"])  # type: ignore[index]

    await OriginValidationMiddleware(bare_app)({"type": "websocket"}, None, None)
    assert received == ["websocket"]
