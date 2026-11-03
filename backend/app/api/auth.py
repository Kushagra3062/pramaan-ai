"""Fail-closed Supabase JWT authentication and workspace authorization."""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from functools import lru_cache

import jwt
from fastapi import Depends, HTTPException, Request, status
from jwt import PyJWKClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.db.engine import get_db
from app.models.enums import UserStatus, WorkspaceRole
from app.models.workspace import User, WorkspaceMember

_ALGORITHMS = ["RS256", "ES256"]


@dataclass(frozen=True)
class AuthenticatedUser:
    user: User
    subject: str


def _unauthorized(detail: str = "Authentication required.") -> HTTPException:
    return HTTPException(status.HTTP_401_UNAUTHORIZED, detail=detail, headers={"WWW-Authenticate": "Bearer"})


@lru_cache(maxsize=8)
def _jwks_client(url: str) -> PyJWKClient:
    return PyJWKClient(url, cache_keys=True, lifespan=600)


def get_authenticated_user(
    request: Request, session: Session = Depends(get_db), settings: Settings = Depends(get_settings)
) -> AuthenticatedUser:
    authorization = request.headers.get("Authorization", "")
    if not authorization.startswith("Bearer ") or not authorization[7:].strip():
        raise _unauthorized()
    if not settings.supabase_url:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail="Authentication is not configured.")
    token = authorization[7:].strip()
    issuer = f"{settings.supabase_url.rstrip('/')}/auth/v1"
    try:
        key = _jwks_client(f"{issuer}/.well-known/jwks.json").get_signing_key_from_jwt(token)
        claims = jwt.decode(
            token,
            key.key,
            algorithms=_ALGORITHMS,
            audience=settings.supabase_jwt_audience,
            issuer=issuer,
            # PyJWT accepts a token with no exp claim unless it is explicitly required.
            options={"require": ["exp"]},
        )
        subject = claims.get("sub")
        if not isinstance(subject, str) or not subject:
            raise jwt.InvalidTokenError("missing subject")
    except Exception:
        raise _unauthorized("Invalid or expired access token.") from None
    user = session.scalar(select(User).where(User.auth_subject == subject))
    if user is None or user.status != UserStatus.ACTIVE.value:
        raise _unauthorized("Authenticated identity is not an active Pramaan user.")
    return AuthenticatedUser(user=user, subject=subject)


def require_workspace_roles(*roles: WorkspaceRole):
    allowed = {role.value for role in roles}

    def dependency(
        workspace_id: uuid.UUID,
        principal: AuthenticatedUser = Depends(get_authenticated_user),
        session: Session = Depends(get_db),
    ) -> WorkspaceMember:
        membership = session.scalar(select(WorkspaceMember).where(WorkspaceMember.workspace_id == workspace_id, WorkspaceMember.user_id == principal.user.id))
        if membership is None or membership.role not in allowed:
            raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Workspace permission denied.")
        return membership
    return dependency


require_workspace_read = require_workspace_roles(*WorkspaceRole)
require_workspace_write = require_workspace_roles(WorkspaceRole.OWNER, WorkspaceRole.ADMIN, WorkspaceRole.EDITOR)
