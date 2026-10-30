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

