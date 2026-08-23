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
from app.category_pipeline import default_pipeline
from app.database import Base
from app.doubao import DoubaoResponse
from app.main import _category_execution_snapshot
from app.media import PdfPreprocessResult
from app.inspiration_aesthetic_foundation import (
    DIMENSION_KEYS as INSPIRATION_DIMENSION_KEYS,
    FOUNDATION_VERSION,
)
from app.model_3d_su_category_seed import (
    MODEL_3D_SU_CALL_B_VERSION,
    MODEL_3D_SU_CATEGORY_KEY,
    seed_model_3d_su,
)
from app.models import (
    Asset,
    CategoryEvaluationV3Config,
    EvaluationCategoryProfile,
    EvaluationJob,
    EvaluationResult,
    ModelConfig,
    PromptVersion,
)
from tests.v3_contract_fixtures import add_active_v3_contract


PROJECT_ROOT = Path(__file__).resolve().parents[2]
MODEL_3D_SU_DIMENSION_KEYS = (
    "model_detail",
    "material_rendering",
    "lighting",
    "design_trend",
    "visual_composition",
)


def _inspiration_call_a_payload(*, evidence: list[str]) -> dict[str, object]:
    return {
        "redline_triggered": {"transparent_checkerboard": True},
        "reason": ["透明棋盘格"],
        "hard_defects": [],
        "image_defects": [],
        "decisive_evidence": {
            "redline_triggered": {"transparent_checkerboard": evidence},
            "hard_defects": [],
            "image_defects": [],
        },
        "decision_status": "complete",
        "uncertain_fields": [],
        "track_classification": "class_one",
        "track_confidence": 0.95,
        "media_type": "real_photo",
        "media_confidence": 0.9,
        "trait": "实景照片",
        "primary_category": "建筑设计",
        "secondary_category": "商业建筑",
        "classification_confidence": 0.9,
    }


def _inspiration_foundation_payload(score: int = 80) -> dict[str, object]:
    return {
        "contract_version": FOUNDATION_VERSION,
        "aesthetic_score": score,
        "dimensions": {
            key: {
                "grade": 3,
                "evidence": [f"{key} 可见证据"],
                "shortcomings": [f"{key} 可见不足"],
            }
            for key in INSPIRATION_DIMENSION_KEYS
        },
        "overall_evidence": ["整体构图与材质表现可见"],
        "confidence": 0.82,
    }


