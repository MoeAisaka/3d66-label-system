from __future__ import annotations

from typing import Final

from fastapi import Depends, HTTPException

from .models import User


ROLE_PERMISSIONS: Final[dict[str, frozenset[str]]] = {
    "admin": frozenset({"*"}),
    "manager": frozenset(
        {
            "assets:write",
            "jobs:write",
            "reviews:write",
            "prompts:write",
            "dimensions:write",
            "models:read",
            "models:write",
            "automation:write",
            "releases:read",
            "releases:write",
            "reports:read",
        }
    ),
    "reviewer": frozenset({"assets:read", "jobs:read", "reviews:write", "releases:read", "reports:read"}),
    "analyst": frozenset(
        {"assets:read", "jobs:read", "reviews:read", "releases:read", "prompts:read", "dimensions:read", "models:read", "reports:read"}
    ),
    "viewer": frozenset({"assets:read", "jobs:read", "reviews:read", "releases:read", "reports:read"}),
}

ROLE_LABELS: Final[dict[str, str]] = {
    "admin": "系统管理员",
    "manager": "项目管理员",
    "reviewer": "审核员",
    "analyst": "分析员",
    "viewer": "只读成员",
}


def effective_role(user: User) -> str:
    # is_admin is retained for old databases and test fixtures.
    if user.is_admin:
        return "admin"
    role = (user.role or "viewer").strip().lower()
    # Old fixtures and pre-RBAC rows used is_admin as the sole authority.
    if role == "admin":
        return "viewer"
    return role if role in ROLE_PERMISSIONS else "viewer"


def has_permission(user: User, permission: str) -> bool:
    permissions = ROLE_PERMISSIONS[effective_role(user)]
    return "*" in permissions or permission in permissions


def require_permission(permission: str):
    from .main import current_user

    def dependency(user: User = Depends(current_user)) -> User:
        if not has_permission(user, permission):
            raise HTTPException(status_code=403, detail=f"缺少权限：{permission}")
        return user

    return dependency
