"""aiohttp_lifespanの統合テスト。"""

import aiohttp
import pytest
from starlette.applications import Starlette
from starlette.requests import Request

from core_toolkit.aiohttp_lifespan import AioHttpLifespanResource, get_aiohttp_client
from core_toolkit.lifespan import create_lifespan


def _make_request(app: Starlette) -> Request:
    return Request({"type": "http", "app": app})


@pytest.mark.asyncio
async def test_get_aiohttp_client_returns_registered_client():
    app = Starlette()
    lifespan = create_lifespan(AioHttpLifespanResource("backend"))

    async with lifespan(app):
        client = get_aiohttp_client("backend")(_make_request(app))
        assert isinstance(client, aiohttp.ClientSession)
        assert not client.closed


def test_get_aiohttp_client_raises_runtime_error_when_missing():
    app = Starlette()

    with pytest.raises(RuntimeError, match="backend"):
        get_aiohttp_client("backend")(_make_request(app))


@pytest.mark.asyncio
async def test_multiple_clients_registered_independently():
    app = Starlette()
    lifespan = create_lifespan(
        AioHttpLifespanResource("backend"),
        AioHttpLifespanResource("internal"),
    )

    async with lifespan(app):
        backend_client = get_aiohttp_client("backend")(_make_request(app))
        internal_client = get_aiohttp_client("internal")(_make_request(app))
        assert backend_client is not internal_client


@pytest.mark.asyncio
async def test_aiohttp_lifespan_resource_closes_client_on_exit():
    app = Starlette()
    lifespan = create_lifespan(AioHttpLifespanResource("backend"))

    async with lifespan(app):
        client = get_aiohttp_client("backend")(_make_request(app))
        assert not client.closed

    assert client.closed


@pytest.mark.asyncio
async def test_session_kwargs_are_passed_through():
    app = Starlette()
    lifespan = create_lifespan(
        AioHttpLifespanResource("backend", headers={"X-Test": "1"})
    )

    async with lifespan(app):
        client = get_aiohttp_client("backend")(_make_request(app))
        # toolkit側は接続プール・タイムアウト等のデフォルトを一切注入しない
        # （完全パススルー）。呼び出し側が渡したkwargsがそのまま反映されることを
        # 確認する。
        assert client.headers["X-Test"] == "1"