def _run_inspiration_redline_worker(
    monkeypatch,
    tmp_path,
    *,
    evidence: list[str],
) -> dict[str, object]:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    db = Session(engine, expire_on_commit=False)
    source_path = tmp_path / ("confirmed.jpg" if evidence else "unconfirmed.jpg")
    source_path.write_bytes(b"jpeg")
    asset = Asset(
        original_name=source_path.name,
        stored_name=source_path.name,
        mime_type="image/jpeg",
        size_bytes=source_path.stat().st_size,
        sha256=("7" if evidence else "8") * 64,
        category_key="inspiration_image",
    )
    model = ModelConfig(
        provider="custom-compatible",
        model_id="vision-inspiration-prefilter",
        encrypted_api_key="credential-reference",
        high_risk_review_enabled=False,
        active=True,
    )
    db.add_all([asset, model])
    db.flush()
    v3_bundle = add_active_v3_contract(db, "inspiration_image")
    v3_bundle["contract"]["redline_policy"]["rules"] = [
        {
            "key": "transparent_checkerboard",
            "signal": "production_fields.reason",
            "match_any": ["透明棋盘格"],
            "exemptions": [],
            "enabled": True,
        }
    ]
    config = db.scalar(
        select(CategoryEvaluationV3Config).where(
            CategoryEvaluationV3Config.category_key == "inspiration_image"
        )
    )
    config.contract_json = json.dumps(v3_bundle["contract"], ensure_ascii=False)
    bindings = v3_bundle["contract"]["prompt_bindings"]
    prompt_a = PromptVersion(
        category_key="inspiration_image",
        pipeline_scope="shared",
        stage="A",
        name="灵感图前检",
        version=bindings["call_a_version"],
        system_prompt="return inspiration precheck json",
        user_prompt="evaluate {{image_metadata}}",
        rubric_version="inspiration-rubric-v1",
        status="published",
    )
    prompt_b = PromptVersion(
        category_key="inspiration_image",
        pipeline_scope="shared",
        stage="B",
        name="灵感图美感评测",
        version=bindings["call_b_version"],
        system_prompt="return inspiration aesthetic json",
        user_prompt="evaluate {{precheck_json}}",
        rubric_version="inspiration-rubric-v1",
        status="published",
    )
    profile = EvaluationCategoryProfile(
        category_key="inspiration_image",
        display_name="灵感图",
        model_config_id=model.id,
        status="active",
        allowed_mime_types_json='["image/jpeg"]',
        preprocess_config_json='{"preprocess":"image"}',
        pipeline_config_json=json.dumps(default_pipeline("space_image")),
        rubric_version="inspiration-rubric-v1",
    )
    db.add_all([prompt_a, prompt_b, profile])
    db.flush()
    profile.prompt_a_id = prompt_a.id
    profile.prompt_b_id = prompt_b.id
    snapshot = _category_execution_snapshot(
        profile,
        prompt_a_id=prompt_a.id,
        prompt_b_id=prompt_b.id,
        model_config=model,
        v3_authoritative_bundle=v3_bundle,
    )
    job = EvaluationJob(
        asset_id=asset.id,
        category_key="inspiration_image",
        category_profile_snapshot_json=snapshot,
        prompt_a_id=prompt_a.id,
        prompt_b_id=prompt_b.id,
        status="processing",
    )
    db.add(job)
    db.commit()

    @contextmanager
    def test_scope():
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise

    calls: list[str] = []
    call_a_user_prompts: list[str] = []

    class FakeClient:
        def __init__(self, _config) -> None:
            pass

        async def chat_json(self, _system_prompt, user_prompt, **_kwargs):
            calls.append("A")
            call_a_user_prompts.append(user_prompt)
            parsed = _inspiration_call_a_payload(evidence=evidence)
            return DoubaoResponse(
                parsed=parsed,
                raw_text=json.dumps(parsed, ensure_ascii=False),
                raw_payload=parsed,
            )

        async def chat_json_images(self, _system_prompt, _samples, **_kwargs):
            calls.append("B")
            parsed = _inspiration_foundation_payload()
            return DoubaoResponse(
                parsed=parsed,
                raw_text=json.dumps(parsed, ensure_ascii=False),
                raw_payload=parsed,
            )

    monkeypatch.setattr(worker, "session_scope", test_scope)
    monkeypatch.setattr(worker, "settings", SimpleNamespace(upload_dir=tmp_path))
    monkeypatch.setattr(worker, "DoubaoClient", FakeClient)
    monkeypatch.setattr(worker, "_is_inspiration_baseline_job", lambda _job: True)
    monkeypatch.setattr(
        worker,
        "prepare_model_image",
        lambda *_args, **_kwargs: (source_path, "image/jpeg"),
    )
    from app import inspiration_aesthetic_foundation

    monkeypatch.setattr(
        inspiration_aesthetic_foundation,
        "anchor_request_from_contract",
        lambda *_args, **_kwargs: (
            [("待评图片", source_path, "image/jpeg")],
            1,
        ),
    )
    try:
        asyncio.run(worker.evaluate_job(job.id))
        db.expire_all()
        result = db.query(EvaluationResult).filter_by(job_id=job.id).one()
        return {
            "calls": calls,
            "call_a_user_prompts": call_a_user_prompts,
            "level": result.level,
            "score": result.score,
            "needs_review": result.needs_review,
            "scoring": json.loads(result.scoring_json),
            "precheck": json.loads(result.precheck_json),
        }
    finally:
        db.close()
        engine.dispose()


def test_confirmed_call_a_redline_returns_l5_without_call_b(
    monkeypatch,
    tmp_path,
) -> None:
    outcome = _run_inspiration_redline_worker(
        monkeypatch,
        tmp_path,
        evidence=["主体外区域显示透明棋盘格"],
    )

    assert outcome["calls"] == ["A"]
    assert "transparent_checkerboard" in outcome["call_a_user_prompts"][0]
    assert "透明棋盘格" in outcome["call_a_user_prompts"][0]
    assert "只能选“是截图”" not in outcome["call_a_user_prompts"][0]
    assert outcome["level"] == "L5"
    assert outcome["scoring"]["hard_reject"] is True
    assert outcome["scoring"]["hit_rules"] == ["transparent_checkerboard"]
    assert outcome["scoring"]["scoring_capabilities"]["format_version"] == (
        "scoring-capabilities-v1"
    )


