from __future__ import annotations

import asyncio
import json
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app import worker
from app.doubao import DoubaoResponse
from app.main import _category_execution_snapshot
from app.media import ProposalPdfModelInput, ProposalPdfPage
from app.dimension_schema_registry import canonical_json
from app.database import Base
from app.models import (
    Asset,
    CategoryEvaluationV3Config,
    CategoryEvaluationV3Revision,
    EvaluationCategoryProfile,
    EvaluationJob,
    EvaluationResult,
    ModelConfig,
    PromptVersion,
)
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
        assert "document.page_contact_sheet" not in {
            item["module"] for item in pipeline["processors"]
        }
        prompts = db.scalars(select(PromptVersion).where(
            PromptVersion.category_key == "proposal_text_pdf"
        )).all()
        assert len(prompts) == 2
        by_stage = {prompt.stage: prompt for prompt in prompts}
        assert by_stage["A"].version == PROPOSAL_CALL_A_VERSION
        assert by_stage["B"].version == PROPOSAL_CALL_B_VERSION
        assert by_stage["A"].system_prompt == (ASSETS / "call_a_proposal_text_v2.txt").read_text(encoding="utf-8")
        assert by_stage["B"].system_prompt == (ASSETS / "call_b_proposal_text_v2.txt").read_text(encoding="utf-8")
        assert by_stage["A"].user_prompt == by_stage["B"].user_prompt == ""
        row = db.scalar(select(CategoryEvaluationV3Config).where(
            CategoryEvaluationV3Config.category_key == "proposal_text_pdf"
        ))
        assert row is not None and row.status == "active" and row.revision == 1
        contract = json.loads(row.contract_json)
        assert contract["spec_version"] == PROPOSAL_SPEC_VERSION
        assert contract["profile_type"] == "text-proposal-additive-v1"
        assert contract["pdf_input_channel"]["long_image_stitching"] is False
        assert contract["pdf_input_channel"]["call_a"] == {
            "mode": "paged_batches", "batch_size": 16, "max_side_px": 1024,
            "scan_all_pages": True, "stop_on_redline": True,
            "redline_merge": "union",
            "information_merge": "document_first_seen_with_audit",
        }
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


