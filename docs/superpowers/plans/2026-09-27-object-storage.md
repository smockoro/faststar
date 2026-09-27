# ObjectStorage接続部品（S3/GCS/Azure Blob差し替え対応） Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** S3 / GCS / Azure Blob を設定値（`scheme`）だけで差し替えられる `ObjectStorage` 抽象化を、core-toolkit / fastapi-toolkit / fastmcp-toolkit の3層に追加する。

**Architecture:** 自前の `ObjectStorage` ABC（CRUD・署名付きURL・マルチパート）を core-toolkit に置き、S3(`aiobotocore`)/GCS(`gcloud-aio-storage`)/Azure(`azure-storage-blob` の `.aio`)/InMemory の4アダプタで実装する。バケットは呼び出しごとに論理名で指定し、論理名→物理名の解決は基底クラスが `Mapping` で行う。fastapi-toolkitは薄い再エクスポート、fastmcp-toolkitは独自のlifespan配線（ABC・アダプタ自体はcore-toolkitのものをそのまま使う）を持つ。

**Tech Stack:** Python 3.14 / `aiobotocore` / `gcloud-aio-storage` / `azure-storage-blob[aio]` / pytest + pytest-asyncio（手書きフェイクによる単体テスト、実バックエンドは対象外）

**Spec:** `docs/superpowers/specs/2026-09-27-object-storage-design.md`

## Global Constraints

- Python 3.14+、型ヒント必須、公開APIのdocstringは日本語・Googleスタイル（プロジェクトCLAUDE.md）
- ruffルールは `E, F, I, UP`。`uv run ruff check` / `uv run ruff format --check` を通すこと
- 非同期ネイティブのみ（S3: `aiobotocore`、GCS: `gcloud-aio-storage`、Azure: `.aio`）。同期SDK+`to_thread`は使わない
- バケットは呼び出しごとに**論理名**で指定する。物理名（コンテナ名）への解決は `Mapping[str, str]`（登録時に固定、未登録は`UnknownBucketError`）
- 署名付きURLはダウンロード用(GET)とアップロード用(PUT、単発)のみ。パート単位の署名URLは作らない
- マルチパートはサーバー経由のみ。GCSは`compose`（公開APIのみ、32個超は段階結合）で実現し、resumable uploadの内部APIには依存しない
- `InMemoryObjectStorage`の署名URLは`NotSupportedError`。ダミーURLは返さない
- バケット管理（作成・削除・一覧）、`copy`、メタデータ更新、ACL、`put_stream`、パート単位署名URL、ローカルファイルシステムアダプタは非スコープ
- 正規化する例外は `UnknownBucketError` / `BucketNotFoundError` / `ObjectNotFoundError` / `PermissionDeniedError` / `NotSupportedError` の5種のみ。それ以外は `ObjectStorageError` に包む
- `get_object_storage(name)` / `_get_object_storage(name)` は呼び出すたびに新しいクロージャを返すため、`dependency_overrides`のキーにする場合はモジュールレベルで1回だけ呼んで変数に固定する

---

## Task 1: `ObjectStorage` ABC・データ型・例外階層・論理名解決（core-toolkit）

**Files:**
- Create: `core-toolkit/src/core_toolkit/object_storage/__init__.py`
- Create: `core-toolkit/src/core_toolkit/object_storage/base.py`
- Test: `core-toolkit/tests/test_object_storage_base.py`

**Interfaces:**
- Produces: `core_toolkit.object_storage.base.ObjectStorageError`（基底例外）、`UnknownBucketError`、`BucketNotFoundError`、`ObjectNotFoundError`、`PermissionDeniedError`、`NotSupportedError`、`ObjectInfo`（frozen dataclass: `bucket, key, size, content_type, etag, last_modified, metadata`）、`MultipartUpload`（ABC: `upload_part(part_number, data)`, `complete() -> ObjectInfo`, `abort()`, `async with`対応）、`ObjectStorage`（ABC: `put/get/get_stream/head/delete/list/presigned_download_url/presigned_upload_url/begin_multipart`が抽象、`exists/put_file/download_file`が具象）、定数 `DEFAULT_MULTIPART_THRESHOLD`, `DEFAULT_PART_SIZE`, `MIN_PART_SIZE`, `MAX_PART_NUMBER`
- 以降のタスクはすべてこのモジュールの型・例外・定数をそのまま使う

- [ ] **Step 1: 失敗するテストを書く**

`core-toolkit/tests/test_object_storage_base.py`:

```python
"""ObjectStorage基底クラス（論理名解決・put_file/download_fileの便利メソッド・
MultipartUploadの自動abort）の単体テスト。

具象実装はcore_toolkit.object_storage.memory.InMemoryObjectStorageで別途
テストするため（tests/test_object_storage_memory.py）、ここでは基本操作を
差し替え可能な最小のフェイクだけを使い、基底クラス自身のロジックのみを
検証する。
"""

from collections.abc import AsyncIterator, Mapping
from pathlib import Path

import pytest

from core_toolkit.object_storage.base import (
    MultipartUpload,
    ObjectInfo,
    ObjectNotFoundError,
    ObjectStorage,
    UnknownBucketError,
)


class RecordingMultipartUpload(MultipartUpload):
    def __init__(self, bucket: str, key: str) -> None:
        self.bucket = bucket
        self.key = key
        self.parts: dict[int, bytes] = {}
        self.completed = False
        self.aborted = False

    async def upload_part(self, part_number: int, data: bytes) -> None:
        self.parts[part_number] = data

    async def complete(self) -> ObjectInfo:
        self.completed = True
        body = b"".join(self.parts[n] for n in sorted(self.parts))
        return ObjectInfo(
            bucket=self.bucket, key=self.key, size=len(body), content_type=None,
            etag=None, last_modified=None, metadata={},
        )

    async def abort(self) -> None:
        self.aborted = True


class FakeStorage(ObjectStorage):
    """テスト用の最小実装。dataはメモリの``dict``に保持する。"""

    def __init__(self, buckets: Mapping[str, str]) -> None:
        super().__init__(buckets)
        self._objects: dict[tuple[str, str], bytes] = {}
        self.multipart_sessions: list[RecordingMultipartUpload] = []
        self.fail_upload_part_number: int | None = None

    async def put(self, bucket, key, data, *, content_type=None, metadata=None) -> ObjectInfo:
        physical = self._resolve_bucket(bucket)
        self._objects[(physical, key)] = data
        return ObjectInfo(
            bucket=bucket, key=key, size=len(data), content_type=content_type,
            etag=None, last_modified=None, metadata=metadata or {},
        )

    async def get(self, bucket, key) -> bytes:
        physical = self._resolve_bucket(bucket)
        try:
            return self._objects[(physical, key)]
        except KeyError:
            raise ObjectNotFoundError(f"{bucket}/{key}") from None

    async def get_stream(self, bucket, key, *, chunk_size=64 * 1024) -> AsyncIterator[bytes]:
        data = await self.get(bucket, key)
        for i in range(0, len(data), chunk_size):
            yield data[i : i + chunk_size]

    async def head(self, bucket, key) -> ObjectInfo:
        data = await self.get(bucket, key)
        return ObjectInfo(
            bucket=bucket, key=key, size=len(data), content_type=None,
            etag=None, last_modified=None, metadata={},
        )

    async def delete(self, bucket, key) -> None:
        physical = self._resolve_bucket(bucket)
        self._objects.pop((physical, key), None)

    async def list(self, bucket, prefix=""):
        physical = self._resolve_bucket(bucket)
        for (b, k), data in self._objects.items():
            if b == physical and k.startswith(prefix):
                yield ObjectInfo(
                    bucket=bucket, key=k, size=len(data), content_type=None,
                    etag=None, last_modified=None, metadata={},
                )

    async def presigned_download_url(self, bucket, key, *, expires) -> str:
        raise NotImplementedError

    async def presigned_upload_url(self, bucket, key, *, expires, content_type=None) -> str:
        raise NotImplementedError

    async def begin_multipart(self, bucket, key, *, content_type=None, metadata=None) -> MultipartUpload:
        self._resolve_bucket(bucket)
        session = RecordingMultipartUpload(bucket, key)
        if self.fail_upload_part_number is not None:
            original = session.upload_part

            async def failing_upload_part(part_number, data):
                if part_number == self.fail_upload_part_number:
                    raise RuntimeError("boom")
                await original(part_number, data)

            session.upload_part = failing_upload_part  # type: ignore[method-assign]
        self.multipart_sessions.append(session)
        return session


@pytest.fixture
def storage() -> FakeStorage:
    return FakeStorage({"uploads": "uploads-x7f3"})


@pytest.mark.asyncio
async def test_resolve_bucket_returns_physical_name(storage: FakeStorage):
    await storage.put("uploads", "a.txt", b"hello")
    assert await storage.get("uploads", "a.txt") == b"hello"


@pytest.mark.asyncio
async def test_resolve_bucket_raises_for_unknown_logical_name(storage: FakeStorage):
    with pytest.raises(UnknownBucketError, match="uploads"):
        await storage.put("unknown", "a.txt", b"hello")


@pytest.mark.asyncio
async def test_exists_returns_true_when_object_present(storage: FakeStorage):
    await storage.put("uploads", "a.txt", b"hello")
    assert await storage.exists("uploads", "a.txt") is True


@pytest.mark.asyncio
async def test_exists_returns_false_when_object_missing(storage: FakeStorage):
    assert await storage.exists("uploads", "missing.txt") is False


@pytest.mark.asyncio
async def test_put_file_uses_single_put_below_threshold(storage: FakeStorage, tmp_path: Path):
    path = tmp_path / "small.bin"
    path.write_bytes(b"x" * 10)

    info = await storage.put_file("uploads", "small.bin", path, multipart_threshold=100)

    assert info.size == 10
    assert storage.multipart_sessions == []
    assert await storage.get("uploads", "small.bin") == b"x" * 10


@pytest.mark.asyncio
async def test_put_file_uses_multipart_above_threshold(storage: FakeStorage, tmp_path: Path):
    path = tmp_path / "large.bin"
    path.write_bytes(b"a" * 30)

    info = await storage.put_file(
        "uploads", "large.bin", path, multipart_threshold=10, part_size=10
    )

    assert info.size == 30
    assert len(storage.multipart_sessions) == 1
    session = storage.multipart_sessions[0]
    assert session.completed is True
    assert sorted(session.parts) == [1, 2, 3]


@pytest.mark.asyncio
async def test_put_file_aborts_multipart_on_part_failure(storage: FakeStorage, tmp_path: Path):
    path = tmp_path / "large.bin"
    path.write_bytes(b"a" * 30)
    storage.fail_upload_part_number = 2

    with pytest.raises(RuntimeError, match="boom"):
        await storage.put_file(
            "uploads", "large.bin", path, multipart_threshold=10, part_size=10
        )

    session = storage.multipart_sessions[0]
    assert session.aborted is True
    assert session.completed is False


@pytest.mark.asyncio
async def test_download_file_writes_stream_to_path(storage: FakeStorage, tmp_path: Path):
    await storage.put("uploads", "a.txt", b"hello world")
    dest = tmp_path / "out.txt"

    await storage.download_file("uploads", "a.txt", dest)

    assert dest.read_bytes() == b"hello world"
```

- [ ] **Step 2: 失敗することを確認する**

Run: `cd core-toolkit && uv run pytest tests/test_object_storage_base.py -v`
Expected: FAIL（`core_toolkit.object_storage` が存在しない）

- [ ] **Step 3: 実装する**

`core-toolkit/src/core_toolkit/object_storage/base.py`:

```python
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

    async def __aenter__(self) -> "MultipartUpload":
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
            return await self.put(bucket, key, data, content_type=content_type, metadata=metadata)

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
```

`core-toolkit/src/core_toolkit/object_storage/__init__.py`:

```python
"""ObjectStorage抽象化（S3/GCS/Azure Blob/インメモリの差し替え対応）。"""

from core_toolkit.object_storage.base import (
    BucketNotFoundError,
    MultipartUpload,
    NotSupportedError,
    ObjectInfo,
    ObjectNotFoundError,
    ObjectStorage,
    ObjectStorageError,
    PermissionDeniedError,
    UnknownBucketError,
)

__all__ = [
    "BucketNotFoundError",
    "MultipartUpload",
    "NotSupportedError",
    "ObjectInfo",
    "ObjectNotFoundError",
    "ObjectStorage",
    "ObjectStorageError",
    "PermissionDeniedError",
    "UnknownBucketError",
]
```

