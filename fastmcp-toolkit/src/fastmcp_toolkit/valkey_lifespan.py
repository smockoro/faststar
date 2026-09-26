"""FastMCPサーバー向けValkey接続lifespan統合。

Starlette向けの ``core_toolkit.valkey_lifespan.ValkeyLifespanResource`` とは
ライフサイクルの形が異なる（``app.state`` に書き込む ``asynccontextmanager``
ではなく、dictをyieldする非同期ジェネレータ）ため、独立した実装を持つ。
DIは ``fastmcp`` が内部で使う ``uncalled_for.Depends`` に乗せる。名前付きで
複数クライアントを同時に利用でき、``|`` 演算子で他のlifespanと合成できる。

利用には ``fastmcp-toolkit[valkey]`` extraのインストールが必要。
"""

from collections.abc import AsyncIterator, Callable
from typing import Any, cast

from fastmcp import Context, FastMCP
from fastmcp.server.dependencies import CurrentContext
from fastmcp.server.lifespan import Lifespan, lifespan
from glide import GlideClient, GlideClientConfiguration, NodeAddress
from uncalled_for import Depends


def _lifespan_key(name: str) -> str:
    return f"valkey_client:{name}"


def valkey_lifespan(
    name: str, host: str, port: int = 6379, **config_kwargs: Any
) -> Lifespan:
    """Valkeyクライアントのライフサイクルを管理するFastMCP lifespanを生成する。

    起動時に ``valkey-glide`` クライアントを生成してlifespan_contextに
    格納し、終了時にcloseする。``FastMCP(lifespan=valkey_lifespan(name, host))``
    として使う。他のlifespanと ``|`` 演算子で合成できる。名前ごとに
    ``lifespan_context`` のキーを分けるため、複数のValkeyクライアントを
    同時に登録できる。同じ``name``で複数回``valkey_lifespan(...)``を登録した場合、
    ``lifespan_context``のキーが衝突し、後から登録した方で静かに上書きされる。
    同じ名前を重複登録しないこと。

    Args:
        name: このクライアントを識別する名前。``CurrentValkeyClient`` で
            同じ名前を指定して取得する。
        host: 接続先ホスト名。
        port: 接続先ポート番号。
        config_kwargs: ``GlideClientConfiguration`` にそのまま渡す追加引数。

    Returns:
        FastMCPの ``lifespan=`` にそのまま渡せる合成可能なLifespan。
    """

    @lifespan
    async def _valkey_lifespan(server: FastMCP) -> AsyncIterator[dict[str, Any]]:
        config = GlideClientConfiguration(
            addresses=[NodeAddress(host, port)],
            **config_kwargs,
        )
        client = await GlideClient.create(config)
        try:
            yield {_lifespan_key(name): client}
        finally:
            await client.close()

    return _valkey_lifespan


def _get_valkey_client(name: str) -> Callable[[Context], GlideClient]:
    def get_client(ctx: Context = CurrentContext()) -> GlideClient:
        client = ctx.lifespan_context.get(_lifespan_key(name))
        if client is None:
            raise RuntimeError(
                f"lifespan_context['{_lifespan_key(name)}'] is not set. "
                f"Did you forget to pass lifespan=valkey_lifespan(name='{name}', ...) "
                "to FastMCP(...)?"
            )
        return cast(GlideClient, client)

    get_client.__name__ = f"get_valkey_client_{name}"
    return get_client


def CurrentValkeyClient(name: str) -> GlideClient:  # noqa: N802
    """``valkey_lifespan(name, ...)`` が生成したValkeyクライアントを取得するDepends。

    ``fastmcp.server.dependencies.CurrentContext`` と同じ命名パターンで、
    ツール関数の引数デフォルト値として使う。

    Example::

        @app.tool
        async def my_tool(valkey: GlideClient = CurrentValkeyClient("cache")) -> str:
            ...

    Args:
        name: ``valkey_lifespan(name=...)`` に登録した名前。

    Returns:
        GlideClient: ``Depends(...)`` でラップされた、実行時に解決される
        Valkeyクライアント。
    """
    return cast(GlideClient, Depends(_get_valkey_client(name)))
