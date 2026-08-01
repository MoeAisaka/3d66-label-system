from __future__ import annotations

import asyncio
import json
from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app import worker
from app.database import Base
from app.doubao import DoubaoResponse
from app.main import _category_execution_snapshot
from app.media import PdfPreprocessResult
from app.models import (
    Asset,
    EvaluationCategoryProfile,
    EvaluationJob,
    EvaluationResult,
    ModelConfig,
    PromptVersion,
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


def test_material_prompt_context_is_explicit_and_can_be_disabled() -> None:
    enabled = worker._category_prompt_context(
        category_key="material_image",
        preprocess_config={"material_focus": True},
        document_context=None,
        pdf_summary=None,
    )
    assert "纹理尺度" in enabled
    assert "接缝" in enabled
    assert worker._category_prompt_context(
        category_key="material_image",
        preprocess_config={"material_focus": False},
        document_context=None,
        pdf_summary=None,
    ) == ""


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
    snapshot = _category_execution_snapshot(
        profile,
        prompt_a_id=prompt.id,
        prompt_b_id=None,
        model_config=model,
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
            return DoubaoResponse(
                parsed=_combined_payload(),
                raw_text="{}",
                raw_payload=_combined_payload(),
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
        assert len(calls) == 2
        assert "多模态前处理器" in calls[0][0]
        assert "这是客厅材质与照明方案" in calls[1][1]
        assert "客厅照明与材质说明" in calls[1][1]
        db.expire_all()
        completed_job = db.get(EvaluationJob, job.id)
        result = db.query(EvaluationResult).filter_by(job_id=job.id).one()
        preprocess = json.loads(result.preprocess_json)
        aesthetic = json.loads(result.aesthetic_json)
        raw_response_a = json.loads(result.raw_response_a)
        assert completed_job.status == "completed"
        assert result.level in {"L1", "L2", "L3", "L4", "L5"}
        assert preprocess["category_key"] == "pdf_text"
        assert preprocess["multimodal_summary"]["status"] == "completed"
        assert preprocess["multimodal_summary"]["model_id"] == "vision-v1"
        assert "spatial_design_coherence" not in aesthetic["dimensions"]
        assert aesthetic["dimensions"]["spatial_design_furnishing"]["grade"] == 3
        assert "detail_finish" not in aesthetic["dimensions"]
        assert aesthetic["dimensions"]["detail_completion"]["grade"] == 3
        assert "contemporary_relevance" not in aesthetic["dimensions"]
        assert aesthetic["dimensions"]["inspiration_reference"]["grade"] == 3
        assert "spatial_design_coherence" in raw_response_a["dimensions"]
        assert "spatial_design_furnishing" not in raw_response_a["dimensions"]
    finally:
        db.close()
        engine.dispose()
