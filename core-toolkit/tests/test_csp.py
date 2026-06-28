"""CSPMiddlewareのテスト。"""

import pytest
from httpx import ASGITransport, AsyncClient
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Route

from core_toolkit.middleware.csp import CSPMiddleware


async def _ok(request: Request) -> Response:
    return Response(content="ok")


async def _not_found(request: Request) -> Response:
    return Response(status_code=404, content="not found")


def _create_app(handler=_ok, **kwargs: object) -> Starlette:
    app = Starlette(routes=[Route("/", handler)])
    app.add_middleware(CSPMiddleware, **kwargs)
    return app


async def _get(app: Starlette) -> Response:
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        return await client.get("/")


@pytest.mark.asyncio
async def test_default_includes_self_default_src():
    """デフォルトCSPにdefault-src 'self'が含まれる。"""
    resp = await _get(_create_app())
    assert "default-src 'self'" in resp.headers["content-security-policy"]


@pytest.mark.asyncio
async def test_default_includes_frame_ancestors_none():
    """デフォルトCSPにframe-ancestors 'none'が含まれる（クリックジャッキング防止）。"""
    resp = await _get(_create_app())
    assert "frame-ancestors 'none'" in resp.headers["content-security-policy"]


@pytest.mark.asyncio
async def test_default_includes_object_src_none():
    """デフォルトCSPにobject-src 'none'が含まれる（プラグイン無効化）。"""
    resp = await _get(_create_app())
    assert "object-src 'none'" in resp.headers["content-security-policy"]


@pytest.mark.asyncio
async def test_default_includes_upgrade_insecure_requests():
    """デフォルトCSPにupgrade-insecure-requestsが含まれる。"""
    resp = await _get(_create_app())
    assert "upgrade-insecure-requests" in resp.headers["content-security-policy"]


@pytest.mark.asyncio
async def test_custom_directives():
    """カスタムdirectivesが反映される。"""
    resp = await _get(
        _create_app(
            directives={
                "default-src": "'self'",
                "script-src": "'self' 'unsafe-inline'",
            }
        )
    )
    csp = resp.headers["content-security-policy"]
    assert "default-src 'self'" in csp
    assert "script-src 'self' 'unsafe-inline'" in csp
    assert "frame-ancestors" not in csp


@pytest.mark.asyncio
async def test_custom_excludes_default_keys():
    """カスタムdirectivesを渡すとデフォルトキーは使われない。"""
    resp = await _get(_create_app(directives={"default-src": "'none'"}))
    csp = resp.headers["content-security-policy"]
    assert "object-src" not in csp
    assert "upgrade-insecure-requests" not in csp


@pytest.mark.asyncio
async def test_valueless_directive_no_trailing_space():
    """値なしディレクティブはキーのみで末尾スペースなし。"""
    resp = await _get(_create_app(directives={"upgrade-insecure-requests": ""}))
    assert resp.headers["content-security-policy"] == "upgrade-insecure-requests"


@pytest.mark.asyncio
async def test_single_directive_no_semicolon():
    """ディレクティブが1つのとき末尾セミコロンなし。"""
    resp = await _get(_create_app(directives={"default-src": "'none'"}))
    assert resp.headers["content-security-policy"] == "default-src 'none'"


@pytest.mark.asyncio
async def test_multiple_directives_semicolon_separated():
    """複数ディレクティブはセミコロン+スペース区切り。"""
    resp = await _get(
        _create_app(
            directives={
                "default-src": "'self'",
                "object-src": "'none'",
            }
        )
    )
    assert (
        resp.headers["content-security-policy"]
        == "default-src 'self'; object-src 'none'"
    )


@pytest.mark.asyncio
async def test_empty_directives_dict():
    """空のdictを渡すとCSPヘッダーは空文字列。"""
    resp = await _get(_create_app(directives={}))
    assert resp.headers["content-security-policy"] == ""


@pytest.mark.asyncio
async def test_on_error():
    """エラーレスポンスでも付与される。"""
    resp = await _get(_create_app(_not_found))
    assert resp.status_code == 404
    assert "content-security-policy" in resp.headers


@pytest.mark.asyncio
async def test_websocket_scope_passthrough():
    """websocketスコープはスルーされる。"""
    received: list[str] = []

    async def bare_app(scope: object, receive: object, send: object) -> None:
        received.append(scope["type"])  # type: ignore[index]

    await CSPMiddleware(bare_app)({"type": "websocket"}, None, None)
    assert received == ["websocket"]