- [ ] **Step 4: 成功することを確認する**

Run: `cd core-toolkit && uv run pytest tests/test_object_storage_base.py -v`
Expected: PASS（全11テスト）

- [ ] **Step 5: lintを通す**

Run: `cd core-toolkit && uv run ruff check src/core_toolkit/object_storage tests/test_object_storage_base.py && uv run ruff format --check src/core_toolkit/object_storage tests/test_object_storage_base.py`
Expected: エラーなし（フォーマット崩れがあれば `uv run ruff format` で直してから再確認）

- [ ] **Step 6: コミット**

```bash
git add core-toolkit/src/core_toolkit/object_storage/__init__.py core-toolkit/src/core_toolkit/object_storage/base.py core-toolkit/tests/test_object_storage_base.py
git commit -m "feat(core-toolkit): add ObjectStorage ABC and exception hierarchy"
```

---

## Task 2: `InMemoryObjectStorage` と共通契約テストスイート（core-toolkit）

**Files:**
- Create: `core-toolkit/src/core_toolkit/object_storage/memory.py`
- Modify: `core-toolkit/src/core_toolkit/object_storage/__init__.py`
- Create: `core-toolkit/tests/object_storage_contract.py`（テストヘルパー。`test_`接頭辞を付けずpytestの収集対象から外す）
- Test: `core-toolkit/tests/test_object_storage_memory.py`

**Interfaces:**
- Consumes: Task1の`ObjectStorage`/`MultipartUpload`/`ObjectInfo`/`ObjectNotFoundError`/`ObjectStorageError`/`NotSupportedError`/`MAX_PART_NUMBER`
- Produces: `core_toolkit.object_storage.memory.InMemoryObjectStorage(buckets, *, min_part_size=MIN_PART_SIZE)`。`tests.object_storage_contract.ObjectStorageContract`（pytestクラスmixin。サブクラスは`storage`/`logical_bucket`fixtureを定義する）。Task3〜5はこのcontractを再利用する

- [ ] **Step 1: 失敗するテストを書く**

`core-toolkit/tests/object_storage_contract.py`（テスト本体ではなく共有ヘルパー）:

```python
"""ObjectStorage実装が満たすべき契約（共通の振る舞い）を検証するテストスイート。

``InMemoryObjectStorage``と各バックエンドのフェイクアダプタの両方に対して
同じテストを実行することで、フェイクと実装の間の挙動差を検出する足場とする。
サブクラスは``storage``fixtureで``ObjectStorage``インスタンスを、
``logical_bucket``fixtureで登録済みの論理バケット名を返す。
"""

from pathlib import Path

from core_toolkit.object_storage.base import ObjectNotFoundError, ObjectStorage


class ObjectStorageContract:
    """継承先で``storage``/``logical_bucket``fixtureを定義して使う。"""

    async def test_put_then_get_roundtrips(self, storage: ObjectStorage, logical_bucket: str):
        await storage.put(logical_bucket, "a/b.txt", b"hello")
        assert await storage.get(logical_bucket, "a/b.txt") == b"hello"

    async def test_get_missing_raises_object_not_found(self, storage: ObjectStorage, logical_bucket: str):
        import pytest

        with pytest.raises(ObjectNotFoundError):
            await storage.get(logical_bucket, "missing.txt")

    async def test_head_returns_size_and_content_type(self, storage: ObjectStorage, logical_bucket: str):
        await storage.put(logical_bucket, "a.txt", b"hello", content_type="text/plain")
        info = await storage.head(logical_bucket, "a.txt")
        assert info.size == 5
        assert info.content_type == "text/plain"

    async def test_delete_is_idempotent(self, storage: ObjectStorage, logical_bucket: str):
        await storage.put(logical_bucket, "a.txt", b"hello")
        await storage.delete(logical_bucket, "a.txt")
        await storage.delete(logical_bucket, "a.txt")  # 2回目も例外を出さない
        assert await storage.exists(logical_bucket, "a.txt") is False

    async def test_list_returns_objects_matching_prefix(self, storage: ObjectStorage, logical_bucket: str):
        await storage.put(logical_bucket, "dir/a.txt", b"1")
        await storage.put(logical_bucket, "dir/b.txt", b"2")
        await storage.put(logical_bucket, "other.txt", b"3")

        keys = {info.key async for info in storage.list(logical_bucket, prefix="dir/")}

        assert keys == {"dir/a.txt", "dir/b.txt"}

    async def test_get_stream_yields_full_content(self, storage: ObjectStorage, logical_bucket: str):
        body = b"x" * 100
        await storage.put(logical_bucket, "a.bin", body)

        chunks = [chunk async for chunk in storage.get_stream(logical_bucket, "a.bin", chunk_size=10)]

        assert b"".join(chunks) == body

    async def test_multipart_upload_completes_in_order(self, storage: ObjectStorage, logical_bucket: str):
        upload = await storage.begin_multipart(logical_bucket, "multi.bin")
        await upload.upload_part(1, b"aaaaa")
        await upload.upload_part(2, b"bb")

        info = await upload.complete()

        assert info.size == 7
        assert await storage.get(logical_bucket, "multi.bin") == b"aaaaabb"

    async def test_multipart_upload_abort_discards_parts(self, storage: ObjectStorage, logical_bucket: str):
        upload = await storage.begin_multipart(logical_bucket, "aborted.bin")
        await upload.upload_part(1, b"aaaaa")

        await upload.abort()

        assert await storage.exists(logical_bucket, "aborted.bin") is False

    async def test_put_file_roundtrips_via_multipart(
        self, storage: ObjectStorage, logical_bucket: str, tmp_path: Path
    ):
        path = tmp_path / "big.bin"
        path.write_bytes(b"z" * 21)

        info = await storage.put_file(
            logical_bucket, "big.bin", path, multipart_threshold=10, part_size=10,
        )

        assert info.size == 21
        assert await storage.get(logical_bucket, "big.bin") == path.read_bytes()
```

`core-toolkit/tests/test_object_storage_memory.py`:

```python
"""InMemoryObjectStorageの単体テスト。共通契約はObjectStorageContractで検証し、
ここではInMemory固有の挙動（プロセス外に出ないこと、min_part_sizeの強制、
署名付きURLが未対応であること）を検証する。
"""

from datetime import timedelta

import pytest

from core_toolkit.object_storage.base import NotSupportedError, ObjectStorageError
from core_toolkit.object_storage.memory import InMemoryObjectStorage
from tests.object_storage_contract import ObjectStorageContract


class TestInMemoryObjectStorageContract(ObjectStorageContract):
    @pytest.fixture
    def storage(self) -> InMemoryObjectStorage:
        return InMemoryObjectStorage({"uploads": "uploads-x7f3"}, min_part_size=1)

    @pytest.fixture
    def logical_bucket(self) -> str:
        return "uploads"


@pytest.mark.asyncio
async def test_presigned_download_url_raises_not_supported():
    storage = InMemoryObjectStorage({"uploads": "uploads-x7f3"})
    with pytest.raises(NotSupportedError):
        await storage.presigned_download_url("uploads", "a.txt", expires=timedelta(minutes=5))


@pytest.mark.asyncio
async def test_presigned_upload_url_raises_not_supported():
    storage = InMemoryObjectStorage({"uploads": "uploads-x7f3"})
    with pytest.raises(NotSupportedError):
        await storage.presigned_upload_url("uploads", "a.txt", expires=timedelta(minutes=5))


@pytest.mark.asyncio
async def test_multipart_rejects_undersized_non_final_part():
    storage = InMemoryObjectStorage({"uploads": "uploads-x7f3"})  # 既定のmin_part_size
    upload = await storage.begin_multipart("uploads", "a.bin")
    await upload.upload_part(1, b"too small")
    await upload.upload_part(2, b"second")

    with pytest.raises(ObjectStorageError, match="part 1"):
        await upload.complete()


@pytest.mark.asyncio
async def test_two_instances_do_not_share_data():
    a = InMemoryObjectStorage({"uploads": "bucket-a"})
    b = InMemoryObjectStorage({"uploads": "bucket-a"})

    await a.put("uploads", "x.txt", b"only in a")

    assert await b.exists("uploads", "x.txt") is False
```

- [ ] **Step 2: 失敗することを確認する**

Run: `cd core-toolkit && uv run pytest tests/test_object_storage_memory.py -v`
Expected: FAIL（`core_toolkit.object_storage.memory` が存在しない）

- [ ] **Step 3: 実装する**

`core-toolkit/src/core_toolkit/object_storage/memory.py`:

```python
"""InMemoryObjectStorage: ローカル開発・ユニットテスト用のObjectStorage実装。

プロセス外にはデータを持ち出さない。署名付きURLは``NotSupportedError``を
送出する（署名付きURLは実バックエンドに対してのみ検証すべきという方針の
ため。詳細は設計spec参照）。
"""

from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from core_toolkit.object_storage.base import (
    MAX_PART_NUMBER,
    MIN_PART_SIZE,
    MultipartUpload,
    NotSupportedError,
    ObjectInfo,
    ObjectNotFoundError,
    ObjectStorage,
    ObjectStorageError,
)


@dataclass
class _StoredObject:
    data: bytes
    content_type: str | None
    metadata: dict[str, str]
    last_modified: datetime


class InMemoryObjectStorage(ObjectStorage):
    """メモリ上の``dict``にオブジェクトを保持するObjectStorage実装。

    Args:
        buckets: 論理バケット名から物理バケット名へのマッピング。
        min_part_size: マルチパートで最後以外のパートに要求する最小サイズ。
            実バックエンド（既定5 MiB）と同じ制約をテストで検証しやすくする
            ため、テストではより小さい値に差し替えられる。
    """

    def __init__(self, buckets: Mapping[str, str], *, min_part_size: int = MIN_PART_SIZE) -> None:
        super().__init__(buckets)
        self._min_part_size = min_part_size
        self._objects: dict[tuple[str, str], _StoredObject] = {}

    async def put(self, bucket, key, data, *, content_type=None, metadata=None) -> ObjectInfo:
        physical = self._resolve_bucket(bucket)
        stored = _StoredObject(
            data=data, content_type=content_type, metadata=dict(metadata or {}),
            last_modified=datetime.now(UTC),
        )
        self._objects[(physical, key)] = stored
        return self._to_info(bucket, key, stored)

    async def get(self, bucket, key) -> bytes:
        return self._get_stored(bucket, key).data

    async def get_stream(self, bucket, key, *, chunk_size=64 * 1024) -> AsyncIterator[bytes]:
        data = self._get_stored(bucket, key).data
        for i in range(0, len(data), chunk_size):
            yield data[i : i + chunk_size]

    async def head(self, bucket, key) -> ObjectInfo:
        return self._to_info(bucket, key, self._get_stored(bucket, key))

    async def delete(self, bucket, key) -> None:
        physical = self._resolve_bucket(bucket)
        self._objects.pop((physical, key), None)

    async def list(self, bucket, prefix="") -> AsyncIterator[ObjectInfo]:
        physical = self._resolve_bucket(bucket)
        for (b, k), stored in list(self._objects.items()):
            if b == physical and k.startswith(prefix):
                yield self._to_info(bucket, k, stored)

    async def presigned_download_url(self, bucket, key, *, expires: timedelta) -> str:
        raise NotSupportedError(
            "InMemoryObjectStorage does not support presigned URLs. "
            "Test presigned URL behavior against a real backend."
        )

    async def presigned_upload_url(self, bucket, key, *, expires: timedelta, content_type=None) -> str:
        raise NotSupportedError(
            "InMemoryObjectStorage does not support presigned URLs. "
            "Test presigned URL behavior against a real backend."
        )

    async def begin_multipart(self, bucket, key, *, content_type=None, metadata=None) -> MultipartUpload:
        physical = self._resolve_bucket(bucket)
        return _InMemoryMultipartUpload(
            store=self, bucket=bucket, physical_bucket=physical, key=key,
            content_type=content_type, metadata=dict(metadata or {}),
            min_part_size=self._min_part_size,
        )

    def _get_stored(self, bucket: str, key: str) -> _StoredObject:
        physical = self._resolve_bucket(bucket)
        try:
            return self._objects[(physical, key)]
        except KeyError:
            raise ObjectNotFoundError(f"{bucket}/{key} not found") from None

    def _to_info(self, bucket: str, key: str, stored: _StoredObject) -> ObjectInfo:
        return ObjectInfo(
            bucket=bucket, key=key, size=len(stored.data), content_type=stored.content_type,
            etag=None, last_modified=stored.last_modified, metadata=dict(stored.metadata),
        )


class _InMemoryMultipartUpload(MultipartUpload):
    def __init__(
        self, *, store: InMemoryObjectStorage, bucket: str, physical_bucket: str, key: str,
        content_type: str | None, metadata: dict[str, str], min_part_size: int,
    ) -> None:
        self._store = store
        self._bucket = bucket
        self._key = key
        self._content_type = content_type
        self._metadata = metadata
        self._min_part_size = min_part_size
        self._parts: dict[int, bytes] = {}

    async def upload_part(self, part_number: int, data: bytes) -> None:
        if not (1 <= part_number <= MAX_PART_NUMBER):
            raise ObjectStorageError(
                f"part_number must be between 1 and {MAX_PART_NUMBER}, got {part_number}"
            )
        self._parts[part_number] = data

    async def complete(self) -> ObjectInfo:
        numbers = sorted(self._parts)
        for number in numbers[:-1]:
            if len(self._parts[number]) < self._min_part_size:
                raise ObjectStorageError(
                    f"part {number} is smaller than the minimum part size "
                    f"({self._min_part_size} bytes) for a non-final part"
                )
        body = b"".join(self._parts[n] for n in numbers)
        return await self._store.put(
            self._bucket, self._key, body,
            content_type=self._content_type, metadata=self._metadata,
        )

    async def abort(self) -> None:
        self._parts.clear()
```

