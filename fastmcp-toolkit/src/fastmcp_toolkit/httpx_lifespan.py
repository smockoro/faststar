"""FastMCPサーバー向けhttpx AsyncClient lifespan統合。

Starlette向けの ``core_toolkit.httpx_lifespan.HttpxLifespanResource`` とは
ライフサイクルの形が異なる（``app.state`` に書き込む ``asynccontextmanager``
ではなく、dictをyieldする非同期ジェネレータ）ため、独立した実装を持つ。
DIは ``fastmcp`` が内部で使う ``uncalled_for.Depends`` に乗せる。名前付きで
複数クライアントを同時に利用でき、``|`` 演算子で他のlifespanと合成できる。

利用には ``fastmcp-toolkit[httpx]`` extraのインストールが必要。
"""

from collections.abc import AsyncIterator, Callable
from typing import Any, cast

import httpx
from fastmcp import Context, FastMCP
from fastmcp.server.dependencies import CurrentContext
from fastmcp.server.lifespan import Lifespan, lifespan
from uncalled_for import Depends


def _lifespan_key(name: str) -> str:
    return f"httpx_client:{name}"


def httpx_lifespan(name: str, **client_kwargs: Any) -> Lifespan:
    """httpx AsyncClientのライフサイクルを管理するFastMCP lifespanを生成する。

    起動時に ``httpx.AsyncClient`` を生成してlifespan_contextに格納し、
    終了時にacloseする。``FastMCP(lifespan=httpx_lifespan(name, ...))`` として
    使う。他のlifespanと ``|`` 演算子で合成できる。名前ごとに
    ``lifespan_context`` のキーを分けるため、複数のクライアントを同時に登録
    できる。同じ``name``で複数回登録した場合、``lifespan_context``のキーが
    衝突し、後から登録した方で静かに上書きされる。同じ名前を重複登録しない
    こと。

    Args:
        name: このクライアントを識別する名前。``CurrentHttpxClient`` で
            同じ名前を指定して取得する。
        client_kwargs: ``httpx.AsyncClient`` にそのまま渡す追加引数。

    Returns:
        FastMCPの ``lifespan=`` にそのまま渡せる合成可能なLifespan。
    """

    @lifespan
    async def _httpx_lifespan(server: FastMCP) -> AsyncIterator[dict[str, Any]]:
        client = httpx.AsyncClient(**client_kwargs)
        try:
            yield {_lifespan_key(name): client}
        finally:
            await client.aclose()

    return _httpx_lifespan


def _get_httpx_client(name: str) -> Callable[[Context], httpx.AsyncClient]:
    def get_client(ctx: Context = CurrentContext()) -> httpx.AsyncClient:
        client = ctx.lifespan_context.get(_lifespan_key(name))
        if client is None:
            raise RuntimeError(
                f"lifespan_context['{_lifespan_key(name)}'] is not set. "
                f"Did you forget to pass lifespan=httpx_lifespan(name='{name}', ...) "
                "to FastMCP(...)?"
            )
        return cast(httpx.AsyncClient, client)

    get_client.__name__ = f"get_httpx_client_{name}"
    return get_client


def CurrentHttpxClient(name: str) -> httpx.AsyncClient:
    """``httpx_lifespan(name, ...)`` が生成したAsyncClientを取得するDepends。

    ``fastmcp.server.dependencies.CurrentContext`` と同じ命名パターンで、
    ツール関数の引数デフォルト値として使う。

    Example::

        @app.tool
        async def my_tool(
            client: httpx.AsyncClient = CurrentHttpxClient("backend"),
        ) -> str:
            ...

    Args:
        name: ``httpx_lifespan(name=...)`` に登録した名前。

    Returns:
        httpx.AsyncClient: ``Depends(...)`` でラップされた、実行時に
        解決されるAsyncClient。
    """
    return cast(httpx.AsyncClient, Depends(_get_httpx_client(name)))
