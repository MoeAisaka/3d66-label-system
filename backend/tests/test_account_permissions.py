from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.authz import has_permission
from app.database import Base
from app.models import User


def _user(role: str) -> User:
    return User(username=role, password_hash="x", display_name=role, role=role, is_admin=role == "admin")


def test_role_permission_matrix() -> None:
    assert has_permission(_user("admin"), "models:write")
    assert has_permission(_user("manager"), "models:write")
    assert has_permission(_user("reviewer"), "reviews:write")
    assert not has_permission(_user("reviewer"), "models:write")
    assert not has_permission(_user("viewer"), "assets:write")


def test_user_role_persists() -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        db.add(_user("manager"))
        db.commit()
        persisted = db.query(User).one()
        assert persisted.role == "manager"
        assert has_permission(persisted, "automation:write")
