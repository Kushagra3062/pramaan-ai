import shutil
import tempfile
import uuid
from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.db.engine import engine, get_db
from app.main import app
from app.models.workspace import User, Workspace


@pytest.fixture
def temp_storage_dir() -> Generator[str, None, None]:
    """Temporary storage directory for testing documents."""
    temp_dir = tempfile.mkdtemp(prefix="pramaan_test_storage_")
    yield temp_dir
    shutil.rmtree(temp_dir, ignore_errors=True)


@pytest.fixture
def client(db_session: Session) -> Generator[TestClient, None, None]:
    """FastAPI TestClient with db_session overridden."""
    def _override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = _override_get_db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


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
