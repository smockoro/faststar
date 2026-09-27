"""スキーム(``scheme``)を指定してObjectStorageバックエンドを構築するファクトリ。

各SDKクライアントはこの関数の``async with``ブロックの中でのみ生存する
（aiobotocoreのクライアントは``async with``の外では使えないため）。
SDKは遅延importする。対応するextra（``core-toolkit[s3]``等）が未
インストールの環境でも、他のschemeは影響を受けない。
"""

from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from typing import Any

from core_toolkit.object_storage.base import ObjectStorage

_KNOWN_SCHEMES = ("s3", "gs", "azure", "memory")


@asynccontextmanager
async def open_object_storage(
    scheme: str, buckets: Mapping[str, str], **client_kwargs: Any
) -> AsyncIterator[ObjectStorage]:
    """指定した``scheme``のObjectStorageを構築し、生存期間を``async with``で管理する。

    Args:
        scheme: ``"s3"`` / ``"gs"`` / ``"azure"`` / ``"memory"``。
        buckets: 論理バケット名から物理バケット名（コンテナ名）へのマッピング。
        client_kwargs: バックエンドのSDKクライアントへそのまま渡す追加引数。
            ``"s3"``: ``aiobotocore.session.get_session().create_client("s3", ...)``へ。
            ``"gs"``: ``gcloud.aio.storage.Storage(...)``へ。
            ``"azure"``: ``account_url``（必須）・``credential``に加え、署名付き
            URL生成に使う``account_key``（省略可、``str``）を含められる。
            ``account_key``は``BlobServiceClient``には渡さずこの関数内で取り出す。
            ``"memory"``: 使用しない。

    Raises:
        ValueError: ``scheme``が未知の場合。
        TypeError: ``scheme="azure"``で``account_url``が指定されていない場合。
        ModuleNotFoundError: 対応するextraがインストールされていない場合。
    """
    if scheme == "s3":
        try:
            import aiobotocore.session
        except ModuleNotFoundError as error:
            raise ModuleNotFoundError(
                "scheme='s3' requires the 'core-toolkit[s3]' extra."
            ) from error
        from core_toolkit.object_storage.s3 import S3ObjectStorage

        session = aiobotocore.session.get_session()
        async with session.create_client("s3", **client_kwargs) as client:
            yield S3ObjectStorage(buckets, client)

    elif scheme == "gs":
        try:
            from gcloud.aio.storage import Storage
        except ModuleNotFoundError as error:
            raise ModuleNotFoundError(
                "scheme='gs' requires the 'core-toolkit[gcs]' extra."
            ) from error
        from core_toolkit.object_storage.gcs import GcsObjectStorage

        async with Storage(**client_kwargs) as client:
            yield GcsObjectStorage(buckets, client)

    elif scheme == "azure":
        try:
            from azure.storage.blob.aio import BlobServiceClient
        except ModuleNotFoundError as error:
            raise ModuleNotFoundError(
                "scheme='azure' requires the 'core-toolkit[azure]' extra."
            ) from error
        from core_toolkit.object_storage.azure import AzureObjectStorage

        remaining_kwargs = dict(client_kwargs)
        try:
            account_url = remaining_kwargs.pop("account_url")
        except KeyError:
            raise TypeError(
                "scheme='azure' requires the 'account_url' keyword argument "
                "(e.g. account_url='https://<account>.blob.core.windows.net')."
            ) from None
        credential = remaining_kwargs.pop("credential", None)
        account_key = remaining_kwargs.pop("account_key", None)
        async with BlobServiceClient(
            account_url, credential=credential, **remaining_kwargs
        ) as client:
            yield AzureObjectStorage(buckets, client, account_key=account_key)

    elif scheme == "memory":
        from core_toolkit.object_storage.memory import InMemoryObjectStorage

        yield InMemoryObjectStorage(buckets)

    else:
        raise ValueError(
            f"unknown scheme: {scheme!r}. Expected one of {_KNOWN_SCHEMES}."
        )
