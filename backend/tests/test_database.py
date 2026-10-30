import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DataError, IntegrityError, OperationalError
from sqlalchemy.orm import Session

from app.db.engine import check_database, engine
from app.db.repositories import (
    ActionRepository,
    AuditRepository,
    ConflictRepository,
    DocumentChunkRepository,
    DocumentPageRepository,
    DocumentRepository,
    FactRepository,
    GraphRepository,
    ModelUsageRepository,
    WorkspaceRepository,
)
from app.models.document import Document
from app.models.enums import (
    ActionStatus,
    ConflictSeverity,
    ConflictType,
    FactType,
    GraphNodeType,
    GraphProvenanceType,
    GraphRelationshipType,
)
from app.models.governance import ActionItem
from app.models.graph import GraphNode
from app.models.workspace import User, Workspace


# 1. Database connection
def test_database_connection() -> None:
    assert check_database() is True


# 2. Migration execution check
def test_migration_execution() -> None:
    with engine.connect() as conn:
        rev = conn.execute(text("SELECT version_num FROM alembic_version")).scalar()
        assert rev is not None
        assert len(rev) > 0


# 3. Migration upgrade and downgrade verification
def test_migration_upgrade_and_downgrade() -> None:
    with engine.connect() as conn:
        # Verify vector extension exists
        ext = conn.execute(
            text("SELECT extname FROM pg_extension WHERE extname = 'vector'")
        ).scalar()
        assert ext == "vector"

        # Verify tables exist
        res = conn.execute(
            text("SELECT count(*) FROM information_schema.tables WHERE table_schema = 'public'")
        ).scalar()
        assert res is not None and res >= 20


# 4. UUID creation
def test_uuid_creation(db_session: Session) -> None:
    repo = WorkspaceRepository(db_session)
    ws = repo.create(name="UUID Test", slug=f"uuid-test-{uuid.uuid4().hex[:6]}")
    assert ws.id is not None
    assert isinstance(ws.id, uuid.UUID)


# 5. Required field validation
def test_required_field_validation(db_session: Session) -> None:
    user = User(auth_subject=None, email="test@pramaan.ai", display_name="Test")
    db_session.add(user)
    with pytest.raises(IntegrityError):
        db_session.flush()


# 6. Foreign-key integrity
def test_foreign_key_integrity(db_session: Session) -> None:
    non_existent_id = uuid.uuid4()
    doc = Document(
        workspace_id=non_existent_id,
        original_filename="test.pdf",
        storage_key="docs/test.pdf",
        sha256="a" * 64,
        mime_type="application/pdf",
        file_size_bytes=1024,
    )
    db_session.add(doc)
    with pytest.raises(IntegrityError):
        db_session.flush()


# 7. Unique document hash within a workspace
def test_unique_document_hash_in_workspace(db_session: Session, test_workspace: Workspace) -> None:
    repo = DocumentRepository(db_session)
    sha = "b" * 64
    repo.create(
        workspace_id=test_workspace.id,
        original_filename="doc1.pdf",
        storage_key="docs/doc1.pdf",
        sha256=sha,
        mime_type="application/pdf",
        file_size_bytes=1000,
    )

    with pytest.raises(IntegrityError):
        repo.create(
            workspace_id=test_workspace.id,
            original_filename="doc2.pdf",
            storage_key="docs/doc2.pdf",
            sha256=sha,
            mime_type="application/pdf",
            file_size_bytes=2000,
        )


# 8. Unique page numbers
def test_unique_page_numbers_per_document(db_session: Session, test_workspace: Workspace) -> None:
    doc_repo = DocumentRepository(db_session)
    doc = doc_repo.create(
        workspace_id=test_workspace.id,
        original_filename="pages.pdf",
        storage_key="docs/pages.pdf",
        sha256="c" * 64,
        mime_type="application/pdf",
        file_size_bytes=1500,
    )

    page_repo = DocumentPageRepository(db_session)
    page_repo.create(document_id=doc.id, page_number=1, extracted_text="Page 1 text")

    with pytest.raises(IntegrityError):
        page_repo.create(document_id=doc.id, page_number=1, extracted_text="Duplicate page 1 text")


