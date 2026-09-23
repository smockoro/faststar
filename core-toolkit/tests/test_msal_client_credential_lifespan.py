"""msal_lifespanのClient Credentials部分の統合テスト。

実際のAADへは接続せず、``ConfidentialClientApplication.acquire_token_for_client``
をmonkeypatchでスタブする。
"""

import pytest
import requests
from fakeredis.aioredis import FakeRedis
from msal import ConfidentialClientApplication, SerializableTokenCache
from starlette.applications import Starlette
from starlette.requests import Request

from core_toolkit.lifespan import create_lifespan
from core_toolkit.msal_errors import MsalClaimsChallengeError, MsalTokenError
from core_toolkit.msal_lifespan import (
    MsalClientCredentialLifespanResource,
    _TimeoutSession,
    default_msal_http_session,
    get_msal_app_token,
)
from core_toolkit.redis_lifespan import RedisLifespanResource, get_redis_client
from core_toolkit.token_cache_cipher import JweTokenCacheCipher


def _key(seed: int) -> bytes:
    return bytes([seed]) * 32


def _make_request(app: Starlette) -> Request:
    return Request({"type": "http", "app": app})


def _make_resource(cipher: JweTokenCacheCipher) -> MsalClientCredentialLifespanResource:
    return MsalClientCredentialLifespanResource(
        name="downstream-api",
        client_id="client-id",
        client_credential="secret",
        authority="https://login.microsoftonline.com/tenant-id",
        redis_client_name="cache",
        cipher=cipher,
    )


def test_default_msal_http_session_retries_on_429_and_5xx():
    session = default_msal_http_session()

    adapter = session.get_adapter("https://login.microsoftonline.com")

    assert adapter.max_retries.total == 3
    assert 429 in adapter.max_retries.status_forcelist
    assert 500 in adapter.max_retries.status_forcelist
    # トークン取得は/oauth2/v2.0/tokenへのPOSTのため、allowed_methodsに
    # POSTが含まれていないとリトライが一切効かない(urllib3のデフォルトは
    # POSTを含まない)。
    assert "POST" in adapter.max_retries.allowed_methods


def test_default_msal_http_session_sets_default_timeout():
    session = default_msal_http_session()

    assert isinstance(session, _TimeoutSession)
    assert session._timeout == 10.0


def test_default_msal_http_session_sets_custom_timeout():
    session = default_msal_http_session(timeout=5.0)

    assert session._timeout == 5.0


def test_default_msal_http_session_forces_timeout_on_request(
    monkeypatch: pytest.MonkeyPatch,
):
    session = default_msal_http_session(timeout=5.0)

    captured: dict[str, object] = {}

    def fake_request(self, *args, **kwargs):
        captured.update(kwargs)
        response = requests.Response()
        response.status_code = 200
        return response

    monkeypatch.setattr(requests.Session, "request", fake_request)

    session.get("https://login.microsoftonline.com")

    assert captured["timeout"] == 5.0


