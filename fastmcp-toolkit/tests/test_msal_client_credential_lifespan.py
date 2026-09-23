"""fastmcp_toolkit.msal_lifespanのClient Credentials部分の統合テスト。"""

import pytest
from core_toolkit.token_cache_cipher import default_token_cache_cipher
from fakeredis.aioredis import FakeRedis
from fastmcp import Client, FastMCP
from msal import ConfidentialClientApplication

from fastmcp_toolkit.msal_lifespan import (
    CurrentMsalAppToken,
    msal_client_credential_lifespan,
)
from fastmcp_toolkit.redis_lifespan import redis_lifespan


def _key(seed: int) -> bytes:
    return bytes([seed]) * 32


def _make_app(cipher):
    return FastMCP(
        "test",
        lifespan=redis_lifespan("cache", "redis://localhost:6379/0")
        | msal_client_credential_lifespan(
            "downstream-api",
            client_id="client-id",
            client_credential="secret",
            authority="https://login.microsoftonline.com/tenant-id",
            redis_client_name="cache",
            cipher=cipher,
        ),
    )


@pytest.mark.asyncio
async def test_tool_resolves_msal_app_token_via_depends(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr("fastmcp_toolkit.redis_lifespan.Redis", FakeRedis)
    monkeypatch.setattr(
        ConfidentialClientApplication,
        "acquire_token_for_client",
        lambda self, scopes: {"access_token": "fake-token"},
    )

    cipher = default_token_cache_cipher(keys={"v1": _key(1)}, current_kid="v1")
    app = _make_app(cipher)

    @app.tool
    async def call_downstream(
        token: str = CurrentMsalAppToken(
            "downstream-api", scopes=["api://xxx/.default"]
        ),
    ) -> str:
        return token

    async with Client(app) as client:
        result = await client.call_tool("call_downstream", {})

    assert result.data == "fake-token"


@pytest.mark.asyncio
async def test_tool_raises_when_lifespan_not_registered():
    app = FastMCP("test")

    @app.tool
    async def call_downstream(
        token: str = CurrentMsalAppToken("downstream-api", scopes=["scope"]),
    ) -> str:
        return token

    async with Client(app) as client:
        # RPC境界を越えるとサーバー側の詳細な例外メッセージは失われ、
        # クライアントに届くのはDepends解決対象のパラメータ名を含む
        # "Failed to resolve dependency '<param>' for <fn>" のみになる
        # (fastmcp-toolkitのdb_lifespanで確認済み、a9b2fb9参照)。
        with pytest.raises(Exception, match=r"Failed to resolve dependency 'token'"):
            await client.call_tool("call_downstream", {})


@pytest.mark.asyncio
async def test_tool_raises_on_acquire_failure(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("fastmcp_toolkit.redis_lifespan.Redis", FakeRedis)
    monkeypatch.setattr(
        ConfidentialClientApplication,
        "acquire_token_for_client",
        lambda self, scopes: {
            "error": "invalid_client",
            "error_description": "bad secret",
        },
    )

    cipher = default_token_cache_cipher(keys={"v1": _key(1)}, current_kid="v1")
    app = _make_app(cipher)

    @app.tool
    async def call_downstream(
        token: str = CurrentMsalAppToken("downstream-api", scopes=["scope"]),
    ) -> str:
        return token

    async with Client(app) as client:
        with pytest.raises(Exception, match=r"Failed to resolve dependency 'token'"):
            await client.call_tool("call_downstream", {})


@pytest.mark.asyncio
async def test_repeated_calls_reuse_the_same_cache_instance(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr("fastmcp_toolkit.redis_lifespan.Redis", FakeRedis)

    call_count = {"n": 0}

    def fake_acquire(self, scopes):
        call_count["n"] += 1
        self.token_cache.has_state_changed = True
        return {"access_token": f"token-{call_count['n']}"}

    monkeypatch.setattr(
        ConfidentialClientApplication, "acquire_token_for_client", fake_acquire
    )

    cipher = default_token_cache_cipher(keys={"v1": _key(1)}, current_kid="v1")
    app = _make_app(cipher)

    @app.tool
    async def call_downstream(
        token: str = CurrentMsalAppToken(
            "downstream-api", scopes=["api://xxx/.default"]
        ),
    ) -> str:
        return token

    async with Client(app) as client:
        first = await client.call_tool("call_downstream", {})
        second = await client.call_tool("call_downstream", {})

    assert first.data == "token-1"
    assert second.data == "token-2"
    assert call_count["n"] == 2


@pytest.mark.asyncio
async def test_executor_is_shutdown_on_lifespan_exit(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("fastmcp_toolkit.redis_lifespan.Redis", FakeRedis)
    from fastmcp_toolkit.msal_lifespan import _lifespan_key

    cipher = default_token_cache_cipher(keys={"v1": _key(1)}, current_kid="v1")
    composed = redis_lifespan(
        "cache", "redis://localhost:6379/0"
    ) | msal_client_credential_lifespan(
        "downstream-api",
        client_id="client-id",
        client_credential="secret",
        authority="https://login.microsoftonline.com/tenant-id",
        redis_client_name="cache",
        cipher=cipher,
    )
    app = FastMCP("test", lifespan=composed)

    holder = {}
    async with composed(app) as ctx:
        holder["handle"] = ctx[_lifespan_key("downstream-api")]

    with pytest.raises(
        RuntimeError, match="cannot schedule new futures after shutdown"
    ):
        holder["handle"].executor.submit(lambda: None)