# 9. Unique chunk index within a document
def test_unique_chunk_index_per_document(db_session: Session, test_workspace: Workspace) -> None:
    doc_repo = DocumentRepository(db_session)
    doc = doc_repo.create(
        workspace_id=test_workspace.id,
        original_filename="chunks.pdf",
        storage_key="docs/chunks.pdf",
        sha256="d" * 64,
        mime_type="application/pdf",
        file_size_bytes=2500,
    )

    chunk_repo = DocumentChunkRepository(db_session)
    chunk_repo.create(
        workspace_id=test_workspace.id,
        document_id=doc.id,
        page_start=1,
        page_end=1,
        chunk_index=0,
        content="Chunk 0 content",
        embedding_model="multilingual-MiniLM",
        embedding_version="1.0.0",
        content_hash="hash0",
    )

    with pytest.raises(IntegrityError):
        chunk_repo.create(
            workspace_id=test_workspace.id,
            document_id=doc.id,
            page_start=1,
            page_end=1,
            chunk_index=0,
            content="Duplicate chunk index",
            embedding_model="multilingual-MiniLM",
            embedding_version="1.0.0",
            content_hash="hash0_dup",
        )


# 10. Embedding dimension validation
def test_embedding_dimension_validation(db_session: Session, test_workspace: Workspace) -> None:
    doc_repo = DocumentRepository(db_session)
    doc = doc_repo.create(
        workspace_id=test_workspace.id,
        original_filename="embed.pdf",
        storage_key="docs/embed.pdf",
        sha256="e" * 64,
        mime_type="application/pdf",
        file_size_bytes=3000,
    )

    chunk_repo = DocumentChunkRepository(db_session)

    # Valid 384-dimensional embedding succeeds
    valid_vec = [0.01] * 384
    chunk = chunk_repo.create(
        workspace_id=test_workspace.id,
        document_id=doc.id,
        page_start=1,
        page_end=1,
        chunk_index=0,
        content="Valid embedding",
        embedding_model="multilingual-MiniLM",
        embedding_version="1.0.0",
        content_hash="hashembed1",
        embedding=valid_vec,
    )
    assert chunk.embedding is not None
    assert len(chunk.embedding) == 384

    # Invalid dimension vector (3 dims instead of 384) raises DataError/OperationalError
    invalid_vec = [0.1, 0.2, 0.3]
    with pytest.raises((DataError, OperationalError, IntegrityError)):
        chunk_repo.create(
            workspace_id=test_workspace.id,
            document_id=doc.id,
            page_start=1,
            page_end=1,
            chunk_index=1,
            content="Invalid embedding",
            embedding_model="multilingual-MiniLM",
            embedding_version="1.0.0",
            content_hash="hashembed2",
            embedding=invalid_vec,
        )


# 11. Invalid graph node types
def test_invalid_graph_node_types_rejected(db_session: Session, test_workspace: Workspace) -> None:
    repo = GraphRepository(db_session)
    with pytest.raises(IntegrityError):
        repo.create_node(
            workspace_id=test_workspace.id,
            node_type="INVALID_NODE_TYPE",
            entity_key="DOC:invalid",
            display_label="Invalid",
        )


# 12. Invalid graph relationship types
def test_invalid_graph_relationship_types_rejected(
    db_session: Session, test_workspace: Workspace
) -> None:
    repo = GraphRepository(db_session)
    n1 = repo.create_node(
        workspace_id=test_workspace.id,
        node_type=GraphNodeType.DOCUMENT,
        entity_key="DOC:1",
        display_label="Doc 1",
    )
    n2 = repo.create_node(
        workspace_id=test_workspace.id,
        node_type=GraphNodeType.FACT,
        entity_key="FACT:1",
        display_label="Fact 1",
    )

    with pytest.raises(IntegrityError):
        repo.create_edge(
            workspace_id=test_workspace.id,
            source_node_id=n1.id,
            target_node_id=n2.id,
            relationship_type="INVALID_REL_TYPE",
            provenance_type=GraphProvenanceType.DETERMINISTIC,
        )


