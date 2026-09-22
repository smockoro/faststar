"""aiohttp_lifespan / CurrentAiohttpClient の統合テスト。

Client(app)（インメモリトランスポート）はサーバーのlifespanを実際に
entryするため、ツール関数からCurrentAiohttpClient(name)がDepends経由で
正しく解決されることをend-to-endで検証する。
"""

import aiohttp
import pytest
from fastmcp import Client, FastMCP

from fastmcp_toolkit.aiohttp_lifespan import CurrentAiohttpClient, aiohttp_lifespan


@pytest.mark.asyncio
async def test_tool_resolves_aiohttp_client_via_depends():
    app = FastMCP("test", lifespan=aiohttp_lifespan("backend"))

    @app.tool
    async def whoami(
        session: aiohttp.ClientSession = CurrentAiohttpClient("backend"),
    ) -> bool:
        return session.closed

    async with Client(app) as client:
        result = await client.call_tool("whoami", {})

    assert result.data is False


@pytest.mark.asyncio
async def test_multiple_named_clients_are_independent():
    app = FastMCP(
        "test",
        lifespan=aiohttp_lifespan("backend") | aiohttp_lifespan("internal"),
    )

    @app.tool
    async def compare(
        backend: aiohttp.ClientSession = CurrentAiohttpClient("backend"),
        internal: aiohttp.ClientSession = CurrentAiohttpClient("internal"),
    ) -> bool:
        return backend is not internal

    async with Client(app) as client:
        result = await client.call_tool("compare", {})

    assert result.data is True


@pytest.mark.asyncio
async def test_tool_raises_runtime_error_when_lifespan_not_registered():
    app = FastMCP("test")

    @app.tool
    async def whoami(
        session: aiohttp.ClientSession = CurrentAiohttpClient("backend"),
    ) -> str:
        return type(session).__name__

    async with Client(app) as client:
        # RPC境界を越えるとサーバー側の詳細な例外メッセージは失われ、クライアント
        # に届くのはDepends解決対象のパラメータ名を含む"Failed to resolve
        # dependency '<param>' for <fn>"のみになる（db_lifespanのa9b2fb9参照）。
        # そのため登録名"backend"ではなくパラメータ名"session"でmatchする。
        with pytest.raises(Exception, match=r"Failed to resolve dependency 'session'"):
            await client.call_tool("whoami", {})


def test_get_aiohttp_client_error_message_includes_registration_hint():
    from types import SimpleNamespace

    from fastmcp_toolkit.aiohttp_lifespan import _get_aiohttp_client

    get_client = _get_aiohttp_client("backend")
    with pytest.raises(RuntimeError, match="backend"):
        get_client(SimpleNamespace(lifespan_context={}))
