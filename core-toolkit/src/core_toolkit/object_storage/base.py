"""ObjectStorage抽象化の基底型（ABC・データ型・例外階層・論理名解決）。

S3/GCS/Azure Blobの差し替えを可能にするための共通インターフェース。
アプリは``bucket``引数に論理名のみを渡し、物理バケット名への解決は
``ObjectStorage``のコンストラクタに渡した``buckets``マッピングが行う。
"""

import abc
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

DEFAULT_MULTIPART_THRESHOLD = 8 * 1024 * 1024  # 8 MiB
DEFAULT_PART_SIZE = 8 * 1024 * 1024  # 8 MiB
MIN_PART_SIZE = 5 * 1024 * 1024  # 5 MiB（S3の制約。最後のパート以外はこれ以上必要）
MAX_PART_NUMBER = 10_000


class ObjectStorageError(Exception):
    """ObjectStorage操作全般の基底例外。未分類のSDK例外はこれに包む。"""


class UnknownBucketError(ObjectStorageError):
    """論理バケット名が登録されていない場合に送出する。"""

    def __init__(self, name: str, known_names: Mapping[str, str]) -> None:
        known = ", ".join(sorted(known_names)) or "(none)"
        super().__init__(
            f"bucket '{name}' is not registered. Known logical bucket names: {known}"
        )
        self.name = name


class BucketNotFoundError(ObjectStorageError):
    """物理バケットが存在しない場合に送出する（設定ミスの検知用）。"""


class ObjectNotFoundError(ObjectStorageError):
    """対象オブジェクトが存在しない場合に送出する。"""


class PermissionDeniedError(ObjectStorageError):
    """権限不足で操作が拒否された場合に送出する。"""


class NotSupportedError(ObjectStorageError):
    """そのバックエンドが対応しない操作を呼び出した場合に送出する。"""


@dataclass(frozen=True)
class ObjectInfo:
    """オブジェクトのメタ情報。"""

    bucket: str
    key: str
    size: int
    content_type: str | None
    etag: str | None
    last_modified: datetime | None
    metadata: Mapping[str, str]


class MultipartUpload(abc.ABC):
    """マルチパートアップロードの1セッション。

    ``async with``で使うと、``complete()``を呼ばずにブロックを抜けた場合に
    自動で``abort()``する。
    """

    @abc.abstractmethod
    async def upload_part(self, part_number: int, data: bytes) -> None:
        """1パート分のデータを送信する。

        Args:
            part_number: 1始まりのパート番号（最大``MAX_PART_NUMBER``）。
            data: パートのバイト列。最後のパート以外は``MIN_PART_SIZE``以上
                である必要がある（バックエンドによっては実際の強制は
                サーバー側で行われる）。
        """

    @abc.abstractmethod
    async def complete(self) -> ObjectInfo:
        """送信済みの全パートを結合し、アップロードを完了する。"""

    @abc.abstractmethod
    async def abort(self) -> None:
        """アップロードを中止し、送信済みパートを破棄する。"""

    async def __aenter__(self) -> MultipartUpload:
        return self

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None:
        if exc_type is None:
            return
        await self.abort()


class ObjectStorage(abc.ABC):
    """CRUD・署名付きURL・マルチパートアップロードを提供する共通インターフェース。

    Args:
        buckets: 論理バケット名から物理バケット名へのマッピング。
    """

    def __init__(self, buckets: Mapping[str, str]) -> None:
        self._buckets = dict(buckets)

    def _resolve_bucket(self, bucket: str) -> str:
        try:
            return self._buckets[bucket]
        except KeyError:
            raise UnknownBucketError(bucket, self._buckets) from None

    # --- 基本操作（アダプタが実装する） ---

    @abc.abstractmethod
    async def put(
        self,
        bucket: str,
        key: str,
        data: bytes,
        *,
        content_type: str | None = None,
        metadata: Mapping[str, str] | None = None,
    ) -> ObjectInfo: ...

    @abc.abstractmethod
    async def get(self, bucket: str, key: str) -> bytes: ...

    @abc.abstractmethod
    def get_stream(
        self, bucket: str, key: str, *, chunk_size: int = 64 * 1024
    ) -> AsyncIterator[bytes]: ...

    @abc.abstractmethod
    async def head(self, bucket: str, key: str) -> ObjectInfo: ...

    @abc.abstractmethod
    async def delete(self, bucket: str, key: str) -> None: ...

    @abc.abstractmethod
    def list(self, bucket: str, prefix: str = "") -> AsyncIterator[ObjectInfo]: ...

    @abc.abstractmethod
    async def presigned_download_url(
        self, bucket: str, key: str, *, expires: timedelta
    ) -> str: ...

    @abc.abstractmethod
    async def presigned_upload_url(
        self,
        bucket: str,
        key: str,
        *,
        expires: timedelta,
        content_type: str | None = None,
    ) -> str: ...

    @abc.abstractmethod
    async def begin_multipart(
        self,
        bucket: str,
        key: str,
        *,
        content_type: str | None = None,
        metadata: Mapping[str, str] | None = None,
    ) -> MultipartUpload: ...

    # --- 便利メソッド（基本操作の組み合わせのみ） ---

    async def exists(self, bucket: str, key: str) -> bool:
        """オブジェクトが存在するかを確認する。"""
        try:
            await self.head(bucket, key)
        except ObjectNotFoundError:
            return False
        return True

    async def put_file(
        self,
        bucket: str,
        key: str,
        path: Path,
        *,
        content_type: str | None = None,
        metadata: Mapping[str, str] | None = None,
        multipart_threshold: int = DEFAULT_MULTIPART_THRESHOLD,
        part_size: int = DEFAULT_PART_SIZE,
    ) -> ObjectInfo:
        """ローカルファイルをアップロードする。

        ``path``のサイズが``multipart_threshold``以下なら単発の``put``、
        超える場合はマルチパートアップロードを行う。途中で失敗した場合は
        アップロードを中止する（``MultipartUpload``の自動abortに乗る）。
        """
        size = path.stat().st_size
        if size <= multipart_threshold:
            data = path.read_bytes()
            return await self.put(
                bucket, key, data, content_type=content_type, metadata=metadata
            )

        async with await self.begin_multipart(
            bucket, key, content_type=content_type, metadata=metadata
        ) as upload:
            with path.open("rb") as f:
                part_number = 1
                while chunk := f.read(part_size):
                    await upload.upload_part(part_number, chunk)
                    part_number += 1
            return await upload.complete()

    async def download_file(self, bucket: str, key: str, path: Path) -> None:
        """オブジェクトをローカルファイルへ書き出す。"""
        with path.open("wb") as f:
            async for chunk in self.get_stream(bucket, key):
                f.write(chunk)
