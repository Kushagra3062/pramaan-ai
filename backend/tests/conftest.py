import uuid
from collections.abc import Generator

import pytest
from sqlalchemy.orm import Session

from app.db.engine import engine
from app.models.workspace import User, Workspace


@pytest.fixture
def db_session() -> Generator[Session, None, None]:
    """Provide an isolated database session that automatically rolls back all changes."""
    connection = engine.connect()
    transaction = connection.begin()
    session = Session(bind=connection, expire_on_commit=False)

    try:
        yield session
    finally:
        session.close()
        if transaction.is_active:
            transaction.rollback()
        connection.close()


@pytest.fixture
def test_workspace(db_session: Session) -> Workspace:
    """Fixture providing a temporary workspace within the rollback transaction."""
    unique_id = uuid.uuid4().hex[:8]
    workspace = Workspace(
        name=f"Test Workspace {unique_id}",
        slug=f"test-workspace-{unique_id}",
    )
    db_session.add(workspace)
    db_session.flush()
    return workspace


@pytest.fixture
def test_user(db_session: Session) -> User:
    """Fixture providing a temporary user within the rollback transaction."""
    unique_id = uuid.uuid4().hex[:8]
    user = User(
        auth_subject=f"auth0|test-{unique_id}",
        email=f"tester-{unique_id}@pramaan.ai",
        display_name=f"Tester {unique_id}",
        status="ACTIVE",
    )
    db_session.add(user)
    db_session.flush()
    return user
