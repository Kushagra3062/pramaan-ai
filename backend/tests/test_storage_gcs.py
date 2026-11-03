"""Offline unit tests for the private GCS storage adapter."""

import uuid

import pytest

from app.ingestion.storage import GCSStorageAdapter, StorageError, SupabaseStorageAdapter


class FakeBlob:
    def __init__(self, objects: dict[str, bytes], key: str) -> None:
        self.objects = objects
        self.key = key

    def upload_from_string(self, data: bytes, content_type: str) -> None:
        assert content_type == "application/pdf"
        self.objects[self.key] = data

    def download_as_bytes(self) -> bytes:
        return self.objects[self.key]

    def delete(self) -> None:
        self.objects.pop(self.key, None)


class FakeBucket:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    def blob(self, key: str) -> FakeBlob:
        return FakeBlob(self.objects, key)


class FakeClient:
    def __init__(self) -> None:
        self.requested_bucket: str | None = None
        self.bucket_instance = FakeBucket()

    def bucket(self, name: str) -> FakeBucket:
        self.requested_bucket = name
        return self.bucket_instance


def test_gcs_storage_adapter_round_trip_uses_opaque_workspace_key():
    client = FakeClient()
    adapter = GCSStorageAdapter("private-documents", client=client)
    workspace_id, document_id = uuid.uuid4(), uuid.uuid4()

    key = adapter.save(workspace_id, document_id, b"%PDF-1.7", "pdf")

    assert client.requested_bucket == "private-documents"
    assert key == f"{workspace_id}/{document_id}.pdf"
    assert adapter.load(key) == b"%PDF-1.7"
    adapter.delete(key)
    with pytest.raises(StorageError):
        adapter.load(key)


def test_gcs_storage_adapter_rejects_unsafe_suffix():
    adapter = GCSStorageAdapter("private-documents", client=FakeClient())

    with pytest.raises(StorageError, match="Invalid document suffix"):
        adapter.save(uuid.uuid4(), uuid.uuid4(), b"data", "../pdf")


class FakeSupabaseBucket:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    def upload(self, key: str, data: bytes, options: dict[str, str]) -> None:
        assert options == {"content-type": "application/pdf", "upsert": "false"}
        self.objects[key] = data

    def download(self, key: str) -> bytes:
        return self.objects[key]

    def remove(self, keys: list[str]) -> None:
        for key in keys:
            self.objects.pop(key, None)


class FakeSupabaseStorage:
    def __init__(self) -> None:
        self.bucket_name: str | None = None
        self.bucket = FakeSupabaseBucket()

    def from_(self, name: str) -> FakeSupabaseBucket:
        self.bucket_name = name
        return self.bucket


class FakeSupabaseClient:
    def __init__(self) -> None:
        self.storage = FakeSupabaseStorage()


def test_supabase_storage_adapter_round_trip_is_private_and_workspace_scoped():
    client = FakeSupabaseClient()
    adapter = SupabaseStorageAdapter(
        "https://project.supabase.co", "backend-only-service-role", "pramaan-documents", client=client
    )
    workspace_id, document_id = uuid.uuid4(), uuid.uuid4()

    key = adapter.save(workspace_id, document_id, b"%PDF-1.7", "pdf")

    assert client.storage.bucket_name == "pramaan-documents"
    assert key == f"{workspace_id}/{document_id}.pdf"
    assert adapter.load(key) == b"%PDF-1.7"
    adapter.delete(key)
    with pytest.raises(StorageError):
        adapter.load(key)


def test_supabase_storage_adapter_rejects_non_https_and_unsafe_suffix():
    with pytest.raises(StorageError, match="HTTPS"):
        SupabaseStorageAdapter("http://project", "key", "bucket", client=FakeSupabaseClient())
    adapter = SupabaseStorageAdapter(
        "https://project.supabase.co", "key", "bucket", client=FakeSupabaseClient()
    )
    with pytest.raises(StorageError, match="Invalid document suffix"):
        adapter.save(uuid.uuid4(), uuid.uuid4(), b"data", "txt")
