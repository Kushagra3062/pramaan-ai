"""
Storage adapters for private document persistence.

Provides an abstract interface and a local-filesystem implementation for
development.  The interface is designed so a Supabase Storage adapter can
replace it in production without changing the consuming services.
"""

from __future__ import annotations

import abc
import logging
import os
import shutil
import uuid
from typing import Any

logger = logging.getLogger(__name__)


class StorageError(Exception):
    """Raised when a storage operation fails."""


class StorageAdapter(abc.ABC):
    """Abstract interface for private document storage."""

    @abc.abstractmethod
    def save(self, workspace_id: uuid.UUID, document_id: uuid.UUID, data: bytes, suffix: str) -> str:
        """Persist *data* and return an opaque storage key."""

    @abc.abstractmethod
    def load(self, storage_key: str) -> bytes:
        """Retrieve file content by its storage key."""

    @abc.abstractmethod
    def delete(self, storage_key: str) -> None:
        """Delete a stored file.  Must not raise if the file is already gone."""


class LocalStorageAdapter(StorageAdapter):
    """
    Local-filesystem storage for development.

    Files are stored under ``<base_dir>/<workspace_id>/<document_id>.<suffix>``
    and should never reside inside a publicly-served directory.
    """

    def __init__(self, base_dir: str) -> None:
        self.base_dir = os.path.abspath(base_dir)
        os.makedirs(self.base_dir, exist_ok=True)
        logger.info(
            "local_storage_initialized",
            extra={"event": "storage.initialized", "base_dir": self.base_dir},
        )

    def _resolve_path(self, storage_key: str) -> str:
        path = os.path.normpath(os.path.join(self.base_dir, storage_key))
        if not path.startswith(self.base_dir):
            raise StorageError("Path traversal detected in storage key")
        return path

    def save(self, workspace_id: uuid.UUID, document_id: uuid.UUID, data: bytes, suffix: str) -> str:
        safe_suffix = suffix.lstrip(".").lower()
        storage_key = f"{workspace_id}/{document_id}.{safe_suffix}"
        full_path = self._resolve_path(storage_key)
        os.makedirs(os.path.dirname(full_path), exist_ok=True)

        try:
            with open(full_path, "wb") as f:
                f.write(data)
        except OSError as exc:
            raise StorageError(f"Failed to write file: {exc}") from exc

        logger.info(
            "file_stored",
            extra={
                "event": "storage.file_stored",
                "storage_key": storage_key,
                "size_bytes": len(data),
            },
        )
        return storage_key

    def load(self, storage_key: str) -> bytes:
        full_path = self._resolve_path(storage_key)
        try:
            with open(full_path, "rb") as f:
                return f.read()
        except FileNotFoundError:
            raise StorageError(f"File not found: {storage_key}") from None
        except OSError as exc:
            raise StorageError(f"Failed to read file: {exc}") from exc

    def delete(self, storage_key: str) -> None:
        full_path = self._resolve_path(storage_key)
        try:
            os.remove(full_path)
            logger.info(
                "file_deleted",
                extra={"event": "storage.file_deleted", "storage_key": storage_key},
            )
        except FileNotFoundError:
            logger.warning(
                "file_already_deleted",
                extra={"event": "storage.file_already_deleted", "storage_key": storage_key},
            )
        except OSError as exc:
            logger.error(
                "file_delete_failed",
                extra={"event": "storage.file_delete_failed", "storage_key": storage_key, "error": str(exc)},
            )

        # Attempt to clean up empty workspace directory
        try:
            workspace_dir = os.path.dirname(full_path)
            if os.path.isdir(workspace_dir) and not os.listdir(workspace_dir):
                shutil.rmtree(workspace_dir, ignore_errors=True)
        except OSError:
            pass