`core-toolkit/src/core_toolkit/object_storage/__init__.py`（更新。既存importに追記）:

```python
"""ObjectStorage抽象化（S3/GCS/Azure Blob/インメモリの差し替え対応）。"""

from core_toolkit.object_storage.base import (
    BucketNotFoundError,
    MultipartUpload,
    NotSupportedError,
    ObjectInfo,
    ObjectNotFoundError,
    ObjectStorage,
    ObjectStorageError,
    PermissionDeniedError,
    UnknownBucketError,
)
from core_toolkit.object_storage.memory import InMemoryObjectStorage

__all__ = [
    "BucketNotFoundError",
    "InMemoryObjectStorage",
    "MultipartUpload",
    "NotSupportedError",
    "ObjectInfo",
    "ObjectNotFoundError",
    "ObjectStorage",
    "ObjectStorageError",
    "PermissionDeniedError",
    "UnknownBucketError",
]
```

- [ ] **Step 4: 成功することを確認する**

Run: `cd core-toolkit && uv run pytest tests/test_object_storage_memory.py -v`
Expected: PASS（契約テスト9件 + InMemory固有4件、計13件）

- [ ] **Step 5: lintを通す**

Run: `cd core-toolkit && uv run ruff check src/core_toolkit/object_storage tests/ && uv run ruff format --check src/core_toolkit/object_storage tests/`
Expected: エラーなし

- [ ] **Step 6: コミット**

```bash
git add core-toolkit/src/core_toolkit/object_storage/memory.py core-toolkit/src/core_toolkit/object_storage/__init__.py core-toolkit/tests/object_storage_contract.py core-toolkit/tests/test_object_storage_memory.py
git commit -m "feat(core-toolkit): add InMemoryObjectStorage and shared contract test suite"
```

---

## Task 3: S3アダプタ（`aiobotocore`、core-toolkit）

**Files:**
- Create: `core-toolkit/src/core_toolkit/object_storage/s3.py`
- Modify: `core-toolkit/pyproject.toml`（`s3` extraを追加）
- Test: `core-toolkit/tests/test_object_storage_s3.py`

**Interfaces:**
- Consumes: Task1/2の`ObjectStorage`/`MultipartUpload`/`ObjectInfo`/`ObjectNotFoundError`/`ObjectStorageError`/`PermissionDeniedError`、Task2の`tests.object_storage_contract.ObjectStorageContract`
- Produces: `core_toolkit.object_storage.s3.S3ObjectStorage(buckets, client)`。`client`は`async with session.create_client("s3", ...)`で得たエンター済みのaiobotocoreクライアント（Task6で使う）

- [ ] **Step 1: 失敗するテストを書く**

`core-toolkit/tests/test_object_storage_s3.py`:

```python
"""S3ObjectStorage（core_toolkit.object_storage.s3）の単体テスト。

aiobotocoreの実クライアントはネットワークI/Oを要するため、S3 REST APIの
挙動（get_paginatorが返す非同期ページャ、404/AccessDenied時に
``botocore.exceptions.ClientError``を送出すること、``generate_presigned_url``
が非同期であること等）を模した最小のフェイククライアントを使う。
"""

from datetime import timedelta

import pytest
from botocore.exceptions import ClientError

from core_toolkit.object_storage.base import (
    ObjectNotFoundError,
    ObjectStorageError,
    PermissionDeniedError,
)
from core_toolkit.object_storage.s3 import S3ObjectStorage
from tests.object_storage_contract import ObjectStorageContract


def _client_error(code: str, operation: str) -> ClientError:
    return ClientError({"Error": {"Code": code, "Message": code}}, operation)


class _FakeStreamingBody:
    def __init__(self, data: bytes) -> None:
        self._data = data
        self._pos = 0

    async def read(self, size: int = -1) -> bytes:
        if size is None or size < 0:
            chunk, self._pos = self._data[self._pos :], len(self._data)
        else:
            chunk = self._data[self._pos : self._pos + size]
            self._pos += len(chunk)
        return chunk

    async def __aenter__(self) -> "_FakeStreamingBody":
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        return None


class FakePaginator:
    def __init__(self, objects: dict[tuple[str, str], dict]) -> None:
        self._objects = objects

    def paginate(self, *, Bucket: str, Prefix: str = ""):
        items = [
            {"Key": key, "Size": len(obj["Body"]), "ETag": obj.get("ETag"), "LastModified": None}
            for (bucket, key), obj in self._objects.items()
            if bucket == Bucket and key.startswith(Prefix)
        ]

        async def _pages():
            yield {"Contents": items}

        return _pages()


class FakeS3Client:
    """S3 REST APIの挙動を模したフェイク。"""

    def __init__(self) -> None:
        self.objects: dict[tuple[str, str], dict] = {}
        self._multipart_parts: dict[str, dict[int, bytes]] = {}
        self._pending_multipart: dict[str, dict] = {}
        self._next_upload_id = 1

    async def put_object(self, *, Bucket, Key, Body, ContentType=None, Metadata=None):
        self.objects[(Bucket, Key)] = {
            "Body": Body, "ContentType": ContentType, "Metadata": Metadata or {}, "ETag": '"fake-etag"',
        }
        return {"ETag": '"fake-etag"'}

    async def get_object(self, *, Bucket, Key):
        try:
            obj = self.objects[(Bucket, Key)]
        except KeyError:
            raise _client_error("NoSuchKey", "GetObject") from None
        return {"Body": _FakeStreamingBody(obj["Body"])}

    async def head_object(self, *, Bucket, Key):
        try:
            obj = self.objects[(Bucket, Key)]
        except KeyError:
            raise _client_error("404", "HeadObject") from None
        return {
            "ContentLength": len(obj["Body"]), "ContentType": obj["ContentType"],
            "ETag": obj["ETag"], "LastModified": None, "Metadata": obj["Metadata"],
        }

    async def delete_object(self, *, Bucket, Key):
        self.objects.pop((Bucket, Key), None)
        return {}

    def get_paginator(self, operation_name: str) -> FakePaginator:
        assert operation_name == "list_objects_v2"
        return FakePaginator(self.objects)

    async def generate_presigned_url(self, client_method, *, Params, ExpiresIn):
        return f"https://fake-s3.example/{Params['Bucket']}/{Params['Key']}?method={client_method}&expires={ExpiresIn}"

    async def create_multipart_upload(self, *, Bucket, Key, ContentType=None, Metadata=None):
        upload_id = f"upload-{self._next_upload_id}"
        self._next_upload_id += 1
        self._multipart_parts[upload_id] = {}
        self._pending_multipart[upload_id] = {
            "Bucket": Bucket, "Key": Key, "ContentType": ContentType, "Metadata": Metadata or {},
        }
        return {"UploadId": upload_id}

    async def upload_part(self, *, Bucket, Key, UploadId, PartNumber, Body):
        self._multipart_parts[UploadId][PartNumber] = Body
        return {"ETag": f'"part-{PartNumber}"'}

    async def complete_multipart_upload(self, *, Bucket, Key, UploadId, MultipartUpload):
        parts = self._multipart_parts.pop(UploadId)
        pending = self._pending_multipart.pop(UploadId)
        ordered = sorted(MultipartUpload["Parts"], key=lambda p: p["PartNumber"])
        body = b"".join(parts[p["PartNumber"]] for p in ordered)
        self.objects[(Bucket, Key)] = {
            "Body": body, "ContentType": pending["ContentType"], "Metadata": pending["Metadata"],
            "ETag": '"multipart-etag"',
        }
        return {"ETag": '"multipart-etag"'}

    async def abort_multipart_upload(self, *, Bucket, Key, UploadId):
        self._multipart_parts.pop(UploadId, None)
        self._pending_multipart.pop(UploadId, None)


class TestS3ObjectStorageContract(ObjectStorageContract):
    @pytest.fixture
    def storage(self) -> S3ObjectStorage:
        return S3ObjectStorage({"uploads": "uploads-x7f3"}, FakeS3Client())

    @pytest.fixture
    def logical_bucket(self) -> str:
        return "uploads"


@pytest.mark.asyncio
async def test_get_missing_object_raises_object_not_found():
    storage = S3ObjectStorage({"uploads": "uploads-x7f3"}, FakeS3Client())
    with pytest.raises(ObjectNotFoundError):
        await storage.get("uploads", "missing.txt")


@pytest.mark.asyncio
async def test_delete_missing_object_does_not_raise():
    storage = S3ObjectStorage({"uploads": "uploads-x7f3"}, FakeS3Client())
    await storage.delete("uploads", "missing.txt")  # 例外を出さない


@pytest.mark.asyncio
async def test_generic_client_error_is_wrapped_as_object_storage_error():
    class FailingClient(FakeS3Client):
        async def get_object(self, *, Bucket, Key):
            raise _client_error("InternalError", "GetObject")

    storage = S3ObjectStorage({"uploads": "uploads-x7f3"}, FailingClient())
    with pytest.raises(ObjectStorageError):
        await storage.get("uploads", "a.txt")


@pytest.mark.asyncio
async def test_permission_denied_error_is_mapped():
    class DeniedClient(FakeS3Client):
        async def get_object(self, *, Bucket, Key):
            raise _client_error("AccessDenied", "GetObject")

    storage = S3ObjectStorage({"uploads": "uploads-x7f3"}, DeniedClient())
    with pytest.raises(PermissionDeniedError):
        await storage.get("uploads", "a.txt")


@pytest.mark.asyncio
async def test_presigned_download_url_uses_physical_bucket_name():
    storage = S3ObjectStorage({"uploads": "uploads-x7f3"}, FakeS3Client())
    url = await storage.presigned_download_url("uploads", "a.txt", expires=timedelta(minutes=5))
    assert "uploads-x7f3" in url
    assert "a.txt" in url
```

- [ ] **Step 2: 失敗することを確認する**

Run: `cd core-toolkit && uv run pytest tests/test_object_storage_s3.py -v`
Expected: FAIL（`core_toolkit.object_storage.s3` および `aiobotocore`/`botocore` が無い）

- [ ] **Step 3: pyproject.tomlにs3 extraを追加する**

`core-toolkit/pyproject.toml`の`[project.optional-dependencies]`に追記（既存の`mysql`ブロックの後）:

```toml
s3 = [
    "aiobotocore>=3.0",
    "botocore",
]
```

Run: `cd core-toolkit && uv sync --all-extras`

- [ ] **Step 4: 実装する**

`core-toolkit/src/core_toolkit/object_storage/s3.py`:

```python
"""aiobotocoreベースのS3 ObjectStorageアダプタ。

利用には ``core-toolkit[s3]`` extraのインストールが必要。
"""

from collections.abc import AsyncIterator, Mapping
from datetime import timedelta
from typing import Any

from botocore.exceptions import ClientError

from core_toolkit.object_storage.base import (
    MultipartUpload,
    ObjectInfo,
    ObjectNotFoundError,
    ObjectStorage,
    ObjectStorageError,
    PermissionDeniedError,
)

_NOT_FOUND_CODES = {"404", "NoSuchKey", "NotFound"}
_PERMISSION_DENIED_CODES = {"403", "AccessDenied"}


def _raise_for_client_error(error: ClientError, *, bucket: str, key: str) -> None:
    code = error.response.get("Error", {}).get("Code", "")
    if code in _NOT_FOUND_CODES:
        raise ObjectNotFoundError(f"{bucket}/{key} not found") from error
    if code in _PERMISSION_DENIED_CODES:
        raise PermissionDeniedError(f"permission denied for {bucket}/{key}") from error
    raise ObjectStorageError(f"S3 operation failed for {bucket}/{key}: {error}") from error


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

    async def put(self, bucket, key, data, *, content_type=None, metadata=None) -> ObjectInfo:
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
            bucket=bucket, key=key, size=len(data), content_type=content_type,
            etag=resp.get("ETag"), last_modified=None, metadata=dict(metadata or {}),
        )

    async def get(self, bucket, key) -> bytes:
        physical = self._resolve_bucket(bucket)
        try:
            resp = await self._client.get_object(Bucket=physical, Key=key)
        except ClientError as error:
            _raise_for_client_error(error, bucket=bucket, key=key)
            raise
        async with resp["Body"] as stream:
            return await stream.read()

    async def get_stream(self, bucket, key, *, chunk_size=64 * 1024) -> AsyncIterator[bytes]:
        physical = self._resolve_bucket(bucket)
        try:
            resp = await self._client.get_object(Bucket=physical, Key=key)
        except ClientError as error:
            _raise_for_client_error(error, bucket=bucket, key=key)
            raise
        async with resp["Body"] as stream:
            while chunk := await stream.read(chunk_size):
                yield chunk

    async def head(self, bucket, key) -> ObjectInfo:
        physical = self._resolve_bucket(bucket)
        try:
            resp = await self._client.head_object(Bucket=physical, Key=key)
        except ClientError as error:
            _raise_for_client_error(error, bucket=bucket, key=key)
            raise
        return ObjectInfo(
            bucket=bucket, key=key, size=resp["ContentLength"],
            content_type=resp.get("ContentType"), etag=resp.get("ETag"),
            last_modified=resp.get("LastModified"), metadata=resp.get("Metadata", {}),
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
        async for page in paginator.paginate(Bucket=physical, Prefix=prefix):
            for item in page.get("Contents", []):
                yield ObjectInfo(
                    bucket=bucket, key=item["Key"], size=item["Size"],
                    content_type=None, etag=item.get("ETag"),
                    last_modified=item.get("LastModified"), metadata={},
                )

    async def presigned_download_url(self, bucket, key, *, expires: timedelta) -> str:
        physical = self._resolve_bucket(bucket)
        return await self._client.generate_presigned_url(
            "get_object", Params={"Bucket": physical, "Key": key},
            ExpiresIn=int(expires.total_seconds()),
        )

    async def presigned_upload_url(self, bucket, key, *, expires: timedelta, content_type=None) -> str:
        physical = self._resolve_bucket(bucket)
        params: dict[str, Any] = {"Bucket": physical, "Key": key}
        if content_type is not None:
            params["ContentType"] = content_type
        return await self._client.generate_presigned_url(
            "put_object", Params=params, ExpiresIn=int(expires.total_seconds()),
        )

    async def begin_multipart(self, bucket, key, *, content_type=None, metadata=None) -> MultipartUpload:
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
            raise
        return _S3MultipartUpload(
            client=self._client, bucket=bucket, physical_bucket=physical, key=key,
            upload_id=resp["UploadId"],
        )


class _S3MultipartUpload(MultipartUpload):
    def __init__(self, *, client: Any, bucket: str, physical_bucket: str, key: str, upload_id: str) -> None:
        self._client = client
        self._bucket = bucket
        self._physical_bucket = physical_bucket
        self._key = key
        self._upload_id = upload_id
        self._parts: dict[int, str] = {}

    async def upload_part(self, part_number: int, data: bytes) -> None:
        try:
            resp = await self._client.upload_part(
                Bucket=self._physical_bucket, Key=self._key, UploadId=self._upload_id,
                PartNumber=part_number, Body=data,
            )
        except ClientError as error:
            _raise_for_client_error(error, bucket=self._bucket, key=self._key)
            raise
        self._parts[part_number] = resp["ETag"]

    async def complete(self) -> ObjectInfo:
        parts = [
            {"PartNumber": number, "ETag": etag}
            for number, etag in sorted(self._parts.items())
        ]
        try:
            await self._client.complete_multipart_upload(
                Bucket=self._physical_bucket, Key=self._key, UploadId=self._upload_id,
                MultipartUpload={"Parts": parts},
            )
            head = await self._client.head_object(Bucket=self._physical_bucket, Key=self._key)
        except ClientError as error:
            _raise_for_client_error(error, bucket=self._bucket, key=self._key)
            raise
        return ObjectInfo(
            bucket=self._bucket, key=self._key, size=head["ContentLength"],
            content_type=head.get("ContentType"), etag=head.get("ETag"),
            last_modified=head.get("LastModified"), metadata=head.get("Metadata", {}),
        )

    async def abort(self) -> None:
        try:
            await self._client.abort_multipart_upload(
                Bucket=self._physical_bucket, Key=self._key, UploadId=self._upload_id,
            )
        except ClientError as error:
            _raise_for_client_error(error, bucket=self._bucket, key=self._key)
```

- [ ] **Step 5: 成功することを確認する**

Run: `cd core-toolkit && uv run pytest tests/test_object_storage_s3.py -v`
Expected: PASS（契約テスト9件 + S3固有5件、計14件）

- [ ] **Step 6: lintを通す**

Run: `cd core-toolkit && uv run ruff check src/core_toolkit/object_storage tests/ && uv run ruff format --check src/core_toolkit/object_storage tests/`
Expected: エラーなし

- [ ] **Step 7: コミット**

```bash
git add core-toolkit/pyproject.toml core-toolkit/src/core_toolkit/object_storage/s3.py core-toolkit/tests/test_object_storage_s3.py core-toolkit/uv.lock
git commit -m "feat(core-toolkit): add S3ObjectStorage adapter (aiobotocore)"
```

---

## Task 4: GCSアダプタ（`gcloud-aio-storage`、core-toolkit）

**Files:**
- Create: `core-toolkit/src/core_toolkit/object_storage/gcs.py`
- Modify: `core-toolkit/pyproject.toml`（`gcs` extraを追加）
- Test: `core-toolkit/tests/test_object_storage_gcs.py`

**Interfaces:**
- Consumes: Task1/2の`ObjectStorage`/`MultipartUpload`/`ObjectInfo`/`ObjectNotFoundError`/`ObjectStorageError`、Task2の`ObjectStorageContract`
- Produces: `core_toolkit.object_storage.gcs.GcsObjectStorage(buckets, client)`。`client`は`async with Storage(...)`で得たエンター済みの`gcloud.aio.storage.Storage`（Task6で使う）

**注記（設計spec由来）**: GCSの署名付きURL生成（`Blob.get_signed_url`）はサービスアカウント鍵によるPEM署名またはIAM APIを要し、フェイクでは正しく検証できない。そのためGCSの単体テストでは署名付きURLをテストしない（実バックエンドに対する検証に委ねる、設計spec「テスト方針」参照）。S3のテストで署名付きURLをテストしているのと非対称に見えるが、意図的な判断であるため、レビュー時に指摘不要。

- [ ] **Step 1: 失敗するテストを書く**

`core-toolkit/tests/test_object_storage_gcs.py`:

```python
"""GcsObjectStorage（core_toolkit.object_storage.gcs）の単体テスト。

gcloud-aio-storageの実クライアントはネットワークI/Oを要するため、GCS JSON
APIの挙動（オブジェクトリソースのフィールド、エラー時に
``aiohttp.ClientResponseError``を送出すること、composeによる結合等）を
模した最小のフェイククライアントを使う。署名付きURL生成は実際の暗号署名
（サービスアカウント鍵またはIAM API）を要するため、ここではテストせず
実バックエンドに対する検証に委ねる（設計spec参照）。
"""

from datetime import UTC, datetime

import aiohttp
import pytest

from core_toolkit.object_storage.base import ObjectNotFoundError, ObjectStorageError
from core_toolkit.object_storage.gcs import GcsObjectStorage
from tests.object_storage_contract import ObjectStorageContract


def _response_error(status: int) -> aiohttp.ClientResponseError:
    request_info = aiohttp.RequestInfo(
        url=aiohttp.client.URL("http://fake"), method="GET", headers={}, real_url=aiohttp.client.URL("http://fake"),
    )
    return aiohttp.ClientResponseError(request_info=request_info, history=(), status=status, message="error")


class _FakeStreamResponse:
    def __init__(self, data: bytes) -> None:
        self._data = data
        self._pos = 0

    async def read(self, size: int = -1) -> bytes:
        if size is None or size < 0:
            chunk, self._pos = self._data[self._pos :], len(self._data)
        else:
            chunk = self._data[self._pos : self._pos + size]
            self._pos += len(chunk)
        return chunk

    async def __aenter__(self) -> "_FakeStreamResponse":
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        return None


class FakeGcsStorage:
    """GCS JSON APIの挙動を模したフェイク。"""

    def __init__(self) -> None:
        self.objects: dict[tuple[str, str], dict] = {}

    def _resource(self, bucket: str, name: str) -> dict:
        obj = self.objects[(bucket, name)]
        return {
            "name": name, "bucket": bucket, "size": str(len(obj["data"])),
            "contentType": obj["content_type"], "etag": "fake-etag",
            "updated": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "metadata": obj["metadata"],
        }

    async def upload(self, bucket, object_name, file_data, *, content_type=None, metadata=None, **_kwargs):
        self.objects[(bucket, object_name)] = {
            "data": file_data, "content_type": content_type, "metadata": dict(metadata or {}),
        }
        return self._resource(bucket, object_name)

    async def download(self, bucket, object_name, **_kwargs) -> bytes:
        try:
            return self.objects[(bucket, object_name)]["data"]
        except KeyError:
            raise _response_error(404) from None

    async def download_stream(self, bucket, object_name, **_kwargs):
        data = await self.download(bucket, object_name)
        return _FakeStreamResponse(data)

    async def download_metadata(self, bucket, object_name, **_kwargs) -> dict:
        try:
            return self._resource(bucket, object_name)
        except KeyError:
            raise _response_error(404) from None

    async def delete(self, bucket, object_name, **_kwargs) -> str:
        try:
            del self.objects[(bucket, object_name)]
        except KeyError:
            raise _response_error(404) from None
        return ""

    async def list_objects(self, bucket, *, params=None, **_kwargs) -> dict:
        prefix = (params or {}).get("prefix", "")
        items = [
            self._resource(b, name) for (b, name) in self.objects
            if b == bucket and name.startswith(prefix)
        ]
        return {"items": items} if items else {}

    async def compose(self, bucket, object_name, source_object_names, *, content_type=None, **_kwargs) -> dict:
        body = b"".join(self.objects[(bucket, name)]["data"] for name in source_object_names)
        self.objects[(bucket, object_name)] = {"data": body, "content_type": content_type, "metadata": {}}
        return self._resource(bucket, object_name)


class TestGcsObjectStorageContract(ObjectStorageContract):
    @pytest.fixture
    def storage(self) -> GcsObjectStorage:
        return GcsObjectStorage({"uploads": "uploads-x7f3"}, FakeGcsStorage())

    @pytest.fixture
    def logical_bucket(self) -> str:
        return "uploads"


@pytest.mark.asyncio
async def test_get_missing_object_raises_object_not_found():
    storage = GcsObjectStorage({"uploads": "uploads-x7f3"}, FakeGcsStorage())
    with pytest.raises(ObjectNotFoundError):
        await storage.get("uploads", "missing.txt")


@pytest.mark.asyncio
async def test_delete_missing_object_does_not_raise():
    storage = GcsObjectStorage({"uploads": "uploads-x7f3"}, FakeGcsStorage())
    await storage.delete("uploads", "missing.txt")


@pytest.mark.asyncio
async def test_multipart_complete_composes_more_than_32_parts():
    storage = GcsObjectStorage({"uploads": "uploads-x7f3"}, FakeGcsStorage())
    upload = await storage.begin_multipart("uploads", "big.bin")
    for i in range(1, 35):
        await upload.upload_part(i, bytes([i % 256]))

    info = await upload.complete()

    assert info.size == 34
    assert await storage.get("uploads", "big.bin") == bytes(i % 256 for i in range(1, 35))


@pytest.mark.asyncio
async def test_generic_response_error_is_wrapped_as_object_storage_error():
    class FailingStorage(FakeGcsStorage):
        async def download(self, bucket, object_name, **_kwargs):
            raise _response_error(500)

    storage = GcsObjectStorage({"uploads": "uploads-x7f3"}, FailingStorage())
    with pytest.raises(ObjectStorageError):
        await storage.get("uploads", "a.txt")
```