def test_unconfirmed_call_a_redline_continues_to_b_and_is_not_reapplied(
    monkeypatch,
    tmp_path,
) -> None:
    outcome = _run_inspiration_redline_worker(monkeypatch, tmp_path, evidence=[])

    assert outcome["calls"] == ["A", "B"]
    assert outcome["level"] != "L5"
    assert outcome["score"] == 80
    assert outcome["scoring"]["hard_reject"] is False
    assert outcome["scoring"]["redline_prefilter"]["raw_hit"] is True
    assert outcome["scoring"]["redline_prefilter"]["hit"] is False
    assert outcome["precheck"]["production_fields"]["reason"] == ["透明棋盘格"]
    assert outcome["scoring"]["scoring_capabilities"]["format_version"] == (
        "scoring-capabilities-v1"
    )


def _combined_payload() -> dict[str, object]:
    return {
        "scope": {"is_in_scope": True, "profile_route": "space"},
        "classification": {
            "primary_category": "方案文本",
            "category_confidence": 0.95,
        },
        "media_analysis": {
            "media_type": "render",
            "ai_status": "no",
            "shooting_style": "professional",
        },
        "special_flags": {
            "is_collage": False,
            "is_multi_view_layout": True,
            "is_unfinished_site": False,
            "is_pure_white_product": False,
        },
        "quality_analysis": {
            "asset_file_damage": "none",
            "quality_issue_codes": [],
            "observable_evidence": ["页图清晰"],
        },
        "dimensions": {
            "composition_viewpoint": {"grade": 3, "evidence": ["版式清楚"]},
            "lighting_atmosphere": {"grade": 3, "evidence": ["照明说明完整"]},
            "color_material": {"grade": 3, "evidence": ["材质信息可辨"]},
            "spatial_design_coherence": {"grade": 3, "evidence": ["方案一致"]},
            "visual_hierarchy": {"grade": 3, "evidence": ["层级清楚"]},
            "detail_finish": {"grade": 3, "evidence": ["细节完整"]},
            "contemporary_relevance": {"grade": 3, "evidence": ["表达常规"]},
            "presentation_integrity": {"grade": 3, "evidence": ["页面完整"]},
        },
        "decision_rules": {
            "hard_gate_triggered": False,
            "level_cap": "none",
            "manual_review_required": False,
        },
        "overall_confidence": 0.9,
    }


def _precheck_payload() -> dict[str, object]:
    return {
        "classification": {
            "scope_status": "in_scope",
            "primary_category": "住宅空间",
            "primary_confidence": 0.95,
        },
        "media_form": {
            "rendering": {"status": "no", "confidence": 0.9, "evidence": []},
            "casual_snapshot": {"status": "no", "confidence": 0.9, "evidence": []},
            "ai_generated": {"status": "no", "confidence": 0.9, "evidence": []},
            "documentary_record": {"status": "no", "confidence": 0.9, "evidence": []},
            "collage_or_multiview": {"status": "no", "confidence": 0.9, "evidence": []},
            "unfinished_scene": {"status": "no", "confidence": 0.9, "evidence": []},
            "white_background_product": {"status": "no", "confidence": 0.9, "evidence": []},
        },
        "image_quality": {
            "quality_severity": "normal",
            "confidence": 0.95,
            "evidence": ["图像清晰"],
        },
        "needs_review": False,
        "review_reasons": [],
    }


def _aesthetic_payload() -> dict[str, object]:
    dimensions = {
        key: {"hit_rules": []}
        for key in (
            "subject_focus",
            "mood_atmosphere",
            "composition_lighting",
            "reference_value",
            "visual_impact",
        )
    }
    return {
        "aesthetic_score": 100,
        "aesthetic_evidence": ["整体画面清晰，主体与构图可见"],
        "aesthetic_confidence": 0.9,
        "overall_evidence": ["整体画面清晰，主体与构图可见"],
        "dimensions": dimensions,
        "scoring_profile": "space_aesthetic_v1.3",
        "assessment_confidence": 0.9,
        "needs_review": False,
        "review_reasons": [],
        "decision_rules": {
            "hard_gate_triggered": False,
            "level_cap": "none",
            "manual_review_required": False,
        },
    }


