from typing import Generic, TypeVar

from sqlalchemy.orm import Session

from app.models.base import Base

ModelType = TypeVar("ModelType", bound=Base)


class BaseRepository(Generic[ModelType]):
    """Base repository providing session access and common persistence operations."""

    def __init__(self, session: Session, model_cls: type[ModelType]) -> None:
        self.session = session
        self.model_cls = model_cls

    def add(self, entity: ModelType) -> ModelType:
        self.session.add(entity)
        self.session.flush()
        return entity

    def get_by_id(self, entity_id: object) -> ModelType | None:
        return self.session.get(self.model_cls, entity_id)

    def delete(self, entity: ModelType) -> None:
        self.session.delete(entity)
        self.session.flush()