- [ ] **Step 2: 失敗することを確認する**

Run: `cd core-toolkit && uv run pytest tests/test_object_storage_gcs.py -v`
Expected: FAIL（`core_toolkit.object_storage.gcs` および `gcloud-aio-storage` が無い）

- [ ] **Step 3: pyproject.tomlにgcs extraを追加する**

`core-toolkit/pyproject.toml`の`[project.optional-dependencies]`に追記:

```toml
gcs = [
    "gcloud-aio-storage>=9.0",
    "aiohttp>=3.14.1",
]
```

Run: `cd core-toolkit && uv sync --all-extras`

- [ ] **Step 4: 実装する**

`core-toolkit/src/core_toolkit/object_storage/gcs.py`:

```python
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


def _raise_for_response_error(error: aiohttp.ClientResponseError, *, bucket: str, key: str) -> None:
    if error.status == 404:
        raise ObjectNotFoundError(f"{bucket}/{key} not found") from error
    if error.status == 403:
        raise PermissionDeniedError(f"permission denied for {bucket}/{key}") from error
    raise ObjectStorageError(f"GCS operation failed for {bucket}/{key}: {error}") from error


def _parse_gcs_object(bucket: str, resource: dict[str, Any]) -> ObjectInfo:
    updated = resource.get("updated")
    return ObjectInfo(
        bucket=bucket,
        key=resource["name"],
        size=int(resource.get("size", 0)),
        content_type=resource.get("contentType"),
        etag=resource.get("etag"),
        last_modified=datetime.fromisoformat(updated.replace("Z", "+00:00")) if updated else None,
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

    async def put(self, bucket, key, data, *, content_type=None, metadata=None) -> ObjectInfo:
        physical = self._resolve_bucket(bucket)
        try:
            resource = await self._client.upload(
                physical, key, data, content_type=content_type, metadata=dict(metadata or {}),
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

    async def get_stream(self, bucket, key, *, chunk_size=64 * 1024) -> AsyncIterator[bytes]:
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
        return await blob.get_signed_url(int(expires.total_seconds()), http_method="GET")

    async def presigned_upload_url(self, bucket, key, *, expires: timedelta, content_type=None) -> str:
        physical = self._resolve_bucket(bucket)
        blob = Bucket(self._client, physical).new_blob(key)
        headers = {"content-type": content_type} if content_type else None
        return await blob.get_signed_url(
            int(expires.total_seconds()), http_method="PUT", headers=headers,
        )

    async def begin_multipart(self, bucket, key, *, content_type=None, metadata=None) -> MultipartUpload:
        physical = self._resolve_bucket(bucket)
        return _GcsMultipartUpload(
            client=self._client, bucket=bucket, physical_bucket=physical, key=key,
            content_type=content_type, metadata=dict(metadata or {}),
        )


class _GcsMultipartUpload(MultipartUpload):
    """GCSには真のマルチパートAPIが無いため、パートを一時オブジェクトとして
    アップロードし、``complete()``で``compose``して結合する。``compose``は
    1回につき最大32個までしか結合できないため、超える場合は段階的に結合する。
    """

    def __init__(
        self, *, client: Storage, bucket: str, physical_bucket: str, key: str,
        content_type: str | None, metadata: dict[str, str],
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
                staging_name = f"{_MULTIPART_TMP_PREFIX}{self._key}.staging{round_index}"
                round_index += 1
                await self._client.compose(
                    self._physical_bucket, staging_name, [current, *batch],
                    content_type=self._content_type,
                )
                for name in [current, *batch]:
                    await self._client.delete(self._physical_bucket, name)
                current = staging_name

            resource = await self._client.compose(
                self._physical_bucket, self._key, [current], content_type=self._content_type,
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
```

- [ ] **Step 5: 成功することを確認する**

Run: `cd core-toolkit && uv run pytest tests/test_object_storage_gcs.py -v`
Expected: PASS（契約テスト9件 + GCS固有4件、計13件）

- [ ] **Step 6: lintを通す**

Run: `cd core-toolkit && uv run ruff check src/core_toolkit/object_storage tests/ && uv run ruff format --check src/core_toolkit/object_storage tests/`
Expected: エラーなし

- [ ] **Step 7: コミット**

```bash
git add core-toolkit/pyproject.toml core-toolkit/src/core_toolkit/object_storage/gcs.py core-toolkit/tests/test_object_storage_gcs.py core-toolkit/uv.lock
git commit -m "feat(core-toolkit): add GcsObjectStorage adapter (gcloud-aio-storage)"
```

---

## Task 5: Azure Blobアダプタ（`azure-storage-blob[aio]`、core-toolkit）

**Files:**
- Create: `core-toolkit/src/core_toolkit/object_storage/azure.py`
- Modify: `core-toolkit/pyproject.toml`（`azure` extraを追加）
- Test: `core-toolkit/tests/test_object_storage_azure.py`

**Interfaces:**
- Consumes: Task1/2の`ObjectStorage`/`MultipartUpload`/`ObjectInfo`/`NotSupportedError`/`ObjectNotFoundError`/`ObjectStorageError`、Task2の`ObjectStorageContract`
- Produces: `core_toolkit.object_storage.azure.AzureObjectStorage(buckets, client, *, account_key=None)`。`client`は`async with BlobServiceClient(...)`で得たエンター済みのクライアント（Task6で使う）。`account_key`が`None`の場合、署名付きURLは`NotSupportedError`

**注記**: `BlobClient.credential`はSDK内部で`SharedKeyCredentialPolicy`にラップされ、生のアカウントキー文字列を取り出せない。そのため署名付きURL生成用に生の`account_key`を別途コンストラクタで受け取る。

- [ ] **Step 1: 失敗するテストを書く**

`core-toolkit/tests/test_object_storage_azure.py`:

```python
"""AzureObjectStorage（core_toolkit.object_storage.azure）の単体テスト。

azure-storage-blob(.aio)の実クライアントはネットワークI/Oを要するため、
Blob Service REST APIの挙動（``ResourceNotFoundError``、``stage_block``/
``commit_block_list``によるブロックベースのマルチパート等）を模した
最小のフェイククライアントを使う。``generate_blob_sas``は純粋なHMAC署名
計算のみでネットワークI/Oを要さないため、署名付きURL生成は実際に呼び出して
検証する。
"""

import base64
from datetime import timedelta

import pytest
from azure.core.exceptions import HttpResponseError, ResourceNotFoundError

from core_toolkit.object_storage.azure import AzureObjectStorage
from core_toolkit.object_storage.base import NotSupportedError, ObjectNotFoundError, ObjectStorageError
from tests.object_storage_contract import ObjectStorageContract


class _ContentSettings:
    def __init__(self, content_type):
        self.content_type = content_type


class _FakeBlobProperties:
    def __init__(self, size, content_type, metadata, name=None):
        self.size = size
        self.content_settings = _ContentSettings(content_type)
        self.etag = "fake-etag"
        self.last_modified = None
        self.metadata = metadata
        self.name = name


class _FakeDownloader:
    def __init__(self, data: bytes) -> None:
        self._data = data

    async def readall(self) -> bytes:
        return self._data

    async def chunks(self):
        chunk_size = 16
        for i in range(0, len(self._data), chunk_size):
            yield self._data[i : i + chunk_size]


class FakeBlobClient:
    def __init__(self, store: dict, container: str, blob_name: str) -> None:
        self._store = store
        self._container = container
        self._blob_name = blob_name
        self.url = f"https://fake.blob.core.windows.net/{container}/{blob_name}"

    def _key(self):
        return (self._container, self._blob_name)

    async def upload_blob(self, data, *, overwrite=True, metadata=None, content_settings=None, **_kwargs):
        content_type = content_settings.content_type if content_settings else None
        self._store[self._key()] = {"data": data, "content_type": content_type, "metadata": dict(metadata or {}), "blocks": {}}
        return {}

    async def download_blob(self, **_kwargs):
        try:
            obj = self._store[self._key()]
        except KeyError:
            raise ResourceNotFoundError("blob not found") from None
        return _FakeDownloader(obj["data"])

    async def get_blob_properties(self, **_kwargs):
        try:
            obj = self._store[self._key()]
        except KeyError:
            raise ResourceNotFoundError("blob not found") from None
        return _FakeBlobProperties(len(obj["data"]), obj["content_type"], obj["metadata"])

    async def delete_blob(self, **_kwargs):
        try:
            del self._store[self._key()]
        except KeyError:
            raise ResourceNotFoundError("blob not found") from None

    async def stage_block(self, block_id: str, data: bytes, **_kwargs):
        obj = self._store.setdefault(self._key(), {"data": b"", "content_type": None, "metadata": {}, "blocks": {}})
        obj["blocks"][block_id] = data
        return {}

    async def commit_block_list(self, block_list, *, content_settings=None, metadata=None, **_kwargs):
        obj = self._store[self._key()]
        body = b"".join(obj["blocks"][block.id] for block in block_list)
        content_type = content_settings.content_type if content_settings else None
        self._store[self._key()] = {"data": body, "content_type": content_type, "metadata": dict(metadata or {}), "blocks": {}}
        return {}


class FakeContainerClient:
    def __init__(self, store: dict, container: str) -> None:
        self._store = store
        self._container = container

    def get_blob_client(self, blob_name: str) -> FakeBlobClient:
        return FakeBlobClient(self._store, self._container, blob_name)

    async def list_blobs(self, name_starts_with: str | None = None, **_kwargs):
        prefix = name_starts_with or ""
        for (container, name), obj in list(self._store.items()):
            if container == self._container and name.startswith(prefix):
                yield _FakeBlobProperties(len(obj["data"]), obj["content_type"], obj["metadata"], name=name)


class FakeBlobServiceClient:
    """Blob Service REST APIの挙動を模したフェイク。"""

    account_name = "fakeaccount"

    def __init__(self) -> None:
        self._store: dict[tuple[str, str], dict] = {}

    def get_container_client(self, container: str) -> FakeContainerClient:
        return FakeContainerClient(self._store, container)


class TestAzureObjectStorageContract(ObjectStorageContract):
    @pytest.fixture
    def storage(self) -> AzureObjectStorage:
        return AzureObjectStorage({"uploads": "uploads-x7f3"}, FakeBlobServiceClient())

    @pytest.fixture
    def logical_bucket(self) -> str:
        return "uploads"


@pytest.mark.asyncio
async def test_get_missing_blob_raises_object_not_found():
    storage = AzureObjectStorage({"uploads": "uploads-x7f3"}, FakeBlobServiceClient())
    with pytest.raises(ObjectNotFoundError):
        await storage.get("uploads", "missing.txt")


@pytest.mark.asyncio
async def test_delete_missing_blob_does_not_raise():
    storage = AzureObjectStorage({"uploads": "uploads-x7f3"}, FakeBlobServiceClient())
    await storage.delete("uploads", "missing.txt")


@pytest.mark.asyncio
async def test_presigned_download_url_raises_without_account_key():
    storage = AzureObjectStorage({"uploads": "uploads-x7f3"}, FakeBlobServiceClient())
    with pytest.raises(NotSupportedError):
        await storage.presigned_download_url("uploads", "a.txt", expires=timedelta(minutes=5))


@pytest.mark.asyncio
async def test_presigned_download_url_returns_url_with_sas_signature():
    account_key = base64.b64encode(b"x" * 32).decode()
    storage = AzureObjectStorage(
        {"uploads": "uploads-x7f3"}, FakeBlobServiceClient(), account_key=account_key,
    )

    url = await storage.presigned_download_url("uploads", "a.txt", expires=timedelta(minutes=5))

    assert url.startswith("https://fake.blob.core.windows.net/uploads-x7f3/a.txt?")
    assert "sig=" in url


@pytest.mark.asyncio
async def test_generic_http_response_error_is_wrapped_as_object_storage_error():
    class FailingBlobClient(FakeBlobClient):
        async def download_blob(self, **_kwargs):
            raise HttpResponseError("boom")

    class FailingContainerClient(FakeContainerClient):
        def get_blob_client(self, blob_name):
            return FailingBlobClient(self._store, self._container, blob_name)

    class FailingBlobServiceClient(FakeBlobServiceClient):
        def get_container_client(self, container):
            return FailingContainerClient(self._store, container)

    storage = AzureObjectStorage({"uploads": "uploads-x7f3"}, FailingBlobServiceClient())
    with pytest.raises(ObjectStorageError):
        await storage.get("uploads", "a.txt")
```

