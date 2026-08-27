"""素材库关键词检索：服务端匹配，页外素材也能搜到。

背景：锚点图挑选器过去只拉一页（limit=200）再本地过滤，素材库有数千条时
页外素材永远搜不到——运营反馈「素材库里明明有、就是搜不出来」的根因。
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app, current_user
from app.models import Asset, User


@pytest.fixture()
def client_with_assets():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

    with sessions() as db:
        # 目标图先插入（id 最小）；列表按 created_at/id 倒序，300 条噪音会把它
        # 挤到第一页（limit=200）之外。噪音名刻意不含下划线，便于验证通配符转义。
        db.add(Asset(
            original_name="好图补充/好_15189719.jpeg",
            stored_name="target.jpeg",
            mime_type="image/jpeg", size_bytes=2048, width=200, height=200,
            sha256="f" * 64, category_key="space_image", status="uploaded",
        ))
        db.flush()
        for index in range(300):
            db.add(Asset(
                original_name=f"噪音素材-{index:04d}.jpg",
                stored_name=f"noise{index:04d}.jpg",
                mime_type="image/jpeg", size_bytes=1024, width=100, height=100,
                sha256=f"{index:064x}", category_key="space_image", status="uploaded",
            ))
        db.commit()
        target_id = db.scalar(
            Asset.__table__.select().where(
                Asset.original_name == "好图补充/好_15189719.jpeg"
            ).with_only_columns(Asset.id)
        )

    def override_db():
        with sessions() as session:
            yield session

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[current_user] = lambda: User(
        id=1, username="tester", password_hash="x", role="admin"
    )
    with TestClient(app) as client:
        yield client, target_id
    app.dependency_overrides.clear()


def test_keyword_finds_asset_beyond_first_page(client_with_assets) -> None:
    client, target_id = client_with_assets
    # 不带关键词：目标图在第一页之外
    plain = client.get("/api/assets", params={"limit": 200}).json()
    assert plain["total"] == 301
    assert all("15189719" not in item["name"] for item in plain["items"])

    # 带关键词：服务端检索命中
    found = client.get("/api/assets", params={"keyword": "15189719"}).json()
    assert found["total"] == 1
    assert found["items"][0]["id"] == target_id


def test_keyword_matches_asset_id_exactly(client_with_assets) -> None:
    """纯数字关键词要能按资产编号命中——运营常直接输编号。

    取一个数字串不出现在任何素材名里的 id（噪音名只到 0299），
    这样命中只可能来自 id 精确匹配，而不是名称模糊匹配。
    """
    client, _ = client_with_assets
    everything = client.get("/api/assets", params={"limit": 1000}).json()
    max_id = max(item["id"] for item in everything["items"])
    found = client.get("/api/assets", params={"keyword": str(max_id)}).json()
    assert found["total"] == 1
    assert found["items"][0]["id"] == max_id


def test_keyword_escapes_sql_wildcards(client_with_assets) -> None:
    """% 与 _ 必须按字面量匹配，否则运营输入通配符会捞出全库。"""
    client, target_id = client_with_assets
    # 没有素材名含 %
    assert client.get("/api/assets", params={"keyword": "%"}).json()["total"] == 0
    # 只有目标图名含字面下划线；未转义时 _ 是单字符通配符，会命中全部 301 条
    underscore = client.get("/api/assets", params={"keyword": "_"}).json()
    assert underscore["total"] == 1
    assert underscore["items"][0]["id"] == target_id


def test_blank_keyword_is_ignored(client_with_assets) -> None:
    client, _ = client_with_assets
    assert client.get("/api/assets", params={"keyword": "   "}).json()["total"] == 301
