import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.repositories.base import BaseRepository
from app.models.document import Document, DocumentChunk, DocumentPage, PageExtractionAttempt
from app.models.enums import DocumentProcessingStatus


class DocumentRepository(BaseRepository[Document]):
    def __init__(self, session: Session) -> None:
        super().__init__(session, Document)

    def create(
        self,
        workspace_id: uuid.UUID,
        original_filename: str,
        storage_key: str,
        sha256: str,
        mime_type: str,
        file_size_bytes: int,
        uploaded_by: uuid.UUID | None = None,
        page_count: int | None = None,
        detected_language: str | None = None,
        processing_status: str = DocumentProcessingStatus.UPLOADED.value,
        error_details: dict[str, Any] | None = None,
        graph_node_id: uuid.UUID | None = None,
    ) -> Document:
        doc = Document(
            workspace_id=workspace_id,
            uploaded_by=uploaded_by,
            original_filename=original_filename,
            storage_key=storage_key,
            sha256=sha256,
            mime_type=mime_type,
            file_size_bytes=file_size_bytes,
            page_count=page_count,
            detected_language=detected_language,
            processing_status=processing_status,
            error_details=error_details,
            graph_node_id=graph_node_id,
        )
        return self.add(doc)

    def get_by_sha256(self, workspace_id: uuid.UUID, sha256: str) -> Document | None:
        stmt = select(Document).where(
            Document.workspace_id == workspace_id,
            Document.sha256 == sha256,
        )
        return self.session.scalars(stmt).first()

    def list_by_workspace(
        self,
        workspace_id: uuid.UUID,
        status: str | None = None,
        include_deleted: bool = False,
        limit: int = 100,
        offset: int = 0,
    ) -> list[Document]:
        stmt = select(Document).where(Document.workspace_id == workspace_id)
        if status:
            stmt = stmt.where(Document.processing_status == status)
        if not include_deleted:
            stmt = stmt.where(Document.deleted_at.is_(None))
        stmt = stmt.order_by(Document.created_at.desc()).limit(limit).offset(offset)
        return list(self.session.scalars(stmt).all())

    def count_by_workspace(
        self,
        workspace_id: uuid.UUID,
        status: str | None = None,
        include_deleted: bool = False,
    ) -> int:
        stmt = select(func.count(Document.id)).where(Document.workspace_id == workspace_id)
        if status:
            stmt = stmt.where(Document.processing_status == status)
        if not include_deleted:
            stmt = stmt.where(Document.deleted_at.is_(None))
        return self.session.scalar(stmt) or 0

    def update_status(
        self,
        document_id: uuid.UUID,
        status: str,
        error_details: dict[str, Any] | None = None,
    ) -> Document | None:
        doc = self.get_by_id(document_id)
        if doc is not None:
            doc.processing_status = status
            if error_details is not None:
                doc.error_details = error_details
            self.session.flush()
        return doc

    def soft_delete(self, document_id: uuid.UUID) -> Document | None:
        doc = self.get_by_id(document_id)
        if doc is not None:
            doc.deleted_at = datetime.now(UTC)
            doc.processing_status = DocumentProcessingStatus.DELETED.value
            self.session.flush()
        return doc


class DocumentPageRepository(BaseRepository[DocumentPage]):
    def __init__(self, session: Session) -> None:
        super().__init__(session, DocumentPage)

    def create(
        self,
        document_id: uuid.UUID,
        page_number: int,
        extracted_text: str,
        extraction_method: str = "native_pdf",
        extraction_status: str = "COMPLETED",
        page_metadata: dict[str, Any] | None = None,
    ) -> DocumentPage:
        page = DocumentPage(
            document_id=document_id,
            page_number=page_number,
            extracted_text=extracted_text,
            extraction_method=extraction_method,
            extraction_status=extraction_status,
            page_metadata=page_metadata or {},
        )
        return self.add(page)

    def get_by_page_number(self, document_id: uuid.UUID, page_number: int) -> DocumentPage | None:
        stmt = select(DocumentPage).where(
            DocumentPage.document_id == document_id,
            DocumentPage.page_number == page_number,
        )
        return self.session.scalars(stmt).first()

    def list_by_document(self, document_id: uuid.UUID) -> list[DocumentPage]:
        stmt = (
            select(DocumentPage)
            .where(DocumentPage.document_id == document_id)
            .order_by(DocumentPage.page_number.asc())
        )
        return list(self.session.scalars(stmt).all())


