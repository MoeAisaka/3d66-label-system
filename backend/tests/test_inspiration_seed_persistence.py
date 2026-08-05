from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.inspiration_category_seed import (
    INSPIRATION_CALL_A_VERSION,
    INSPIRATION_CALL_B_VERSION,
    INSPIRATION_SPEC_VERSION,
)
from app.models import CategoryEvaluationV3Config, PromptVersion
from app.seed import _seed_inspiration_image_prompts, _seed_inspiration_image_v3_config


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _engine():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return engine


def test_new_b_prompt_is_appended_without_overwriting_old_version() -> None:
    engine = _engine()
    with Session(engine) as db:
        db.add(
            PromptVersion(
                stage="B",
                category_key="inspiration_image",
                pipeline_scope="full_pipeline",
                name="旧灵感图 B",
                version="inspiration-b-v1",
                system_prompt="旧提示词不可覆盖",
                user_prompt="",
                rubric_version="inspiration-rubric-v1",
                status="published",
            )
        )
        db.commit()
        settings = SimpleNamespace(project_root=PROJECT_ROOT)
        _seed_inspiration_image_prompts(db, settings)
        _seed_inspiration_image_prompts(db, settings)
        db.commit()
        prompts = db.scalars(
            select(PromptVersion)
            .where(PromptVersion.category_key == "inspiration_image")
            .order_by(PromptVersion.id)
        ).all()
        assert [prompt.version for prompt in prompts].count(INSPIRATION_CALL_B_VERSION) == 1
        assert [prompt.version for prompt in prompts].count(INSPIRATION_CALL_A_VERSION) == 1
        assert db.scalar(
            select(PromptVersion.system_prompt).where(
                PromptVersion.version == "inspiration-b-v1"
            )
        ) == "旧提示词不可覆盖"


def test_existing_inspiration_config_is_replaced_once_and_stays_active() -> None:
    engine = _engine()
    with Session(engine) as db:
        db.add(
            CategoryEvaluationV3Config(
                category_key="inspiration_image",
                display_name="灵感图",
                status="active",
                contract_json=json.dumps({"spec_version": "legacy"}),
                classification_map_json="{}",
                subcategory_dimensions_json="{}",
                dimension_deduction_rules_json="{}",
                media_penalty_enabled=True,
                revision=7,
                contract_hash="legacy",
                created_by="test",
            )
        )
        db.commit()
        _seed_inspiration_image_v3_config(db)
        db.commit()
        row = db.scalar(
            select(CategoryEvaluationV3Config).where(
                CategoryEvaluationV3Config.category_key == "inspiration_image"
            )
        )
        assert row is not None
        assert row.status == "active"
        assert row.revision == 8
        assert row.media_penalty_enabled is False
        assert json.loads(row.contract_json)["spec_version"] == INSPIRATION_SPEC_VERSION
        frozen = row.contract_json
        _seed_inspiration_image_v3_config(db)
        db.commit()
        assert row.revision == 8
        assert row.contract_json == frozen
