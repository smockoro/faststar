"""azure-storage-blob(.aio)ベースのAzure Blob ObjectStorageアダプタ。

利用には ``core-toolkit[azure]`` extraのインストールが必要。
"""

import base64
import uuid
from collections.abc import AsyncIterator, Mapping
from datetime import UTC, datetime, timedelta
from typing import NoReturn

from azure.core.exceptions import HttpResponseError, ResourceNotFoundError
from azure.storage.blob import (
    BlobBlock,
    BlobSasPermissions,
    ContentSettings,
    generate_blob_sas,
)
from azure.storage.blob.aio import BlobServiceClient

from core_toolkit.object_storage.base import (
    MAX_PART_NUMBER,
    BucketNotFoundError,
    MultipartUpload,
    NotSupportedError,
    ObjectInfo,
    ObjectNotFoundError,
    ObjectStorage,
    ObjectStorageError,
    PermissionDeniedError,
)

_CONTAINER_NOT_FOUND_CODE = "ContainerNotFound"


def _is_container_not_found(error: HttpResponseError) -> bool:
    """エラーがコンテナ（物理バケット）不在によるものかを判定する。

    ``ResourceNotFoundError``はコンテナ不在・blob不在の両方で送出されるため、
    ``x-ms-error-code``由来の``error_code``で区別する。``error_code``は
    ``StorageErrorCode``（str系Enum）または素の``str``のことがある。
    """
    code = getattr(error, "error_code", None)
    return getattr(code, "value", code) == _CONTAINER_NOT_FOUND_CODE


def _raise_for_http_error(
    error: HttpResponseError, *, bucket: str, key: str
) -> NoReturn:
    """``HttpResponseError``をObjectStorageの例外階層へ正規化して送出する。"""
    if _is_container_not_found(error):
        raise BucketNotFoundError(
            f"physical container for logical bucket '{bucket}' does not exist"
        ) from error
    if isinstance(error, ResourceNotFoundError):
        raise ObjectNotFoundError(f"{bucket}/{key} not found") from error
    if getattr(error, "status_code", None) == 403:
        raise PermissionDeniedError(f"permission denied for {bucket}/{key}") from error
    raise ObjectStorageError(
        f"Azure Blob operation failed for {bucket}/{key}: {error}"
    ) from error