- [ ] **Step 2: 失敗することを確認する**

Run: `cd core-toolkit && uv run pytest tests/test_object_storage_azure.py -v`
Expected: FAIL（`core_toolkit.object_storage.azure` および `azure-storage-blob` が無い）

- [ ] **Step 3: pyproject.tomlにazure extraを追加する**

`core-toolkit/pyproject.toml`の`[project.optional-dependencies]`に追記:

```toml
azure = [
    "azure-storage-blob[aio]>=12.19",
    "azure-core>=1.30",
]
```

Run: `cd core-toolkit && uv sync --all-extras`

- [ ] **Step 4: 実装する**

`core-toolkit/src/core_toolkit/object_storage/azure.py`:

```python
"""azure-storage-blob(.aio)ベースのAzure Blob ObjectStorageアダプタ。

利用には ``core-toolkit[azure]`` extraのインストールが必要。
"""

import base64
from collections.abc import AsyncIterator, Mapping
from datetime import UTC, datetime, timedelta

from azure.core.exceptions import HttpResponseError, ResourceNotFoundError
from azure.storage.blob import BlobBlock, BlobSasPermissions, ContentSettings, generate_blob_sas
from azure.storage.blob.aio import BlobServiceClient

from core_toolkit.object_storage.base import (
    MultipartUpload,
    NotSupportedError,
    ObjectInfo,
    ObjectNotFoundError,
    ObjectStorage,
    ObjectStorageError,
    PermissionDeniedError,
)


def _raise_for_http_error(error: HttpResponseError, *, bucket: str, key: str) -> None:
    if getattr(error, "status_code", None) == 403:
        raise PermissionDeniedError(f"permission denied for {bucket}/{key}") from error
    raise ObjectStorageError(f"Azure Blob operation failed for {bucket}/{key}: {error}") from error


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
        self, buckets: Mapping[str, str], client: BlobServiceClient, *, account_key: str | None = None
    ) -> None:
        super().__init__(buckets)
        self._client = client
        self._account_key = account_key

    def _blob_client(self, bucket: str, key: str):
        physical = self._resolve_bucket(bucket)
        return self._client.get_container_client(physical).get_blob_client(key)

    async def put(self, bucket, key, data, *, content_type=None, metadata=None) -> ObjectInfo:
        blob = self._blob_client(bucket, key)
        settings = ContentSettings(content_type=content_type) if content_type else None
        try:
            await blob.upload_blob(data, overwrite=True, metadata=dict(metadata or {}), content_settings=settings)
        except HttpResponseError as error:
            _raise_for_http_error(error, bucket=bucket, key=key)
        return ObjectInfo(
            bucket=bucket, key=key, size=len(data), content_type=content_type,
            etag=None, last_modified=None, metadata=dict(metadata or {}),
        )

    async def get(self, bucket, key) -> bytes:
        blob = self._blob_client(bucket, key)
        try:
            downloader = await blob.download_blob()
            return await downloader.readall()
        except ResourceNotFoundError as error:
            raise ObjectNotFoundError(f"{bucket}/{key} not found") from error
        except HttpResponseError as error:
            _raise_for_http_error(error, bucket=bucket, key=key)
            raise

    async def get_stream(self, bucket, key, *, chunk_size=64 * 1024) -> AsyncIterator[bytes]:
        blob = self._blob_client(bucket, key)
        try:
            downloader = await blob.download_blob()
        except ResourceNotFoundError as error:
            raise ObjectNotFoundError(f"{bucket}/{key} not found") from error
        except HttpResponseError as error:
            _raise_for_http_error(error, bucket=bucket, key=key)
            raise
        async for chunk in downloader.chunks():
            yield chunk

    async def head(self, bucket, key) -> ObjectInfo:
        blob = self._blob_client(bucket, key)
        try:
            props = await blob.get_blob_properties()
        except ResourceNotFoundError as error:
            raise ObjectNotFoundError(f"{bucket}/{key} not found") from error
        except HttpResponseError as error:
            _raise_for_http_error(error, bucket=bucket, key=key)
            raise
        return ObjectInfo(
            bucket=bucket, key=key, size=props.size,
            content_type=props.content_settings.content_type if props.content_settings else None,
            etag=props.etag, last_modified=props.last_modified, metadata=dict(props.metadata or {}),
        )

    async def delete(self, bucket, key) -> None:
        blob = self._blob_client(bucket, key)
        try:
            await blob.delete_blob()
        except ResourceNotFoundError:
            return  # Azureは404相当を返すため、存在しない場合は成功扱いにして冪等化する
        except HttpResponseError as error:
            _raise_for_http_error(error, bucket=bucket, key=key)

    async def list(self, bucket, prefix="") -> AsyncIterator[ObjectInfo]:
        physical = self._resolve_bucket(bucket)
        container = self._client.get_container_client(physical)
        async for props in container.list_blobs(name_starts_with=prefix):
            yield ObjectInfo(
                bucket=bucket, key=props.name, size=props.size,
                content_type=props.content_settings.content_type if props.content_settings else None,
                etag=props.etag, last_modified=props.last_modified, metadata=dict(props.metadata or {}),
            )

    async def presigned_download_url(self, bucket, key, *, expires: timedelta) -> str:
        return await self._generate_sas_url(bucket, key, expires=expires, permission=BlobSasPermissions(read=True))

    async def presigned_upload_url(self, bucket, key, *, expires: timedelta, content_type=None) -> str:
        return await self._generate_sas_url(
            bucket, key, expires=expires, permission=BlobSasPermissions(write=True, create=True),
        )

    async def _generate_sas_url(self, bucket: str, key: str, *, expires: timedelta, permission: BlobSasPermissions) -> str:
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

    async def begin_multipart(self, bucket, key, *, content_type=None, metadata=None) -> MultipartUpload:
        blob = self._blob_client(bucket, key)
        return _AzureMultipartUpload(
            blob=blob, bucket=bucket, key=key, content_type=content_type, metadata=dict(metadata or {}),
        )


class _AzureMultipartUpload(MultipartUpload):
    """Azureの``stage_block``/``commit_block_list``をラップする。

    未コミットのブロックは``abort()``を呼ばなくても7日で自動破棄されるため、
    ``abort()``は追加の後始末を行わない。
    """

    def __init__(self, *, blob, bucket: str, key: str, content_type: str | None, metadata: dict[str, str]) -> None:
        self._blob = blob
        self._bucket = bucket
        self._key = key
        self._content_type = content_type
        self._metadata = metadata
        self._block_ids: dict[int, str] = {}

    async def upload_part(self, part_number: int, data: bytes) -> None:
        block_id = base64.b64encode(f"{part_number:08d}".encode()).decode()
        try:
            await self._blob.stage_block(block_id, data)
        except HttpResponseError as error:
            _raise_for_http_error(error, bucket=self._bucket, key=self._key)
        self._block_ids[part_number] = block_id

    async def complete(self) -> ObjectInfo:
        block_list = [BlobBlock(block_id=self._block_ids[n]) for n in sorted(self._block_ids)]
        settings = ContentSettings(content_type=self._content_type) if self._content_type else None
        try:
            await self._blob.commit_block_list(block_list, content_settings=settings, metadata=self._metadata)
            props = await self._blob.get_blob_properties()
        except HttpResponseError as error:
            _raise_for_http_error(error, bucket=self._bucket, key=self._key)
            raise
        return ObjectInfo(
            bucket=self._bucket, key=self._key, size=props.size,
            content_type=props.content_settings.content_type if props.content_settings else None,
            etag=props.etag, last_modified=props.last_modified, metadata=dict(props.metadata or {}),
        )

    async def abort(self) -> None:
        self._block_ids.clear()
```

- [ ] **Step 5: 成功することを確認する**

Run: `cd core-toolkit && uv run pytest tests/test_object_storage_azure.py -v`
Expected: PASS（契約テスト9件 + Azure固有5件、計14件）

- [ ] **Step 6: lintを通す**

Run: `cd core-toolkit && uv run ruff check src/core_toolkit/object_storage tests/ && uv run ruff format --check src/core_toolkit/object_storage tests/`
Expected: エラーなし

- [ ] **Step 7: コミット**

```bash
git add core-toolkit/pyproject.toml core-toolkit/src/core_toolkit/object_storage/azure.py core-toolkit/tests/test_object_storage_azure.py core-toolkit/uv.lock
git commit -m "feat(core-toolkit): add AzureObjectStorage adapter (azure-storage-blob)"
```

---

## Task 6: `open_object_storage`ファクトリと`ObjectStorageLifespanResource`（core-toolkit）

**Files:**
- Create: `core-toolkit/src/core_toolkit/object_storage/factory.py`
- Modify: `core-toolkit/src/core_toolkit/object_storage/__init__.py`
- Create: `core-toolkit/src/core_toolkit/object_storage_lifespan.py`
- Test: `core-toolkit/tests/test_object_storage_factory.py`
- Test: `core-toolkit/tests/test_object_storage_lifespan.py`

**Interfaces:**
- Consumes: Task1〜5の`ObjectStorage`実装4種、`core_toolkit.lifespan.LifespanResource`/`create_lifespan`
- Produces: `core_toolkit.object_storage.factory.open_object_storage(scheme, buckets, **client_kwargs)`（`@asynccontextmanager`）。`core_toolkit.object_storage_lifespan.ObjectStorageLifespanResource(name, scheme, buckets, **client_kwargs)` / `get_object_storage(name)`。Task7/8はこの2つを使う

- [ ] **Step 1: 失敗するテストを書く**

`core-toolkit/tests/test_object_storage_factory.py`:

