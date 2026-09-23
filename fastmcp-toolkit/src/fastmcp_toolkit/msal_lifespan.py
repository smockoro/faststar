"""FastMCPサーバー向けMSAL lifespan統合（Client Credentials）。

Starlette向けの``core_toolkit.msal_lifespan.MsalClientCredentialLifespanResource``
とはライフサイクルの形が異なる（``app.state``に書き込む``asynccontextmanager``
ではなく、dictをyieldする非同期ジェネレータ）ため、独立した実装を持つ。DIは
``fastmcp``が内部で使う``uncalled_for.Depends``に乗せる。

FastMCPの``ComposedLifespan``は合成する各lifespan関数を``server``のみを
引数に互いに独立して実行し、起動時に他のlifespanの結果へアクセスする手段を
持たない（``fastmcp.server.lifespan``参照）。そのため、Redisからのトークン
キャッシュロードは起動時ではなく、``CurrentContext()``が使える初回のツール
呼び出し時に遅延実行する（並行して初回呼び出しが複数走った場合に二重ロード
が起きうるが、``deserialize``は状態を冪等に置き換えるだけなので実害はない）。

利用には ``fastmcp-toolkit[msal]`` extraのインストールが必要。
"""

import asyncio
from collections.abc import AsyncIterator, Callable, Coroutine
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, cast

from core_toolkit.msal_errors import MsalClaimsChallengeError, MsalTokenError
from core_toolkit.msal_lifespan import default_msal_http_session
from core_toolkit.token_cache_cipher import TokenCacheCipher
from fastmcp import Context, FastMCP
from fastmcp.server.dependencies import CurrentContext
from fastmcp.server.lifespan import Lifespan, lifespan
from msal import ConfidentialClientApplication, SerializableTokenCache
from uncalled_for import Depends

from fastmcp_toolkit.redis_lifespan import _get_redis_client


def _lifespan_key(name: str) -> str:
    return f"msal_client_credential:{name}"


def _cache_key(name: str) -> str:
    return f"msal:app_cache:{name}"


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
    cache_loaded: bool = field(default=False)


def msal_client_credential_lifespan(
    name: str,
    client_id: str,
    client_credential: str,
    authority: str,
    redis_client_name: str,
    cipher: TokenCacheCipher,
    max_workers: int = 4,
    http_client_factory: Any = None,
    **app_kwargs: Any,
) -> Lifespan:
    """Client Credentials用のConfidentialClientApplicationライフサイクルを
    管理するFastMCP lifespanを生成する。

    起動時は空の``SerializableTokenCache``でConfidentialClientApplicationを
    生成するだけで、Redisからのロードは行わない（モジュールdocstring参照）。
    ``redis_lifespan(name=redis_client_name, ...)``を同じ
    ``FastMCP(lifespan=...)``に``|``で合成しておく必要がある。

    Args:
        name: このクライアントを識別する名前。``CurrentMsalAppToken``で
            同じ名前を指定して取得する。
        client_id: アプリ（クライアント）ID。
        client_credential: クライアントシークレット文字列。
        authority: 認証機関URL。
        redis_client_name: トークンキャッシュ永続化に使うRedisクライアント名
            （``redis_lifespan(name=...)``で登録済みのもの）。
        cipher: トークンキャッシュの暗号化実装。
        max_workers: MSAL呼び出し専用ThreadPoolExecutorのワーカー数。
        http_client_factory: ``requests.Session``を生成するファクトリ。
        app_kwargs: ``ConfidentialClientApplication``にそのまま渡す追加引数。

    Returns:
        FastMCPの``lifespan=``にそのまま渡せる合成可能なLifespan。
    """
    factory = http_client_factory or default_msal_http_session

    @lifespan
    async def _msal_client_credential_lifespan(
        server: FastMCP,
    ) -> AsyncIterator[dict[str, Any]]:
        session = factory()
        executor = ThreadPoolExecutor(
            max_workers=max_workers, thread_name_prefix=f"msal-{name}"
        )
        cache = SerializableTokenCache()

        client = ConfidentialClientApplication(
            client_id=client_id,
            client_credential=client_credential,
            authority=authority,
            token_cache=cache,
            http_client=session,
            **app_kwargs,
        )

        handle = MsalClientCredentialHandle(
            app=client,
            cache=cache,
            executor=executor,
            cipher=cipher,
            redis_client_name=redis_client_name,
            cache_key=_cache_key(name),
        )
        try:
            yield {_lifespan_key(name): handle}
        finally:
            executor.shutdown(wait=True)
            session.close()

    return _msal_client_credential_lifespan


def _get_msal_app_token(
    name: str, scopes: list[str]
) -> Callable[[Context], Coroutine[Any, Any, str]]:
    async def get_token(ctx: Context = CurrentContext()) -> str:
        handle = ctx.lifespan_context.get(_lifespan_key(name))
        if handle is None:
            raise RuntimeError(
                f"lifespan_context['{_lifespan_key(name)}'] is not set. "
                f"Did you forget to pass "
                f"lifespan=msal_client_credential_lifespan(name='{name}', ...) "
                "to FastMCP(...)?"
            )
        handle = cast(MsalClientCredentialHandle, handle)

        if not handle.cache_loaded:
            redis = _get_redis_client(handle.redis_client_name)(ctx)
            raw = await redis.get(handle.cache_key)
            if raw:
                handle.cache.deserialize(handle.cipher.decrypt(raw).decode())
            handle.cache_loaded = True

        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(
            handle.executor, lambda: handle.app.acquire_token_for_client(scopes=scopes)
        )

        if handle.cache.has_state_changed:
            payload = handle.cipher.encrypt(handle.cache.serialize().encode())
            redis = _get_redis_client(handle.redis_client_name)(ctx)
            await redis.set(handle.cache_key, payload)

        _raise_for_result(result)
        return result["access_token"]

    get_token.__name__ = f"get_msal_app_token_{name}"
    return get_token


