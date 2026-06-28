"""ServerHeaderStripMiddlewareのテスト。"""

import pytest
from httpx import ASGITransport, AsyncClient
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Route

from core_toolkit.middleware.server_header_strip import ServerHeaderStripMiddleware


async def _with_server_headers(request: Request) -> Response:
    return Response(
        content="ok",
        headers={"Server": "uvicorn", "X-Powered-By": "starlette"},
    )


async def _ok(request: Request) -> Response:
    return Response(content="ok")


def _create_app(handler=_with_server_headers, **kwargs: object) -> Starlette:
    app = Starlette(routes=[Route("/", handler)])
    app.add_middleware(ServerHeaderStripMiddleware, **kwargs)
    return app


async def _get(app: Starlette) -> Response:
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        return await client.get("/")


@pytest.mark.asyncio
async def test_default_strips_server():
    """デフォルトでServerヘッダーが削除される。"""
    resp = await _get(_create_app())
    assert "server" not in resp.headers


@pytest.mark.asyncio
async def test_default_strips_x_powered_by():
    """デフォルトでX-Powered-Byヘッダーが削除される。"""
    resp = await _get(_create_app())
    assert "x-powered-by" not in resp.headers


@pytest.mark.asyncio
async def test_custom_target_strips():
    """カスタムターゲットが削除され、非対象は残る。"""

    async def handler(request: Request) -> Response:
        return Response(
            content="ok",
            headers={
                "Server": "uvicorn",
                "X-Powered-By": "starlette",
                "X-Runtime": "123ms",
            },
        )

    app = _create_app(handler, headers=("server", "x-runtime"))
    resp = await _get(app)
    assert "server" not in resp.headers
    assert "x-runtime" not in resp.headers
    assert resp.headers["x-powered-by"] == "starlette"


@pytest.mark.asyncio
async def test_case_insensitive():
    """ヘッダー名の大文字小文字を問わず削除される。"""

    async def handler(request: Request) -> Response:
        return Response(
            content="ok",
            headers={"SERVER": "uppercase", "x-powered-by": "lowercase"},
        )

    app = _create_app(handler)
    resp = await _get(app)
    assert "server" not in resp.headers
    assert "x-powered-by" not in resp.headers


@pytest.mark.asyncio
async def test_target_absent_does_not_fail():
    """削除対象ヘッダーが存在しなくても正常終了する。"""
    resp = await _get(_create_app(_ok))
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_empty_headers_tuple():
    """空のheadersタプルを渡すとどのヘッダーも削除しない。"""
    resp = await _get(_create_app(headers=()))
    assert resp.headers["server"] == "uvicorn"
    assert resp.headers["x-powered-by"] == "starlette"


@pytest.mark.asyncio
async def test_preserves_other_headers():
    """削除対象以外のヘッダーは保持される。"""

    async def handler(request: Request) -> Response:
        return Response(
            content="ok",
            headers={"Server": "uvicorn", "X-Custom": "keep-me"},
        )

    app = _create_app(handler)
    resp = await _get(app)
    assert "server" not in resp.headers
    assert resp.headers["x-custom"] == "keep-me"


@pytest.mark.asyncio
async def test_websocket_scope_passthrough():
    """websocketスコープはスルーされる。"""
    received: list[str] = []

    async def bare_app(scope: object, receive: object, send: object) -> None:
        received.append(scope["type"])  # type: ignore[index]

    await ServerHeaderStripMiddleware(bare_app)({"type": "websocket"}, None, None)
    assert received == ["websocket"]
