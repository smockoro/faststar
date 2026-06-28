"""PermissionsPolicyMiddlewareのテスト。"""

import pytest
from httpx import ASGITransport, AsyncClient
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Route

from core_toolkit.middleware.permissions_policy import PermissionsPolicyMiddleware


async def _ok(request: Request) -> Response:
    return Response(content="ok")


def _create_app(**kwargs: object) -> Starlette:
    app = Starlette(routes=[Route("/", _ok)])
    app.add_middleware(PermissionsPolicyMiddleware, **kwargs)
    return app


async def _get(app: Starlette) -> Response:
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        return await client.get("/")


@pytest.mark.asyncio
async def test_default_camera():
    """デフォルトにcamera=()が含まれる。"""
    resp = await _get(_create_app())
    assert "camera=()" in resp.headers["permissions-policy"]


@pytest.mark.asyncio
async def test_default_microphone():
    """デフォルトにmicrophone=()が含まれる。"""
    resp = await _get(_create_app())
    assert "microphone=()" in resp.headers["permissions-policy"]


@pytest.mark.asyncio
async def test_default_geolocation():
    """デフォルトにgeolocation=()が含まれる。"""
    resp = await _get(_create_app())
    assert "geolocation=()" in resp.headers["permissions-policy"]


@pytest.mark.asyncio
async def test_default_payment():
    """デフォルトにpayment=()が含まれる。"""
    resp = await _get(_create_app())
    assert "payment=()" in resp.headers["permissions-policy"]


@pytest.mark.asyncio
async def test_default_usb():
    """デフォルトにusb=()が含まれる。"""
    resp = await _get(_create_app())
    assert "usb=()" in resp.headers["permissions-policy"]


@pytest.mark.asyncio
async def test_default_interest_cohort():
    """デフォルトにinterest-cohort=()が含まれる。"""
    resp = await _get(_create_app())
    assert "interest-cohort=()" in resp.headers["permissions-policy"]


@pytest.mark.asyncio
async def test_custom_features():
    """カスタムfeaturesが反映される。"""
    resp = await _get(_create_app(features={"camera": "(self)", "microphone": "()"}))
    policy = resp.headers["permissions-policy"]
    assert "camera=(self)" in policy
    assert "microphone=()" in policy
    assert "geolocation" not in policy


@pytest.mark.asyncio
async def test_single_feature_no_comma():
    """機能が1つのときカンマなし。"""
    resp = await _get(_create_app(features={"camera": "()"}))
    assert resp.headers["permissions-policy"] == "camera=()"


@pytest.mark.asyncio
async def test_multiple_features_comma_separated():
    """複数機能はカンマ+スペース区切り。"""
    resp = await _get(_create_app(features={"camera": "()", "microphone": "()"}))
    assert resp.headers["permissions-policy"] == "camera=(), microphone=()"


@pytest.mark.asyncio
async def test_empty_features_dict():
    """空のdictを渡すとPermissions-Policyヘッダーは空文字列。"""
    resp = await _get(_create_app(features={}))
    assert resp.headers["permissions-policy"] == ""


@pytest.mark.asyncio
async def test_websocket_scope_passthrough():
    """websocketスコープはスルーされる。"""
    received: list[str] = []

    async def bare_app(scope: object, receive: object, send: object) -> None:
        received.append(scope["type"])  # type: ignore[index]

    await PermissionsPolicyMiddleware(bare_app)({"type": "websocket"}, None, None)
    assert received == ["websocket"]
