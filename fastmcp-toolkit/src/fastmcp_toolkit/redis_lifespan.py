"""FastMCPサーバー向けRedis接続lifespan統合。

Starlette向けの ``core_toolkit.redis_lifespan.RedisLifespanResource`` とは
ライフサイクルの形が異なる（``app.state`` に書き込む ``asynccontextmanager``
ではなく、dictをyieldする非同期ジェネレータ）ため、独立した実装を持つ。
DIは ``fastmcp`` が内部で使う ``uncalled_for.Depends`` に乗せる。名前付きで
複数クライアントを同時に利用でき、``|`` 演算子で他のlifespanと合成できる。

利用には ``fastmcp-toolkit[redis]`` extraのインストールが必要。
"""

from collections.abc import AsyncIterator, Callable
from typing import Any, cast

from fastmcp import Context, FastMCP
from fastmcp.server.dependencies import CurrentContext
from fastmcp.server.lifespan import Lifespan, lifespan
from redis.asyncio import Redis
from uncalled_for import Depends


def _lifespan_key(name: str) -> str:
    return f"redis_client:{name}"


def redis_lifespan(name: str, url: str, **client_kwargs: Any) -> Lifespan:
    """Redisクライアントのライフサイクルを管理するFastMCP lifespanを生成する。

    起動時に ``redis.asyncio`` クライアントを生成してlifespan_contextに
    格納し、終了時にcloseする。``FastMCP(lifespan=redis_lifespan(name, url))``
    として使う。他のlifespanと ``|`` 演算子で合成できる。名前ごとに
    ``lifespan_context`` のキーを分けるため、複数のRedisクライアントを
    同時に登録できる。同じ``name``で複数回``redis_lifespan(...)``を登録した場合、
    ``lifespan_context``のキーが衝突し、後から登録した方で静かに上書きされる。
    同じ名前を重複登録しないこと。

    Args:
        name: このクライアントを識別する名前。``CurrentRedisClient`` で
            同じ名前を指定して取得する。
        url: 接続先のURL（例: ``redis://localhost:6379/0``）。
        client_kwargs: ``Redis.from_url`` にそのまま渡す追加引数。

    Returns:
        FastMCPの ``lifespan=`` にそのまま渡せる合成可能なLifespan。
    """

    @lifespan
    async def _redis_lifespan(server: FastMCP) -> AsyncIterator[dict[str, Any]]:
        client = Redis.from_url(url, **client_kwargs)
        try:
            yield {_lifespan_key(name): client}
        finally:
            await client.aclose()

    return _redis_lifespan


def _get_redis_client(name: str) -> Callable[[Context], Redis]:
    def get_client(ctx: Context = CurrentContext()) -> Redis:
        client = ctx.lifespan_context.get(_lifespan_key(name))
        if client is None:
            raise RuntimeError(
                f"lifespan_context['{_lifespan_key(name)}'] is not set. "
                f"Did you forget to pass lifespan=redis_lifespan(name='{name}', ...) "
                "to FastMCP(...)?"
            )
        return cast(Redis, client)

    get_client.__name__ = f"get_redis_client_{name}"
    return get_client


def CurrentRedisClient(name: str) -> Redis:
    """``redis_lifespan(name, ...)`` が生成したRedisクライアントを取得するDepends。

    ``fastmcp.server.dependencies.CurrentContext`` と同じ命名パターンで、
    ツール関数の引数デフォルト値として使う。

    Example::

        @app.tool
        async def my_tool(redis: Redis = CurrentRedisClient("cache")) -> str:
            ...

    Args:
        name: ``redis_lifespan(name=...)`` に登録した名前。

    Returns:
        Redis: ``Depends(...)`` でラップされた、実行時に解決されるRedis
        クライアント。
    """
    return cast(Redis, Depends(_get_redis_client(name)))
