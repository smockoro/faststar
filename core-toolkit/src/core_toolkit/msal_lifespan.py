"""Starlette/ASGIアプリ向けMSAL（Microsoft Authentication Library）
lifespan管理。

名前付きで複数のConfidentialClientApplicationを同時に登録・取得できる。
MSALは同期API（requestsベース）のため、名前付きリソースごとに専用の
ThreadPoolExecutorを内蔵し、呼び出しをそこへ隔離する。

利用には ``core-toolkit[msal]`` extraのインストールが必要。
"""

import asyncio
from collections.abc import AsyncGenerator, Callable, Coroutine
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
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


class _TimeoutSession(requests.Session):
    """全リクエストにデフォルトタイムアウトを強制する``requests.Session``。

    ``requests``はセッション全体に効くタイムアウト設定を持たないため、
    ``request()``をオーバーライドして未指定時のみ``timeout``を補う。
    """

    def __init__(self, timeout: float) -> None:
        super().__init__()
        self._timeout = timeout

    def request(self, *args: Any, **kwargs: Any) -> requests.Response:
        kwargs.setdefault("timeout", self._timeout)
        return super().request(*args, **kwargs)


def default_msal_http_session(timeout: float = 10.0) -> requests.Session:
    """5xx/429にリトライし、全リクエストにタイムアウトを強制する
    requests.Sessionを生成する。

    ``http_client=``へ自前のSessionを渡すと、MSALの``verify``/``proxies``/
    ``timeout``引数は無視される（内部Sessionにしか効かない）ため、
    リトライ設定・タイムアウト設定込みでここに持たせる。トークン取得は
    ``/oauth2/v2.0/token``へのPOSTであり、``urllib3.Retry``の
    ``allowed_methods``はデフォルトでPOSTを含まないため、明示的に含める。

    Args:
        timeout: 各リクエストに強制するタイムアウト秒数（未指定時のみ適用）。
    """
    session = _TimeoutSession(timeout)
    retries = Retry(
        total=3,
        backoff_factor=0.1,
        status_forcelist=[429, 500, 501, 502, 503, 504],
        allowed_methods=frozenset({"GET", "POST"}),
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
    async def context(self, app: Starlette) -> AsyncGenerator[Any, Any]:
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


def get_msal_app_token(
    name: str, scopes: list[str]
) -> Callable[[Request], Coroutine[Any, Any, str]]:
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


@dataclass
class MsalOboConfig:
    client_id: str
    client_credential: str = field(repr=False)
    authority: str
    session: requests.Session
    http_cache: dict
    executor: ThreadPoolExecutor
    redis_client_name: str
    cipher: TokenCacheCipher
    cache_ttl: int


class MsalOboLifespanResource(LifespanResource):
    """OBO用。ConfidentialClientApplicationインスタンス自体はリクエストごと
    に生成するため、ここではSession/http_cache/executorのみをlifespanで
    共有する。

    ``token_cache``がインスタンス属性であり、OBOはリクエストごとに異なる
    ユーザーのキャッシュを使う。共有インスタンスの``token_cache``を都度
    差し替えると並行リクエスト間でキャッシュを取り違えるレースコンディション
    になるため、``ConfidentialClientApplication``自体はリクエストごとに
    ``get_msal_obo_token``側で生成する。

    Args:
        name: このコンフィグを識別する名前。``get_msal_obo_token``で
            同じ名前を指定して取得する。
        client_id: アプリ（クライアント）ID。
        client_credential: クライアントシークレット文字列。
        authority: 認証機関URL。
        redis_client_name: トークンキャッシュ永続化に使うRedisクライアント名。
        cipher: トークンキャッシュの暗号化実装。
        max_workers: MSAL呼び出し専用ThreadPoolExecutorのワーカー数。
        cache_ttl: Redisに保存するユーザー単位キャッシュのTTL（秒）。
        http_client_factory: ``requests.Session``を生成するファクトリ。
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
        cache_ttl: int = 3600,
        http_client_factory: Any = None,
    ) -> None:
        self._name = name
        self._client_id = client_id
        self._client_credential = client_credential
        self._authority = authority
        self._redis_client_name = redis_client_name
        self._cipher = cipher
        self._max_workers = max_workers
        self._cache_ttl = cache_ttl
        self._http_client_factory = http_client_factory or default_msal_http_session

    @asynccontextmanager
    async def context(self, app: Starlette) -> AsyncGenerator[Any, Any]:
        session = self._http_client_factory()
        executor = ThreadPoolExecutor(
            max_workers=self._max_workers, thread_name_prefix=f"msal-obo-{self._name}"
        )
        config = MsalOboConfig(
            client_id=self._client_id,
            client_credential=self._client_credential,
            authority=self._authority,
            session=session,
            http_cache={},
            executor=executor,
            redis_client_name=self._redis_client_name,
            cipher=self._cipher,
            cache_ttl=self._cache_ttl,
        )
        configs: dict[str, MsalOboConfig] = getattr(app.state, "msal_obo_configs", {})
        app.state.msal_obo_configs = {**configs, self._name: config}

        try:
            yield config
        finally:
            executor.shutdown(wait=True)
            session.close()


def get_msal_obo_token(
    name: str, scopes: list[str]
) -> Callable[[Request, str, str], Coroutine[Any, Any, str]]:
    """名前を指定してOBOのアクセストークンを取得するprovider関数を生成する。

    ``user_assertion``（呼び出し元ユーザーのアクセストークン文字列）と
    ``user_id``（トークンキャッシュのキーに使うユーザー識別子）は、
    アプリ側がJWTから取り出して渡す。

    Args:
        name: ``MsalOboLifespanResource(name=...)``に登録した名前。
        scopes: 要求するスコープ。

    Returns:
        ``request: Request``・``user_assertion: str``・``user_id: str``を
        引数に取る非同期provider関数。
    """

    async def get_token(request: Request, user_assertion: str, user_id: str) -> str:
        configs: dict[str, MsalOboConfig] = getattr(
            request.app.state, "msal_obo_configs", {}
        )
        if name not in configs:
            raise RuntimeError(
                f"msal_obo_configs['{name}'] is not set. "
                f"Did you forget to register "
                f"MsalOboLifespanResource(name='{name}', ...) "
                "in create_lifespan(...)?"
            )
        config = configs[name]

        redis = get_redis_client(config.redis_client_name)(request)
        cache_key = f"msal:obo_cache:{name}:{user_id}"

        cache = SerializableTokenCache()
        raw = await redis.get(cache_key)
        if raw:
            cache.deserialize(config.cipher.decrypt(raw).decode())

        def call_msal() -> dict[str, Any]:
            client = ConfidentialClientApplication(
                client_id=config.client_id,
                client_credential=config.client_credential,
                authority=config.authority,
                token_cache=cache,
                http_client=config.session,
                http_cache=config.http_cache,
            )
            return client.acquire_token_on_behalf_of(user_assertion, scopes=scopes)

        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(config.executor, call_msal)

        if cache.has_state_changed:
            payload = config.cipher.encrypt(cache.serialize().encode())
            await redis.set(cache_key, payload, ex=config.cache_ttl)

        _raise_for_result(result)
        return result["access_token"]

    get_token.__name__ = f"get_msal_obo_token_{name}"
    return get_token