class AzureObjectStorage(ObjectStorage):
    """azure-storage-blobの``BlobServiceClient``をラップするObjectStorage実装。

    Args:
        buckets: 論理コンテナ名から物理コンテナ名へのマッピング。
        client: エンター済みの``BlobServiceClient``。
        account_key: 署名付きURL生成に使うアカウントキー。``None``の場合、
            ``presigned_download_url``/``presigned_upload_url``は
            ``NotSupportedError``を送出する。
    """

    def __init__(
        self,
        buckets: Mapping[str, str],
        client: BlobServiceClient,
        *,
        account_key: str | None = None,
    ) -> None:
        super().__init__(buckets)
        self._client = client
        self._account_key = account_key

    def _blob_client(self, bucket: str, key: str):
        physical = self._resolve_bucket(bucket)
        return self._client.get_container_client(physical).get_blob_client(key)

    async def put(
        self, bucket, key, data, *, content_type=None, metadata=None
    ) -> ObjectInfo:
        blob = self._blob_client(bucket, key)
        settings = ContentSettings(content_type=content_type) if content_type else None
        try:
            await blob.upload_blob(
                data,
                overwrite=True,
                metadata=dict(metadata or {}),
                content_settings=settings,
            )
        except HttpResponseError as error:
            _raise_for_http_error(error, bucket=bucket, key=key)
        return ObjectInfo(
            bucket=bucket,
            key=key,
            size=len(data),
            content_type=content_type,
            etag=None,
            last_modified=None,
            metadata=dict(metadata or {}),
        )

    async def get(self, bucket, key) -> bytes:
        blob = self._blob_client(bucket, key)
        try:
            downloader = await blob.download_blob()
            return await downloader.readall()
        except HttpResponseError as error:
            _raise_for_http_error(error, bucket=bucket, key=key)

    async def get_stream(
        self, bucket, key, *, chunk_size=64 * 1024
    ) -> AsyncIterator[bytes]:
        blob = self._blob_client(bucket, key)
        try:
            downloader = await blob.download_blob()
        except HttpResponseError as error:
            _raise_for_http_error(error, bucket=bucket, key=key)
        async for chunk in downloader.chunks():
            yield chunk

    async def head(self, bucket, key) -> ObjectInfo:
        blob = self._blob_client(bucket, key)
        try:
            props = await blob.get_blob_properties()
        except HttpResponseError as error:
            _raise_for_http_error(error, bucket=bucket, key=key)
        return ObjectInfo(
            bucket=bucket,
            key=key,
            size=props.size,
            content_type=props.content_settings.content_type
            if props.content_settings
            else None,
            etag=props.etag,
            last_modified=props.last_modified,
            metadata=dict(props.metadata or {}),
        )

    async def delete(self, bucket, key) -> None:
        blob = self._blob_client(bucket, key)
        try:
            await blob.delete_blob()
        except ResourceNotFoundError as error:
            if _is_container_not_found(error):
                _raise_for_http_error(error, bucket=bucket, key=key)
            # blobが存在しない場合は成功扱いにして冪等化する（コンテナ不在は
            # 設定ミスなので冪等化せずBucketNotFoundErrorにする）
            return
        except HttpResponseError as error:
            _raise_for_http_error(error, bucket=bucket, key=key)

    async def list(self, bucket, prefix="") -> AsyncIterator[ObjectInfo]:
        physical = self._resolve_bucket(bucket)
        container = self._client.get_container_client(physical)
        blobs = aiter(container.list_blobs(name_starts_with=prefix))
        while True:
            # SDK例外だけを正規化し、async for本体からジェネレータへ投げ込まれる
            # 例外を巻き込まないよう、yieldはtryの外に置く。
            try:
                props = await anext(blobs)
            except StopAsyncIteration:
                return
            except HttpResponseError as error:
                _raise_for_http_error(error, bucket=bucket, key=prefix)
            yield ObjectInfo(
                bucket=bucket,
                key=props.name,
                size=props.size,
                content_type=props.content_settings.content_type
                if props.content_settings
                else None,
                etag=props.etag,
                last_modified=props.last_modified,
                metadata=dict(props.metadata or {}),
            )

    async def presigned_download_url(self, bucket, key, *, expires: timedelta) -> str:
        return await self._generate_sas_url(
            bucket, key, expires=expires, permission=BlobSasPermissions(read=True)
        )

    async def presigned_upload_url(
        self, bucket, key, *, expires: timedelta, content_type=None
    ) -> str:
        """アップロード（PUT）用のSAS付きURLを生成する。

        Azureでは``content_type``は署名に反映されない（SASの仕組み上、S3のように
        アップロード時のContent-Typeを署名で強制できない）。アップロードする
        クライアント側で``x-ms-blob-content-type``ヘッダを付けること。
        また、PUTで``Put Blob``を行うには``x-ms-blob-type: BlockBlob``ヘッダが
        必要。

        Args:
            bucket: 論理コンテナ名。
            key: blob名。
            expires: URLの有効期間。
            content_type: Azureでは無視される（ABC互換のために受け取る）。

        Returns:
            SAS付きURL。

        Raises:
            NotSupportedError: ``account_key``が設定されていない場合。
        """
        return await self._generate_sas_url(
            bucket,
            key,
            expires=expires,
            permission=BlobSasPermissions(write=True, create=True),
        )

    async def _generate_sas_url(
        self,
        bucket: str,
        key: str,
        *,
        expires: timedelta,
        permission: BlobSasPermissions,
    ) -> str:
        if self._account_key is None:
            raise NotSupportedError(
                "presigned URLs require an account_key. "
                "Pass account_key=... when registering this backend."
            )
        physical = self._resolve_bucket(bucket)
        blob = self._blob_client(bucket, key)
        sas = generate_blob_sas(
            account_name=self._client.account_name,
            container_name=physical,
            blob_name=key,
            account_key=self._account_key,
            permission=permission,
            expiry=datetime.now(UTC) + expires,
        )
        return f"{blob.url}?{sas}"

    async def begin_multipart(
        self, bucket, key, *, content_type=None, metadata=None
    ) -> MultipartUpload:
        blob = self._blob_client(bucket, key)
        return _AzureMultipartUpload(
            blob=blob,
            bucket=bucket,
            key=key,
            content_type=content_type,
            metadata=dict(metadata or {}),
        )


