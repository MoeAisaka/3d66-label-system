from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import CategoryEvaluationV3Config, EvaluationCategoryProfile, PromptVersion
from app.proposal_text_pipeline import (
    build_deterministic_page_precheck,
    build_manual_review_precheck,
    call_validated_json,
)
from app.proposal_text_seed import (
    PROPOSAL_CALL_A_VERSION,
    PROPOSAL_CALL_B_VERSION,
    PROPOSAL_SPEC_VERSION,
    seed_proposal_text_pdf,
)
from app.seed import seed_defaults
from app.worker_v3_authoritative import (
    build_v3_authoritative_scoring,
    evaluate_v3_authoritative,
    v3_authoritative_category,
)
from tests.test_proposal_text_contract import call_b, passed_call_a


ASSETS = Path(__file__).resolve().parents[1] / "app" / "proposal_text_assets"


def _engine():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return engine


def test_seed_persists_exact_prompts_profile_and_additive_contract() -> None:
    engine = _engine()
    with Session(engine) as db:
        seed_defaults(db)
        seed_defaults(db)
        profile = db.scalar(select(EvaluationCategoryProfile).where(
            EvaluationCategoryProfile.category_key == "proposal_text_pdf"
        ))
        assert profile is not None and profile.status == "active"
        assert json.loads(profile.allowed_mime_types_json) == ["application/pdf"]
        pipeline = json.loads(profile.pipeline_config_json)
        assert pipeline["input_kind"] == "pdf"
        assert pipeline["prompt_mode"] == "follow"
        assert "document.multimodal_summary" not in {
            item["module"] for item in pipeline["processors"]
        }
        prompts = db.scalars(select(PromptVersion).where(
            PromptVersion.category_key == "proposal_text_pdf"
        )).all()
        assert len(prompts) == 2
        by_stage = {prompt.stage: prompt for prompt in prompts}
        assert by_stage["A"].version == PROPOSAL_CALL_A_VERSION
        assert by_stage["B"].version == PROPOSAL_CALL_B_VERSION
        assert by_stage["A"].system_prompt == (ASSETS / "call_a_proposal_text_v1.txt").read_text(encoding="utf-8")
        assert by_stage["B"].system_prompt == (ASSETS / "call_b_proposal_text_v1.txt").read_text(encoding="utf-8")
        assert by_stage["A"].user_prompt == by_stage["B"].user_prompt == ""
        row = db.scalar(select(CategoryEvaluationV3Config).where(
            CategoryEvaluationV3Config.category_key == "proposal_text_pdf"
        ))
        assert row is not None and row.status == "active" and row.revision == 1
        contract = json.loads(row.contract_json)
        assert contract["spec_version"] == PROPOSAL_SPEC_VERSION
        assert contract["profile_type"] == "text-proposal-additive-v1"
        assert v3_authoritative_category(db, "proposal_text_pdf")["contract"] == contract


def test_under_15_pages_is_deterministic_redline_before_models() -> None:
    precheck = build_deterministic_page_precheck("tiny.pdf", 14)
    assert precheck is not None
    assert precheck["预检结果"]["状态"] == "淘汰"
    assert precheck["红线检查"]["命中项"][0]["类型"] == "页数不足"
    assert build_deterministic_page_precheck("ok.pdf", 15) is None
    assert build_deterministic_page_precheck("unknown.pdf", None) is None


def test_invalid_output_retries_once_then_fails_closed_without_rewrite() -> None:
    calls = 0
    raw = {"score": 999}

    async def invoke():
        nonlocal calls
        calls += 1
        return SimpleNamespace(parsed=raw, raw_payload={"attempt": calls}, raw_text="bad")

    outcome = asyncio.run(call_validated_json(invoke, lambda value: (_ for _ in ()).throw(ValueError("bad"))))
    assert calls == 2
    assert outcome.value is None
    assert len(outcome.responses) == 2
    assert raw == {"score": 999}
    review = build_manual_review_precheck("bad.pdf", 18, "调用A输出连续2次校验失败")
    assert review["预检结果"]["状态"] == "人工复核"


def test_v3_proposal_dispatch_aggregates_without_extra_model_call() -> None:
    contract = json.loads((ASSETS / "v3_contract_proposal_text_v1.json").read_text(encoding="utf-8"))
    bundle = {
        "contract": contract,
        "classification_map": {"profile_type": "text-proposal-additive-v1"},
        "subcategory_dimensions": {"profile_type": "text-proposal-additive-v1"},
        "config_revision": 1,
    }
    result = asyncio.run(evaluate_v3_authoritative(
        object(), Path("unused.png"), "image/png",
        v3_bundle=bundle, precheck=passed_call_a(), aesthetic=call_b(),
    ))
    scoring = build_v3_authoritative_scoring(result, precheck=passed_call_a())
    assert scoring["score"] == 90
    assert scoring["level"] == "L1"
    assert scoring["proposal_aesthetic_score"] == 90
    assert scoring["scoring_mode"] == "v3_authoritative"

