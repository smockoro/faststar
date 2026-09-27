"""gcloud-aio-storageベースのGCS ObjectStorageアダプタ。

利用には ``core-toolkit[gcs]`` extraのインストールが必要。

GCSには真のマルチパートAPIが無いため、マルチパートのパートは
``.multipart-tmp/``で始まる一時オブジェクトとして同じバケットに置く。
このプレフィックスはアダプタの予約領域であり、``list()``の結果からは除外する。
"""

import uuid
from collections.abc import AsyncIterator, Mapping
from datetime import datetime, timedelta
from typing import Any, NoReturn

import aiohttp
import structlog
from gcloud.aio.storage import Bucket, Storage

from core_toolkit.object_storage.base import (
    MAX_PART_NUMBER,
    BucketNotFoundError,
    MultipartUpload,
    ObjectInfo,
    ObjectNotFoundError,
    ObjectStorage,
    ObjectStorageError,
    PermissionDeniedError,
)

_logger = structlog.get_logger(__name__)

_MULTIPART_TMP_PREFIX = ".multipart-tmp/"
_COMPOSE_BATCH_SIZE = 32
# V4署名付きURLの有効期限の上限（GCSの仕様。gcloud-aio-storageはこれを超えると
# ValueErrorを送出する）。
_MAX_SIGNED_URL_EXPIRES = timedelta(days=7)
# GCS JSON APIがバケット不在時に返すエラーメッセージ。gcloud-aio-auth は
# レスポンスボディを``ClientResponseError.message``に含めるため、これで
# オブジェクト不在（"No such object: ..."）と区別できる。
_BUCKET_NOT_FOUND_MARKER = "The specified bucket does not exist"


def _is_bucket_not_found(error: aiohttp.ClientResponseError) -> bool:
    return error.status == 404 and _BUCKET_NOT_FOUND_MARKER in str(error.message)