class DocumentChunkRepository(BaseRepository[DocumentChunk]):
    def __init__(self, session: Session) -> None:
        super().__init__(session, DocumentChunk)

    def create(
        self,
        workspace_id: uuid.UUID,
        document_id: uuid.UUID,
        page_start: int,
        page_end: int,
        chunk_index: int,
        content: str,
        embedding_model: str,
        embedding_version: str,
        content_hash: str,
        token_count: int | None = None,
        embedding: list[float] | None = None,
    ) -> DocumentChunk:
        chunk = DocumentChunk(
            workspace_id=workspace_id,
            document_id=document_id,
            page_start=page_start,
            page_end=page_end,
            chunk_index=chunk_index,
            content=content,
            embedding_model=embedding_model,
            embedding_version=embedding_version,
            content_hash=content_hash,
            token_count=token_count,
            embedding=embedding,
        )
        return self.add(chunk)

    def list_by_document(self, document_id: uuid.UUID) -> list[DocumentChunk]:
        stmt = (
            select(DocumentChunk)
            .where(DocumentChunk.document_id == document_id)
            .order_by(DocumentChunk.chunk_index.asc())
        )
        return list(self.session.scalars(stmt).all())

    def delete_by_document(self, document_id: uuid.UUID) -> int:
        """Delete all chunks belonging to a document."""
        chunks = self.list_by_document(document_id)
        count = len(chunks)
        for chunk in chunks:
            self.session.delete(chunk)
        self.session.flush()
        return count

    def list_by_workspace(
        self,
        workspace_id: uuid.UUID,
        limit: int = 100,
        offset: int = 0,
    ) -> list[DocumentChunk]:
        stmt = (
            select(DocumentChunk)
            .where(DocumentChunk.workspace_id == workspace_id)
            .order_by(DocumentChunk.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        return list(self.session.scalars(stmt).all())


class PageExtractionAttemptRepository(BaseRepository[PageExtractionAttempt]):
    def __init__(self, session: Session) -> None:
        super().__init__(session, PageExtractionAttempt)

    def record_attempt(
        self,
        document_id: uuid.UUID,
        document_page_id: uuid.UUID,
        page_number: int,
        provider: str,
        extracted_text: str,
        status: str,
        model_id: str | None = None,
        prompt_version: str | None = None,
        language_configuration: str | None = None,
        usage_metadata: dict[str, Any] | None = None,
        latency_ms: int | None = None,
        input_hash: str | None = None,
        error_code: str | None = None,
    ) -> PageExtractionAttempt:
        attempt = PageExtractionAttempt(
            document_id=document_id,
            document_page_id=document_page_id,
            page_number=page_number,
            provider=provider,
            model_id=model_id,
            prompt_version=prompt_version,
            language_configuration=language_configuration,
            extracted_text=extracted_text,
            status=status,
            usage_metadata=usage_metadata or {},
            latency_ms=latency_ms,
            input_hash=input_hash,
            error_code=error_code,
        )
        return self.add(attempt)

    def list_by_page(self, document_page_id: uuid.UUID) -> list[PageExtractionAttempt]:
        stmt = (
            select(PageExtractionAttempt)
            .where(PageExtractionAttempt.document_page_id == document_page_id)
            .order_by(PageExtractionAttempt.created_at.desc())
        )
        return list(self.session.scalars(stmt).all())

    def list_by_document(self, document_id: uuid.UUID) -> list[PageExtractionAttempt]:
        stmt = (
            select(PageExtractionAttempt)
            .where(PageExtractionAttempt.document_id == document_id)
            .order_by(PageExtractionAttempt.page_number.asc(), PageExtractionAttempt.created_at.desc())
        )
        return list(self.session.scalars(stmt).all())
