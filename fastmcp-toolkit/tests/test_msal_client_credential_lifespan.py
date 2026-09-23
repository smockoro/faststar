"""fastmcp_toolkit.msal_lifespanのClient Credentials部分の統合テスト。"""

import pytest
from core_toolkit.token_cache_cipher import default_token_cache_cipher
from fakeredis.aioredis import FakeRedis
from fastmcp import Client, FastMCP
from msal import ConfidentialClientApplication, SerializableTokenCache

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
async def test_first_call_loads_existing_cache_from_redis(
    monkeypatch: pytest.MonkeyPatch,
):
    """初回ツール呼び出し時にhandle.cache_loadedがFalseの分岐
    (_get_msal_app_token内)で、Redisの暗号化済みキャッシュが実際に
    ロード・復号されることを検証する(モジュールdocstring・Finding 5参照)。
    起動時ロードではなく初回呼び出し時ロードのため、
    test_msal_obo_lifespan.test_second_call_reuses_persisted_cache_from_redis
    と同じ手法(deserialize呼び出しの追跡)に寄せている。
    """
    monkeypatch.setattr("fastmcp_toolkit.redis_lifespan.Redis", FakeRedis)
    await FakeRedis.from_url("redis://localhost:6379/0").flushall()

    cipher = default_token_cache_cipher(keys={"v1": _key(1)}, current_kid="v1")
    seed_serialized = SerializableTokenCache().serialize()
    seed_payload = cipher.encrypt(seed_serialized.encode())
    seed_redis = FakeRedis.from_url("redis://localhost:6379/0")
    await seed_redis.set("msal:app_cache:downstream-api", seed_payload)

    deserialize_calls: list[str] = []
    original_deserialize = SerializableTokenCache.deserialize

    def tracking_deserialize(self, state: str) -> None:
        deserialize_calls.append(state)
        original_deserialize(self, state)

    monkeypatch.setattr(SerializableTokenCache, "deserialize", tracking_deserialize)

    loaded: dict[str, str] = {}

    def fake_acquire(self, scopes):
        # deserialize済みのキャッシュ内容をhandle.cache_loaded分岐の直後に
        # 捕捉する。実トークン追加は模倣せず、ロードされた状態そのものを見る。
        loaded["value"] = self.token_cache.serialize()
        self.token_cache.has_state_changed = True
        return {"access_token": "fake-token"}

    monkeypatch.setattr(
        ConfidentialClientApplication, "acquire_token_for_client", fake_acquire
    )

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
    # Redisにseedしたキャッシュが実際にdeserializeされたことを確認する。
    assert deserialize_calls == [seed_serialized]
    assert loaded["value"] == seed_serialized

    # has_state_changed=Trueのため、更新後のキャッシュが同じRedisキーへ
    # 暗号化済みで書き戻されることも確認する。
    written_redis = FakeRedis.from_url("redis://localhost:6379/0")
    raw = await written_redis.get("msal:app_cache:downstream-api")
    assert raw is not None
    assert raw != seed_payload
    assert cipher.decrypt(raw)  # 復号できる(=正しい鍵で暗号化されている)


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
