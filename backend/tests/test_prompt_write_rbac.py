"""提示词写操作改走 RBAC 的回归。

背景：改已有、克隆、发布、回滚四个端点原先绑 ``admin_user``，绕过了 RBAC 权限表。
而 ``authz.ROLE_PERMISSIONS`` 里 ``manager``（项目管理员）本就持有 ``prompts:write``。
结果是运营的「改提示词 → 发布 → 回滚」闭环必须管理员代操作，自主迭代无从谈起。

本回归锁定两件事：

1. 持有 ``prompts:write`` 的非管理员（manager）可以走通这四个端点。
2. 不持有该权限的角色（viewer）仍被拒 403——放开权限不等于放开给所有人。

注意：已发布/已被引用版本禁止原地改这类保护在函数体内，与权限无关，另有测试覆盖
（``test_prompt_version_management.py``），本文件不重复。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.authz import ROLE_PERMISSIONS, has_permission
from app.database import Base
from app.main import app, current_user, get_db
from app.models import PromptVersion, User


def _draft(version: str = "kg-draft-v1") -> PromptVersion:
    return PromptVersion(
        category_key="space_image",
        pipeline_scope="baseline_regression",
        stage="A",
        name="草稿",
        version=version,
        system_prompt="这是一段足够长的系统提示词内容，用于通过最小长度校验。",
        user_prompt="用户提示词内容",
        rubric_version="rubric-v1",
        status="draft",
    )


def _update_payload(version: str = "kg-draft-v1", note: str = "运营自助修改") -> dict:
    return {
        "category_key": "space_image",
        "pipeline_scope": "baseline_regression",
        "stage": "A",
        "name": "草稿改名",
        "version": version,
        "system_prompt": "这是修改后的系统提示词内容，同样需要足够长度以通过校验。",
        "user_prompt": "修改后的用户提示词",
        "rubric_version": "rubric-v1",
        "change_note": note,
    }


class _Ctx:
    def __init__(self, role: str, *, is_admin: bool = False) -> None:
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(self.engine)
        self.db = Session(self.engine, expire_on_commit=False)
        self.user = User(
            username=f"prompt-{role}",
            password_hash="unused",
            display_name=f"测试{role}",
            is_admin=is_admin,
            role=role,
        )
        self.db.add(self.user)
        self.db.flush()
        db = self.db
        app.dependency_overrides[get_db] = lambda: (yield db)
        app.dependency_overrides[current_user] = lambda: self.user
        self.client = TestClient(app)

    def close(self) -> None:
        app.dependency_overrides.clear()
        self.db.close()
        self.engine.dispose()


@pytest.fixture
def manager_ctx():
    ctx = _Ctx("manager")
    try:
        yield ctx
    finally:
        ctx.close()


@pytest.fixture
def viewer_ctx():
    ctx = _Ctx("viewer")
    try:
        yield ctx
    finally:
        ctx.close()


# --- 权限表前提 -------------------------------------------------------------


def test_manager_holds_prompts_write_but_viewer_does_not() -> None:
    """本次改动的立论前提：manager 本来就该有这个权限。"""
    assert "prompts:write" in ROLE_PERMISSIONS["manager"]
    assert "prompts:write" not in ROLE_PERMISSIONS.get("viewer", set())
    assert has_permission(User(role="manager", is_admin=False), "prompts:write")
    assert not has_permission(User(role="viewer", is_admin=False), "prompts:write")


# --- manager 可走通四个端点 -------------------------------------------------


def test_manager_can_update_draft(manager_ctx: _Ctx) -> None:
    prompt = _draft()
    manager_ctx.db.add(prompt)
    manager_ctx.db.flush()
    response = manager_ctx.client.put(
        f"/api/prompts/{prompt.id}", json=_update_payload()
    )
    assert response.status_code == 200, response.text
    manager_ctx.db.refresh(prompt)
    assert prompt.name == "草稿改名"


def test_manager_can_clone(manager_ctx: _Ctx) -> None:
    prompt = _draft()
    manager_ctx.db.add(prompt)
    manager_ctx.db.flush()
    response = manager_ctx.client.post(
        f"/api/prompts/{prompt.id}/clone",
        json=_update_payload(version="kg-draft-v2", note="另存为新版本"),
    )
    assert response.status_code == 200, response.text
    versions = {row.version for row in manager_ctx.db.query(PromptVersion).all()}
    assert {"kg-draft-v1", "kg-draft-v2"} <= versions


def test_manager_can_publish(manager_ctx: _Ctx) -> None:
    prompt = _draft()
    manager_ctx.db.add(prompt)
    manager_ctx.db.flush()
    response = manager_ctx.client.post(f"/api/prompts/{prompt.id}/publish", json={})
    assert response.status_code == 200, response.text
    manager_ctx.db.refresh(prompt)
    assert prompt.status == "published"


def test_manager_reaches_rollback_business_logic(manager_ctx: _Ctx) -> None:
    """回滚：草稿态应被业务规则拒（409），而不是被权限拒（403）。

    409 恰好证明请求已通过鉴权、进入了函数体——这正是本次改动要达到的效果。
    """
    prompt = _draft()
    manager_ctx.db.add(prompt)
    manager_ctx.db.flush()
    response = manager_ctx.client.post(f"/api/prompts/{prompt.id}/rollback")
    assert response.status_code == 409, response.text
    assert "只能回滚当前已发布版本" in response.text


# --- 无权限角色仍被拒 -------------------------------------------------------


def test_viewer_is_rejected_on_all_write_endpoints(viewer_ctx: _Ctx) -> None:
    prompt = _draft()
    viewer_ctx.db.add(prompt)
    viewer_ctx.db.flush()

    assert (
        viewer_ctx.client.put(
            f"/api/prompts/{prompt.id}", json=_update_payload()
        ).status_code
        == 403
    )
    assert (
        viewer_ctx.client.post(
            f"/api/prompts/{prompt.id}/clone",
            json=_update_payload(version="kg-draft-v9"),
        ).status_code
        == 403
    )
    assert (
        viewer_ctx.client.post(
            f"/api/prompts/{prompt.id}/publish", json={}
        ).status_code
        == 403
    )
    assert (
        viewer_ctx.client.post(f"/api/prompts/{prompt.id}/rollback").status_code == 403
    )
    # 被拒后草稿未被改动。
    viewer_ctx.db.refresh(prompt)
    assert prompt.status == "draft"
    assert prompt.name == "草稿"
