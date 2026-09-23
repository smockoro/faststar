"""fastapi_toolkit.msal_lifespanの統合テスト。

MsalClientCredentialLifespanResource/MsalOboLifespanResource自体の
起動・終了処理・トークン取得ロジックはcore-toolkit側で検証済みのため、
ここではFastAPI固有の部分――``Annotated[T, Depends(...)]``や``Request``
引数が実際のエンドポイントで解決されること――だけを検証する。
"""

from typing import Annotated
from unittest.mock import MagicMock

import pytest
from fakeredis.aioredis import FakeRedis
from fastapi import Depends, FastAPI, Request
from httpx import ASGITransport, AsyncClient
from msal import ConfidentialClientApplication

from fastapi_toolkit.lifespan import create_lifespan
from fastapi_toolkit.msal_lifespan import (
    MsalClientCredentialLifespanResource,
    MsalOboLifespanResource,
    default_token_cache_cipher,
    get_msal_app_token,
    get_msal_obo_token,
)
from fastapi_toolkit.redis_lifespan import RedisLifespanResource


def _key(seed: int) -> bytes:
    return bytes([seed]) * 32


def _mock_http_session():
    """モック用HTTPセッションを作成。MSALが呼ぶgetメソッドをモック化する。"""
    session = MagicMock()
    session.get.return_value.status_code = 200
    session.get.return_value.text = '{"token_endpoint":"https://login.microsoftonline.com/tenant-id/oauth2/v2.0/token","authorization_endpoint":"https://login.microsoftonline.com/tenant-id/oauth2/v2.0/authorize"}'
    return session


@pytest.mark.asyncio
async def test_msal_app_token_resolves_via_depends(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("core_toolkit.redis_lifespan.Redis", FakeRedis)
    monkeypatch.setattr(
        ConfidentialClientApplication,
        "acquire_token_for_client",
        lambda self, scopes: {"access_token": "fake-token"},
    )

    app = FastAPI()
    cipher = default_token_cache_cipher(keys={"v1": _key(1)}, current_kid="v1")

    DownstreamApiToken = Annotated[str, Depends(get_msal_app_token("downstream-api", scopes=["api://xxx/.default"]))]

    @app.get("/proxy")
    async def proxy(token: DownstreamApiToken) -> dict[str, str]:
        return {"token": token}

    lifespan = create_lifespan(
        RedisLifespanResource("cache", "redis://localhost:6379/0"),
        MsalClientCredentialLifespanResource(
            name="downstream-api",
            client_id="client-id",
            client_credential="secret",
            authority="https://login.microsoftonline.com/tenant-id",
            redis_client_name="cache",
            cipher=cipher,
            http_client_factory=_mock_http_session,
        ),
    )

    async with lifespan(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/proxy")

    assert resp.status_code == 200
    assert resp.json() == {"token": "fake-token"}


@pytest.mark.asyncio
async def test_msal_obo_token_resolves_via_explicit_call(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("core_toolkit.redis_lifespan.Redis", FakeRedis)
    monkeypatch.setattr(
        ConfidentialClientApplication,
        "acquire_token_on_behalf_of",
        lambda self, user_assertion, scopes: {"access_token": f"obo-{user_assertion}"},
    )

    app = FastAPI()
    cipher = default_token_cache_cipher(keys={"v1": _key(1)}, current_kid="v1")

    @app.get("/graph")
    async def call_graph(request: Request) -> dict[str, str]:
        token = await get_msal_obo_token("graph", scopes=["User.Read"])(
            request, user_assertion="user-jwt", user_id="user-1"
        )
        return {"token": token}

    lifespan = create_lifespan(
        RedisLifespanResource("cache", "redis://localhost:6379/0"),
        MsalOboLifespanResource(
            name="graph",
            client_id="client-id",
            client_credential="secret",
            authority="https://login.microsoftonline.com/tenant-id",
            redis_client_name="cache",
            cipher=cipher,
            http_client_factory=_mock_http_session,
        ),
    )

    async with lifespan(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/graph")

    assert resp.status_code == 200
    assert resp.json() == {"token": "obo-user-jwt"}
