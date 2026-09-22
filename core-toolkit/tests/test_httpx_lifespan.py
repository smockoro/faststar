"""httpx_lifespanの統合テスト。"""

import httpx
import pytest
from starlette.applications import Starlette
from starlette.requests import Request

from core_toolkit.httpx_lifespan import HttpxLifespanResource, get_httpx_client
from core_toolkit.lifespan import create_lifespan


def _make_request(app: Starlette) -> Request:
    return Request({"type": "http", "app": app})


@pytest.mark.asyncio
async def test_get_httpx_client_returns_registered_client():
    app = Starlette()
    lifespan = create_lifespan(HttpxLifespanResource("backend"))

    async with lifespan(app):
        client = get_httpx_client("backend")(_make_request(app))
        assert isinstance(client, httpx.AsyncClient)
        assert not client.is_closed


def test_get_httpx_client_raises_runtime_error_when_missing():
    app = Starlette()

    with pytest.raises(RuntimeError, match="backend"):
        get_httpx_client("backend")(_make_request(app))


@pytest.mark.asyncio
async def test_multiple_clients_registered_independently():
    app = Starlette()
    lifespan = create_lifespan(
        HttpxLifespanResource("backend"),
        HttpxLifespanResource("internal"),
    )

    async with lifespan(app):
        backend_client = get_httpx_client("backend")(_make_request(app))
        internal_client = get_httpx_client("internal")(_make_request(app))
        assert backend_client is not internal_client


@pytest.mark.asyncio
async def test_httpx_lifespan_resource_closes_client_on_exit():
    app = Starlette()
    lifespan = create_lifespan(HttpxLifespanResource("backend"))

    async with lifespan(app):
        client = get_httpx_client("backend")(_make_request(app))
        assert not client.is_closed

    assert client.is_closed


@pytest.mark.asyncio
async def test_client_kwargs_are_passed_through():
    app = Starlette()
    lifespan = create_lifespan(
        HttpxLifespanResource("backend", base_url="https://example.test")
    )

    async with lifespan(app):
        client = get_httpx_client("backend")(_make_request(app))
        assert str(client.base_url) == "https://example.test"
