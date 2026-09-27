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
        await storage.presigned_download_url(
            "uploads", "a.txt", expires=timedelta(minutes=5)
        )


@pytest.mark.asyncio
async def test_presigned_upload_url_raises_not_supported():
    storage = InMemoryObjectStorage({"uploads": "uploads-x7f3"})
    with pytest.raises(NotSupportedError):
        await storage.presigned_upload_url(
            "uploads", "a.txt", expires=timedelta(minutes=5)
        )


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
