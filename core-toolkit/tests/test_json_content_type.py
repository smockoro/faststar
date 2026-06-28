"""JsonContentTypeMiddlewareのテスト。"""

import pytest
from httpx import ASGITransport, AsyncClient
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from core_toolkit.middleware.json_content_type import JsonContentTypeMiddleware


async def _ok(request: Request) -> JSONResponse:
    return JSONResponse({"ok": True})


def _create_app() -> Starlette:
    app = Starlette(routes=[Route("/", _ok, methods=["GET", "POST", "PUT", "DELETE"])])
    app.add_middleware(JsonContentTypeMiddleware)
    return app


@pytest.mark.asyncio
async def test_post_json_passes():
    """POSTでapplication/jsonは通過する。"""
    async with AsyncClient(
        transport=ASGITransport(app=_create_app()), base_url="http://test"
    ) as client:
        resp = await client.post("/", json={"method": "test"})
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_post_json_with_charset_passes():
    """POSTでapplication/json; charset=utf-8も通過する。"""
    async with AsyncClient(
        transport=ASGITransport(app=_create_app()), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/",
            content=b'{"key":"value"}',
            headers={"content-type": "application/json; charset=utf-8"},
        )
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_post_form_rejected():
    """POSTでapplication/x-www-form-urlencodedは400で拒否される。"""
    async with AsyncClient(
        transport=ASGITransport(app=_create_app()), base_url="http://test"
    ) as client:
        resp = await client.post("/", data={"key": "value"})
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_post_text_plain_rejected():
    """POSTでtext/plainは400で拒否される。"""
    async with AsyncClient(
        transport=ASGITransport(app=_create_app()), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/",
            content=b"hello",
            headers={"content-type": "text/plain"},
        )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_post_missing_content_type_rejected():
    """POSTでContent-Typeなしは400で拒否される。"""
    async with AsyncClient(
        transport=ASGITransport(app=_create_app()), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/",
            content=b"{}",
            headers={"content-type": ""},
        )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_get_no_content_type_check():
    """GETはContent-Typeを検証しない。"""
    async with AsyncClient(
        transport=ASGITransport(app=_create_app()), base_url="http://test"
    ) as client:
        resp = await client.get("/")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_delete_no_content_type_check():
    """DELETEはContent-Typeを検証しない。"""
    async with AsyncClient(
        transport=ASGITransport(app=_create_app()), base_url="http://test"
    ) as client:
        resp = await client.delete("/")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_post_multipart_rejected():
    """POSTでmultipart/form-dataは400で拒否される。"""
    async with AsyncClient(
        transport=ASGITransport(app=_create_app()), base_url="http://test"
    ) as client:
        resp = await client.post("/", files={"file": b"data"})
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_websocket_scope_passthrough():
    """websocketスコープはスルーされる。"""
    received: list[str] = []

    async def bare_app(scope: object, receive: object, send: object) -> None:
        received.append(scope["type"])  # type: ignore[index]

    await JsonContentTypeMiddleware(bare_app)({"type": "websocket"}, None, None)
    assert received == ["websocket"]