def _run_model_3d_su_worker(
    monkeypatch,
    tmp_path,
    *,
    dimensions: dict[str, dict[str, object]],
) -> dict[str, object]:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    db = Session(engine, expire_on_commit=False)
    source_path = tmp_path / "model-3d-su.jpg"
    source_path.write_bytes(b"jpeg")
    asset = Asset(
        original_name=source_path.name,
        stored_name=source_path.name,
        mime_type="image/jpeg",
        size_bytes=source_path.stat().st_size,
        sha256="9" * 64,
        category_key=MODEL_3D_SU_CATEGORY_KEY,
    )
    model = ModelConfig(
        provider="custom-compatible",
        model_id="vision-model-3d-su",
        encrypted_api_key="credential-reference",
        high_risk_review_enabled=False,
        active=True,
    )
    db.add_all([asset, model])
    db.commit()
    seed_model_3d_su(db, SimpleNamespace(project_root=PROJECT_ROOT))
    db.commit()
    profile = db.scalar(
        select(EvaluationCategoryProfile).where(
            EvaluationCategoryProfile.category_key == MODEL_3D_SU_CATEGORY_KEY
        )
    )
    prompt_a = db.get(PromptVersion, profile.prompt_a_id)
    prompt_b = db.get(PromptVersion, profile.prompt_b_id)
    v3_bundle = worker.v3_authoritative_category(db, MODEL_3D_SU_CATEGORY_KEY)
    snapshot = _category_execution_snapshot(
        profile,
        prompt_a_id=prompt_a.id,
        prompt_b_id=prompt_b.id,
        model_config=model,
        v3_authoritative_bundle=v3_bundle,
    )
    job = EvaluationJob(
        asset_id=asset.id,
        category_key=MODEL_3D_SU_CATEGORY_KEY,
        category_profile_snapshot_json=snapshot,
        prompt_a_id=prompt_a.id,
        prompt_b_id=prompt_b.id,
        status="processing",
    )
    db.add(job)
    db.commit()

    @contextmanager
    def test_scope():
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise

    calls: list[tuple[str, str]] = []

    class FakeClient:
        def __init__(self, _config) -> None:
            pass

        async def chat_json(self, system_prompt, user_prompt, **_kwargs):
            calls.append((system_prompt, user_prompt))
            if len(calls) == 1:
                parsed = _precheck_payload()
                parsed["classification"]["primary_category"] = "家装"
            else:
                parsed = {
                    "aesthetic_score": 100,
                    "aesthetic_evidence": ["五维评测所依据的整体画面证据"],
                    "overall_evidence": ["五维评测所依据的整体画面证据"],
                    "aesthetic_confidence": 0.9,
                    "dimensions": dimensions,
                    "overall_note": "按五维锚点完成评审。",
                }
            return DoubaoResponse(
                parsed=parsed,
                raw_text=json.dumps(parsed, ensure_ascii=False),
                raw_payload=parsed,
            )

    monkeypatch.setattr(worker, "session_scope", test_scope)
    monkeypatch.setattr(worker, "settings", SimpleNamespace(upload_dir=tmp_path))
    monkeypatch.setattr(worker, "DoubaoClient", FakeClient)
    monkeypatch.setattr(
        worker,
        "prepare_model_image",
        lambda *_args, **_kwargs: (source_path, "image/jpeg"),
    )
    try:
        asyncio.run(worker.evaluate_job(job.id))
        db.expire_all()
        result = db.query(EvaluationResult).filter_by(job_id=job.id).one()
        return {
            "calls": calls,
            "prompt_b_system": prompt_b.system_prompt,
            "job_status": db.get(EvaluationJob, job.id).status,
            "score": result.score,
            "level": result.level,
            "needs_review": result.needs_review,
            "prompt_b_version": result.prompt_b_version,
            "scoring": json.loads(result.scoring_json),
        }
    finally:
        db.close()
        engine.dispose()


