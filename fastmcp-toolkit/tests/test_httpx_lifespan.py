"""httpx_lifespan / CurrentHttpxClient の統合テスト。

Client(app)（インメモリトランスポート）はサーバーのlifespanを実際に
entryするため、ツール関数からCurrentHttpxClient(name)がDepends経由で
正しく解決されることをend-to-endで検証する。
"""

import httpx
import pytest
from fastmcp import Client, FastMCP

from fastmcp_toolkit.httpx_lifespan import CurrentHttpxClient, httpx_lifespan


@pytest.mark.asyncio
async def test_tool_resolves_httpx_client_via_depends():
    app = FastMCP("test", lifespan=httpx_lifespan("backend"))

    @app.tool
    async def whoami(client: httpx.AsyncClient = CurrentHttpxClient("backend")) -> bool:
        return client.is_closed

    async with Client(app) as client:
        result = await client.call_tool("whoami", {})

    assert result.data is False


@pytest.mark.asyncio
async def test_multiple_named_clients_are_independent():
    app = FastMCP(
        "test",
        lifespan=httpx_lifespan("backend") | httpx_lifespan("internal"),
    )

    @app.tool
    async def compare(
        backend: httpx.AsyncClient = CurrentHttpxClient("backend"),
        internal: httpx.AsyncClient = CurrentHttpxClient("internal"),
    ) -> bool:
        return backend is not internal

    async with Client(app) as client:
        result = await client.call_tool("compare", {})

    assert result.data is True


@pytest.mark.asyncio
async def test_tool_raises_runtime_error_when_lifespan_not_registered():
    app = FastMCP("test")

    @app.tool
    async def whoami(client: httpx.AsyncClient = CurrentHttpxClient("backend")) -> str:
        return type(client).__name__

    async with Client(app) as client:
        # test_aiohttp_lifespan.pyの同名テストと同じ理由（a9b2fb9参照）で、
        # 登録名"backend"ではなくパラメータ名"client"でmatchする。
        with pytest.raises(Exception, match=r"Failed to resolve dependency 'client'"):
            await client.call_tool("whoami", {})


def test_get_httpx_client_error_message_includes_registration_hint():
    from types import SimpleNamespace

    from fastmcp_toolkit.httpx_lifespan import _get_httpx_client

    get_client = _get_httpx_client("backend")
    with pytest.raises(RuntimeError, match="backend"):
        get_client(SimpleNamespace(lifespan_context={}))