@pytest.mark.asyncio
async def test_get_msal_app_token_returns_access_token(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("core_toolkit.redis_lifespan.Redis", FakeRedis)
    monkeypatch.setattr(
        ConfidentialClientApplication,
        "acquire_token_for_client",
        lambda self, scopes: {"access_token": "fake-token", "token_type": "Bearer"},
    )

    app = Starlette()
    cipher = JweTokenCacheCipher(keys={"v1": _key(1)}, current_kid="v1")
    lifespan = create_lifespan(
        RedisLifespanResource("cache", "redis://localhost:6379/0"),
        _make_resource(cipher),
    )

    async with lifespan(app):
        token = await get_msal_app_token(
            "downstream-api", scopes=["api://xxx/.default"]
        )(_make_request(app))

    assert token == "fake-token"


@pytest.mark.asyncio
async def test_get_msal_app_token_raises_runtime_error_when_missing():
    app = Starlette()

    with pytest.raises(RuntimeError, match="downstream-api"):
        await get_msal_app_token("downstream-api", scopes=["scope"])(_make_request(app))


@pytest.mark.asyncio
async def test_get_msal_app_token_raises_msal_token_error_on_failure(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr("core_toolkit.redis_lifespan.Redis", FakeRedis)
    monkeypatch.setattr(
        ConfidentialClientApplication,
        "acquire_token_for_client",
        lambda self, scopes: {
            "error": "invalid_client",
            "error_description": "AADSTS7000215: Invalid client secret",
            "error_codes": [7000215],
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
            await get_msal_app_token("downstream-api", scopes=["api://xxx/.default"])(
                _make_request(app)
            )

    assert exc_info.value.error == "invalid_client"
    assert exc_info.value.error_codes == [7000215]


@pytest.mark.asyncio
async def test_get_msal_app_token_raises_claims_challenge_error(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr("core_toolkit.redis_lifespan.Redis", FakeRedis)
    monkeypatch.setattr(
        ConfidentialClientApplication,
        "acquire_token_for_client",
        lambda self, scopes: {
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
            await get_msal_app_token("downstream-api", scopes=["api://xxx/.default"])(
                _make_request(app)
            )

    assert exc_info.value.claims == '{"access_token":{}}'


@pytest.mark.asyncio
async def test_token_cache_is_persisted_to_redis_encrypted(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr("core_toolkit.redis_lifespan.Redis", FakeRedis)

    def fake_acquire(self, scopes):
        # MSAL内部でトークンが追加された状態を模倣する
        self.token_cache.has_state_changed = True
        return {"access_token": "fake-token"}

    monkeypatch.setattr(
        ConfidentialClientApplication, "acquire_token_for_client", fake_acquire
    )

    app = Starlette()
    cipher = JweTokenCacheCipher(keys={"v1": _key(1)}, current_kid="v1")
    lifespan = create_lifespan(
        RedisLifespanResource("cache", "redis://localhost:6379/0"),
        _make_resource(cipher),
    )

    async with lifespan(app):
        await get_msal_app_token("downstream-api", scopes=["api://xxx/.default"])(
            _make_request(app)
        )
        redis = get_redis_client("cache")(_make_request(app))
        raw = await redis.get("msal:app_cache:downstream-api")

    assert raw is not None
    assert b"fake-token" not in raw
    assert cipher.decrypt(raw)  # 復号できる(=正しい鍵で暗号化されている)


@pytest.mark.asyncio
async def test_existing_cache_is_loaded_from_redis_on_startup(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr("core_toolkit.redis_lifespan.Redis", FakeRedis)
    seed_serialized = SerializableTokenCache().serialize()

    app = Starlette()
    cipher = JweTokenCacheCipher(keys={"v1": _key(1)}, current_kid="v1")

    seed_lifespan = create_lifespan(
        RedisLifespanResource("cache", "redis://localhost:6379/0")
    )
    async with seed_lifespan(app):
        redis = get_redis_client("cache")(_make_request(app))
        await redis.set(
            "msal:app_cache:downstream-api", cipher.encrypt(seed_serialized.encode())
        )

    loaded: dict[str, str] = {}

    def fake_acquire(self, scopes):
        loaded["value"] = self.token_cache.serialize()
        return {"access_token": "fake-token"}

    monkeypatch.setattr(
        ConfidentialClientApplication, "acquire_token_for_client", fake_acquire
    )

    lifespan = create_lifespan(
        RedisLifespanResource("cache", "redis://localhost:6379/0"),
        _make_resource(cipher),
    )

    async with lifespan(app):
        await get_msal_app_token("downstream-api", scopes=["scope"])(_make_request(app))

    assert loaded["value"] == seed_serialized


@pytest.mark.asyncio
async def test_executor_is_shutdown_on_lifespan_exit(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("core_toolkit.redis_lifespan.Redis", FakeRedis)

    app = Starlette()
    cipher = JweTokenCacheCipher(keys={"v1": _key(1)}, current_kid="v1")
    lifespan = create_lifespan(
        RedisLifespanResource("cache", "redis://localhost:6379/0"),
        _make_resource(cipher),
    )

    handles = {}
    async with lifespan(app):
        handles.update(app.state.msal_client_credential_handles)

    with pytest.raises(
        RuntimeError, match="cannot schedule new futures after shutdown"
    ):
        handles["downstream-api"].executor.submit(lambda: None)
