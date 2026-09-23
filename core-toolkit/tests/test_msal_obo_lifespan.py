"""msal_lifespanのOBO部分の統合テスト。

実際のAADへは接続せず、``ConfidentialClientApplication.acquire_token_on_behalf_of``
をmonkeypatchでスタブする。
"""

import pytest
from fakeredis.aioredis import FakeRedis
from msal import ConfidentialClientApplication
from starlette.applications import Starlette
from starlette.requests import Request

from core_toolkit.lifespan import create_lifespan
from core_toolkit.msal_errors import MsalClaimsChallengeError, MsalTokenError
from core_toolkit.msal_lifespan import MsalOboLifespanResource, get_msal_obo_token
from core_toolkit.redis_lifespan import RedisLifespanResource, get_redis_client
from core_toolkit.token_cache_cipher import JweTokenCacheCipher


def _key(seed: int) -> bytes:
    return bytes([seed]) * 32


def _make_request(app: Starlette) -> Request:
    return Request({"type": "http", "app": app})


def _make_resource(cipher: JweTokenCacheCipher) -> MsalOboLifespanResource:
    return MsalOboLifespanResource(
        name="graph",
        client_id="client-id",
        client_credential="secret",
        authority="https://login.microsoftonline.com/tenant-id",
        redis_client_name="cache",
        cipher=cipher,
    )


@pytest.mark.asyncio
async def test_get_msal_obo_token_returns_access_token(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("core_toolkit.redis_lifespan.Redis", FakeRedis)
    monkeypatch.setattr(
        ConfidentialClientApplication,
        "acquire_token_on_behalf_of",
        lambda self, user_assertion, scopes: {"access_token": "fake-token"},
    )

    app = Starlette()
    cipher = JweTokenCacheCipher(keys={"v1": _key(1)}, current_kid="v1")
    lifespan = create_lifespan(
        RedisLifespanResource("cache", "redis://localhost:6379/0"),
        _make_resource(cipher),
    )

    async with lifespan(app):
        token = await get_msal_obo_token("graph", scopes=["User.Read"])(
            _make_request(app), user_assertion="user-jwt", user_id="user-1"
        )

    assert token == "fake-token"


@pytest.mark.asyncio
async def test_get_msal_obo_token_raises_runtime_error_when_missing():
    app = Starlette()

    with pytest.raises(RuntimeError, match="graph"):
        await get_msal_obo_token("graph", scopes=["User.Read"])(
            _make_request(app), user_assertion="user-jwt", user_id="user-1"
        )


@pytest.mark.asyncio
async def test_get_msal_obo_token_raises_msal_token_error_on_failure(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr("core_toolkit.redis_lifespan.Redis", FakeRedis)
    monkeypatch.setattr(
        ConfidentialClientApplication,
        "acquire_token_on_behalf_of",
        lambda self, user_assertion, scopes: {
            "error": "invalid_grant",
            "error_description": "AADSTS50013: Assertion is invalid",
            "error_codes": [50013],
        },
    )

    app = Starlette()
    cipher = JweTokenCacheCipher(keys={"v1": _key(1)}, current_kid="v1")
    lifespan = create_lifespan(
        RedisLifespanResource("cache", "redis://localhost:6379/0"),
        _make_resource(cipher),
    )

    async with lifespan(app):
        with pytest.raises(MsalTokenError) as exc_info:
            await get_msal_obo_token("graph", scopes=["User.Read"])(
                _make_request(app), user_assertion="user-jwt", user_id="user-1"
            )

    assert exc_info.value.error == "invalid_grant"


@pytest.mark.asyncio
async def test_get_msal_obo_token_raises_claims_challenge_error(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr("core_toolkit.redis_lifespan.Redis", FakeRedis)
    monkeypatch.setattr(
        ConfidentialClientApplication,
        "acquire_token_on_behalf_of",
        lambda self, user_assertion, scopes: {
            "error": "interaction_required",
            "error_description": "MFA required",
            "claims": '{"access_token":{}}',
        },
    )

    app = Starlette()
    cipher = JweTokenCacheCipher(keys={"v1": _key(1)}, current_kid="v1")
    lifespan = create_lifespan(
        RedisLifespanResource("cache", "redis://localhost:6379/0"),
        _make_resource(cipher),
    )

    async with lifespan(app):
        with pytest.raises(MsalClaimsChallengeError) as exc_info:
            await get_msal_obo_token("graph", scopes=["User.Read"])(
                _make_request(app), user_assertion="user-jwt", user_id="user-1"
            )

    assert exc_info.value.claims == '{"access_token":{}}'


@pytest.mark.asyncio
async def test_different_users_get_independent_cache_keys(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr("core_toolkit.redis_lifespan.Redis", FakeRedis)

    def fake_acquire(self, user_assertion, scopes):
        self.token_cache.has_state_changed = True
        return {"access_token": f"token-for-{user_assertion}"}

    monkeypatch.setattr(
        ConfidentialClientApplication, "acquire_token_on_behalf_of", fake_acquire
    )

    app = Starlette()
    cipher = JweTokenCacheCipher(keys={"v1": _key(1)}, current_kid="v1")
    lifespan = create_lifespan(
        RedisLifespanResource("cache", "redis://localhost:6379/0"),
        _make_resource(cipher),
    )

    async with lifespan(app):
        provider = get_msal_obo_token("graph", scopes=["User.Read"])
        token1 = await provider(
            _make_request(app), user_assertion="jwt-1", user_id="user-1"
        )
        token2 = await provider(
            _make_request(app), user_assertion="jwt-2", user_id="user-2"
        )

        redis = get_redis_client("cache")(_make_request(app))
        raw1 = await redis.get("msal:obo_cache:graph:user-1")
        raw2 = await redis.get("msal:obo_cache:graph:user-2")

    assert token1 == "token-for-jwt-1"
    assert token2 == "token-for-jwt-2"
    assert raw1 is not None
    assert raw2 is not None
    assert raw1 != raw2


@pytest.mark.asyncio
async def test_token_cache_is_persisted_with_ttl(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("core_toolkit.redis_lifespan.Redis", FakeRedis)

    def fake_acquire(self, user_assertion, scopes):
        self.token_cache.has_state_changed = True
        return {"access_token": "fake-token"}

    monkeypatch.setattr(
        ConfidentialClientApplication, "acquire_token_on_behalf_of", fake_acquire
    )

    app = Starlette()
    cipher = JweTokenCacheCipher(keys={"v1": _key(1)}, current_kid="v1")
    lifespan = create_lifespan(
        RedisLifespanResource("cache", "redis://localhost:6379/0"),
        _make_resource(cipher),
    )

    async with lifespan(app):
        await get_msal_obo_token("graph", scopes=["User.Read"])(
            _make_request(app), user_assertion="jwt-1", user_id="user-1"
        )
        redis = get_redis_client("cache")(_make_request(app))
        ttl = await redis.ttl("msal:obo_cache:graph:user-1")

    assert ttl > 0


@pytest.mark.asyncio
async def test_executor_is_shutdown_on_lifespan_exit(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("core_toolkit.redis_lifespan.Redis", FakeRedis)

    app = Starlette()
    cipher = JweTokenCacheCipher(keys={"v1": _key(1)}, current_kid="v1")
    lifespan = create_lifespan(
        RedisLifespanResource("cache", "redis://localhost:6379/0"),
        _make_resource(cipher),
    )

    configs = {}
    async with lifespan(app):
        configs.update(app.state.msal_obo_configs)

    with pytest.raises(
        RuntimeError, match="cannot schedule new futures after shutdown"
    ):
        configs["graph"].executor.submit(lambda: None)