@pytest.mark.parametrize(
    ("rule_profile", "expected_score", "expected_level"),
    [
        (("severe_defect", "minor_defect"), 0, "L4"),
        (("severe_defect",), 20, "L4"),
        (("obvious_defect",), 50, "L3"),
        (("minor_defect",), 80, "L1"),
        ((), 100, "L1"),
    ],
)
def test_model_3d_su_worker_uses_rule_hits_and_preserves_evidence(
    monkeypatch,
    tmp_path,
    rule_profile,
    expected_score,
    expected_level,
) -> None:
    outcome = _run_model_3d_su_worker(
        monkeypatch,
        tmp_path,
        dimensions={
            key: {
                "hit_rules": [
                    {
                        "rule_id": rule_id,
                        "confidence": "high",
                        "evidence": f"{key} 命中 {rule_id}",
                    }
                    for rule_id in rule_profile
                ]
            }
            for key in MODEL_3D_SU_DIMENSION_KEYS
        },
    )

    calls = outcome["calls"]
    assert outcome["job_status"] == "completed"
    assert len(calls) == 2
    assert "aesthetic_score" in calls[1][0]
    # system 由运营手选的调用B版本自己决定，不再被合同生成的正文冒名顶替。
    assert calls[1][0].startswith("你是 TPENG 标签实验台的 3D & SU 模型调用 B")
    # 规则清单与输出合同仍由服务端强制注入 user 侧，运营丢不掉也改不了形状。
    assert "必须逐条核验以下维度规则" in calls[1][1]
    assert "扣分规则" in calls[1][1]
    assert "hit_rules" in calls[1][1]
    assert "调用A预检字段" in calls[1][1]
    assert "{{" not in calls[1][1]
    assert "grade" not in calls[1][1]
    assert outcome["score"] == expected_score
    assert outcome["level"] == expected_level
    assert outcome["needs_review"] is False
    assert outcome["prompt_b_version"] == MODEL_3D_SU_CALL_B_VERSION
    assert outcome["scoring"]["dimension_scoring_mode"] == "rule_deduction"
    assert outcome["scoring"]["dimension_deduction_output"]["dimensions"][
        MODEL_3D_SU_DIMENSION_KEYS[0]
    ]["hit_rules"] == [
        {
            "rule_id": rule_id,
            "confidence": "high",
            "evidence": f"{MODEL_3D_SU_DIMENSION_KEYS[0]} 命中 {rule_id}",
        }
        for rule_id in rule_profile
    ]


@pytest.mark.parametrize(
    "malformation",
    ["missing_dimension", "extra_dimension", "unknown_rule", "empty_evidence"],
)
def test_model_3d_su_worker_safe_fallbacks_on_invalid_rule_output(
    monkeypatch,
    tmp_path,
    malformation,
) -> None:
    dimensions = {
        key: {"hit_rules": []}
        for key in MODEL_3D_SU_DIMENSION_KEYS
    }
    if malformation == "missing_dimension":
        dimensions.pop(MODEL_3D_SU_DIMENSION_KEYS[-1])
    elif malformation == "extra_dimension":
        dimensions["unexpected_dimension"] = {
            "hit_rules": [],
        }
    elif malformation == "unknown_rule":
        dimensions[MODEL_3D_SU_DIMENSION_KEYS[0]]["hit_rules"] = [
            {
                "rule_id": "not_configured",
                "confidence": "high",
                "evidence": "合同之外的规则",
            }
        ]
    else:
        dimensions[MODEL_3D_SU_DIMENSION_KEYS[0]]["hit_rules"] = [
            {"rule_id": "minor_defect", "confidence": "high", "evidence": ""}
        ]
    outcome = _run_model_3d_su_worker(
        monkeypatch,
        tmp_path,
        dimensions=dimensions,
    )

    assert outcome["job_status"] == "completed"
    assert outcome["score"] is None
    assert outcome["level"] is None
    assert outcome["needs_review"] is True
    assert outcome["scoring"]["scoring_mode"] == "v3_authoritative_failed"
    assert "调用B" in "；".join(outcome["scoring"]["review_reasons"])