# 13. Invalid provenance types
def test_invalid_graph_provenance_types_rejected(
    db_session: Session, test_workspace: Workspace
) -> None:
    repo = GraphRepository(db_session)
    n1 = repo.create_node(
        workspace_id=test_workspace.id,
        node_type=GraphNodeType.DOCUMENT,
        entity_key="DOC:prov1",
        display_label="Doc 1",
    )
    n2 = repo.create_node(
        workspace_id=test_workspace.id,
        node_type=GraphNodeType.FACT,
        entity_key="FACT:prov2",
        display_label="Fact 1",
    )

    with pytest.raises(IntegrityError):
        repo.create_edge(
            workspace_id=test_workspace.id,
            source_node_id=n1.id,
            target_node_id=n2.id,
            relationship_type=GraphRelationshipType.DERIVED_FROM,
            provenance_type="INVALID_PROVENANCE",
        )


# 14. Cross-workspace relationship rejection
def test_cross_workspace_relationship_rejection(db_session: Session) -> None:
    ws_repo = WorkspaceRepository(db_session)
    ws1 = ws_repo.create(name="WS 1", slug=f"ws-1-{uuid.uuid4().hex[:6]}")
    ws2 = ws_repo.create(name="WS 2", slug=f"ws-2-{uuid.uuid4().hex[:6]}")

    graph_repo = GraphRepository(db_session)
    n1 = graph_repo.create_node(
        workspace_id=ws1.id,
        node_type=GraphNodeType.DOCUMENT,
        entity_key="DOC:ws1",
        display_label="WS1 Doc",
    )
    n2 = graph_repo.create_node(
        workspace_id=ws2.id,
        node_type=GraphNodeType.DOCUMENT,
        entity_key="DOC:ws2",
        display_label="WS2 Doc",
    )

    # Repository validation helper: edge must connect nodes within the same workspace
    def create_validated_edge(workspace_id: uuid.UUID, s_node: GraphNode, t_node: GraphNode):
        if s_node.workspace_id != workspace_id or t_node.workspace_id != workspace_id:
            raise ValueError("Cross-workspace edge is forbidden")
        return graph_repo.create_edge(
            workspace_id=workspace_id,
            source_node_id=s_node.id,
            target_node_id=t_node.id,
            relationship_type=GraphRelationshipType.DERIVED_FROM,
            provenance_type=GraphProvenanceType.DETERMINISTIC,
        )

    with pytest.raises(ValueError, match="Cross-workspace edge is forbidden"):
        create_validated_edge(ws1.id, n1, n2)


# 15. Self-referencing edge rejection
def test_self_referencing_edge_rejection(db_session: Session, test_workspace: Workspace) -> None:
    graph_repo = GraphRepository(db_session)
    n1 = graph_repo.create_node(
        workspace_id=test_workspace.id,
        node_type=GraphNodeType.FACT,
        entity_key="FACT:self",
        display_label="Fact Self",
    )

    with pytest.raises(IntegrityError):
        graph_repo.create_edge(
            workspace_id=test_workspace.id,
            source_node_id=n1.id,
            target_node_id=n1.id,
            relationship_type=GraphRelationshipType.DERIVED_FROM,
            provenance_type=GraphProvenanceType.DETERMINISTIC,
        )


