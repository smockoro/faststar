"""gcloud-aio-storageベースのGCS ObjectStorageアダプタ。

利用には ``core-toolkit[gcs]`` extraのインストールが必要。
"""

from collections.abc import AsyncIterator, Mapping
from datetime import datetime, timedelta
from typing import Any

import aiohttp
from gcloud.aio.storage import Bucket, Storage

from core_toolkit.object_storage.base import (
    MultipartUpload,
    ObjectInfo,
    ObjectNotFoundError,
    ObjectStorage,
    ObjectStorageError,
    PermissionDeniedError,
)

_MULTIPART_TMP_PREFIX = ".multipart-tmp/"
_COMPOSE_BATCH_SIZE = 32


def _raise_for_response_error(
    error: aiohttp.ClientResponseError, *, bucket: str, key: str
) -> None:
    if error.status == 404:
        raise ObjectNotFoundError(f"{bucket}/{key} not found") from error
    if error.status == 403:
        raise PermissionDeniedError(f"permission denied for {bucket}/{key}") from error
    raise ObjectStorageError(
        f"GCS operation failed for {bucket}/{key}: {error}"
    ) from error


def _parse_gcs_object(bucket: str, resource: dict[str, Any]) -> ObjectInfo:
    updated = resource.get("updated")
    return ObjectInfo(
        bucket=bucket,
        key=resource["name"],
        size=int(resource.get("size", 0)),
        content_type=resource.get("contentType"),
        etag=resource.get("etag"),
        last_modified=datetime.fromisoformat(updated.replace("Z", "+00:00"))
        if updated
        else None,
        metadata=dict(resource.get("metadata") or {}),
    )


class GcsObjectStorage(ObjectStorage):
    """gcloud-aio-storageの``Storage``クライアントをラップするObjectStorage実装。

    Args:
        buckets: 論理バケット名から物理バケット名へのマッピング。
        client: ``async with Storage(...)`` で得たエンター済みのクライアント。
    """

    def __init__(self, buckets: Mapping[str, str], client: Storage) -> None:
        super().__init__(buckets)
        self._client = client

    async def put(
        self, bucket, key, data, *, content_type=None, metadata=None
    ) -> ObjectInfo:
        physical = self._resolve_bucket(bucket)
        try:
            resource = await self._client.upload(
                physical,
                key,
                data,
                content_type=content_type,
                metadata=dict(metadata or {}),
            )
        except aiohttp.ClientResponseError as error:
            _raise_for_response_error(error, bucket=bucket, key=key)
            raise
        return _parse_gcs_object(bucket, resource)

    async def get(self, bucket, key) -> bytes:
        physical = self._resolve_bucket(bucket)
        try:
            return await self._client.download(physical, key)
        except aiohttp.ClientResponseError as error:
            _raise_for_response_error(error, bucket=bucket, key=key)
            raise

    async def get_stream(
        self, bucket, key, *, chunk_size=64 * 1024
    ) -> AsyncIterator[bytes]:
        physical = self._resolve_bucket(bucket)
        try:
            stream = await self._client.download_stream(physical, key)
        except aiohttp.ClientResponseError as error:
            _raise_for_response_error(error, bucket=bucket, key=key)
            raise
        async with stream:
            while chunk := await stream.read(chunk_size):
                yield chunk

    async def head(self, bucket, key) -> ObjectInfo:
        physical = self._resolve_bucket(bucket)
        try:
            resource = await self._client.download_metadata(physical, key)
        except aiohttp.ClientResponseError as error:
            _raise_for_response_error(error, bucket=bucket, key=key)
            raise
        return _parse_gcs_object(bucket, resource)

    async def delete(self, bucket, key) -> None:
        physical = self._resolve_bucket(bucket)
        try:
            await self._client.delete(physical, key)
        except aiohttp.ClientResponseError as error:
            if error.status == 404:
                return  # GCSは404を返すため、存在しない場合は成功扱いにして冪等化する
            _raise_for_response_error(error, bucket=bucket, key=key)

    async def list(self, bucket, prefix="") -> AsyncIterator[ObjectInfo]:
        physical = self._resolve_bucket(bucket)
        page_token: str | None = None
        while True:
            params: dict[str, str] = {"prefix": prefix}
            if page_token:
                params["pageToken"] = page_token
            resp = await self._client.list_objects(physical, params=params)
            for resource in resp.get("items", []):
                yield _parse_gcs_object(bucket, resource)
            page_token = resp.get("nextPageToken")
            if not page_token:
                break

    async def presigned_download_url(self, bucket, key, *, expires: timedelta) -> str:
        physical = self._resolve_bucket(bucket)
        blob = Bucket(self._client, physical).new_blob(key)
        return await blob.get_signed_url(
            int(expires.total_seconds()), http_method="GET"
        )

    async def presigned_upload_url(
        self, bucket, key, *, expires: timedelta, content_type=None
    ) -> str:
        physical = self._resolve_bucket(bucket)
        blob = Bucket(self._client, physical).new_blob(key)
        headers = {"content-type": content_type} if content_type else None
        return await blob.get_signed_url(
            int(expires.total_seconds()),
            http_method="PUT",
            headers=headers,
        )

    async def begin_multipart(
        self, bucket, key, *, content_type=None, metadata=None
    ) -> MultipartUpload:
        physical = self._resolve_bucket(bucket)
        return _GcsMultipartUpload(
            client=self._client,
            bucket=bucket,
            physical_bucket=physical,
            key=key,
            content_type=content_type,
            metadata=dict(metadata or {}),
        )