def test_material_prompt_context_is_explicit_and_can_be_disabled() -> None:
    enabled = worker._category_prompt_context(
        category_key="material_image",
        preprocess_config={"material_focus": True},
        document_context=None,
        pdf_summary=None,
    )
    assert "纹理尺度" in enabled
    assert "接缝" in enabled
    disabled = worker._category_prompt_context(
        category_key="material_image",
        preprocess_config={"material_focus": False},
        document_context=None,
        pdf_summary=None,
    )
    assert "纹理尺度" not in disabled
    assert "接缝" not in disabled
    assert "仅提示词评测输出合同" in disabled
    assert "不得返回 dimensions" in disabled


def test_freeform_prompt_context_does_not_inject_behavior_rules() -> None:
    pipeline = default_pipeline("material_image")
    pipeline["prompt_context"] = {"instruction": "必须输出管理员指定格式"}
    context = worker._category_prompt_context(
        category_key="material_image",
        preprocess_config={"material_focus": True},
        document_context=None,
        pdf_summary=None,
        pipeline_config=pipeline,
        include_dimension_rules=False,
        freeform=True,
    )
    assert context == ""


def test_pdf_summary_validation_rejects_scores_and_invalid_confidence() -> None:
    summary = worker._validated_pdf_summary(
        {
            "document_type": "室内方案",
            "summary": "方案围绕客厅照明和材质展开。",
            "key_points": ["客厅", "照明"],
            "visual_findings": ["页图包含平面图"],
            "risks": [],
            "confidence": 0.88,
            "level": "L5",
        }
    )
    assert summary["schema_version"] == "pdf-multimodal-summary-v1"
    assert "level" not in summary
    with pytest.raises(RuntimeError, match="总结返回结构"):
        worker._validated_pdf_summary(
            {
                "document_type": "方案",
                "summary": "摘要",
                "key_points": [],
                "visual_findings": [],
                "risks": [],
                "confidence": 1.5,
            }
        )


