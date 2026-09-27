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

import structlog

_logger = structlog.get_logger(__name__)

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
    """物理バケットが存在しない場合に送出する（設定ミスの検知用）。

    バックエンドのSDK/APIがバケット不在とオブジェクト不在を区別できる場合に
    のみ送出する。区別できない場合（例: S3の``HeadObject``はボディの無い
    404を返す）は``ObjectNotFoundError``になる。
    """


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

        同じ``part_number``で再度呼ぶと、そのパートの内容を置き換える。
        パートは送信順ではなく``part_number``の昇順で結合される。

        Args:
            part_number: 1始まりのパート番号（1〜``MAX_PART_NUMBER``）。
            data: パートのバイト列。最後のパート以外は各バックエンドの実務的な
                最小サイズ（S3では``MIN_PART_SIZE``=5 MiB）以上にすること。
                強制タイミングはバックエンドにより異なり、S3はサーバー側で
                ``complete()``時に拒否する。

        Raises:
            ObjectStorageError: 送信に失敗した場合（権限不足なら
                ``PermissionDeniedError``）。
        """

    @abc.abstractmethod
    async def complete(self) -> ObjectInfo:
        """送信済みの全パートを``part_number``昇順で結合し、アップロードを完了する。

        完了するまで対象キーには何も書き込まれず（既存オブジェクトも
        置き換わらない）、完了した時点で``begin_multipart``に渡した
        ``content_type``/``metadata``付きのオブジェクトとして確定する。

        Returns:
            確定したオブジェクトのメタ情報。

        Raises:
            ObjectStorageError: パートが1つも送信されていない場合、パートの
                サイズ制約に違反した場合、またはバックエンドでの結合に失敗した
                場合。失敗した場合も``abort()``で後始末できる状態を保つ。
        """

    @abc.abstractmethod
    async def abort(self) -> None:
        """アップロードを中止し、送信済みパートを破棄する。

        対象キーには何も書き込まない。後始末対象が既に存在しない場合も
        例外を送出しない。

        Raises:
            ObjectStorageError: バックエンドでの破棄処理に失敗した場合。
        """

    async def __aenter__(self) -> MultipartUpload:
        return self

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None:
        if exc_type is None:
            return
        try:
            await self.abort()
        except Exception:
            # abort()の失敗で元の例外（アップロード失敗の本当の原因）を
            # 覆い隠さないよう、ログに残すだけにして元の例外を優先する。
            _logger.warning("multipart_abort_failed", exc_info=True)


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
    ) -> ObjectInfo:
        """オブジェクトを1回のリクエストで書き込む（既存なら上書きする）。

        Args:
            bucket: 論理バケット名（物理名ではない）。
            key: オブジェクトキー。
            data: 書き込むバイト列。
            content_type: オブジェクトのContent-Type。
            metadata: ユーザー定義メタデータ。

        Returns:
            書き込んだオブジェクトのメタ情報。

        Raises:
            UnknownBucketError: ``bucket``が登録されていない場合。
            BucketNotFoundError: 物理バケットが存在しない場合（判定可能な
                バックエンドのみ）。
            PermissionDeniedError: 権限不足の場合。
            ObjectStorageError: その他の失敗。
        """

    @abc.abstractmethod
    async def get(self, bucket: str, key: str) -> bytes:
        """オブジェクト全体をメモリに読み込んで返す。

        Args:
            bucket: 論理バケット名。
            key: オブジェクトキー。

        Returns:
            オブジェクトの内容。

        Raises:
            ObjectNotFoundError: オブジェクトが存在しない場合。
            UnknownBucketError: ``bucket``が登録されていない場合。
            ObjectStorageError: その他の失敗。
        """

    @abc.abstractmethod
    def get_stream(
        self, bucket: str, key: str, *, chunk_size: int = 64 * 1024
    ) -> AsyncIterator[bytes]:
        """オブジェクトの内容をチャンク単位で返す非同期イテレータを返す。

        非同期ジェネレータとして実装されるため、``ObjectNotFoundError``等の
        例外は呼び出し時ではなく最初の反復時（``async for``開始時）に送出される
        ことがある。

        Args:
            bucket: 論理バケット名。
            key: オブジェクトキー。
            chunk_size: 1チャンクの目安のバイト数（バックエンドにより実際の
                チャンクサイズは異なりうる）。

        Returns:
            オブジェクトの内容を先頭から順に返す非同期イテレータ。

        Raises:
            ObjectNotFoundError: オブジェクトが存在しない場合。
            UnknownBucketError: ``bucket``が登録されていない場合。
            ObjectStorageError: その他の失敗。
        """

    @abc.abstractmethod
    async def head(self, bucket: str, key: str) -> ObjectInfo:
        """オブジェクトの内容を読まずにメタ情報のみ取得する。

        Args:
            bucket: 論理バケット名。
            key: オブジェクトキー。

        Returns:
            オブジェクトのメタ情報。

        Raises:
            ObjectNotFoundError: オブジェクトが存在しない場合。
            UnknownBucketError: ``bucket``が登録されていない場合。
            ObjectStorageError: その他の失敗。
        """

    @abc.abstractmethod
    async def delete(self, bucket: str, key: str) -> None:
        """オブジェクトを削除する。冪等であり、存在しなくても成功する。

        Args:
            bucket: 論理バケット名。
            key: オブジェクトキー。

        Raises:
            UnknownBucketError: ``bucket``が登録されていない場合。
            BucketNotFoundError: 物理バケットが存在しない場合（判定可能な
                バックエンドのみ。オブジェクト不在とは区別して冪等化しない）。
            PermissionDeniedError: 権限不足の場合。
            ObjectStorageError: その他の失敗。
        """

    @abc.abstractmethod
    def list(self, bucket: str, prefix: str = "") -> AsyncIterator[ObjectInfo]:
        """``prefix``で始まるキーのオブジェクトを列挙する非同期イテレータを返す。

        ページングはアダプタ内部で処理する。列挙順は保証しない。
        非同期ジェネレータとして実装されるため、例外は呼び出し時ではなく
        最初の反復時に送出されることがある。バックエンドによっては
        ``content_type``/``metadata``が埋まらない（S3の``ListObjectsV2``は
        返さない）ため、必要なら``head``で取得すること。

        Args:
            bucket: 論理バケット名。
            prefix: キーの前方一致条件。空文字列なら全件。

        Returns:
            ``ObjectInfo``を返す非同期イテレータ。

        Raises:
            UnknownBucketError: ``bucket``が登録されていない場合。
            BucketNotFoundError: 物理バケットが存在しない場合（判定可能な
                バックエンドのみ）。
            ObjectStorageError: その他の失敗。
        """

    @abc.abstractmethod
    async def presigned_download_url(
        self, bucket: str, key: str, *, expires: timedelta
    ) -> str:
        """オブジェクトをダウンロード（GET）するための署名付きURLを生成する。

        Args:
            bucket: 論理バケット名。
            key: オブジェクトキー。
            expires: URLの有効期間。上限はバックエンドにより異なる
                （S3/GCSのV4署名は最大7日）。

        Returns:
            署名付きURL。

        Raises:
            NotSupportedError: バックエンドが署名付きURLに対応していない、
                または必要な認証情報が無い場合。
            ObjectStorageError: 有効期間が上限を超える等、生成に失敗した場合。
        """

    @abc.abstractmethod
    async def presigned_upload_url(
        self,
        bucket: str,
        key: str,
        *,
        expires: timedelta,
        content_type: str | None = None,
    ) -> str:
        """オブジェクトをアップロード（PUT）するための署名付きURLを生成する。

        Args:
            bucket: 論理バケット名。
            key: オブジェクトキー。
            expires: URLの有効期間。上限はバックエンドにより異なる。
            content_type: 指定すると、アップロード時のContent-Typeを署名に
                含めて強制する（対応するバックエンドのみ。Azureは署名に
                反映できない）。

        Returns:
            署名付きURL。

        Raises:
            NotSupportedError: バックエンドが署名付きURLに対応していない、
                または必要な認証情報が無い場合。
            ObjectStorageError: 有効期間が上限を超える等、生成に失敗した場合。
        """

    @abc.abstractmethod
    async def begin_multipart(
        self,
        bucket: str,
        key: str,
        *,
        content_type: str | None = None,
        metadata: Mapping[str, str] | None = None,
    ) -> MultipartUpload:
        """マルチパートアップロードのセッションを開始する。

        返り値の``MultipartUpload``に対して``upload_part``を1回以上呼び、
        ``complete()``で確定する。パート番号は1〜``MAX_PART_NUMBER``で、
        最後のパート以外は各バックエンドの実務的な最小サイズ（S3では
        ``MIN_PART_SIZE``=5 MiB）以上にすること。``async with``で使うと、
        ``complete()``せずに例外でブロックを抜けた場合に自動で``abort()``する。
        同じキーに対して複数セッションを並行実行しても、セッション間で
        パートが混ざることは無い（S3/GCS/InMemoryでは最後に``complete()``
        したものが残る）。ただしAzureでは、あるセッションの``complete()``や
        同じキーへの``put``がそのキーの他セッションの送信済みパートを破棄する
        ため、後から``complete()``したセッションは``ObjectStorageError``で
        失敗しうる。

        Args:
            bucket: 論理バケット名。
            key: 最終的に書き込むオブジェクトキー。
            content_type: 完成したオブジェクトのContent-Type。
            metadata: 完成したオブジェクトのユーザー定義メタデータ。

        Returns:
            マルチパートアップロードのセッション。

        Raises:
            UnknownBucketError: ``bucket``が登録されていない場合。
            ObjectStorageError: セッション開始に失敗した場合。
        """

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