class _GcsMultipartUpload(MultipartUpload):
    """GCSには真のマルチパートAPIが無いため、パートを一時オブジェクトとして
    アップロードし、``complete()``で``compose``して結合する。``compose``は
    1回につき最大32個までしか結合できないため、超える場合は段階的に結合する。
    """

    def __init__(
        self,
        *,
        client: Storage,
        bucket: str,
        physical_bucket: str,
        key: str,
        content_type: str | None,
        metadata: dict[str, str],
    ) -> None:
        self._client = client
        self._bucket = bucket
        self._physical_bucket = physical_bucket
        self._key = key
        self._content_type = content_type
        self._metadata = metadata
        self._tmp_names: dict[int, str] = {}

    def _tmp_name(self, part_number: int) -> str:
        return f"{_MULTIPART_TMP_PREFIX}{self._key}.part{part_number}"

    async def upload_part(self, part_number: int, data: bytes) -> None:
        tmp_name = self._tmp_name(part_number)
        await self._client.upload(self._physical_bucket, tmp_name, data)
        self._tmp_names[part_number] = tmp_name

    async def complete(self) -> ObjectInfo:
        ordered = [self._tmp_names[n] for n in sorted(self._tmp_names)]
        try:
            current = ordered[0]
            remaining = ordered[1:]
            round_index = 0
            while remaining:
                batch = remaining[: _COMPOSE_BATCH_SIZE - 1]
                remaining = remaining[_COMPOSE_BATCH_SIZE - 1 :]
                staging_name = (
                    f"{_MULTIPART_TMP_PREFIX}{self._key}.staging{round_index}"
                )
                round_index += 1
                await self._client.compose(
                    self._physical_bucket,
                    staging_name,
                    [current, *batch],
                    content_type=self._content_type,
                )
                for name in [current, *batch]:
                    await self._client.delete(self._physical_bucket, name)
                current = staging_name

            resource = await self._client.compose(
                self._physical_bucket,
                self._key,
                [current],
                content_type=self._content_type,
            )
            if current != self._key:
                await self._client.delete(self._physical_bucket, current)
            return _parse_gcs_object(self._bucket, resource)
        finally:
            self._tmp_names.clear()

    async def abort(self) -> None:
        for tmp_name in self._tmp_names.values():
            await self._client.delete(self._physical_bucket, tmp_name)
        self._tmp_names.clear()