def test_pdf_worker_summarizes_before_single_prompt_evaluation(
    monkeypatch,
    tmp_path,
) -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    db = Session(engine, expire_on_commit=False)
    source_path = tmp_path / "proposal.pdf"
    source_path.write_bytes(b"%PDF-test")
    preview_path = tmp_path / "proposal.png"
    preview_path.write_bytes(b"png")
    asset = Asset(
        original_name="proposal.pdf",
        stored_name=source_path.name,
        mime_type="application/pdf",
        size_bytes=source_path.stat().st_size,
        sha256="e" * 64,
    )
    prompt = PromptVersion(
        category_key="pdf_text",
        stage="A",
        name="PDF 单提示词",
        version="pdf-single-v1",
        system_prompt="return complete evaluation json",
        user_prompt="evaluate {{image_metadata}}",
        rubric_version="pdf-rubric-v1",
        status="published",
    )
    model = ModelConfig(
        provider="custom-compatible",
        model_id="vision-v1",
        encrypted_api_key="credential-reference",
        high_risk_review_enabled=False,
    )
    profile = EvaluationCategoryProfile(
        category_key="pdf_text",
        display_name="PDF 方案文本",
        status="active",
        allowed_mime_types_json='["application/pdf"]',
        preprocess_config_json=(
            '{"preprocess":"pdf","max_pages":4,'
            '"max_text_chars":24000,"multimodal_summary":true}'
        ),
        rubric_version="pdf-rubric-v1",
    )
    db.add_all([asset, prompt, model, profile])
    db.flush()
    v3_bundle = add_active_v3_contract(db, "pdf_text")
    snapshot = _category_execution_snapshot(
        profile,
        prompt_a_id=prompt.id,
        prompt_b_id=None,
        model_config=model,
        v3_authoritative_bundle=v3_bundle,
    )
    job = EvaluationJob(
        asset_id=asset.id,
        category_key="pdf_text",
        category_profile_snapshot_json=snapshot,
        prompt_a_id=prompt.id,
        prompt_b_id=None,
        status="processing",
    )
    db.add(job)
    db.commit()

    @contextmanager
    def test_scope():
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise

    calls: list[tuple[str, str]] = []

    class FakeClient:
        def __init__(self, _config) -> None:
            pass

        async def chat_json(self, system_prompt, user_prompt, **_kwargs):
            calls.append((system_prompt, user_prompt))
            if len(calls) == 1:
                return DoubaoResponse(
                    parsed={
                        "document_type": "室内方案",
                        "summary": "这是客厅材质与照明方案。",
                        "key_points": ["客厅", "材质"],
                        "visual_findings": ["页图包含空间效果图"],
                        "risks": [],
                        "confidence": 0.9,
                    },
                    raw_text="{}",
                    raw_payload={},
                )
            elif len(calls) == 2:
                parsed = _precheck_payload()
                parsed.update(
                    {
                        "aesthetic_score": 68,
                        "aesthetic_evidence": ["方案正文与页图信息完整"],
                        "predicted_level": "L3",
                        "predicted_score": 68,
                        "confidence": 0.86,
                        "reason": "方案正文与页图信息完整，整体达到普通可用水平。",
                    }
                )
            else:
                parsed = _aesthetic_payload()
            return DoubaoResponse(
                parsed=parsed,
                raw_text="{}",
                raw_payload=parsed,
            )

    monkeypatch.setattr(worker, "session_scope", test_scope)
    monkeypatch.setattr(worker, "settings", SimpleNamespace(upload_dir=tmp_path))
    monkeypatch.setattr(worker, "DoubaoClient", FakeClient)
    monkeypatch.setattr(
        worker,
        "prepare_pdf_model_input",
        lambda *_args, **_kwargs: PdfPreprocessResult(
            preview_path,
            "image/png",
            {
                "schema_version": "pdf-preprocess-v2",
                "text": "客厅照明与材质说明",
                "page_count": 1,
                "rendered_pages": 1,
                "text_chars": 10,
                "ocr_status": "not_needed",
            },
        ),
    )
    try:
        asyncio.run(worker.evaluate_job(job.id))
        assert len(calls) == 3
        assert "多模态前处理器" in calls[0][0]
        assert "这是客厅材质与照明方案" in calls[1][1]
        assert "客厅照明与材质说明" in calls[1][1]
        assert "hit_rules" in calls[2][1]
        db.expire_all()
        completed_job = db.get(EvaluationJob, job.id)
        result = db.query(EvaluationResult).filter_by(job_id=job.id).one()
        preprocess = json.loads(result.preprocess_json)
        aesthetic = json.loads(result.aesthetic_json) if result.aesthetic_json else None
        scoring = json.loads(result.scoring_json)
        raw_response_a = json.loads(result.raw_response_a)
        assert completed_job.status == "completed"
        assert result.level in {"L1", "L2", "L3", "L4", "L5"}
        assert preprocess["category_key"] == "pdf_text"
        assert preprocess["multimodal_summary"]["status"] == "completed"
        assert preprocess["multimodal_summary"]["model_id"] == "vision-v1"
        assert isinstance(aesthetic, dict)
        assert scoring["scoring_mode"] == "v3_authoritative"
        assert scoring["dimension_mode"] == "none"
        assert scoring["formal"] is True
        assert scoring["dimension_scoring_mode"] == "rule_deduction"
        assert scoring["v3_context"]["contract"]["category_key"] == "pdf_text"
        assert "dimensions" not in raw_response_a
        assert "不得返回 dimensions" in calls[1][1]
    finally:
        db.close()
        engine.dispose()


