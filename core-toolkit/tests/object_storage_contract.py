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

    async def test_put_then_get_roundtrips(
        self, storage: ObjectStorage, logical_bucket: str
    ):
        await storage.put(logical_bucket, "a/b.txt", b"hello")
        assert await storage.get(logical_bucket, "a/b.txt") == b"hello"

    async def test_get_missing_raises_object_not_found(
        self, storage: ObjectStorage, logical_bucket: str
    ):
        import pytest

        with pytest.raises(ObjectNotFoundError):
            await storage.get(logical_bucket, "missing.txt")

    async def test_head_returns_size_and_content_type(
        self, storage: ObjectStorage, logical_bucket: str
    ):
        await storage.put(logical_bucket, "a.txt", b"hello", content_type="text/plain")
        info = await storage.head(logical_bucket, "a.txt")
        assert info.size == 5
        assert info.content_type == "text/plain"

    async def test_delete_is_idempotent(
        self, storage: ObjectStorage, logical_bucket: str
    ):
        await storage.put(logical_bucket, "a.txt", b"hello")
        await storage.delete(logical_bucket, "a.txt")
        await storage.delete(logical_bucket, "a.txt")  # 2回目も例外を出さない
        assert await storage.exists(logical_bucket, "a.txt") is False

    async def test_list_returns_objects_matching_prefix(
        self, storage: ObjectStorage, logical_bucket: str
    ):
        await storage.put(logical_bucket, "dir/a.txt", b"1")
        await storage.put(logical_bucket, "dir/b.txt", b"2")
        await storage.put(logical_bucket, "other.txt", b"3")

        keys = {info.key async for info in storage.list(logical_bucket, prefix="dir/")}

        assert keys == {"dir/a.txt", "dir/b.txt"}

    async def test_get_stream_yields_full_content(
        self, storage: ObjectStorage, logical_bucket: str
    ):
        body = b"x" * 100
        await storage.put(logical_bucket, "a.bin", body)

        chunks = [
            chunk
            async for chunk in storage.get_stream(
                logical_bucket, "a.bin", chunk_size=10
            )
        ]

        assert b"".join(chunks) == body

    async def test_multipart_upload_completes_in_order(
        self, storage: ObjectStorage, logical_bucket: str
    ):
        upload = await storage.begin_multipart(logical_bucket, "multi.bin")
        await upload.upload_part(1, b"aaaaa")
        await upload.upload_part(2, b"bb")

        info = await upload.complete()

        assert info.size == 7
        assert await storage.get(logical_bucket, "multi.bin") == b"aaaaabb"

    async def test_multipart_upload_abort_discards_parts(
        self, storage: ObjectStorage, logical_bucket: str
    ):
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
            logical_bucket,
            "big.bin",
            path,
            multipart_threshold=10,
            part_size=10,
        )

        assert info.size == 21
        assert await storage.get(logical_bucket, "big.bin") == path.read_bytes()