class _AzureMultipartUpload(MultipartUpload):
    """Azureの``stage_block``/``commit_block_list``をラップする。

    未コミットのブロックはblob単位でサーバー側に共有されるため、ブロックIDに
    セッションごとの``session_id``を含め、同じキーへの並行セッション間で
    ブロックが混ざらないようにする（同一blob内でブロックIDの長さを揃える
    必要があるため、``uuid4().hex``の32文字＋8桁のパート番号で固定長にする）。

    ただしAzureは``Put Block List``（``complete()``）や``Put Blob``（``put``）の
    成功時に、そのblobのリストに含まれない未コミットブロックを破棄する。
    そのため同じキーへの並行セッションでは、後から``complete()``した側が
    ``ObjectStorageError``で失敗しうる（データが混ざることは無い）。

    未コミットのブロックは``abort()``を呼ばなくても7日で自動破棄され、また
    個別に削除するAPIも無いため、``abort()``は追加の後始末を行わない。
    """

    def __init__(
        self,
        *,
        blob,
        bucket: str,
        key: str,
        content_type: str | None,
        metadata: dict[str, str],
    ) -> None:
        self._blob = blob
        self._bucket = bucket
        self._key = key
        self._content_type = content_type
        self._metadata = metadata
        self._session_id = uuid.uuid4().hex
        self._block_ids: dict[int, str] = {}

    async def upload_part(self, part_number: int, data: bytes) -> None:
        if not (1 <= part_number <= MAX_PART_NUMBER):
            raise ObjectStorageError(
                f"part_number must be between 1 and {MAX_PART_NUMBER}, "
                f"got {part_number}"
            )
        block_id = base64.b64encode(
            f"{self._session_id}-{part_number:08d}".encode()
        ).decode()
        try:
            await self._blob.stage_block(block_id, data)
        except HttpResponseError as error:
            _raise_for_http_error(error, bucket=self._bucket, key=self._key)
        self._block_ids[part_number] = block_id

    async def complete(self) -> ObjectInfo:
        if not self._block_ids:
            raise ObjectStorageError(
                f"cannot complete multipart upload for {self._bucket}/{self._key}: "
                "no parts have been uploaded"
            )
        block_list = [
            BlobBlock(block_id=self._block_ids[n]) for n in sorted(self._block_ids)
        ]
        settings = (
            ContentSettings(content_type=self._content_type)
            if self._content_type
            else None
        )
        try:
            await self._blob.commit_block_list(
                block_list, content_settings=settings, metadata=self._metadata
            )
            props = await self._blob.get_blob_properties()
        except HttpResponseError as error:
            _raise_for_http_error(error, bucket=self._bucket, key=self._key)
        return ObjectInfo(
            bucket=self._bucket,
            key=self._key,
            size=props.size,
            content_type=props.content_settings.content_type
            if props.content_settings
            else None,
            etag=props.etag,
            last_modified=props.last_modified,
            metadata=dict(props.metadata or {}),
        )

    async def abort(self) -> None:
        self._block_ids.clear()