```python
"""object_storage.factory.open_object_storageのスキーム分岐テスト。

各バックエンドの実際のI/Oはs3.py/gcs.py/azure.pyそれぞれの単体テストで
検証済みのため、ここではscheme文字列に応じて正しいSDK呼び出しへ
ディスパッチされること、bucketsとclient_kwargsがそのまま渡ることのみを
検証する。
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import pytest

from core_toolkit.object_storage.azure import AzureObjectStorage
from core_toolkit.object_storage.factory import open_object_storage
from core_toolkit.object_storage.gcs import GcsObjectStorage
from core_toolkit.object_storage.memory import InMemoryObjectStorage
from core_toolkit.object_storage.s3 import S3ObjectStorage


@pytest.mark.asyncio
async def test_memory_scheme_returns_in_memory_storage():
    async with open_object_storage("memory", {"uploads": "uploads-x7f3"}) as storage:
        assert isinstance(storage, InMemoryObjectStorage)
        await storage.put("uploads", "a.txt", b"hello")
        assert await storage.get("uploads", "a.txt") == b"hello"


@pytest.mark.asyncio
async def test_unknown_scheme_raises_value_error():
    with pytest.raises(ValueError, match="unknown"):
        async with open_object_storage("unknown", {}):
            pass


@pytest.mark.asyncio
async def test_s3_scheme_dispatches_to_aiobotocore(monkeypatch: pytest.MonkeyPatch):
    import aiobotocore.session

    captured: dict[str, Any] = {}
    sentinel_client = object()

    @asynccontextmanager
    async def fake_create_client(service_name: str, **kwargs: Any) -> AsyncIterator[Any]:
        captured["service_name"] = service_name
        captured["kwargs"] = kwargs
        yield sentinel_client

    class FakeSession:
        def create_client(self, service_name: str, **kwargs: Any):
            return fake_create_client(service_name, **kwargs)

    monkeypatch.setattr(aiobotocore.session, "get_session", lambda: FakeSession())

    async with open_object_storage("s3", {"uploads": "uploads-x7f3"}, region_name="us-east-1") as storage:
        assert isinstance(storage, S3ObjectStorage)

    assert captured["service_name"] == "s3"
    assert captured["kwargs"] == {"region_name": "us-east-1"}


@pytest.mark.asyncio
async def test_gs_scheme_dispatches_to_gcloud_aio_storage(monkeypatch: pytest.MonkeyPatch):
    import gcloud.aio.storage

    captured: dict[str, Any] = {}

    class FakeStorage:
        def __init__(self, **kwargs: Any) -> None:
            captured["kwargs"] = kwargs

        async def __aenter__(self) -> "FakeStorage":
            return self

        async def __aexit__(self, *exc_info: Any) -> None:
            return None

    monkeypatch.setattr(gcloud.aio.storage, "Storage", FakeStorage)

    async with open_object_storage("gs", {"uploads": "uploads-x7f3"}, api_root="http://fake-gcs:9000") as storage:
        assert isinstance(storage, GcsObjectStorage)

    assert captured["kwargs"] == {"api_root": "http://fake-gcs:9000"}


@pytest.mark.asyncio
async def test_azure_scheme_dispatches_to_blob_service_client(monkeypatch: pytest.MonkeyPatch):
    import azure.storage.blob.aio

    captured: dict[str, Any] = {}

    class FakeBlobServiceClient:
        def __init__(self, account_url: str, *, credential: Any = None, **kwargs: Any) -> None:
            captured["account_url"] = account_url
            captured["credential"] = credential
            captured["kwargs"] = kwargs

        async def __aenter__(self) -> "FakeBlobServiceClient":
            return self

        async def __aexit__(self, *exc_info: Any) -> None:
            return None

    monkeypatch.setattr(azure.storage.blob.aio, "BlobServiceClient", FakeBlobServiceClient)

    async with open_object_storage(
        "azure", {"uploads": "uploads-x7f3"},
        account_url="https://example.blob.core.windows.net",
        credential="fake-key", account_key="fake-key",
    ) as storage:
        assert isinstance(storage, AzureObjectStorage)

    assert captured["account_url"] == "https://example.blob.core.windows.net"
    assert captured["credential"] == "fake-key"
    assert captured["kwargs"] == {}
```

`core-toolkit/tests/test_object_storage_lifespan.py`:

```python
"""object_storage_lifespanの統合テスト。scheme="memory"を使うことで、
バックエンドSDKのモンキーパッチ無しに名前付き登録・取得・
論理名の独立性を検証できる。
"""

import pytest
from starlette.applications import Starlette
from starlette.requests import Request

from core_toolkit.lifespan import create_lifespan
from core_toolkit.object_storage.memory import InMemoryObjectStorage
from core_toolkit.object_storage_lifespan import (
    ObjectStorageLifespanResource,
    get_object_storage,
)


def _make_request(app: Starlette) -> Request:
    return Request({"type": "http", "app": app})


@pytest.mark.asyncio
async def test_get_object_storage_returns_registered_storage():
    app = Starlette()
    lifespan = create_lifespan(
        ObjectStorageLifespanResource("main", "memory", {"uploads": "uploads-x7f3"})
    )

    async with lifespan(app):
        storage = get_object_storage("main")(_make_request(app))
        assert isinstance(storage, InMemoryObjectStorage)
        await storage.put("uploads", "a.txt", b"hello")
        assert await storage.get("uploads", "a.txt") == b"hello"


def test_get_object_storage_raises_runtime_error_when_missing():
    app = Starlette()

    with pytest.raises(RuntimeError, match="main"):
        get_object_storage("main")(_make_request(app))


@pytest.mark.asyncio
async def test_multiple_named_backends_are_independent():
    app = Starlette()
    lifespan = create_lifespan(
        ObjectStorageLifespanResource("main", "memory", {"uploads": "uploads-x7f3"}),
        ObjectStorageLifespanResource("archive", "memory", {"uploads": "archive-a91c"}),
    )

    async with lifespan(app):
        main_storage = get_object_storage("main")(_make_request(app))
        archive_storage = get_object_storage("archive")(_make_request(app))
        assert main_storage is not archive_storage

        await main_storage.put("uploads", "k", b"main-value")
        assert await archive_storage.exists("uploads", "k") is False
```

- [ ] **Step 2: 失敗することを確認する**

Run: `cd core-toolkit && uv run pytest tests/test_object_storage_factory.py tests/test_object_storage_lifespan.py -v`
Expected: FAIL（`core_toolkit.object_storage.factory` / `core_toolkit.object_storage_lifespan` が存在しない）

- [ ] **Step 3: 実装する**

`core-toolkit/src/core_toolkit/object_storage/factory.py`:

```python
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
        ModuleNotFoundError: 対応するextraがインストールされていない場合。
    """
    if scheme == "s3":
        try:
            import aiobotocore.session
        except ModuleNotFoundError as error:
            raise ModuleNotFoundError("scheme='s3' requires the 'core-toolkit[s3]' extra.") from error
        from core_toolkit.object_storage.s3 import S3ObjectStorage

        session = aiobotocore.session.get_session()
        async with session.create_client("s3", **client_kwargs) as client:
            yield S3ObjectStorage(buckets, client)

    elif scheme == "gs":
        try:
            from gcloud.aio.storage import Storage
        except ModuleNotFoundError as error:
            raise ModuleNotFoundError("scheme='gs' requires the 'core-toolkit[gcs]' extra.") from error
        from core_toolkit.object_storage.gcs import GcsObjectStorage

        async with Storage(**client_kwargs) as client:
            yield GcsObjectStorage(buckets, client)

    elif scheme == "azure":
        try:
            from azure.storage.blob.aio import BlobServiceClient
        except ModuleNotFoundError as error:
            raise ModuleNotFoundError("scheme='azure' requires the 'core-toolkit[azure]' extra.") from error
        from core_toolkit.object_storage.azure import AzureObjectStorage

        remaining_kwargs = dict(client_kwargs)
        account_url = remaining_kwargs.pop("account_url")
        credential = remaining_kwargs.pop("credential", None)
        account_key = remaining_kwargs.pop("account_key", None)
        async with BlobServiceClient(account_url, credential=credential, **remaining_kwargs) as client:
            yield AzureObjectStorage(buckets, client, account_key=account_key)

    elif scheme == "memory":
        from core_toolkit.object_storage.memory import InMemoryObjectStorage

        yield InMemoryObjectStorage(buckets)

    else:
        raise ValueError(f"unknown scheme: {scheme!r}. Expected one of {_KNOWN_SCHEMES}.")
```

`core-toolkit/src/core_toolkit/object_storage/__init__.py`（更新。factoryのexportを追記）:

```python
"""ObjectStorage抽象化（S3/GCS/Azure Blob/インメモリの差し替え対応）。"""

from core_toolkit.object_storage.base import (
    BucketNotFoundError,
    MultipartUpload,
    NotSupportedError,
    ObjectInfo,
    ObjectNotFoundError,
    ObjectStorage,
    ObjectStorageError,
    PermissionDeniedError,
    UnknownBucketError,
)
from core_toolkit.object_storage.factory import open_object_storage
from core_toolkit.object_storage.memory import InMemoryObjectStorage

__all__ = [
    "BucketNotFoundError",
    "InMemoryObjectStorage",
    "MultipartUpload",
    "NotSupportedError",
    "ObjectInfo",
    "ObjectNotFoundError",
    "ObjectStorage",
    "ObjectStorageError",
    "PermissionDeniedError",
    "UnknownBucketError",
    "open_object_storage",
]
```

`core-toolkit/src/core_toolkit/object_storage_lifespan.py`:

```python
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

    def __init__(self, name: str, scheme: str, buckets: Mapping[str, str], **client_kwargs: Any) -> None:
        self._name = name
        self._scheme = scheme
        self._buckets = buckets
        self._client_kwargs = client_kwargs

    @asynccontextmanager
    async def context(self, app: Starlette) -> AsyncGenerator[Any, Any]:
        async with open_object_storage(self._scheme, self._buckets, **self._client_kwargs) as storage:
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
        stores: dict[str, ObjectStorage] = getattr(request.app.state, "object_storages", {})
        if name not in stores:
            raise RuntimeError(
                f"object_storages['{name}'] is not set. "
                f"Did you forget to register ObjectStorageLifespanResource(name='{name}', ...) "
                "in create_lifespan(...)?"
            )
        return stores[name]

    get_storage.__name__ = f"get_object_storage_{name}"
    return get_storage
```

- [ ] **Step 4: 成功することを確認する**

Run: `cd core-toolkit && uv run pytest tests/test_object_storage_factory.py tests/test_object_storage_lifespan.py -v`
Expected: PASS（factory 5件 + lifespan 3件、計8件）

- [ ] **Step 5: core-toolkitの全テストを実行する**

Run: `cd core-toolkit && uv run pytest -v`
Expected: 既存分含め全PASS（回帰なし）

- [ ] **Step 6: lintを通す**

Run: `cd core-toolkit && uv run ruff check src/ tests/ && uv run ruff format --check src/ tests/`
Expected: エラーなし

- [ ] **Step 7: コミット**

```bash
git add core-toolkit/src/core_toolkit/object_storage/factory.py core-toolkit/src/core_toolkit/object_storage/__init__.py core-toolkit/src/core_toolkit/object_storage_lifespan.py core-toolkit/tests/test_object_storage_factory.py core-toolkit/tests/test_object_storage_lifespan.py
git commit -m "feat(core-toolkit): add open_object_storage factory and ObjectStorageLifespanResource"
```

---

## Task 7: fastapi-toolkit薄い再エクスポート

**Files:**
- Create: `fastapi-toolkit/src/fastapi_toolkit/object_storage_lifespan.py`
- Modify: `fastapi-toolkit/pyproject.toml`（`s3`/`gcs`/`azure` extraを追加）
- Test: `fastapi-toolkit/tests/test_object_storage_lifespan.py`

**Interfaces:**
- Consumes: Task6の`core_toolkit.object_storage_lifespan.ObjectStorageLifespanResource`/`get_object_storage`、`core_toolkit.object_storage.ObjectStorage`、`fastapi_toolkit.lifespan.create_lifespan`
- Produces: `fastapi_toolkit.object_storage_lifespan.ObjectStorageLifespanResource`/`get_object_storage`/`ObjectStorage`（再エクスポート）

- [ ] **Step 1: 失敗するテストを書く**

`fastapi-toolkit/tests/test_object_storage_lifespan.py`:

```python
"""ObjectStorageLifespanResource/get_object_storageがDepends経由で正しく
解決されることを検証する統合テスト。

ObjectStorageLifespanResource自体の起動・終了処理はcore-toolkit側
（core_toolkit/tests/test_object_storage_lifespan.py）でテスト済みのため、
ここではFastAPI固有の部分――``Annotated[T, Depends(...)]``が実際の
エンドポイントで解決されること――だけを検証する。scheme="memory"を使うことで
バックエンドSDKへの依存無しにテストできる。
"""

from typing import Annotated

import pytest
from fastapi import Depends, FastAPI
from httpx import ASGITransport, AsyncClient

from fastapi_toolkit.lifespan import create_lifespan
from fastapi_toolkit.object_storage_lifespan import (
    ObjectStorage,
    ObjectStorageLifespanResource,
    get_object_storage,
)

MainStorage = Annotated[ObjectStorage, Depends(get_object_storage("main"))]


@pytest.mark.asyncio
async def test_object_storage_resolves_via_depends():
    app = FastAPI()

    @app.put("/objects/{key}")
    async def write(key: str, value: str, storage: MainStorage) -> dict[str, bool]:
        await storage.put("uploads", key, value.encode())
        return {"ok": True}

    @app.get("/objects/{key}")
    async def read(key: str, storage: MainStorage) -> dict[str, str]:
        data = await storage.get("uploads", key)
        return {"value": data.decode()}

    lifespan = create_lifespan(
        ObjectStorageLifespanResource("main", "memory", {"uploads": "uploads-x7f3"})
    )
    async with lifespan(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await client.put("/objects/greeting", params={"value": "hello"})
            resp = await client.get("/objects/greeting")

    assert resp.status_code == 200
    assert resp.json() == {"value": "hello"}


@pytest.mark.asyncio
async def test_object_storage_raises_error_response_for_unknown_bucket():
    app = FastAPI()

    @app.get("/objects/{key}")
    async def read(key: str, storage: MainStorage) -> dict[str, str]:
        data = await storage.get("uploads", key)
        return {"value": data.decode()}

    lifespan = create_lifespan(
        ObjectStorageLifespanResource("main", "memory", {"uploads": "uploads-x7f3"})
    )
    async with lifespan(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            with pytest.raises(Exception, match="not found"):
                await client.get("/objects/missing")
```