def _raise_for_response_error(
    error: aiohttp.ClientResponseError, *, bucket: str, key: str
) -> NoReturn:
    """``aiohttp.ClientResponseError``をObjectStorageの例外階層へ正規化して送出する。"""
    if _is_bucket_not_found(error):
        raise BucketNotFoundError(
            f"physical bucket for logical bucket '{bucket}' does not exist"
        ) from error
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
        return _parse_gcs_object(bucket, resource)

    async def get(self, bucket, key) -> bytes:
        physical = self._resolve_bucket(bucket)
        try:
            return await self._client.download(physical, key)
        except aiohttp.ClientResponseError as error:
            _raise_for_response_error(error, bucket=bucket, key=key)

    async def get_stream(
        self, bucket, key, *, chunk_size=64 * 1024
    ) -> AsyncIterator[bytes]:
        physical = self._resolve_bucket(bucket)
        try:
            stream = await self._client.download_stream(physical, key)
        except aiohttp.ClientResponseError as error:
            _raise_for_response_error(error, bucket=bucket, key=key)
        async with stream:
            while chunk := await stream.read(chunk_size):
                yield chunk

    async def head(self, bucket, key) -> ObjectInfo:
        physical = self._resolve_bucket(bucket)
        try:
            resource = await self._client.download_metadata(physical, key)
        except aiohttp.ClientResponseError as error:
            _raise_for_response_error(error, bucket=bucket, key=key)
        return _parse_gcs_object(bucket, resource)

    async def delete(self, bucket, key) -> None:
        physical = self._resolve_bucket(bucket)
        try:
            await self._client.delete(physical, key)
        except aiohttp.ClientResponseError as error:
            if error.status == 404 and not _is_bucket_not_found(error):
                # オブジェクトが存在しない場合は成功扱いにして冪等化する。
                # バケット不在は設定ミスなので冪等化せずBucketNotFoundErrorにする。
                return
            _raise_for_response_error(error, bucket=bucket, key=key)

    async def list(self, bucket, prefix="") -> AsyncIterator[ObjectInfo]:
        """``prefix``で始まるオブジェクトを列挙する。

        マルチパート用の一時オブジェクト（``.multipart-tmp/``で始まるキー）は
        アダプタの内部実装なので結果から除外する。
        """
        physical = self._resolve_bucket(bucket)
        page_token: str | None = None
        while True:
            params: dict[str, str] = {"prefix": prefix}
            if page_token:
                params["pageToken"] = page_token
            try:
                resp = await self._client.list_objects(physical, params=params)
            except aiohttp.ClientResponseError as error:
                _raise_for_response_error(error, bucket=bucket, key=prefix)
            for resource in resp.get("items", []):
                if resource["name"].startswith(_MULTIPART_TMP_PREFIX):
                    continue
                yield _parse_gcs_object(bucket, resource)
            page_token = resp.get("nextPageToken")
            if not page_token:
                break

    async def presigned_download_url(self, bucket, key, *, expires: timedelta) -> str:
        """ダウンロード（GET）用のV4署名付きURLを生成する。

        ``expires``が7日を超える場合、GCSの仕様上生成できないため
        ``ObjectStorageError``を送出する（SDKの``ValueError``は外に出さない）。
        """
        return await self._signed_url(bucket, key, expires=expires, http_method="GET")

    async def presigned_upload_url(
        self, bucket, key, *, expires: timedelta, content_type=None
    ) -> str:
        """アップロード（PUT）用のV4署名付きURLを生成する。

        ``content_type``を指定すると署名に含め、アップロード時に同じ
        Content-Typeヘッダを要求する。``expires``が7日を超える場合は
        ``ObjectStorageError``を送出する。
        """
        headers = {"content-type": content_type} if content_type else None
        return await self._signed_url(
            bucket, key, expires=expires, http_method="PUT", headers=headers
        )

    async def _signed_url(
        self,
        bucket: str,
        key: str,
        *,
        expires: timedelta,
        http_method: str,
        headers: dict[str, str] | None = None,
    ) -> str:
        physical = self._resolve_bucket(bucket)
        if expires > _MAX_SIGNED_URL_EXPIRES:
            raise ObjectStorageError(
                f"GCS signed URLs cannot expire later than "
                f"{_MAX_SIGNED_URL_EXPIRES} (got {expires})"
            )
        blob = Bucket(self._client, physical).new_blob(key)
        try:
            return await blob.get_signed_url(
                int(expires.total_seconds()),
                http_method=http_method,
                headers=headers,
            )
        except aiohttp.ClientResponseError as error:
            # 秘密鍵が無い場合はIAM signBlob APIを呼ぶため、HTTPエラーもありうる
            _raise_for_response_error(error, bucket=bucket, key=key)
        except ValueError as error:
            # 鍵が不正・未対応など、署名そのものが行えない場合
            raise ObjectStorageError(
                f"failed to generate GCS signed URL for {bucket}/{key}: {error}"
            ) from error

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
    アップロードし、``complete()``で``compose``して結合する。

    一時オブジェクト名にはセッションごとの``session_id``を含め、同じキーへの
    並行セッション同士が互いの一時オブジェクトを上書き・削除しないようにする。
    ``compose``は1回につき最大32個までしか結合できないため、パート数が32以下
    なら最終キーへ直接1回で結合し、超える場合はstaging用の一時オブジェクトへ
    段階的に結合してから最終キーへ結合する。

    ``compose``はdestinationに``contentType``しか渡せず、ユーザー定義metadataは
    引き継がれないため、結合後に``patch_metadata``で反映する（結合から反映までの
    短い間、オブジェクトはmetadata無しで見える）。metadataの反映に失敗した
    場合、``complete()``は``ObjectStorageError``を送出するが、結合済みの
    オブジェクト自体は（metadata無しで）残る。
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
        self._session_prefix = f"{_MULTIPART_TMP_PREFIX}{key}/{uuid.uuid4().hex}/"
        self._tmp_names: dict[int, str] = {}
        self._staging_names: list[str] = []

    def _tmp_name(self, part_number: int) -> str:
        return f"{self._session_prefix}part{part_number:05d}"

    def _staging_name(self, round_index: int) -> str:
        return f"{self._session_prefix}staging{round_index:05d}"

    def _raise(self, error: aiohttp.ClientResponseError) -> NoReturn:
        _raise_for_response_error(error, bucket=self._bucket, key=self._key)

    async def upload_part(self, part_number: int, data: bytes) -> None:
        if not (1 <= part_number <= MAX_PART_NUMBER):
            raise ObjectStorageError(
                f"part_number must be between 1 and {MAX_PART_NUMBER}, "
                f"got {part_number}"
            )
        tmp_name = self._tmp_name(part_number)
        try:
            await self._client.upload(self._physical_bucket, tmp_name, data)
        except aiohttp.ClientResponseError as error:
            self._raise(error)
        self._tmp_names[part_number] = tmp_name

    async def complete(self) -> ObjectInfo:
        if not self._tmp_names:
            raise ObjectStorageError(
                f"cannot complete multipart upload for {self._bucket}/{self._key}: "
                "no parts have been uploaded"
            )
        sources = [self._tmp_names[n] for n in sorted(self._tmp_names)]
        try:
            round_index = 0
            while len(sources) > _COMPOSE_BATCH_SIZE:
                staging_name = self._staging_name(round_index)
                round_index += 1
                # 失敗時にabort()で削除できるよう、composeより前に追跡を始める
                self._staging_names.append(staging_name)
                await self._client.compose(
                    self._physical_bucket,
                    staging_name,
                    sources[:_COMPOSE_BATCH_SIZE],
                    content_type=self._content_type,
                )
                consumed_staging = [
                    name
                    for name in sources[:_COMPOSE_BATCH_SIZE]
                    if name in self._staging_names
                ]
                sources = [staging_name, *sources[_COMPOSE_BATCH_SIZE:]]
                # 前段のstagingは次段に取り込み済み。累積サイズのstagingが
                # 段数分溜まらないよう、ここで削除する。
                for name in consumed_staging:
                    await self._delete_if_exists(name)
                    self._staging_names.remove(name)

            resource = await self._client.compose(
                self._physical_bucket,
                self._key,
                sources,
                content_type=self._content_type,
            )
            if self._metadata:
                resource = await self._client.patch_metadata(
                    self._physical_bucket,
                    self._key,
                    {"metadata": self._metadata},
                )
        except aiohttp.ClientResponseError as error:
            # 一時オブジェクトの追跡は残し、呼び出し側がabort()で後始末できるようにする
            self._raise(error)

        # 最終オブジェクトは確定済みなので、一時オブジェクトの削除失敗は
        # complete()の失敗にはしない（一時オブジェクトはlist()からは見えない）。
        try:
            await self._delete_tracked()
        except ObjectStorageError:
            _logger.warning(
                "gcs_multipart_tmp_cleanup_failed",
                bucket=self._bucket,
                key=self._key,
                exc_info=True,
            )
        self._tmp_names.clear()
        self._staging_names.clear()
        return _parse_gcs_object(self._bucket, resource)

    async def abort(self) -> None:
        await self._delete_tracked()
        self._tmp_names.clear()
        self._staging_names.clear()

    async def _delete_if_exists(self, name: str) -> None:
        try:
            await self._client.delete(self._physical_bucket, name)
        except aiohttp.ClientResponseError as error:
            if error.status == 404 and not _is_bucket_not_found(error):
                return
            self._raise(error)

    async def _delete_tracked(self) -> None:
        """追跡中の一時オブジェクトをすべて削除する（存在しないものは無視）。

        1つの削除に失敗しても残りの削除を試み、最初の失敗を最後に送出する。
        """
        first_error: ObjectStorageError | None = None
        for name in [*self._tmp_names.values(), *self._staging_names]:
            try:
                await self._delete_if_exists(name)
            except ObjectStorageError as error:
                if first_error is None:
                    first_error = error
        if first_error is not None:
            raise first_error
