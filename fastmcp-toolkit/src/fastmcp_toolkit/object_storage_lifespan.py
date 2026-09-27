"""FastMCPサーバー向けObjectStorage接続lifespan統合。

Starlette向けの
``core_toolkit.object_storage_lifespan.ObjectStorageLifespanResource`` とは
ライフサイクルの形が異なる（``app.state`` に書き込む``asynccontextmanager``
ではなく、dictをyieldする非同期ジェネレータ）ため、独立したlifespan配線を
持つ。ABC・各バックエンドアダプタ自体はFastAPI/FastMCP非依存の
``core_toolkit.object_storage``をそのまま使う（実体はcore-toolkitに置き、
各toolkitはlifespan/DI配線のみを持つという層分けに従い、fastapi-toolkit
経由にはしない）。DIは``fastmcp``が内部で
使う``uncalled_for.Depends``に乗せる。名前付きで複数バックエンドを同時に
利用でき、``|``演算子で他のlifespanと合成できる。

利用にはバックエンドに応じたextra
（``fastmcp-toolkit[s3]``/``[gcs]``/``[azure]``）のインストールが必要。
``scheme="memory"``は追加依存なしで使える。
"""

from collections.abc import AsyncIterator, Callable, Mapping
from typing import Any, cast

from core_toolkit.object_storage.base import ObjectStorage
from core_toolkit.object_storage.factory import open_object_storage
from fastmcp import Context, FastMCP
from fastmcp.server.dependencies import CurrentContext
from fastmcp.server.lifespan import Lifespan, lifespan
from uncalled_for import Depends


def _lifespan_key(name: str) -> str:
    return f"object_storage:{name}"


def object_storage_lifespan(
    name: str, scheme: str, buckets: Mapping[str, str], **client_kwargs: Any
) -> Lifespan:
    """ObjectStorageバックエンドのライフサイクルを管理するFastMCP lifespanを生成する。

    起動時に``core_toolkit.object_storage.open_object_storage``でバックエンドを
    構築してlifespan_contextに格納し、終了時に後始末する。
    ``FastMCP(lifespan=object_storage_lifespan(name, scheme, buckets))``として使う。
    他のlifespanと``|``演算子で合成できる。名前ごとに``lifespan_context``の
    キーを分けるため、複数のバックエンドを同時に登録できる。同じ``name``で
    複数回``object_storage_lifespan(...)``を登録した場合、``lifespan_context``
    のキーが衝突し、後から登録した方で静かに上書きされる。同じ名前を
    重複登録しないこと。

    Args:
        name: このバックエンドを識別する名前。``CurrentObjectStorage``で
            同じ名前を指定して取得する。
        scheme: ``"s3"`` / ``"gs"`` / ``"azure"`` / ``"memory"``。
        buckets: 論理バケット名から物理バケット名（コンテナ名）へのマッピング。
        client_kwargs: バックエンドのSDKクライアントへそのまま渡す追加引数
            （詳細は``core_toolkit.object_storage.open_object_storage``の
            docstringを参照）。

    Returns:
        FastMCPの``lifespan=``にそのまま渡せる合成可能なLifespan。
    """

    @lifespan
    async def _object_storage_lifespan(
        server: FastMCP,
    ) -> AsyncIterator[dict[str, Any]]:
        async with open_object_storage(scheme, buckets, **client_kwargs) as storage:
            yield {_lifespan_key(name): storage}

    return _object_storage_lifespan


def _get_object_storage(name: str) -> Callable[[Context], ObjectStorage]:
    def get_storage(ctx: Context = CurrentContext()) -> ObjectStorage:
        storage = ctx.lifespan_context.get(_lifespan_key(name))
        if storage is None:
            raise RuntimeError(
                f"lifespan_context['{_lifespan_key(name)}'] is not set. "
                f"Did you forget to pass lifespan=object_storage_lifespan("
                f"name='{name}', ...) to FastMCP(...)?"
            )
        return cast(ObjectStorage, storage)

    get_storage.__name__ = f"get_object_storage_{name}"
    return get_storage


def CurrentObjectStorage(name: str) -> ObjectStorage:  # noqa: N802
    """``object_storage_lifespan(name, ...)``が生成したObjectStorageバックエンドを
    取得するDepends。

    ``fastmcp.server.dependencies.CurrentContext``と同じ命名パターンで、
    ツール関数の引数デフォルト値として使う。

    Example::

        @app.tool
        async def my_tool(
            storage: ObjectStorage = CurrentObjectStorage("main"),
        ) -> str:
            ...

    Args:
        name: ``object_storage_lifespan(name=...)``に登録した名前。

    Returns:
        ObjectStorage: ``Depends(...)``でラップされた、実行時に解決される
        ObjectStorageバックエンド。
    """
    return cast(ObjectStorage, Depends(_get_object_storage(name)))
