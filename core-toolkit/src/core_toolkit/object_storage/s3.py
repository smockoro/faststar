"""aiobotocoreベースのS3 ObjectStorageアダプタ。

利用には ``core-toolkit[s3]`` extraのインストールが必要。
"""

from collections.abc import AsyncIterator, Mapping
from datetime import timedelta
from typing import Any, NoReturn

from botocore.exceptions import BotoCoreError, ClientError

from core_toolkit.object_storage.base import (
    BucketNotFoundError,
    MultipartUpload,
    ObjectInfo,
    ObjectNotFoundError,
    ObjectStorage,
    ObjectStorageError,
    PermissionDeniedError,
)

_BUCKET_NOT_FOUND_CODES = {"NoSuchBucket"}
_NOT_FOUND_CODES = {"404", "NoSuchKey", "NotFound"}
_PERMISSION_DENIED_CODES = {"403", "AccessDenied"}


def _raise_for_client_error(error: ClientError, *, bucket: str, key: str) -> NoReturn:
    """``ClientError``をObjectStorageの例外階層へ正規化して送出する。

    ``NoSuchBucket``は``BucketNotFoundError``にする。ただし``HeadObject``は
    ボディの無い404を返すため、バケット不在とキー不在を区別できず
    ``ObjectNotFoundError``になる。
    """
    code = error.response.get("Error", {}).get("Code", "")
    if code in _BUCKET_NOT_FOUND_CODES:
        raise BucketNotFoundError(
            f"physical bucket for logical bucket '{bucket}' does not exist"
        ) from error
    if code in _NOT_FOUND_CODES:
        raise ObjectNotFoundError(f"{bucket}/{key} not found") from error
    if code in _PERMISSION_DENIED_CODES:
        raise PermissionDeniedError(f"permission denied for {bucket}/{key}") from error
    raise ObjectStorageError(
        f"S3 operation failed for {bucket}/{key}: {error}"
    ) from error


def _raise_for_presign_error(
    error: ClientError | BotoCoreError, *, bucket: str, key: str
) -> NoReturn:
    """署名付きURL生成時の例外を正規化する。

    署名はローカル計算のため、主な失敗要因は認証情報の欠如
    （``NoCredentialsError``）やパラメータ検証エラー（``BotoCoreError``系）。
    """
    if isinstance(error, ClientError):
        _raise_for_client_error(error, bucket=bucket, key=key)
    raise ObjectStorageError(
        f"failed to generate S3 presigned URL for {bucket}/{key}: {error}"
    ) from error


