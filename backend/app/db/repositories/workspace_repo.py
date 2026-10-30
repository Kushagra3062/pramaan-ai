import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.repositories.base import BaseRepository
from app.models.workspace import User, Workspace, WorkspaceMember


class WorkspaceRepository(BaseRepository[Workspace]):
    def __init__(self, session: Session) -> None:
        super().__init__(session, Workspace)

    def create(self, name: str, slug: str) -> Workspace:
        workspace = Workspace(name=name, slug=slug)
        return self.add(workspace)

    def get_by_slug(self, slug: str) -> Workspace | None:
        stmt = select(Workspace).where(Workspace.slug == slug)
        return self.session.scalars(stmt).first()

    def list_all(self, limit: int = 100, offset: int = 0) -> list[Workspace]:
        stmt = select(Workspace).order_by(Workspace.created_at.desc()).limit(limit).offset(offset)
        return list(self.session.scalars(stmt).all())

    def add_member(self, workspace_id: uuid.UUID, user_id: uuid.UUID, role: str) -> WorkspaceMember:
        member = WorkspaceMember(workspace_id=workspace_id, user_id=user_id, role=role)
        self.session.add(member)
        self.session.flush()
        return member

    def get_members(self, workspace_id: uuid.UUID) -> list[WorkspaceMember]:
        stmt = (
            select(WorkspaceMember)
            .where(WorkspaceMember.workspace_id == workspace_id)
            .order_by(WorkspaceMember.created_at.asc())
        )
        return list(self.session.scalars(stmt).all())

    def get_member(self, workspace_id: uuid.UUID, user_id: uuid.UUID) -> WorkspaceMember | None:
        stmt = select(WorkspaceMember).where(
            WorkspaceMember.workspace_id == workspace_id,
            WorkspaceMember.user_id == user_id,
        )
        return self.session.scalars(stmt).first()


class UserRepository(BaseRepository[User]):
    def __init__(self, session: Session) -> None:
        super().__init__(session, User)

    def create(
        self,
        auth_subject: str,
        email: str,
        display_name: str,
        status: str = "ACTIVE",
    ) -> User:
        user = User(
            auth_subject=auth_subject,
            email=email,
            display_name=display_name,
            status=status,
        )
        return self.add(user)

    def get_by_auth_subject(self, auth_subject: str) -> User | None:
        stmt = select(User).where(User.auth_subject == auth_subject)
        return self.session.scalars(stmt).first()

    def get_by_email(self, email: str) -> User | None:
        stmt = select(User).where(User.email == email)
        return self.session.scalars(stmt).first()

    def list_by_status(self, status: str, limit: int = 100, offset: int = 0) -> list[User]:
        stmt = select(User).where(User.status == status).limit(limit).offset(offset)
        return list(self.session.scalars(stmt).all())