def CurrentMsalAppToken(name: str, scopes: list[str]) -> str:  # noqa: N802
    """``msal_client_credential_lifespan(name, ...)``が生成したアプリ単位の
    アクセストークンを取得するDepends。

    Example::

        @app.tool
        async def call_downstream(
            token: str = CurrentMsalAppToken("downstream-api", scopes=["api://xxx/.default"]),
        ) -> str:
            ...

    Args:
        name: ``msal_client_credential_lifespan(name=...)``に登録した名前。
        scopes: 要求するスコープ。

    Returns:
        str: ``Depends(...)``でラップされた、実行時に解決されるアクセストークン。
    """
    return cast(str, Depends(_get_msal_app_token(name, scopes)))


@dataclass
class MsalOboConfig:
    client_id: str
    client_credential: str = field(repr=False)
    authority: str
    session: Any
    http_cache: dict
    executor: ThreadPoolExecutor
    redis_client_name: str
    cipher: TokenCacheCipher
    cache_ttl: int


def _obo_lifespan_key(name: str) -> str:
    return f"msal_obo_config:{name}"


def msal_obo_lifespan(
    name: str,
    client_id: str,
    client_credential: str,
    authority: str,
    redis_client_name: str,
    cipher: TokenCacheCipher,
    max_workers: int = 4,
    cache_ttl: int = 3600,
    http_client_factory: Any = None,
) -> Lifespan:
    """OBO用のSession/http_cache/executorのライフサイクルを管理するFastMCP
    lifespanを生成する。

    ``ConfidentialClientApplication``インスタンス自体はリクエストごとに
    ``acquire_msal_obo_token``側で生成するため、ここではSession/http_cache/
    executorのみを共有する（``token_cache``がユーザーごとに異なるため。
    詳細はspecの「意思決定サマリー」参照）。``redis_lifespan(name=
    redis_client_name, ...)``を同じ``FastMCP(lifespan=...)``に``|``で
    合成しておく必要がある。

    Args:
        name: このコンフィグを識別する名前。``acquire_msal_obo_token``で
            同じ名前を指定して取得する。
        client_id: アプリ（クライアント）ID。
        client_credential: クライアントシークレット文字列。
        authority: 認証機関URL。
        redis_client_name: トークンキャッシュ永続化に使うRedisクライアント名。
        cipher: トークンキャッシュの暗号化実装。
        max_workers: MSAL呼び出し専用ThreadPoolExecutorのワーカー数。
        cache_ttl: Redisに保存するユーザー単位キャッシュのTTL（秒）。
        http_client_factory: ``requests.Session``を生成するファクトリ。

    Returns:
        FastMCPの``lifespan=``にそのまま渡せる合成可能なLifespan。
    """
    factory = http_client_factory or default_msal_http_session

    @lifespan
    async def _msal_obo_lifespan(server: FastMCP) -> AsyncIterator[dict[str, Any]]:
        session = factory()
        executor = ThreadPoolExecutor(
            max_workers=max_workers, thread_name_prefix=f"msal-obo-{name}"
        )
        config = MsalOboConfig(
            client_id=client_id,
            client_credential=client_credential,
            authority=authority,
            session=session,
            http_cache={},
            executor=executor,
            redis_client_name=redis_client_name,
            cipher=cipher,
            cache_ttl=cache_ttl,
        )
        try:
            yield {_obo_lifespan_key(name): config}
        finally:
            executor.shutdown(wait=True)
            session.close()

    return _msal_obo_lifespan


async def acquire_msal_obo_token(
    ctx: Context, name: str, scopes: list[str], user_assertion: str, user_id: str
) -> str:
    """OBOでアクセストークンを取得する。ツール関数内から明示的にawaitする。

    ``user_assertion``（呼び出し元ユーザーのアクセストークン文字列）と
    ``user_id``（トークンキャッシュのキーに使うユーザー識別子）は、アプリ側が
    JWTから取り出して渡す。``user_assertion``/``user_id``という呼び出しごとに
    変わる動的な値を必要とするため、``Depends``の静的解決パターンには乗せず、
    通常の非同期関数として提供する。

    Example::

        @app.tool
        async def call_downstream(
            ctx: Context, user_assertion: str, user_id: str
        ) -> str:
            token = await acquire_msal_obo_token(
                ctx, "graph", scopes=["User.Read"],
                user_assertion=user_assertion, user_id=user_id,
            )
            ...

    Args:
        ctx: ツール関数が受け取った``Context``。
        name: ``msal_obo_lifespan(name=...)``に登録した名前。
        scopes: 要求するスコープ。
        user_assertion: 呼び出し元ユーザーのアクセストークン文字列。
        user_id: トークンキャッシュのキーに使うユーザー識別子。

    Returns:
        str: 取得したアクセストークン。
    """
    config = ctx.lifespan_context.get(_obo_lifespan_key(name))
    if config is None:
        raise RuntimeError(
            f"lifespan_context['{_obo_lifespan_key(name)}'] is not set. "
            f"Did you forget to pass lifespan=msal_obo_lifespan(name='{name}', ...) "
            "to FastMCP(...)?"
        )
    config = cast(MsalOboConfig, config)

    redis = _get_redis_client(config.redis_client_name)(ctx)
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
