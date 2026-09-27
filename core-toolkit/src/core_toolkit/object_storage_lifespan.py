"""Starlette/ASGIアプリ向けObjectStorage接続lifespan管理。

名前付きで複数のObjectStorageバックエンド（S3/GCS/Azure Blob/インメモリ）を
同時に登録・取得できる。バケットは論理名で呼び出しごとに指定する
（``core_toolkit.object_storage.ObjectStorage``を参照）。

利用にはバックエンドに応じたextra（``core-toolkit[s3]``/``[gcs]``/``[azure]``）
のインストールが必要。``scheme="memory"``は追加依存なしで使える。
"""

from collections.abc import AsyncGenerator, Callable, Mapping
from contextlib import asynccontextmanager
from typing import Any

from starlette.applications import Starlette
from starlette.requests import Request

from core_toolkit.lifespan import LifespanResource
from core_toolkit.object_storage.base import ObjectStorage
from core_toolkit.object_storage.factory import open_object_storage


class ObjectStorageLifespanResource(LifespanResource):
    """名前付きでObjectStorageバックエンドを登録するリソース。同じappに複数登録できる。

    Args:
        name: このバックエンドを識別する名前（例: ``"main"``）。
            ``get_object_storage`` で同じ名前を指定して取得する。
        scheme: ``"s3"`` / ``"gs"`` / ``"azure"`` / ``"memory"``。
        buckets: 論理バケット名から物理バケット名（コンテナ名）へのマッピング。
        client_kwargs: バックエンドのSDKクライアントへそのまま渡す追加引数
            （詳細は``core_toolkit.object_storage.open_object_storage``の
            docstringを参照）。
    """

    def __init__(
        self, name: str, scheme: str, buckets: Mapping[str, str], **client_kwargs: Any
    ) -> None:
        self._name = name
        self._scheme = scheme
        self._buckets = buckets
        self._client_kwargs = client_kwargs

    @asynccontextmanager
    async def context(self, app: Starlette) -> AsyncGenerator[Any, Any]:
        async with open_object_storage(
            self._scheme, self._buckets, **self._client_kwargs
        ) as storage:
            stores: dict[str, ObjectStorage] = getattr(app.state, "object_storages", {})
            app.state.object_storages = {**stores, self._name: storage}
            yield storage


def get_object_storage(name: str) -> Callable[[Request], ObjectStorage]:
    """名前を指定してObjectStorageバックエンドを取得するprovider関数を生成する。

    Args:
        name: ``ObjectStorageLifespanResource(name=...)`` に登録した名前。

    Returns:
        ``request: Request`` を1引数に取るprovider関数。対応する
        ``ObjectStorageLifespanResource`` が登録されていない場合は
        ``RuntimeError`` を送出する。
    """

    def get_storage(request: Request) -> ObjectStorage:
        stores: dict[str, ObjectStorage] = getattr(
            request.app.state, "object_storages", {}
        )
        if name not in stores:
            raise RuntimeError(
                f"object_storages['{name}'] is not set. "
                f"Did you forget to register "
                f"ObjectStorageLifespanResource(name='{name}', ...) "
                "in create_lifespan(...)?"
            )
        return stores[name]

    get_storage.__name__ = f"get_object_storage_{name}"
    return get_storage