class GCSStorageAdapter(StorageAdapter):
    """Private Google Cloud Storage adapter for durable Cloud Run document storage.

    The bucket must be private and use uniform bucket-level access. Authentication is
    supplied by Cloud Run's service account through Application Default Credentials;
    no service-account JSON is read from settings or stored in the repository.
    """

    def __init__(
        self,
        bucket_name: str,
        project_id: str | None = None,
        *,
        client: Any | None = None,
    ) -> None:
        if not bucket_name.strip():
            raise StorageError("GCS bucket name cannot be empty")
        if client is None:
            try:
                from google.cloud import storage
            except ImportError as exc:
                raise StorageError("google-cloud-storage is required for GCS document storage") from exc
            client = storage.Client(project=project_id) if project_id else storage.Client()

        self.bucket = client.bucket(bucket_name)

    @staticmethod
    def _storage_key(workspace_id: uuid.UUID, document_id: uuid.UUID, suffix: str) -> str:
        safe_suffix = suffix.lstrip(".").lower()
        if not safe_suffix or "/" in safe_suffix or "\\" in safe_suffix:
            raise StorageError("Invalid document suffix")
        return f"{workspace_id}/{document_id}.{safe_suffix}"

    def save(self, workspace_id: uuid.UUID, document_id: uuid.UUID, data: bytes, suffix: str) -> str:
        storage_key = self._storage_key(workspace_id, document_id, suffix)
        try:
            self.bucket.blob(storage_key).upload_from_string(data, content_type="application/pdf")
        except Exception as exc:
            raise StorageError("Failed to store document in GCS") from exc
        logger.info("gcs_file_stored", extra={"event": "storage.file_stored", "storage_key": storage_key})
        return storage_key

    def load(self, storage_key: str) -> bytes:
        try:
            return bytes(self.bucket.blob(storage_key).download_as_bytes())
        except Exception as exc:
            raise StorageError(f"File not found or unreadable: {storage_key}") from exc

    def delete(self, storage_key: str) -> None:
        try:
            self.bucket.blob(storage_key).delete()
        except Exception:
            # Deletion is compensating cleanup; callers must not fail if an object is already absent.
            logger.warning("gcs_file_delete_failed", extra={"event": "storage.delete_failed", "storage_key": storage_key})


class SupabaseStorageAdapter(StorageAdapter):
    """Private Supabase Storage adapter; service-role key stays backend-only."""
    def __init__(self, url: str, service_role_key: str, bucket_name: str, *, client: Any | None = None) -> None:
        if not url.startswith("https://") or not service_role_key or not bucket_name.strip():
            raise StorageError("Supabase Storage requires HTTPS URL, service-role key, and bucket")
        if client is None:
            try:
                from supabase import create_client
                client = create_client(url, service_role_key)
            except Exception as exc:
                raise StorageError("Supabase Storage client is unavailable") from exc
        self.bucket = client.storage.from_(bucket_name)

    @staticmethod
    def _key(workspace_id: uuid.UUID, document_id: uuid.UUID, suffix: str) -> str:
        safe = suffix.lstrip(".").lower()
        if safe != "pdf":
            raise StorageError("Invalid document suffix")
        return f"{workspace_id}/{document_id}.{safe}"

    def save(self, workspace_id: uuid.UUID, document_id: uuid.UUID, data: bytes, suffix: str) -> str:
        key = self._key(workspace_id, document_id, suffix)
        try:
            self.bucket.upload(key, data, {"content-type": "application/pdf", "upsert": "false"})
        except Exception as exc:
            raise StorageError("Failed to store document in Supabase Storage") from exc
        return key

    def load(self, storage_key: str) -> bytes:
        try:
            return bytes(self.bucket.download(storage_key))
        except Exception as exc:
            raise StorageError("File not found or unreadable") from exc

    def delete(self, storage_key: str) -> None:
        try:
            self.bucket.remove([storage_key])
        except Exception:
            logger.warning("supabase_file_delete_failed", extra={"event": "storage.delete_failed", "storage_key": storage_key})
