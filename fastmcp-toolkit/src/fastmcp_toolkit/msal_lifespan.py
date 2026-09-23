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
from collections.abc import AsyncIterator, Callable
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


def _get_msal_app_token(name: str, scopes: list[str]) -> Callable[[Context], Any]:
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
