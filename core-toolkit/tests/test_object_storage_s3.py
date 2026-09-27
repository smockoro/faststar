"""S3ObjectStorage（core_toolkit.object_storage.s3）の単体テスト。

aiobotocoreの実クライアントはネットワークI/Oを要するため、S3 REST APIの
挙動（get_paginatorが返す非同期ページャ、404/AccessDenied時に
``botocore.exceptions.ClientError``を送出すること、``generate_presigned_url``
が非同期であること等）を模した最小のフェイククライアントを使う。
"""

from datetime import timedelta

import pytest
from botocore.exceptions import ClientError, NoCredentialsError

from core_toolkit.object_storage.base import (
    BucketNotFoundError,
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

    async def __aenter__(self) -> _FakeStreamingBody:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        return None


class FakePaginator:
    def __init__(self, objects: dict[tuple[str, str], dict]) -> None:
        self._objects = objects

    def paginate(self, *, Bucket: str, Prefix: str = ""):
        items = [
            {
                "Key": key,
                "Size": len(obj["Body"]),
                "ETag": obj.get("ETag"),
                "LastModified": None,
            }
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
            "Body": Body,
            "ContentType": ContentType,
            "Metadata": Metadata or {},
            "ETag": '"fake-etag"',
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
            "ContentLength": len(obj["Body"]),
            "ContentType": obj["ContentType"],
            "ETag": obj["ETag"],
            "LastModified": None,
            "Metadata": obj["Metadata"],
        }

    async def delete_object(self, *, Bucket, Key):
        self.objects.pop((Bucket, Key), None)
        return {}

    def get_paginator(self, operation_name: str) -> FakePaginator:
        assert operation_name == "list_objects_v2"
        return FakePaginator(self.objects)

    async def generate_presigned_url(self, client_method, *, Params, ExpiresIn):
        return (
            f"https://fake-s3.example/{Params['Bucket']}/{Params['Key']}"
            f"?method={client_method}&expires={ExpiresIn}"
        )

    async def create_multipart_upload(
        self, *, Bucket, Key, ContentType=None, Metadata=None
    ):
        upload_id = f"upload-{self._next_upload_id}"
        self._next_upload_id += 1
        self._multipart_parts[upload_id] = {}
        self._pending_multipart[upload_id] = {
            "Bucket": Bucket,
            "Key": Key,
            "ContentType": ContentType,
            "Metadata": Metadata or {},
        }
        return {"UploadId": upload_id}

    async def upload_part(self, *, Bucket, Key, UploadId, PartNumber, Body):
        self._multipart_parts[UploadId][PartNumber] = Body
        return {"ETag": f'"part-{PartNumber}"'}

    async def complete_multipart_upload(
        self, *, Bucket, Key, UploadId, MultipartUpload
    ):
        parts = self._multipart_parts.pop(UploadId)
        pending = self._pending_multipart.pop(UploadId)
        ordered = sorted(MultipartUpload["Parts"], key=lambda p: p["PartNumber"])
        body = b"".join(parts[p["PartNumber"]] for p in ordered)
        self.objects[(Bucket, Key)] = {
            "Body": body,
            "ContentType": pending["ContentType"],
            "Metadata": pending["Metadata"],
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
    url = await storage.presigned_download_url(
        "uploads", "a.txt", expires=timedelta(minutes=5)
    )
    assert "uploads-x7f3" in url
    assert "a.txt" in url


# --- I4: BucketNotFoundError ---


class _MissingBucketClient(FakeS3Client):
    """物理バケットが存在しない状態を模す。"""

    async def put_object(self, **_kwargs):
        raise _client_error("NoSuchBucket", "PutObject")

    async def get_object(self, **_kwargs):
        raise _client_error("NoSuchBucket", "GetObject")

    async def head_object(self, **_kwargs):
        # HeadObjectはボディの無い404を返すため、キー不在と区別できない
        raise _client_error("404", "HeadObject")

    async def delete_object(self, **_kwargs):
        raise _client_error("NoSuchBucket", "DeleteObject")

    async def create_multipart_upload(self, **_kwargs):
        raise _client_error("NoSuchBucket", "CreateMultipartUpload")

    def get_paginator(self, operation_name: str):
        class _Paginator:
            def paginate(self, **_kwargs):
                async def _pages():
                    raise _client_error("NoSuchBucket", "ListObjectsV2")
                    yield  # pragma: no cover - 非同期ジェネレータにするため

                return _pages()

        return _Paginator()


@pytest.mark.asyncio
async def test_no_such_bucket_raises_bucket_not_found():
    storage = S3ObjectStorage({"uploads": "uploads-x7f3"}, _MissingBucketClient())
    with pytest.raises(BucketNotFoundError):
        await storage.put("uploads", "a.txt", b"a")
    with pytest.raises(BucketNotFoundError):
        await storage.get("uploads", "a.txt")
    with pytest.raises(BucketNotFoundError):
        await storage.begin_multipart("uploads", "a.bin")


@pytest.mark.asyncio
async def test_head_on_missing_bucket_is_indistinguishable_from_missing_key():
    storage = S3ObjectStorage({"uploads": "uploads-x7f3"}, _MissingBucketClient())
    with pytest.raises(ObjectNotFoundError):
        await storage.head("uploads", "a.txt")


@pytest.mark.asyncio
async def test_delete_on_missing_bucket_raises_bucket_not_found():
    storage = S3ObjectStorage({"uploads": "uploads-x7f3"}, _MissingBucketClient())
    with pytest.raises(BucketNotFoundError):
        await storage.delete("uploads", "a.txt")


@pytest.mark.asyncio
async def test_list_on_missing_bucket_raises_bucket_not_found():
    storage = S3ObjectStorage({"uploads": "uploads-x7f3"}, _MissingBucketClient())
    with pytest.raises(BucketNotFoundError):
        async for _ in storage.list("uploads"):
            pass


# --- I5: 例外の正規化 ---


@pytest.mark.asyncio
async def test_list_wraps_generic_client_error():
    class FailingListClient(FakeS3Client):
        def get_paginator(self, operation_name: str):
            class _Paginator:
                def paginate(self, **_kwargs):
                    async def _pages():
                        raise _client_error("InternalError", "ListObjectsV2")
                        yield  # pragma: no cover

                    return _pages()

            return _Paginator()

    storage = S3ObjectStorage({"uploads": "uploads-x7f3"}, FailingListClient())
    with pytest.raises(ObjectStorageError) as exc_info:
        async for _ in storage.list("uploads"):
            pass
    assert isinstance(exc_info.value.__cause__, ClientError)


@pytest.mark.asyncio
async def test_presigned_urls_normalize_botocore_errors():
    class NoCredentialsClient(FakeS3Client):
        async def generate_presigned_url(self, client_method, **_kwargs):
            raise NoCredentialsError()

    storage = S3ObjectStorage({"uploads": "uploads-x7f3"}, NoCredentialsClient())
    with pytest.raises(ObjectStorageError):
        await storage.presigned_download_url(
            "uploads", "a.txt", expires=timedelta(minutes=5)
        )
    with pytest.raises(ObjectStorageError):
        await storage.presigned_upload_url(
            "uploads", "a.txt", expires=timedelta(minutes=5)
        )


@pytest.mark.asyncio
async def test_presigned_urls_normalize_client_errors():
    class DeniedClient(FakeS3Client):
        async def generate_presigned_url(self, client_method, **_kwargs):
            raise _client_error("AccessDenied", "GeneratePresignedUrl")

    storage = S3ObjectStorage({"uploads": "uploads-x7f3"}, DeniedClient())
    with pytest.raises(PermissionDeniedError):
        await storage.presigned_download_url(
            "uploads", "a.txt", expires=timedelta(minutes=5)
        )


@pytest.mark.asyncio
async def test_abort_ignores_no_such_upload():
    class GoneUploadClient(FakeS3Client):
        async def abort_multipart_upload(self, **_kwargs):
            raise _client_error("NoSuchUpload", "AbortMultipartUpload")

    storage = S3ObjectStorage({"uploads": "uploads-x7f3"}, GoneUploadClient())
    upload = await storage.begin_multipart("uploads", "a.bin")

    await upload.abort()  # 例外を出さない
