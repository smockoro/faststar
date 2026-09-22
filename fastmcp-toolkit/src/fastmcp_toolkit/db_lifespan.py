"""FastMCPサーバー向けDB接続lifespan統合。

Starlette向けの ``core_toolkit.db_lifespan.DbLifespanResource`` とは
ライフサイクルの形が異なる（``app.state`` に書き込む ``asynccontextmanager``
ではなく、dictをyieldする非同期ジェネレータ）ため、ここでは独立した実装を
持つ。DIは ``fastmcp`` が内部で使う ``uncalled_for.Depends`` に乗せ、
``fastapi_toolkit`` の ``Depends`` チェーンと同じ書き味をツール関数側にも
提供する。名前付きで複数エンジンを同時に利用でき、``|`` 演算子で他のlifespan
と合成できる。

利用には ``fastmcp-toolkit[sqlite]``/``fastmcp-toolkit[postgres]``/
``fastmcp-toolkit[mysql]`` のいずれかのextraのインストールが必要。
"""

from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager
from typing import Any, cast

from fastmcp import Context, FastMCP
from fastmcp.server.dependencies import CurrentContext
from fastmcp.server.lifespan import Lifespan, lifespan
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine
from uncalled_for import Depends


def _lifespan_key(name: str) -> str:
    return f"db_engine:{name}"


def db_lifespan(name: str, url: str, **engine_kwargs: Any) -> Lifespan:
    """AsyncEngineのライフサイクルを管理するFastMCP lifespanを生成する。

    起動時に ``sqlalchemy.ext.asyncio`` のAsyncEngineを生成してlifespan_context
    に格納し、終了時にdisposeする。``FastMCP(lifespan=db_lifespan(name, url))``
    として使う。他のlifespanと ``|`` 演算子で合成できる。名前ごとに
    ``lifespan_context`` のキーを分けるため、複数のDBを同時に登録できる。

    Args:
        name: このエンジンを識別する名前。``CurrentDbEngine``/
            ``CurrentDbConnection`` で同じ名前を指定して取得する。
        url: 接続先のURL（例: ``sqlite+aiosqlite:///./app.db``）。
        engine_kwargs: ``create_async_engine`` にそのまま渡す追加引数。

    Returns:
        FastMCPの ``lifespan=`` にそのまま渡せる合成可能なLifespan。
    """

    @lifespan
    async def _db_lifespan(server: FastMCP) -> AsyncIterator[dict[str, Any]]:
        engine = create_async_engine(url, **engine_kwargs)
        try:
            yield {_lifespan_key(name): engine}
        finally:
            await engine.dispose()

    return _db_lifespan


def _get_db_engine(name: str) -> Callable[[Context], AsyncEngine]:
    def get_engine(ctx: Context = CurrentContext()) -> AsyncEngine:
        engine = ctx.lifespan_context.get(_lifespan_key(name))
        if engine is None:
            raise RuntimeError(
                f"lifespan_context['{_lifespan_key(name)}'] is not set. "
                f"Did you forget to pass lifespan=db_lifespan(name='{name}', ...) "
                "to FastMCP(...)?"
            )
        return cast(AsyncEngine, engine)

    get_engine.__name__ = f"get_db_engine_{name}"
    return get_engine


def _get_db_connection(
    name: str,
) -> Callable[[Context], AbstractAsyncContextManager[AsyncConnection]]:
    # uncalled_for.Dependsはfactoryの戻り値がAbstractAsyncContextManagerかどうか
    # で後始末の要否を判定する（非同期ジェネレータ関数であること自体は見ない）。
    # そのためengine.begin()（未enterのcontext manager）をそのまま返す。
    def get_connection(
        ctx: Context = CurrentContext(),
    ) -> AbstractAsyncContextManager[AsyncConnection]:
        engine = _get_db_engine(name)(ctx)
        return engine.begin()

    get_connection.__name__ = f"get_db_connection_{name}"
    return get_connection


def CurrentDbEngine(name: str) -> AsyncEngine:  # noqa: N802
    """``db_lifespan(name, ...)`` が生成したAsyncEngineを取得するDepends。

    ``fastmcp.server.dependencies.CurrentContext`` と同じ命名パターンで、
    ツール関数の引数デフォルト値として使う。

    Example::

        @app.tool
        async def my_tool(engine: AsyncEngine = CurrentDbEngine("main")) -> str:
            ...

    Args:
        name: ``db_lifespan(name=...)`` に登録した名前。

    Returns:
        AsyncEngine: ``Depends(...)`` でラップされた、実行時に解決されるEngine。
    """
    return cast(AsyncEngine, Depends(_get_db_engine(name)))


def CurrentDbConnection(name: str) -> AsyncConnection:  # noqa: N802
    """``db_lifespan(name, ...)`` のEngineから呼び出し単位のConnectionを取得する
    Depends。

    ツール呼び出し1回＝1トランザクションとして扱う。正常終了でcommit、
    例外でrollback、いずれの場合も必ずclose。

    Example::

        @app.tool
        async def my_tool(conn: AsyncConnection = CurrentDbConnection("main")) -> str:
            ...

    Args:
        name: ``db_lifespan(name=...)`` に登録した名前。

    Returns:
        AsyncConnection: ``Depends(...)`` でラップされた、実行時に解決される
        トランザクション付きConnection。
    """
    return cast(AsyncConnection, Depends(_get_db_connection(name)))
