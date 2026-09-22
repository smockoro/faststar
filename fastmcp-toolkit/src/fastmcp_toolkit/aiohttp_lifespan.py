"""FastMCPサーバー向けaiohttp ClientSession lifespan統合。

Starlette向けの ``core_toolkit.aiohttp_lifespan.AioHttpLifespanResource`` とは
ライフサイクルの形が異なる（``app.state`` に書き込む ``asynccontextmanager``
ではなく、dictをyieldする非同期ジェネレータ）ため、独立した実装を持つ。
DIは ``fastmcp`` が内部で使う ``uncalled_for.Depends`` に乗せる。名前付きで
複数クライアントを同時に利用でき、``|`` 演算子で他のlifespanと合成できる。

利用には ``fastmcp-toolkit[aiohttp]`` extraのインストールが必要。
"""

from collections.abc import AsyncIterator, Callable
from typing import Any, cast

import aiohttp
from fastmcp import Context, FastMCP
from fastmcp.server.dependencies import CurrentContext
from fastmcp.server.lifespan import Lifespan, lifespan
from uncalled_for import Depends


def _lifespan_key(name: str) -> str:
    return f"aiohttp_client:{name}"


def aiohttp_lifespan(name: str, **session_kwargs: Any) -> Lifespan:
    """aiohttp ClientSessionのライフサイクルを管理するFastMCP lifespanを生成する。

    起動時に ``aiohttp.ClientSession`` を生成してlifespan_contextに格納し、
    終了時にcloseする。``FastMCP(lifespan=aiohttp_lifespan(name, ...))`` として
    使う。他のlifespanと ``|`` 演算子で合成できる。名前ごとに
    ``lifespan_context`` のキーを分けるため、複数のセッションを同時に登録
    できる。同じ``name``で複数回登録した場合、``lifespan_context``のキーが
    衝突し、後から登録した方で静かに上書きされる。同じ名前を重複登録しない
    こと。

    Args:
        name: このセッションを識別する名前。``CurrentAiohttpClient`` で
            同じ名前を指定して取得する。
        session_kwargs: ``aiohttp.ClientSession`` にそのまま渡す追加引数。

    Returns:
        FastMCPの ``lifespan=`` にそのまま渡せる合成可能なLifespan。
    """

    @lifespan
    async def _aiohttp_lifespan(server: FastMCP) -> AsyncIterator[dict[str, Any]]:
        session = aiohttp.ClientSession(**session_kwargs)
        try:
            yield {_lifespan_key(name): session}
        finally:
            await session.close()

    return _aiohttp_lifespan


def _get_aiohttp_client(name: str) -> Callable[[Context], aiohttp.ClientSession]:
    def get_client(ctx: Context = CurrentContext()) -> aiohttp.ClientSession:
        client = ctx.lifespan_context.get(_lifespan_key(name))
        if client is None:
            raise RuntimeError(
                f"lifespan_context['{_lifespan_key(name)}'] is not set. "
                f"Did you forget to pass lifespan=aiohttp_lifespan(name='{name}', ...) "
                "to FastMCP(...)?"
            )
        return cast(aiohttp.ClientSession, client)

    get_client.__name__ = f"get_aiohttp_client_{name}"
    return get_client


def CurrentAiohttpClient(name: str) -> aiohttp.ClientSession:
    """``aiohttp_lifespan(name, ...)`` が生成したClientSessionを取得するDepends。

    ``fastmcp.server.dependencies.CurrentContext`` と同じ命名パターンで、
    ツール関数の引数デフォルト値として使う。

    Example::

        @app.tool
        async def my_tool(
            session: aiohttp.ClientSession = CurrentAiohttpClient("backend"),
        ) -> str:
            ...

    Args:
        name: ``aiohttp_lifespan(name=...)`` に登録した名前。

    Returns:
        aiohttp.ClientSession: ``Depends(...)`` でラップされた、実行時に
        解決されるClientSession。
    """
    return cast(aiohttp.ClientSession, Depends(_get_aiohttp_client(name)))