- [ ] **Step 2: 失敗することを確認する**

Run: `cd fastapi-toolkit && uv run pytest tests/test_object_storage_lifespan.py -v`
Expected: FAIL（`fastapi_toolkit.object_storage_lifespan` が存在しない）

- [ ] **Step 3: pyproject.tomlにextraを追加する**

`fastapi-toolkit/pyproject.toml`の`[project.optional-dependencies]`に追記（既存の`valkey`ブロックの後）:

```toml
s3 = [
  "core-toolkit[s3]",
]
gcs = [
  "core-toolkit[gcs]",
]
azure = [
  "core-toolkit[azure]",
]
```

Run: `cd fastapi-toolkit && uv sync --all-extras`

- [ ] **Step 4: 実装する**

`fastapi-toolkit/src/fastapi_toolkit/object_storage_lifespan.py`:

```python
"""ObjectStorage接続lifespanリソースのFastAPI向け薄いラッパー。

``ObjectStorageLifespanResource``/``get_object_storage`` はFastAPIに依存せず
``core_toolkit.object_storage_lifespan`` に実装されている。このモジュールは
それらを再エクスポートするだけで、名前ごとの型エイリアス
（``Annotated[ObjectStorage, Depends(get_object_storage("main"))]``）は
アプリ側が名前を指定して定義する。

利用にはバックエンドに応じたextra（``fastapi-toolkit[s3]``/``[gcs]``/``[azure]``）
のインストールが必要。``scheme="memory"``は追加依存なしで使える。
"""

from core_toolkit.object_storage import ObjectStorage
from core_toolkit.object_storage_lifespan import (
    ObjectStorageLifespanResource,
    get_object_storage,
)

__all__ = ["ObjectStorage", "ObjectStorageLifespanResource", "get_object_storage"]
```

- [ ] **Step 5: 成功することを確認する**

Run: `cd fastapi-toolkit && uv run pytest tests/test_object_storage_lifespan.py -v`
Expected: PASS（2件）

- [ ] **Step 6: fastapi-toolkitの全テストを実行し、lintを通す**

Run: `cd fastapi-toolkit && uv run pytest -v && uv run ruff check src/ tests/ && uv run ruff format --check src/ tests/`
Expected: 既存分含め全PASS、lintエラーなし

- [ ] **Step 7: コミット**

```bash
git add fastapi-toolkit/pyproject.toml fastapi-toolkit/src/fastapi_toolkit/object_storage_lifespan.py fastapi-toolkit/tests/test_object_storage_lifespan.py fastapi-toolkit/uv.lock
git commit -m "feat(fastapi-toolkit): re-export ObjectStorage lifespan from core-toolkit"
```

---

## Task 8: fastmcp-toolkit独自lifespan配線

**Files:**
- Create: `fastmcp-toolkit/src/fastmcp_toolkit/object_storage_lifespan.py`
- Modify: `fastmcp-toolkit/pyproject.toml`（`s3`/`gcs`/`azure` extraを追加）
- Test: `fastmcp-toolkit/tests/test_object_storage_lifespan.py`

**Interfaces:**
- Consumes: Task6の`core_toolkit.object_storage.base.ObjectStorage`、`core_toolkit.object_storage.factory.open_object_storage`
- Produces: `fastmcp_toolkit.object_storage_lifespan.object_storage_lifespan(name, scheme, buckets, **client_kwargs)` / `CurrentObjectStorage(name)` / `_get_object_storage(name)`（テスト用に公開）

**注記（Redis/Valkeyとの違い、意図的な設計逸脱）**: fastmcp-toolkitの既存のRedis/Valkey lifespanは、クライアント生成ロジック自体を独立に再実装している（[[project_lifespan_di_layering]]参照）。ObjectStorageでは、S3/GCS/Azureアダプタの実装量・複雑さ（エラー正規化、GCSのcompose分割、Azureのブロック管理）が大きいため、**ABC・アダプタ・`open_object_storage`はcore-toolkitのものをそのまま使い、FastMCP側はlifespan配線（`app.state`ではなく`lifespan_context`にdictをyieldする形）のみを独自に持つ**。設計spec「レイヤー構造」節に明記済みの決定であり、Redis/Valkeyとの非対称性はレビュー時に指摘不要。この結果、pyproject.tomlのextraもRedis/Valkey（生SDKパッケージを直接列挙）とは異なり、fastapi-toolkitと同様に`core-toolkit[s3]`等へプロキシする。

- [ ] **Step 1: 失敗するテストを書く**

`fastmcp-toolkit/tests/test_object_storage_lifespan.py`:

```python
"""object_storage_lifespan / CurrentObjectStorage の統合テスト。

Client(app)（インメモリトランスポート）はサーバーのlifespanを実際に
entryするため、ツール関数からCurrentObjectStorage(name)がDepends経由で
正しく解決されることをend-to-endで検証する。scheme="memory"を使うことで
バックエンドSDKへの依存無しにテストできる。
"""

import pytest
from core_toolkit.object_storage import ObjectStorage
from fastmcp import Client, FastMCP

from fastmcp_toolkit.object_storage_lifespan import (
    CurrentObjectStorage,
    _get_object_storage,
    object_storage_lifespan,
)


@pytest.mark.asyncio
async def test_tool_resolves_object_storage_via_depends():
    app = FastMCP(
        "test",
        lifespan=object_storage_lifespan("main", "memory", {"uploads": "uploads-x7f3"}),
    )

    @app.tool
    async def write_and_read(
        key: str, value: str, storage: ObjectStorage = CurrentObjectStorage("main")
    ) -> str:
        await storage.put("uploads", key, value.encode())
        data = await storage.get("uploads", key)
        return data.decode()

    async with Client(app) as client:
        result = await client.call_tool("write_and_read", {"key": "k", "value": "v"})

    assert result.data == "v"


@pytest.mark.asyncio
async def test_multiple_named_backends_are_independent():
    app = FastMCP(
        "test",
        lifespan=object_storage_lifespan("main", "memory", {"uploads": "bucket-a"})
        | object_storage_lifespan("archive", "memory", {"uploads": "bucket-b"}),
    )

    @app.tool
    async def compare(
        main: ObjectStorage = CurrentObjectStorage("main"),
        archive: ObjectStorage = CurrentObjectStorage("archive"),
    ) -> bool:
        await main.put("uploads", "k", b"main-value")
        return main is not archive and await archive.exists("uploads", "k") is False

    async with Client(app) as client:
        result = await client.call_tool("compare", {})

    assert result.data is True


@pytest.mark.asyncio
async def test_tool_raises_runtime_error_when_lifespan_not_registered():
    app = FastMCP("test")

    @app.tool
    async def whoami(storage: ObjectStorage = CurrentObjectStorage("main")) -> str:
        return type(storage).__name__

    async with Client(app) as client:
        # RPC境界を越えるとサーバー側の詳細な例外メッセージ（"lifespan_context[...] is
        # not set..."）は失われ、クライアントに届くのはDepends解決対象の
        # パラメータ名を含む"Failed to resolve dependency '<param>' for <fn>"のみ
        # になる（fastmcp-toolkitのdb_lifespan/redis_lifespan/valkey_lifespanで
        # 確認済み）。そのため登録名"main"ではなくパラメータ名"storage"でmatchする。
        with pytest.raises(Exception, match=r"Failed to resolve dependency 'storage'"):
            await client.call_tool("whoami", {})


def test_get_object_storage_error_message_includes_registration_hint():
    from types import SimpleNamespace

    get_storage = _get_object_storage("main")
    with pytest.raises(RuntimeError, match="main"):
        get_storage(SimpleNamespace(lifespan_context={}))
```

- [ ] **Step 2: 失敗することを確認する**

Run: `cd fastmcp-toolkit && uv run pytest tests/test_object_storage_lifespan.py -v`
Expected: FAIL（`fastmcp_toolkit.object_storage_lifespan` が存在しない）

- [ ] **Step 3: pyproject.tomlにextraを追加する**

`fastmcp-toolkit/pyproject.toml`の`[project.optional-dependencies]`に追記（既存の`valkey`ブロックの後）:

```toml
s3 = [
    "core-toolkit[s3]",
]
gcs = [
    "core-toolkit[gcs]",
]
azure = [
    "core-toolkit[azure]",
]
```

Run: `cd fastmcp-toolkit && uv sync --all-extras`

- [ ] **Step 4: 実装する**

`fastmcp-toolkit/src/fastmcp_toolkit/object_storage_lifespan.py`:

```python
"""FastMCPサーバー向けObjectStorage接続lifespan統合。

Starlette向けの
``core_toolkit.object_storage_lifespan.ObjectStorageLifespanResource`` とは
ライフサイクルの形が異なる（``app.state`` に書き込む``asynccontextmanager``
ではなく、dictをyieldする非同期ジェネレータ）ため、独立したlifespan配線を
持つ。ABC・各バックエンドアダプタ自体はFastAPI/FastMCP非依存の
``core_toolkit.object_storage``をそのまま使う（fastapi-toolkit経由にしない。
[[project_lifespan_di_layering]]の層分けに従う）。DIは``fastmcp``が内部で
使う``uncalled_for.Depends``に乗せる。名前付きで複数バックエンドを同時に
利用でき、``|``演算子で他のlifespanと合成できる。

利用にはバックエンドに応じたextra
（``fastmcp-toolkit[s3]``/``[gcs]``/``[azure]``）のインストールが必要。
``scheme="memory"``は追加依存なしで使える。
"""

from collections.abc import AsyncIterator, Callable, Mapping
from typing import Any, cast

from fastmcp import Context, FastMCP
from fastmcp.server.dependencies import CurrentContext
from fastmcp.server.lifespan import Lifespan, lifespan
from uncalled_for import Depends

from core_toolkit.object_storage.base import ObjectStorage
from core_toolkit.object_storage.factory import open_object_storage


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
    async def _object_storage_lifespan(server: FastMCP) -> AsyncIterator[dict[str, Any]]:
        async with open_object_storage(scheme, buckets, **client_kwargs) as storage:
            yield {_lifespan_key(name): storage}

    return _object_storage_lifespan


def _get_object_storage(name: str) -> Callable[[Context], ObjectStorage]:
    def get_storage(ctx: Context = CurrentContext()) -> ObjectStorage:
        storage = ctx.lifespan_context.get(_lifespan_key(name))
        if storage is None:
            raise RuntimeError(
                f"lifespan_context['{_lifespan_key(name)}'] is not set. "
                f"Did you forget to pass lifespan=object_storage_lifespan(name='{name}', ...) "
                "to FastMCP(...)?"
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
```

- [ ] **Step 5: 成功することを確認する**

Run: `cd fastmcp-toolkit && uv run pytest tests/test_object_storage_lifespan.py -v`
Expected: PASS（4件）

- [ ] **Step 6: fastmcp-toolkitの全テストを実行し、lintを通す**

Run: `cd fastmcp-toolkit && uv run pytest -v && uv run ruff check src/ tests/ && uv run ruff format --check src/ tests/`
Expected: 既存分含め全PASS、lintエラーなし

- [ ] **Step 7: コミット**

```bash
git add fastmcp-toolkit/pyproject.toml fastmcp-toolkit/src/fastmcp_toolkit/object_storage_lifespan.py fastmcp-toolkit/tests/test_object_storage_lifespan.py fastmcp-toolkit/uv.lock
git commit -m "feat(fastmcp-toolkit): add ObjectStorage lifespan integration"
```

---

## 完了後の確認

全タスク完了後、3パッケージ全体で回帰がないことを確認する:

```bash
cd core-toolkit && uv run pytest && uv run ruff check src/ tests/ && uv run ruff format --check src/ tests/
cd ../fastapi-toolkit && uv run pytest && uv run ruff check src/ tests/ && uv run ruff format --check src/ tests/
cd ../fastmcp-toolkit && uv run pytest examples/ tests/ && uv run ruff check src/ tests/ && uv run ruff format --check src/ tests/
```