@pytest.mark.parametrize(
    ("mode", "selected_keys"),
    [
        ("all", []),
        ("selected", ["composition_viewpoint", "color_material"]),
        ("none", []),
    ],
)
def test_worker_uses_v3_contract_despite_legacy_dimension_mode(
    monkeypatch,
    tmp_path,
    mode,
    selected_keys,
) -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    db = Session(engine, expire_on_commit=False)
    source_path = tmp_path / f"room-{mode}.jpg"
    source_path.write_bytes(b"jpeg")
    asset = Asset(
        original_name=source_path.name,
        stored_name=source_path.name,
        mime_type="image/jpeg",
        size_bytes=source_path.stat().st_size,
        sha256=("a" if mode == "all" else "b" if mode == "selected" else "c") * 64,
        category_key="space_image",
    )
    prompt_a = PromptVersion(
        stage="A",
        name="范围与分类",
        version=f"A-dimension-{mode}",
        system_prompt="return precheck json",
        user_prompt="evaluate {{image_metadata}}",
        rubric_version="rubric-v2.1",
        status="published",
    )
    prompt_b = PromptVersion(
        stage="B",
        name="美感维度",
        version=f"B-dimension-{mode}",
        system_prompt="return aesthetic json",
        user_prompt="evaluate {{precheck_json}} {{rubric_version}}",
        rubric_version="rubric-v2.1",
        status="published",
    )
    model = ModelConfig(
        provider="custom-compatible",
        model_id=f"vision-{mode}",
        encrypted_api_key="credential-reference",
        high_risk_review_enabled=False,
    )
    pipeline = default_pipeline("space_image")
    pipeline["prompt_mode"] = "single" if mode == "none" else "ab"
    pipeline["dimensions"] = {
        "enabled": mode != "none",
        "mode": mode,
        "selected_keys": selected_keys,
    }
    profile = EvaluationCategoryProfile(
        category_key="space_image",
        display_name="空间图",
        status="active",
        allowed_mime_types_json='["image/jpeg"]',
        preprocess_config_json='{"preprocess":"image"}',
        pipeline_config_json=json.dumps(pipeline, ensure_ascii=False),
        rubric_version="rubric-v2.1",
    )
    db.add_all([asset, prompt_a, prompt_b, model, profile])
    db.flush()
    v3_bundle = add_active_v3_contract(db)
    snapshot = _category_execution_snapshot(
        profile,
        prompt_a_id=prompt_a.id,
        prompt_b_id=None if mode == "none" else prompt_b.id,
        model_config=model,
        v3_authoritative_bundle=v3_bundle,
    )
    job = EvaluationJob(
        asset_id=asset.id,
        category_key="space_image",
        category_profile_snapshot_json=snapshot,
        prompt_a_id=prompt_a.id,
        prompt_b_id=None if mode == "none" else prompt_b.id,
        status="processing",
    )
    db.add(job)
    db.commit()

    @contextmanager
    def test_scope():
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise

    calls: list[str] = []

    class FakeClient:
        def __init__(self, _config) -> None:
            pass

        async def chat_json(self, _system_prompt, user_prompt, **_kwargs):
            calls.append(user_prompt)
            parsed = _precheck_payload() if len(calls) == 1 else _aesthetic_payload()
            if len(calls) == 1 and mode == "none":
                parsed.update(
                    {
                        "predicted_level": "L3",
                        "predicted_score": 68,
                        "confidence": 0.86,
                        "reason": "主体、画质与空间表达整体达到普通可用水平。",
                    }
                )
            return DoubaoResponse(
                parsed=parsed,
                raw_text="{}",
                raw_payload=parsed,
            )

    monkeypatch.setattr(worker, "session_scope", test_scope)
    monkeypatch.setattr(worker, "settings", SimpleNamespace(upload_dir=tmp_path))
    monkeypatch.setattr(worker, "DoubaoClient", FakeClient)
    monkeypatch.setattr(
        worker,
        "prepare_model_image",
        lambda *args, **kwargs: (source_path, "image/jpeg"),
    )
    try:
        asyncio.run(worker.evaluate_job(job.id))
        db.expire_all()
        result = db.query(EvaluationResult).filter_by(job_id=job.id).one()
        scoring = json.loads(result.scoring_json)
        aesthetic = (
            json.loads(result.aesthetic_json)
            if result.aesthetic_json
            else None
        )
        assert db.get(EvaluationJob, job.id).status == "completed"
        assert len(calls) == 2
        assert "hit_rules" in calls[1]
        assert scoring["dimension_mode"] == mode
        assert scoring["dimension_selection"]["mode"] == mode
        assert scoring["scoring_mode"] == "v3_authoritative"
        assert scoring["formal"] is True
        assert scoring["dimension_scoring_mode"] == "rule_deduction"
        assert scoring["v3_context"]["contract"]["category_key"] == "space_image"
        assert isinstance(aesthetic, dict)
        assert result.level in {"L1", "L2", "L3", "L4", "L5"}
    finally:
        db.close()
        engine.dispose()
