"""Starlette/ASGIアプリ向けMSAL（Microsoft Authentication Library）
lifespan管理。

名前付きで複数のConfidentialClientApplicationを同時に登録・取得できる。
MSALは同期API（requestsベース）のため、名前付きリソースごとに専用の
ThreadPoolExecutorを内蔵し、呼び出しをそこへ隔離する。

利用には ``core-toolkit[msal]`` extraのインストールが必要。
"""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

import requests
from msal import ConfidentialClientApplication, SerializableTokenCache
from requests.adapters import HTTPAdapter, Retry
from starlette.applications import Starlette
from starlette.requests import Request

from core_toolkit.lifespan import LifespanResource
from core_toolkit.msal_errors import MsalClaimsChallengeError, MsalTokenError
from core_toolkit.redis_lifespan import get_redis_client
from core_toolkit.token_cache_cipher import TokenCacheCipher


def default_msal_http_session() -> requests.Session:
    """3xx/5xx/429にリトライするrequests.Sessionを生成する。

    ``http_client=``へ自前のSessionを渡すと、MSALの``verify``/``proxies``/
    ``timeout``引数は無視される（内部Sessionにしか効かない）ため、
    リトライ設定込みでここに持たせる。
    """
    session = requests.Session()
    retries = Retry(
        total=3,
        backoff_factor=0.1,
        status_forcelist=[429, 500, 501, 502, 503, 504],
    )
    session.mount("https://", HTTPAdapter(max_retries=retries))
    return session


def _raise_for_result(result: dict[str, Any]) -> None:
    if "access_token" in result:
        return
    if "claims" in result:
        raise MsalClaimsChallengeError(
            result.get("error"),
            result.get("error_description"),
            result["claims"],
            result.get("error_codes"),
        )
    raise MsalTokenError(
        result.get("error"), result.get("error_description"), result.get("error_codes")
    )


@dataclass
class MsalClientCredentialHandle:
    app: ConfidentialClientApplication
    cache: SerializableTokenCache
    executor: ThreadPoolExecutor
    cipher: TokenCacheCipher
    redis_client_name: str
    cache_key: str


class MsalClientCredentialLifespanResource(LifespanResource):
    """Client Credentials用。アプリ単位のConfidentialClientApplicationを
    lifespanで1つ持ち、Redisにアプリ単位のトークンキャッシュを永続化する。

    ``context()``内で名前付きRedisクライアントを取得するため、
    ``create_lifespan(...)``へ渡す際は対応する``RedisLifespanResource``を
    先に登録しておく必要がある。

    Args:
        name: このクライアントを識別する名前。``get_msal_app_token``で
            同じ名前を指定して取得する。
        client_id: アプリ（クライアント）ID。
        client_credential: クライアントシークレット文字列。
        authority: 認証機関URL（例: ``https://login.microsoftonline.com/<tenant>``）。
        redis_client_name: トークンキャッシュ永続化に使うRedisクライアント名
            （``RedisLifespanResource(name=...)``で登録済みのもの）。
        cipher: トークンキャッシュの暗号化実装。
        max_workers: MSAL呼び出し専用ThreadPoolExecutorのワーカー数。
        http_client_factory: ``requests.Session``を生成するファクトリ。
            省略時は``default_msal_http_session``を使う。
        app_kwargs: ``ConfidentialClientApplication``にそのまま渡す追加引数。
    """

    def __init__(
        self,
        name: str,
        client_id: str,
        client_credential: str,
        authority: str,
        redis_client_name: str,
        cipher: TokenCacheCipher,
        max_workers: int = 4,
        http_client_factory: Any = None,
        **app_kwargs: Any,
    ) -> None:
        self._name = name
        self._client_id = client_id
        self._client_credential = client_credential
        self._authority = authority
        self._redis_client_name = redis_client_name
        self._cipher = cipher
        self._max_workers = max_workers
        self._http_client_factory = http_client_factory or default_msal_http_session
        self._app_kwargs = app_kwargs

    def _cache_key(self) -> str:
        return f"msal:app_cache:{self._name}"

    @asynccontextmanager
    async def context(self, app: Starlette):
        redis = get_redis_client(self._redis_client_name)(
            Request(scope={"type": "http", "app": app})
        )
        session = self._http_client_factory()
        executor = ThreadPoolExecutor(
            max_workers=self._max_workers, thread_name_prefix=f"msal-{self._name}"
        )
        cache = SerializableTokenCache()
        raw = await redis.get(self._cache_key())
        if raw:
            cache.deserialize(self._cipher.decrypt(raw).decode())

        client = ConfidentialClientApplication(
            client_id=self._client_id,
            client_credential=self._client_credential,
            authority=self._authority,
            token_cache=cache,
            http_client=session,
            **self._app_kwargs,
        )

        handle = MsalClientCredentialHandle(
            app=client,
            cache=cache,
            executor=executor,
            cipher=self._cipher,
            redis_client_name=self._redis_client_name,
            cache_key=self._cache_key(),
        )
        handles: dict[str, MsalClientCredentialHandle] = getattr(
            app.state, "msal_client_credential_handles", {}
        )
        app.state.msal_client_credential_handles = {**handles, self._name: handle}

        try:
            yield client
        finally:
            executor.shutdown(wait=True)
            session.close()


def get_msal_app_token(name: str, scopes: list[str]):
    """名前を指定してClient Credentialsのアクセストークンを取得する
    provider関数を生成する。

    Args:
        name: ``MsalClientCredentialLifespanResource(name=...)``に登録した名前。
        scopes: 要求するスコープ（例: ``["api://xxx/.default"]``）。

    Returns:
        ``request: Request``を1引数に取る非同期provider関数。対応する
        リソースが登録されていない場合は``RuntimeError``、トークン取得に
        失敗した場合は``MsalTokenError``（Conditional Access等の場合は
        ``MsalClaimsChallengeError``）を送出する。
    """

    async def get_token(request: Request) -> str:
        handles: dict[str, MsalClientCredentialHandle] = getattr(
            request.app.state, "msal_client_credential_handles", {}
        )
        if name not in handles:
            raise RuntimeError(
                f"msal_client_credential_handles['{name}'] is not set. "
                f"Did you forget to register "
                f"MsalClientCredentialLifespanResource(name='{name}', ...) "
                "in create_lifespan(...)?"
            )
        handle = handles[name]

        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(
            handle.executor, lambda: handle.app.acquire_token_for_client(scopes=scopes)
        )

        if handle.cache.has_state_changed:
            payload = handle.cipher.encrypt(handle.cache.serialize().encode())
            redis = get_redis_client(handle.redis_client_name)(request)
            await redis.set(handle.cache_key, payload)

        _raise_for_result(result)
        return result["access_token"]

    get_token.__name__ = f"get_msal_app_token_{name}"
    return get_token