def test_worker_recovers_invalid_a_batch_then_scores_source_pdf_and_audits_tokens(
    monkeypatch,
    tmp_path,
) -> None:
    engine = _engine()
    db = Session(engine, expire_on_commit=False)
    seed_defaults(db)
    profile = db.scalar(select(EvaluationCategoryProfile).where(
        EvaluationCategoryProfile.category_key == "proposal_text_pdf"
    ))
    assert profile is not None
    prompt_a = db.get(PromptVersion, profile.prompt_a_id)
    prompt_b = db.get(PromptVersion, profile.prompt_b_id)
    model = db.get(ModelConfig, profile.model_config_id)
    assert prompt_a is not None and prompt_b is not None and model is not None
    model.high_risk_review_enabled = False
    model.encrypted_api_key = "credential-reference"
    source_path = tmp_path / "sample.pdf"
    source_path.write_bytes(b"%PDF-test")
    page_image = tmp_path / "page.jpg"
    page_image.write_bytes(b"jpeg")
    asset = Asset(
        original_name="sample.pdf",
        stored_name=source_path.name,
        mime_type="application/pdf",
        size_bytes=source_path.stat().st_size,
        sha256="f" * 64,
    )
    db.add(asset)
    db.flush()
    bundle = v3_authoritative_category(db, "proposal_text_pdf")
    assert bundle is not None
    snapshot = _category_execution_snapshot(
        profile,
        prompt_a_id=prompt_a.id,
        prompt_b_id=prompt_b.id,
        model_config=model,
        v3_authoritative_bundle=bundle,
    )
    job = EvaluationJob(
        asset_id=asset.id,
        category_key="proposal_text_pdf",
        category_profile_snapshot_json=snapshot,
        prompt_a_id=prompt_a.id,
        prompt_b_id=prompt_b.id,
        status="processing",
    )
    db.add(job)
    db.commit()

    pages = tuple(
        ProposalPdfPage(
            page_number=page_number,
            text=(
                "效果图 鸟瞰图" if page_number in {5, 21}
                else "场地分析 概念推导" if page_number in {3, 19}
                else f"普通页面 {page_number}"
            ),
            text_source="text_layer",
            call_a_image_path=page_image,
        )
        for page_number in range(1, 33)
    )
    prepared = ProposalPdfModelInput(
        page_count=32,
        actual_page_count=32,
        pages=pages,
        table_of_contents=((1, "概念", 3), (1, "效果展示", 5)),
        batch_size=16,
        call_a_max_side_px=1024,
        cache_key="cache",
    )

    @contextmanager
    def test_scope():
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise

    calls: list[dict[str, object]] = []

    class FakeClient:
        def __init__(self, _config) -> None:
            pass

        async def chat_text_images(
            self, _system_prompt, user_prompt, samples, **kwargs
        ):
            call_number = len(calls) + 1
            calls.append({
                "user_prompt": user_prompt,
                "sample_count": len(samples),
                "detail": kwargs["image_detail"],
            })
            if "【引擎确定性分页输入" in user_prompt:
                parsed = {"invalid": True} if call_number <= 2 else passed_call_a()
            else:
                parsed = call_b()
            return DoubaoResponse(
                parsed=parsed,
                raw_text=json.dumps(parsed, ensure_ascii=False),
                raw_payload={"call": call_number},
                input_tokens=100 * call_number,
                output_tokens=10 * call_number,
                total_tokens=110 * call_number,
            )

    monkeypatch.setattr(worker, "session_scope", test_scope)
    monkeypatch.setattr(worker, "settings", SimpleNamespace(upload_dir=tmp_path))
    monkeypatch.setattr(worker, "DoubaoClient", FakeClient)
    monkeypatch.setattr(
        worker, "prepare_proposal_pdf_model_input",
        lambda *_args, **_kwargs: prepared,
    )
    monkeypatch.setattr(
        worker, "render_proposal_pdf_pages_high_fidelity",
        lambda *_args, page_numbers, **_kwargs: {
            page_number: page_image for page_number in page_numbers
        },
    )

    try:
        asyncio.run(worker.evaluate_job(job.id))
        assert len(calls) == 6
        assert [call["sample_count"] for call in calls] == [16, 16, 8, 8, 16, 16]
        assert [call["detail"] for call in calls] == ["low", "low", "low", "low", "low", "high"]
        assert "第1批" in calls[0]["user_prompt"]
        assert "第2批" in calls[2]["user_prompt"]
        assert "【目录结构】" in calls[5]["user_prompt"]
        db.expire_all()
        result = db.scalar(select(EvaluationResult).where(
            EvaluationResult.job_id == job.id
        ))
        assert result is not None
        preprocess = json.loads(result.preprocess_json)
        channel = preprocess["pdf_input_channel"]
        assert channel["long_image_stitching"] is False
        assert channel["evaluation_object"] == "source_pdf_document"
        assert channel["call_a"]["scanned_pages"] == list(range(1, 33))
        assert channel["call_a"]["attempted_pages"] == list(range(1, 33))
        assert channel["call_a"]["failed_pages"] == []
        assert channel["call_a"]["recovery_batches"] == [list(range(1, 17))]
        assert channel["call_a"]["batch_count"] == 3
        assert len(channel["call_b"]["representative_pages"]) == 16
        assert channel["call_b"]["evaluation_object"] == "source_pdf_document"
        assert result.score == 90
        assert result.level == "L1"
        assert channel["token_usage"] == {
            "measured": True,
            "call_a": {
                "input_tokens": 1500,
                "output_tokens": 150,
                "total_tokens": 1650,
            },
            "call_b": {
                "input_tokens": 600,
                "output_tokens": 60,
                "total_tokens": 660,
            },
            "total": {
                "input_tokens": 2100,
                "output_tokens": 210,
                "total_tokens": 2310,
            },
        }
    finally:
        db.close()
        engine.dispose()


