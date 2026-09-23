"""fastmcp_toolkit.msal_lifespanのOBO部分の統合テスト。"""

import pytest
from core_toolkit.token_cache_cipher import default_token_cache_cipher
from fakeredis.aioredis import FakeRedis
from fastmcp import Client, Context, FastMCP
from msal import ConfidentialClientApplication, SerializableTokenCache

from fastmcp_toolkit.msal_lifespan import acquire_msal_obo_token, msal_obo_lifespan
from fastmcp_toolkit.redis_lifespan import redis_lifespan


def _key(seed: int) -> bytes:
    return bytes([seed]) * 32


def _make_app(cipher):
    return FastMCP(
        "test",
        lifespan=redis_lifespan("cache", "redis://localhost:6379/0")
        | msal_obo_lifespan(
            "graph",
            client_id="client-id",
            client_credential="secret",
            authority="https://login.microsoftonline.com/tenant-id",
            redis_client_name="cache",
            cipher=cipher,
        ),
    )


@pytest.mark.asyncio
async def test_acquire_msal_obo_token_returns_access_token(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr("fastmcp_toolkit.redis_lifespan.Redis", FakeRedis)
    monkeypatch.setattr(
        ConfidentialClientApplication,
        "acquire_token_on_behalf_of",
        lambda self, user_assertion, scopes: {
            "access_token": f"token-for-{user_assertion}"
        },
    )

    cipher = default_token_cache_cipher(keys={"v1": _key(1)}, current_kid="v1")
    app = _make_app(cipher)

    @app.tool
    async def call_graph(ctx: Context, user_assertion: str, user_id: str) -> str:
        return await acquire_msal_obo_token(
            ctx,
            "graph",
            scopes=["User.Read"],
            user_assertion=user_assertion,
            user_id=user_id,
        )

    async with Client(app) as client:
        result = await client.call_tool(
            "call_graph", {"user_assertion": "jwt-1", "user_id": "user-1"}
        )

    assert result.data == "token-for-jwt-1"


@pytest.mark.asyncio
async def test_acquire_msal_obo_token_raises_when_lifespan_not_registered():
    app = FastMCP("test")

    @app.tool
    async def call_graph(ctx: Context, user_assertion: str, user_id: str) -> str:
        return await acquire_msal_obo_token(
            ctx,
            "graph",
            scopes=["User.Read"],
            user_assertion=user_assertion,
            user_id=user_id,
        )

    async with Client(app) as client:
        with pytest.raises(Exception, match="graph"):
            await client.call_tool(
                "call_graph", {"user_assertion": "jwt-1", "user_id": "user-1"}
            )


@pytest.mark.asyncio
async def test_acquire_msal_obo_token_raises_msal_token_error_on_failure(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr("fastmcp_toolkit.redis_lifespan.Redis", FakeRedis)
    monkeypatch.setattr(
        ConfidentialClientApplication,
        "acquire_token_on_behalf_of",
        lambda self, user_assertion, scopes: {
            "error": "invalid_grant",
            "error_description": "AADSTS50013: Assertion is invalid",
        },
    )

    cipher = default_token_cache_cipher(keys={"v1": _key(1)}, current_kid="v1")
    app = _make_app(cipher)

    @app.tool
    async def call_graph(ctx: Context, user_assertion: str, user_id: str) -> str:
        return await acquire_msal_obo_token(
            ctx,
            "graph",
            scopes=["User.Read"],
            user_assertion=user_assertion,
            user_id=user_id,
        )

    async with Client(app) as client:
        with pytest.raises(Exception, match="invalid_grant"):
            await client.call_tool(
                "call_graph", {"user_assertion": "jwt-1", "user_id": "user-1"}
            )


@pytest.mark.asyncio
async def test_acquire_msal_obo_token_raises_claims_challenge_error(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr("fastmcp_toolkit.redis_lifespan.Redis", FakeRedis)
    monkeypatch.setattr(
        ConfidentialClientApplication,
        "acquire_token_on_behalf_of",
        lambda self, user_assertion, scopes: {
            "error": "interaction_required",
            "error_description": "MFA required",
            "claims": '{"access_token":{}}',
        },
    )

    cipher = default_token_cache_cipher(keys={"v1": _key(1)}, current_kid="v1")
    app = _make_app(cipher)

    @app.tool
    async def call_graph(ctx: Context, user_assertion: str, user_id: str) -> str:
        return await acquire_msal_obo_token(
            ctx,
            "graph",
            scopes=["User.Read"],
            user_assertion=user_assertion,
            user_id=user_id,
        )

    async with Client(app) as client:
        with pytest.raises(Exception, match="interaction_required"):
            await client.call_tool(
                "call_graph", {"user_assertion": "jwt-1", "user_id": "user-1"}
            )


@pytest.mark.asyncio
async def test_different_users_get_independent_tokens(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("fastmcp_toolkit.redis_lifespan.Redis", FakeRedis)
    monkeypatch.setattr(
        ConfidentialClientApplication,
        "acquire_token_on_behalf_of",
        lambda self, user_assertion, scopes: {
            "access_token": f"token-for-{user_assertion}"
        },
    )

    cipher = default_token_cache_cipher(keys={"v1": _key(1)}, current_kid="v1")
    app = _make_app(cipher)

    @app.tool
    async def call_graph(ctx: Context, user_assertion: str, user_id: str) -> str:
        return await acquire_msal_obo_token(
            ctx,
            "graph",
            scopes=["User.Read"],
            user_assertion=user_assertion,
            user_id=user_id,
        )

    async with Client(app) as client:
        result1 = await client.call_tool(
            "call_graph", {"user_assertion": "jwt-1", "user_id": "user-1"}
        )
        result2 = await client.call_tool(
            "call_graph", {"user_assertion": "jwt-2", "user_id": "user-2"}
        )

    assert result1.data == "token-for-jwt-1"
    assert result2.data == "token-for-jwt-2"


@pytest.mark.asyncio
async def test_token_cache_is_persisted_with_ttl(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("fastmcp_toolkit.redis_lifespan.Redis", FakeRedis)
    # 同一URLのFakeRedisはプロセス内でインメモリストアを共有するため、
    # 他テストの書き込みが残っていないことを保証してから検証する。
    await FakeRedis.from_url("redis://localhost:6379/0").flushall()

    def fake_acquire(self, user_assertion, scopes):
        self.token_cache.has_state_changed = True
        return {"access_token": f"token-for-{user_assertion}"}

    monkeypatch.setattr(
        ConfidentialClientApplication, "acquire_token_on_behalf_of", fake_acquire
    )

    cipher = default_token_cache_cipher(keys={"v1": _key(1)}, current_kid="v1")
    app = _make_app(cipher)

    @app.tool
    async def call_graph(ctx: Context, user_assertion: str, user_id: str) -> str:
        return await acquire_msal_obo_token(
            ctx,
            "graph",
            scopes=["User.Read"],
            user_assertion=user_assertion,
            user_id=user_id,
        )

    async with Client(app) as client:
        await client.call_tool(
            "call_graph", {"user_assertion": "jwt-1", "user_id": "user-1"}
        )

    # redis_lifespanが接続したものと同じURLのFakeRedisは同一のインメモリ
    # ストアを共有するため、lifespan終了後でも書き込み結果を直接検証できる。
    redis = FakeRedis.from_url("redis://localhost:6379/0")
    cache_key = "msal:obo_cache:graph:user-1"
    raw = await redis.get(cache_key)
    ttl = await redis.ttl(cache_key)

    assert raw is not None
    assert ttl > 0
    # 平文のcache.serialize()ではなく暗号化済みペイロードが保存されている
    # ことを、実際に複合できることをもって確認する。
    decrypted = cipher.decrypt(raw).decode()
    assert decrypted


@pytest.mark.asyncio
async def test_second_call_reuses_persisted_cache_from_redis(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr("fastmcp_toolkit.redis_lifespan.Redis", FakeRedis)
    await FakeRedis.from_url("redis://localhost:6379/0").flushall()

    deserialize_calls: list[str] = []
    original_deserialize = SerializableTokenCache.deserialize

    def tracking_deserialize(self, state: str) -> None:
        deserialize_calls.append(state)
        original_deserialize(self, state)

    monkeypatch.setattr(SerializableTokenCache, "deserialize", tracking_deserialize)

    call_count = {"n": 0}

    def fake_acquire(self, user_assertion, scopes):
        call_count["n"] += 1
        self.token_cache.has_state_changed = True
        return {"access_token": f"token-{call_count['n']}"}

    monkeypatch.setattr(
        ConfidentialClientApplication, "acquire_token_on_behalf_of", fake_acquire
    )

    cipher = default_token_cache_cipher(keys={"v1": _key(1)}, current_kid="v1")
    app = _make_app(cipher)

    @app.tool
    async def call_graph(ctx: Context, user_assertion: str, user_id: str) -> str:
        return await acquire_msal_obo_token(
            ctx,
            "graph",
            scopes=["User.Read"],
            user_assertion=user_assertion,
            user_id=user_id,
        )

    async with Client(app) as client:
        first = await client.call_tool(
            "call_graph", {"user_assertion": "jwt-1", "user_id": "user-1"}
        )
        second = await client.call_tool(
            "call_graph", {"user_assertion": "jwt-1", "user_id": "user-1"}
        )

    assert first.data == "token-1"
    assert second.data == "token-2"
    # 1回目の呼び出し時点ではRedisにキャッシュが無いためdeserializeは呼ばれず、
    # 1回目で書き込んだ暗号化済みキャッシュが2回目の呼び出しで実際に
    # ロード・復号されたことを、deserialize()が1回だけ呼ばれたことで確認する。
    assert len(deserialize_calls) == 1


@pytest.mark.asyncio
async def test_executor_is_shutdown_on_lifespan_exit(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("fastmcp_toolkit.redis_lifespan.Redis", FakeRedis)
    from fastmcp_toolkit.msal_lifespan import _obo_lifespan_key

    cipher = default_token_cache_cipher(keys={"v1": _key(1)}, current_kid="v1")
    composed = redis_lifespan("cache", "redis://localhost:6379/0") | msal_obo_lifespan(
        "graph",
        client_id="client-id",
        client_credential="secret",
        authority="https://login.microsoftonline.com/tenant-id",
        redis_client_name="cache",
        cipher=cipher,
    )
    app = FastMCP("test", lifespan=composed)

    holder = {}
    async with composed(app) as ctx:
        holder["config"] = ctx[_obo_lifespan_key("graph")]

    with pytest.raises(
        RuntimeError, match="cannot schedule new futures after shutdown"
    ):
        holder["config"].executor.submit(lambda: None)
