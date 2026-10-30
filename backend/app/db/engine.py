from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from app.core.config import get_settings

_settings = get_settings()
engine: Engine = create_engine(
    _settings.database_url,
    pool_pre_ping=True,
    pool_recycle=300,
    pool_size=3,
    max_overflow=2,
    connect_args={"connect_timeout": 5},
)


def check_database() -> bool:
    """Return DB readiness without leaking connection details into the response."""
    with engine.connect() as connection:
        connection.execute(text("SELECT 1"))
    return True