def test_seed_upgrades_only_the_known_legacy_pdf_channel_idempotently() -> None:
    engine = _engine()
    with Session(engine) as db:
        seed_defaults(db)
        profile = db.scalar(select(EvaluationCategoryProfile).where(
            EvaluationCategoryProfile.category_key == "proposal_text_pdf"
        ))
        row = db.scalar(select(CategoryEvaluationV3Config).where(
            CategoryEvaluationV3Config.category_key == "proposal_text_pdf"
        ))
        assert profile is not None and row is not None
        legacy_pipeline = json.loads(profile.pipeline_config_json)
        for processor in legacy_pipeline["processors"]:
            if processor["module"] == "document.page_batches":
                processor["module"] = "document.page_contact_sheet"
        profile.pipeline_config_json = canonical_json(legacy_pipeline)
        legacy_contract = json.loads(row.contract_json)
        legacy_contract.pop("pdf_input_channel")
        row.contract_json = canonical_json(legacy_contract)
        original_revision = row.revision
        original_pipeline_revision = profile.pipeline_revision
        db.flush()

        seed_proposal_text_pdf(db)
        upgraded_pipeline = json.loads(profile.pipeline_config_json)
        modules = {
            processor["module"] for processor in upgraded_pipeline["processors"]
        }
        assert "document.page_batches" in modules
        assert "document.page_contact_sheet" not in modules
        assert profile.pipeline_revision == original_pipeline_revision + 1
        assert json.loads(row.contract_json)["pdf_input_channel"]["call_a"][
            "batch_size"
        ] == 16
        assert row.revision == original_revision + 1

        seed_proposal_text_pdf(db)
        assert profile.pipeline_revision == original_pipeline_revision + 1
        assert row.revision == original_revision + 1
    engine.dispose()


def test_seed_upgrades_known_v1_document_contract_to_v2() -> None:
    engine = _engine()
    with Session(engine) as db:
        seed_defaults(db)
        profile = db.scalar(select(EvaluationCategoryProfile).where(
            EvaluationCategoryProfile.category_key == "proposal_text_pdf"
        ))
        row = db.scalar(select(CategoryEvaluationV3Config).where(
            CategoryEvaluationV3Config.category_key == "proposal_text_pdf"
        ))
        assert profile is not None and row is not None
        legacy = json.loads(
            (Path(__file__).parent / "fixtures" / "proposal_text_contract_v1.json")
            .read_text(encoding="utf-8")
        )
        profile.rubric_version = legacy["spec_version"]
        row.contract_json = canonical_json(legacy)
        row.contract_hash = "legacy"
        old_revision = row.revision
        db.flush()

        seed_proposal_text_pdf(db)

        assert profile.rubric_version == PROPOSAL_SPEC_VERSION
        assert json.loads(row.contract_json)["spec_version"] == PROPOSAL_SPEC_VERSION
        assert row.revision == old_revision + 1
    engine.dispose()


def test_proposal_seed_preserves_operator_candidate_revision() -> None:
    engine = _engine()
    with Session(engine) as db:
        seed_defaults(db)
        projected = db.scalar(
            select(CategoryEvaluationV3Config).where(
                CategoryEvaluationV3Config.category_key == "proposal_text_pdf"
            )
        )
        assert projected is not None and projected.projected_revision_id is not None
        candidate_contract = json.loads(projected.contract_json)
        candidate_contract["pdf_input_channel"]["call_a"]["batch_size"] = 12
        candidate_json = canonical_json(candidate_contract)
        candidate = CategoryEvaluationV3Revision(
            category_key=projected.category_key,
            display_name="Proposal 人工候选",
            revision=projected.revision + 1,
            status="candidate",
            parent_revision_id=projected.projected_revision_id,
            contract_json=candidate_json,
            classification_map_json=projected.classification_map_json,
            subcategory_dimensions_json=projected.subcategory_dimensions_json,
            dimension_deduction_rules_json=projected.dimension_deduction_rules_json,
            media_penalty_enabled=projected.media_penalty_enabled,
            contract_hash="candidate-proposal-hash",
            created_by="operator:test",
        )
        db.add(candidate)
        db.commit()
        candidate_id = candidate.id

        seed_proposal_text_pdf(db)
        db.commit()
        preserved = db.get(CategoryEvaluationV3Revision, candidate_id)
        assert preserved is not None
        assert preserved.status == "candidate"
        assert preserved.contract_json == candidate_json
        assert preserved.contract_hash == "candidate-proposal-hash"
    engine.dispose()