class S3ObjectStorage(ObjectStorage):
    """aiobotocoreのS3クライアントをラップするObjectStorage実装。

    Args:
        buckets: 論理バケット名から物理バケット名へのマッピング。
        client: ``async with session.create_client("s3", ...)`` で得た
            エンター済みのクライアント。ライフサイクル管理は
            ``core_toolkit.object_storage.factory.open_object_storage`` が行う。
    """

    def __init__(self, buckets: Mapping[str, str], client: Any) -> None:
        super().__init__(buckets)
        self._client = client

    async def put(
        self, bucket, key, data, *, content_type=None, metadata=None
    ) -> ObjectInfo:
        physical = self._resolve_bucket(bucket)
        kwargs: dict[str, Any] = {"Bucket": physical, "Key": key, "Body": data}
        if content_type is not None:
            kwargs["ContentType"] = content_type
        if metadata:
            kwargs["Metadata"] = dict(metadata)
        try:
            resp = await self._client.put_object(**kwargs)
        except ClientError as error:
            _raise_for_client_error(error, bucket=bucket, key=key)
        return ObjectInfo(
            bucket=bucket,
            key=key,
            size=len(data),
            content_type=content_type,
            etag=resp.get("ETag"),
            last_modified=None,
            metadata=dict(metadata or {}),
        )

    async def get(self, bucket, key) -> bytes:
        physical = self._resolve_bucket(bucket)
        try:
            resp = await self._client.get_object(Bucket=physical, Key=key)
        except ClientError as error:
            _raise_for_client_error(error, bucket=bucket, key=key)
        async with resp["Body"] as stream:
            return await stream.read()

    async def get_stream(
        self, bucket, key, *, chunk_size=64 * 1024
    ) -> AsyncIterator[bytes]:
        physical = self._resolve_bucket(bucket)
        try:
            resp = await self._client.get_object(Bucket=physical, Key=key)
        except ClientError as error:
            _raise_for_client_error(error, bucket=bucket, key=key)
        async with resp["Body"] as stream:
            while chunk := await stream.read(chunk_size):
                yield chunk

    async def head(self, bucket, key) -> ObjectInfo:
        physical = self._resolve_bucket(bucket)
        try:
            resp = await self._client.head_object(Bucket=physical, Key=key)
        except ClientError as error:
            _raise_for_client_error(error, bucket=bucket, key=key)
        return ObjectInfo(
            bucket=bucket,
            key=key,
            size=resp["ContentLength"],
            content_type=resp.get("ContentType"),
            etag=resp.get("ETag"),
            last_modified=resp.get("LastModified"),
            metadata=resp.get("Metadata", {}),
        )

    async def delete(self, bucket, key) -> None:
        physical = self._resolve_bucket(bucket)
        # S3のdelete_objectは対象が存在しなくても成功する（冪等）。
        try:
            await self._client.delete_object(Bucket=physical, Key=key)
        except ClientError as error:
            _raise_for_client_error(error, bucket=bucket, key=key)

    async def list(self, bucket, prefix="") -> AsyncIterator[ObjectInfo]:
        physical = self._resolve_bucket(bucket)
        paginator = self._client.get_paginator("list_objects_v2")
        pages = aiter(paginator.paginate(Bucket=physical, Prefix=prefix))
        while True:
            # SDK例外だけを正規化の対象にし、呼び出し側（async forの本体）から
            # ジェネレータへ投げ込まれる例外を巻き込まないよう、yieldはtryの外に置く。
            try:
                page = await anext(pages)
            except StopAsyncIteration:
                return
            except ClientError as error:
                _raise_for_client_error(error, bucket=bucket, key=prefix)
            for item in page.get("Contents", []):
                yield ObjectInfo(
                    bucket=bucket,
                    key=item["Key"],
                    size=item["Size"],
                    content_type=None,
                    etag=item.get("ETag"),
                    last_modified=item.get("LastModified"),
                    metadata={},
                )

    async def presigned_download_url(self, bucket, key, *, expires: timedelta) -> str:
        physical = self._resolve_bucket(bucket)
        try:
            return await self._client.generate_presigned_url(
                "get_object",
                Params={"Bucket": physical, "Key": key},
                ExpiresIn=int(expires.total_seconds()),
            )
        except (ClientError, BotoCoreError) as error:
            _raise_for_presign_error(error, bucket=bucket, key=key)

    async def presigned_upload_url(
        self, bucket, key, *, expires: timedelta, content_type=None
    ) -> str:
        physical = self._resolve_bucket(bucket)
        params: dict[str, Any] = {"Bucket": physical, "Key": key}
        if content_type is not None:
            params["ContentType"] = content_type
        try:
            return await self._client.generate_presigned_url(
                "put_object",
                Params=params,
                ExpiresIn=int(expires.total_seconds()),
            )
        except (ClientError, BotoCoreError) as error:
            _raise_for_presign_error(error, bucket=bucket, key=key)

    async def begin_multipart(
        self, bucket, key, *, content_type=None, metadata=None
    ) -> MultipartUpload:
        physical = self._resolve_bucket(bucket)
        kwargs: dict[str, Any] = {"Bucket": physical, "Key": key}
        if content_type is not None:
            kwargs["ContentType"] = content_type
        if metadata:
            kwargs["Metadata"] = dict(metadata)
        try:
            resp = await self._client.create_multipart_upload(**kwargs)
        except ClientError as error:
            _raise_for_client_error(error, bucket=bucket, key=key)
        return _S3MultipartUpload(
            client=self._client,
            bucket=bucket,
            physical_bucket=physical,
            key=key,
            upload_id=resp["UploadId"],
        )


class _S3MultipartUpload(MultipartUpload):
    def __init__(
        self,
        *,
        client: Any,
        bucket: str,
        physical_bucket: str,
        key: str,
        upload_id: str,
    ) -> None:
        self._client = client
        self._bucket = bucket
        self._physical_bucket = physical_bucket
        self._key = key
        self._upload_id = upload_id
        self._parts: dict[int, str] = {}

    async def upload_part(self, part_number: int, data: bytes) -> None:
        try:
            resp = await self._client.upload_part(
                Bucket=self._physical_bucket,
                Key=self._key,
                UploadId=self._upload_id,
                PartNumber=part_number,
                Body=data,
            )
        except ClientError as error:
            _raise_for_client_error(error, bucket=self._bucket, key=self._key)
        self._parts[part_number] = resp["ETag"]

    async def complete(self) -> ObjectInfo:
        if not self._parts:
            raise ObjectStorageError(
                f"cannot complete multipart upload for {self._bucket}/{self._key}: "
                "no parts have been uploaded"
            )
        parts = [
            {"PartNumber": number, "ETag": etag}
            for number, etag in sorted(self._parts.items())
        ]
        try:
            await self._client.complete_multipart_upload(
                Bucket=self._physical_bucket,
                Key=self._key,
                UploadId=self._upload_id,
                MultipartUpload={"Parts": parts},
            )
            head = await self._client.head_object(
                Bucket=self._physical_bucket, Key=self._key
            )
        except ClientError as error:
            _raise_for_client_error(error, bucket=self._bucket, key=self._key)
        return ObjectInfo(
            bucket=self._bucket,
            key=self._key,
            size=head["ContentLength"],
            content_type=head.get("ContentType"),
            etag=head.get("ETag"),
            last_modified=head.get("LastModified"),
            metadata=head.get("Metadata", {}),
        )

    async def abort(self) -> None:
        try:
            await self._client.abort_multipart_upload(
                Bucket=self._physical_bucket,
                Key=self._key,
                UploadId=self._upload_id,
            )
        except ClientError as error:
            if error.response.get("Error", {}).get("Code") == "NoSuchUpload":
                return  # 既に完了・中止済みのセッションは後始末不要（冪等化）
            _raise_for_client_error(error, bucket=self._bucket, key=self._key)