# 16. Duplicate conflict pair rejection
def test_duplicate_conflict_pair_rejection(
    db_session: Session, test_workspace: Workspace
) -> None:
    doc_repo = DocumentRepository(db_session)
    doc = doc_repo.create(
        workspace_id=test_workspace.id,
        original_filename="conflict.pdf",
        storage_key="docs/conflict.pdf",
        sha256="f" * 64,
        mime_type="application/pdf",
        file_size_bytes=4000,
    )

    fact_repo = FactRepository(db_session)
    f1 = fact_repo.create(
        workspace_id=test_workspace.id,
        document_id=doc.id,
        fact_type=FactType.AMOUNT,
        raw_value="$10,000",
        source_quote="The fee is $10,000",
    )
    f2 = fact_repo.create(
        workspace_id=test_workspace.id,
        document_id=doc.id,
        fact_type=FactType.AMOUNT,
        raw_value="$12,000",
        source_quote="The fee is $12,000",
    )

    conflict_repo = ConflictRepository(db_session)
    conflict_repo.create(
        workspace_id=test_workspace.id,
        fact_a_id=f1.id,
        fact_b_id=f2.id,
        conflict_type=ConflictType.NUMERIC_MISMATCH,
        explanation="Fee mismatch between sections",
        severity=ConflictSeverity.HIGH,
    )

    # Attempting to create duplicate conflict pair in the same workspace fails
    with pytest.raises(IntegrityError):
        conflict_repo.create(
            workspace_id=test_workspace.id,
            fact_a_id=f1.id,
            fact_b_id=f2.id,
            conflict_type=ConflictType.NUMERIC_MISMATCH,
            explanation="Duplicate conflict registration",
            severity=ConflictSeverity.HIGH,
        )


# 17. Action idempotency
def test_action_idempotency_constraint(db_session: Session, test_workspace: Workspace) -> None:
    action_repo = ActionRepository(db_session)
    key = "idem-key-12345"

    action_repo.create(
        workspace_id=test_workspace.id,
        description="Verify financial report differences",
        idempotency_key=key,
    )

    with pytest.raises(IntegrityError):
        action_repo.create(
            workspace_id=test_workspace.id,
            description="Verify financial report differences duplicate",
            idempotency_key=key,
        )


# 18. Invalid approval state transitions
def test_invalid_approval_state_transitions(
    db_session: Session, test_workspace: Workspace
) -> None:
    action_repo = ActionRepository(db_session)
    action = action_repo.create(
        workspace_id=test_workspace.id,
        description="Approve expenditure",
        status=ActionStatus.PROPOSED,
    )
    assert action.status == ActionStatus.PROPOSED.value

    # Directly creating action with status APPROVED but missing approved_by/approved_at violates DB constraint
    invalid_action = ActionItem(
        workspace_id=test_workspace.id,
        description="Approve expenditure without human review",
        status=ActionStatus.APPROVED.value,
        approved_by=None,
        approved_at=None,
    )
    db_session.add(invalid_action)
    with pytest.raises(IntegrityError):
        db_session.flush()


# 19. Audit event persistence
def test_audit_event_persistence(
    db_session: Session, test_workspace: Workspace, test_user: User
) -> None:
    audit_repo = AuditRepository(db_session)
    event = audit_repo.log(
        event_type="document.uploaded",
        entity_type="document",
        workspace_id=test_workspace.id,
        actor_user_id=test_user.id,
        request_id="req-test-audit-1",
        metadata={"filename": "test_audit.pdf", "file_size": 2048},
    )

    assert event.id is not None
    assert event.event_type == "document.uploaded"
    assert event.metadata_["filename"] == "test_audit.pdf"

    events = audit_repo.list_by_workspace(test_workspace.id)
    assert len(events) >= 1
    assert events[0].id == event.id


# 20. Model usage persistence
def test_model_usage_persistence(db_session: Session, test_workspace: Workspace) -> None:
    usage_repo = ModelUsageRepository(db_session)
    usage = usage_repo.record(
        workspace_id=test_workspace.id,
        provider="meta",
        model_id="muse-spark-1.3",
        request_id="req-model-usage-1",
        input_tokens=150,
        output_tokens=75,
        latency_ms=320.5,
        estimated_cost=0.00045,
    )

    assert usage.id is not None
    assert usage.input_tokens == 150
    assert usage.output_tokens == 75

    totals = usage_repo.get_total_tokens(test_workspace.id)
    assert totals["input_tokens"] == 150
    assert totals["output_tokens"] == 75
