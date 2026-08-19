from __future__ import annotations

import json
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.baseline_regression import (
    correction_input_snapshot,
    deterministic_correction_report,
)
from app.baseline_correction_orchestration import (
    CorrectionCandidateGenerationError,
    CorrectionOrchestrationError,
    RegisteredTuningMechanismGenerator,
    _normalize_generated_candidate,
    generate_correction_candidate,
)
from app.main import _record_baseline_correction_failure


def _completed_run() -> SimpleNamespace:
    return SimpleNamespace(
        id=17,
        status="completed",
        baseline_set_id=5,
        baseline_set_fingerprint="frozen-baseline",
        category_key="inspiration_image",
        strategy_bundle_id=9,
        execution_snapshot_json=json.dumps(
            {
                "dimension_selection": {
                    "mode": "category_default",
                    "effective_keys": ["composition"],
                }
            }
        ),
        metrics_json=json.dumps({"total": 1, "valid_predictions": 1}),
    )


def test_snapshot_freezes_human_node_and_review_evidence_without_promoting_automation() -> None:
    corrected_at = datetime(2026, 8, 14, 4, 0, tzinfo=timezone.utc)
    human_node = {
        "correction_key": "human-title",
        "node_type": "call_a_field",
        "node_path": "call_a.production_fields.title",
        "old_value": "旧标题",
        "new_value": "人工确认标题",
        "evidence": [{"field": "title", "text": "画面主体是现代住宅"}],
        "reason": "调用A标题识别错误",
        "corrector": "运营甲",
        "corrector_confidence": None,
        "corrector_policy": None,
        "corrected_at": corrected_at.isoformat(),
        "downstream_recomputed": False,
    }
    automatic_node = {
        "correction_key": "auto-final-level",
        "node_type": "final_level",
        "node_path": "final_level",
        "old_value": "L3",
        "new_value": "L2",
        "evidence": [],
        "reason": "自动混淆校准",
        "corrector": "auto-corrector-v1",
        "corrector_confidence": 0.94,
        "corrector_policy": "level-confusion-calibration-v1",
        "corrected_at": corrected_at.isoformat(),
        "downstream_recomputed": False,
    }
    review = SimpleNamespace(
        id=81,
        reviewer_name="运营乙",
        panel_id=None,
        panel_revision=None,
        stage="initial",
        decision="corrected",
        corrected_level="L2",
        corrected_score=82.0,
        note="分类正确，但标题字段需要纠正",
        corrections_json=json.dumps(
            [
                {
                    "target_type": "key_field",
                    "field_key": "production_fields.title",
                    "model_value": "旧标题",
                    "human_value": "人工确认标题",
                    "reason_codes": ["field_mismatch"],
                    "note": "以图中主体为准",
                }
            ],
            ensure_ascii=False,
        ),
        created_at=corrected_at,
    )
    evaluation = SimpleNamespace(
        id=44,
        correction_history_json=json.dumps(
            [human_node, automatic_node], ensure_ascii=False
        ),
        review_stage="completed",
        review_revision=3,
        review_panel=None,
        reviews=[review],
    )
    item = SimpleNamespace(
        id=23,
        run_id=17,
        asset_id=101,
        evaluation_id=44,
        evaluation=evaluation,
        expected_level="L1",
        status="completed",
        result_snapshot_json=json.dumps(
            {
                "predicted_level": "L3",
                "category_key": "inspiration_image",
                "level_explanation": {},
            }
        ),
    )

    snapshot = correction_input_snapshot(_completed_run(), [item])

    assert snapshot["schema_version"] == "baseline-correction-input-v2"
    context = snapshot["items"][0]["correction_context"]
    assert context["review_revision"] == 3
    assert context["final_review_id"] == 81
    assert [event["source"] for event in context["node_corrections"]] == [
        "human",
        "automatic",
    ]
    assert context["node_corrections"][0]["reason"] == "调用A标题识别错误"
    assert context["node_corrections"][0]["evidence"] == human_node["evidence"]
    assert context["human_reviews"] == [
        {
            "review_id": 81,
            "reviewer_name": "运营乙",
            "stage": "initial",
            "decision": "corrected",
            "corrected_level": "L2",
            "corrected_score": 82.0,
            "note": "分类正确，但标题字段需要纠正",
            "corrections": json.loads(review.corrections_json),
            "panel_id": None,
            "panel_revision": None,
            "created_at": corrected_at.isoformat(),
            "is_final": True,
        }
    ]
    assert context["human_evidence_count"] == 2
    assert context["automatic_evidence_count"] == 1
    assert context["affected_layers"] == ["A"]

    report = deterministic_correction_report(snapshot)
    assert report["evidence_summary"] == {
        "selected_sample_count": 1,
        "samples_with_human_evidence": 1,
        "human_evidence_count": 2,
        "automatic_evidence_count": 1,
        "coverage_rate": 1.0,
        "affected_layer_counts": {"A": 1},
        "affected_layers": ["A"],
    }
    assert report["sample_evidence"][0]["human_node_corrections"] == [
        context["node_corrections"][0]
    ]
    assert report["sample_evidence"][0][
        "excluded_automatic_evidence_count"
    ] == 1
    assert report["candidate_routing"] == {
        "policy": "human_evidence_only",
        "affected_layers": ["A"],
        "allowed_prompt_stages": ["A"],
        "required_prompt_stage": "A",
    }


@pytest.mark.parametrize(
    ("affected_layers", "human_count", "allowed", "required"),
    [
        (["A"], 1, ["A"], "A"),
        (["B"], 2, ["B"], "B"),
        (["V3"], 1, ["A", "B"], None),
        (["A", "B"], 2, ["A", "B"], None),
        ([], 0, ["A", "B"], None),
    ],
)
def test_report_derives_prompt_stage_constraint_from_human_layers(
    affected_layers: list[str],
    human_count: int,
    allowed: list[str],
    required: str | None,
) -> None:
    context = {
        "schema_version": "baseline-correction-human-evidence-v1",
        "evaluation_id": 44,
        "review_stage": "completed" if human_count else "initial",
        "review_revision": 1 if human_count else 0,
        "final_review_id": 81 if human_count else None,
        "node_corrections": [],
        "human_reviews": [],
        "human_evidence_count": human_count,
        "automatic_evidence_count": 0,
        "affected_layers": affected_layers,
    }
    report = deterministic_correction_report(
        {
            "schema_version": "baseline-correction-input-v2",
            "category_key": "inspiration_image",
            "baseline_run_id": 17,
            "run_metrics": {"total": 1},
            "items": [
                {
                    "item_id": 23,
                    "asset_id": 101,
                    "evaluation_id": 44,
                    "expected_level": "L1",
                    "predicted_level": "L3",
                    "level_explanation": {},
                    "correction_context": context,
                }
            ],
        }
    )

    assert report["schema_version"] == "baseline-correction-report-v2"
    assert report["candidate_routing"] == {
        "policy": "human_evidence_only",
        "affected_layers": affected_layers,
        "allowed_prompt_stages": allowed,
        "required_prompt_stage": required,
    }
    assert report["evidence_summary"]["coverage_rate"] == (
        1.0 if human_count else 0.0
    )


def _generated_candidate(stage: str) -> dict:
    return {
        "prompt": {
            "stage": stage,
            "system_prompt": f"{stage} system prompt with complete anchors",
            "user_prompt": f"{stage} user prompt",
            "change_note": f"adjust stage {stage}",
        },
        "revision": {
            "display_name": f"candidate-{stage}",
            "contract": {"category_key": "inspiration_image"},
            "classification_map": {},
            "subcategory_dimensions": {},
        },
        "summary": {"change_codes": [f"stage_{stage.lower()}"]},
    }


def _prepared(routing: dict) -> SimpleNamespace:
    return SimpleNamespace(
        orchestration={},
        report={
            "schema_version": "baseline-correction-report-v2",
            "candidate_routing": routing,
        },
        correction=SimpleNamespace(category_key="inspiration_image"),
        active_revision=SimpleNamespace(id=1),
        active_prompts={},
    )


@pytest.mark.parametrize(
    ("required_stage", "generated_stage"),
    [("A", "B"), ("B", "A")],
)
def test_candidate_generation_rejects_prompt_stage_that_conflicts_with_human_evidence(
    required_stage: str,
    generated_stage: str,
) -> None:
    prepared = _prepared(
        {
            "policy": "human_evidence_only",
            "affected_layers": [required_stage],
            "allowed_prompt_stages": [required_stage],
            "required_prompt_stage": required_stage,
        }
    )
    generator = SimpleNamespace(
        generate=lambda **_kwargs: _generated_candidate(generated_stage)
    )

    with pytest.raises(CorrectionOrchestrationError) as exc_info:
        generate_correction_candidate(prepared, generator)

    assert exc_info.value.code == "CORRECTION_PROMPT_STAGE_MISMATCH"
    assert required_stage in str(exc_info.value)


@pytest.mark.parametrize("generated_stage", ["A", "B"])
def test_candidate_generation_allows_legal_stage_for_mixed_or_v3_evidence(
    generated_stage: str,
) -> None:
    prepared = _prepared(
        {
            "policy": "human_evidence_only",
            "affected_layers": ["A", "V3"],
            "allowed_prompt_stages": ["A", "B"],
            "required_prompt_stage": None,
        }
    )
    generator = SimpleNamespace(
        generate=lambda **_kwargs: _generated_candidate(generated_stage)
    )

    candidate = generate_correction_candidate(prepared, generator)

    assert candidate.prompt.stage == generated_stage


def test_registered_tuner_receives_human_evidence_and_routing_constraints(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    class CapturingClient:
        def __init__(self, config: object) -> None:
            captured["config"] = config

        async def chat_json(
            self,
            system_prompt: str,
            user_prompt: str,
            **kwargs: object,
        ) -> SimpleNamespace:
            captured["system_prompt"] = system_prompt
            captured["user_prompt"] = user_prompt
            captured["kwargs"] = kwargs
            return SimpleNamespace(parsed=_generated_candidate("A"))

    from app import baseline_correction_orchestration as orchestration_module

    monkeypatch.setattr(
        orchestration_module,
        "DoubaoClient",
        CapturingClient,
    )
    monkeypatch.setattr(
        orchestration_module,
        "revision_bundle",
        lambda _revision: {
            "contract": {"category_key": "inspiration_image"},
            "classification_map": {},
            "subcategory_dimensions": {},
        },
    )
    monkeypatch.setattr(
        orchestration_module,
        "validate_mechanism_artifacts",
        lambda *_args, **_kwargs: "image-rule-deduction-v1",
    )
    entry = SimpleNamespace(
        id=7,
        role="tuning",
        provider="doubao",
        protocol="responses",
        model_id="tuning-model",
        thinking_mode="disabled",
        level="advanced",
        max_tokens=4096,
    )
    report = {
        "schema_version": "baseline-correction-report-v2",
        "sample_evidence": [
            {
                "item_id": 23,
                "human_node_corrections": [
                    {
                        "node_type": "call_a_field",
                        "reason": "调用A标题识别错误",
                        "evidence": ["画面主体是现代住宅"],
                        "source": "human",
                    }
                ],
            }
        ],
        "candidate_routing": {
            "policy": "human_evidence_only",
            "affected_layers": ["A"],
            "allowed_prompt_stages": ["A"],
            "required_prompt_stage": "A",
        },
    }
    active_prompt = SimpleNamespace(
        stage="A",
        name="现役A",
        version="A1",
        system_prompt="active system",
        user_prompt="active user",
        rubric_version="R1",
    )

    RegisteredTuningMechanismGenerator(entry, SimpleNamespace()).generate(
        db=SimpleNamespace(),
        correction=SimpleNamespace(category_key="inspiration_image"),
        active_revision=SimpleNamespace(id=1),
        active_prompts={"A": active_prompt},
        report=report,
    )

    generator_input = json.loads(str(captured["user_prompt"]))
    assert generator_input["schema_version"] == (
        "baseline-correction-generator-input-v3"
    )
    assert generator_input["correction_report"] == report
    assert generator_input["routing_constraints"] == report[
        "candidate_routing"
    ]


def _active_candidate_inputs() -> tuple[SimpleNamespace, dict[str, SimpleNamespace]]:
    revision = SimpleNamespace(
        display_name="现役等级规则",
        category_key="inspiration_image",
        revision=7,
        contract_hash="a" * 64,
        contract_json=json.dumps(
            {
                "category_key": "inspiration_image",
                "spec_version": "active-v1",
                "levels": {"L1": 80, "L2": 60},
            }
        ),
        classification_map_json=json.dumps(
            {"format_version": "classification-map-v1", "routes": {"住宅": "space"}}
        ),
        subcategory_dimensions_json=json.dumps(
            {"space": {"label": "空间", "dimensions": ["composition"]}}
        ),
        dimension_deduction_rules_json="{}",
        media_penalty_enabled=False,
    )
    prompts = {
        "A": SimpleNamespace(
            stage="A",
            name="现役 A",
            version="A1",
            system_prompt="active system prompt",
            user_prompt="active user prompt",
            rubric_version="R1",
        )
    }
    return revision, prompts


def test_partial_candidate_inherits_unchanged_active_revision_and_prompt() -> None:
    active_revision, active_prompts = _active_candidate_inputs()

    candidate = _normalize_generated_candidate(
        {
            "prompt": {
                "stage": "A",
                "system_prompt": "candidate system prompt",
                "change_note": "只调整调用 A 的判定说明",
            },
            "revision": {
                "display_name": "自动纠偏候选",
                "contract": {"spec_version": "candidate-v2"},
            },
        },
        active_revision=active_revision,
        active_prompts=active_prompts,
    )

    assert candidate.prompt.system_prompt == "candidate system prompt"
    assert candidate.prompt.user_prompt == "active user prompt"
    assert candidate.revision.contract == {
        "category_key": "inspiration_image",
        "spec_version": "candidate-v2",
        "levels": {"L1": 80, "L2": 60},
    }
    assert candidate.revision.classification_map == {
        "format_version": "classification-map-v1",
        "routes": {"住宅": "space"},
    }
    assert candidate.revision.subcategory_dimensions == {
        "space": {"label": "空间", "dimensions": ["composition"]}
    }


def test_partial_candidate_rejects_explicit_invalid_active_artifact_replacement() -> None:
    active_revision, active_prompts = _active_candidate_inputs()

    with pytest.raises(CorrectionOrchestrationError) as exc_info:
        _normalize_generated_candidate(
            {
                "prompt": {
                    "stage": "A",
                    "change_note": "错误类型必须受控失败",
                },
                "revision": {"classification_map": []},
            },
            active_revision=active_revision,
            active_prompts=active_prompts,
        )

    assert exc_info.value.code == "CORRECTION_GENERATOR_OUTPUT_INVALID"
    assert "revision.classification_map" in str(exc_info.value)


def test_registered_tuner_repairs_invalid_candidate_once_and_records_trace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    active_revision, active_prompts = _active_candidate_inputs()
    responses = [
        SimpleNamespace(
            parsed={
                "prompt": {"stage": "A", "change_note": "first invalid"},
                "revision": {"classification_map": []},
            },
            raw_text='{"revision":{"classification_map":[]}}',
            request_correlation_id="request-invalid",
            attempt_count=1,
            input_tokens=120,
            output_tokens=60,
            total_tokens=180,
        ),
        SimpleNamespace(
            parsed={
                "prompt": {
                    "stage": "A",
                    "system_prompt": "repaired system prompt",
                    "change_note": "repair invalid structure",
                },
                "revision": {
                    "display_name": "自动修复候选",
                    "contract": {"spec_version": "candidate-repaired"},
                },
            },
            raw_text='{"revision":{"display_name":"自动修复候选"}}',
            request_correlation_id="request-repaired",
            attempt_count=1,
            input_tokens=140,
            output_tokens=70,
            total_tokens=210,
        ),
    ]
    calls: list[dict[str, object]] = []

    class RepairingClient:
        def __init__(self, _config: object) -> None:
            pass

        async def chat_json(
            self,
            system_prompt: str,
            user_prompt: str,
            **kwargs: object,
        ) -> SimpleNamespace:
            calls.append(
                {
                    "system_prompt": system_prompt,
                    "user_prompt": user_prompt,
                    "kwargs": kwargs,
                }
            )
            return responses[len(calls) - 1]

    from app import baseline_correction_orchestration as orchestration_module

    monkeypatch.setattr(orchestration_module, "DoubaoClient", RepairingClient)
    monkeypatch.setattr(
        orchestration_module,
        "validate_mechanism_artifacts",
        lambda *_args, **_kwargs: "image-rule-deduction-v1",
    )
    entry = SimpleNamespace(
        id=7,
        role="tuning",
        provider="doubao",
        protocol="responses",
        model_id="tuning-model",
        thinking_mode="disabled",
        level="advanced",
        max_tokens=4096,
    )

    candidate = RegisteredTuningMechanismGenerator(
        entry, SimpleNamespace(encrypted_api_key="protected")
    ).generate(
        db=SimpleNamespace(),
        correction=SimpleNamespace(category_key="inspiration_image"),
        active_revision=active_revision,
        active_prompts=active_prompts,
        report={
            "candidate_routing": {
                "affected_layers": ["A"],
                "allowed_prompt_stages": ["A"],
                "required_prompt_stage": "A",
            }
        },
    )

    assert len(calls) == 2
    assert "revision.classification_map" in str(calls[1]["user_prompt"])
    assert candidate.revision.contract["spec_version"] == "candidate-repaired"
    assert [entry["status"] for entry in candidate.generation_trace] == [
        "invalid",
        "valid",
    ]
    assert candidate.generation_trace[0]["request_correlation_id"] == (
        "request-invalid"
    )
    assert candidate.generation_trace[1]["request_correlation_id"] == (
        "request-repaired"
    )


def test_registered_tuner_retry_preserves_valid_fields_from_previous_attempt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    active_revision, active_prompts = _active_candidate_inputs()
    active_prompts["A"].user_prompt = ""
    responses = [
        SimpleNamespace(
            parsed={
                "prompt": {
                    "stage": "A",
                    "system_prompt": "expanded system prompt with correction evidence",
                    "change_note": "tighten the system rules",
                },
                "revision": {},
                "summary": {"change_codes": ["tighten_system_rules"]},
            },
            raw_text='{"prompt":{"stage":"A","system_prompt":"expanded"}}',
            request_correlation_id="request-system",
            attempt_count=1,
            input_tokens=120,
            output_tokens=60,
            total_tokens=180,
        ),
        SimpleNamespace(
            parsed={
                "prompt": {
                    "stage": "A",
                    "system_prompt": [],
                    "user_prompt": "repaired user prompt",
                    "change_note": "supply the missing user prompt",
                },
                "revision": {},
                "summary": {"change_codes": ["repair_missing_user_prompt"]},
            },
            raw_text='{"prompt":{"stage":"A","user_prompt":"repaired"}}',
            request_correlation_id="request-user",
            attempt_count=1,
            input_tokens=140,
            output_tokens=70,
            total_tokens=210,
        ),
    ]

    class RepairingClient:
        def __init__(self, _config: object) -> None:
            pass

        async def chat_json(
            self,
            system_prompt: str,
            user_prompt: str,
            **kwargs: object,
        ) -> SimpleNamespace:
            del system_prompt, user_prompt, kwargs
            return responses.pop(0)

    from app import baseline_correction_orchestration as orchestration_module

    monkeypatch.setattr(orchestration_module, "DoubaoClient", RepairingClient)
    monkeypatch.setattr(
        orchestration_module,
        "validate_mechanism_artifacts",
        lambda *_args, **_kwargs: "image-rule-deduction-v1",
    )
    entry = SimpleNamespace(
        id=7,
        role="tuning",
        provider="doubao",
        protocol="responses",
        model_id="tuning-model",
        thinking_mode="disabled",
        level="advanced",
        max_tokens=4096,
    )

    candidate = RegisteredTuningMechanismGenerator(
        entry, SimpleNamespace(encrypted_api_key="protected")
    ).generate(
        db=SimpleNamespace(),
        correction=SimpleNamespace(category_key="inspiration_image"),
        active_revision=active_revision,
        active_prompts=active_prompts,
        report={
            "candidate_routing": {
                "affected_layers": ["A"],
                "allowed_prompt_stages": ["A"],
                "required_prompt_stage": "A",
            }
        },
    )

    assert candidate.prompt.system_prompt == (
        "expanded system prompt with correction evidence"
    )
    assert candidate.prompt.user_prompt == "repaired user prompt"
    assert candidate.prompt.change_note == "supply the missing user prompt"
    assert candidate.summary == {"change_codes": ["repair_missing_user_prompt"]}
    assert [entry["status"] for entry in candidate.generation_trace] == [
        "invalid",
        "valid",
    ]


def test_terminal_candidate_generation_failure_persists_bounded_trace() -> None:
    row = SimpleNamespace(
        stage="candidate_generation",
        attempt_count=1,
        orchestration_json=json.dumps({"base_projection": {"revision": 7}}),
        status="processing",
        progress=35,
        blockers_json="[]",
        error_code="",
        error_message="",
        finished_at=None,
    )
    error = CorrectionCandidateGenerationError(
        "CORRECTION_GENERATOR_OUTPUT_INVALID",
        "revision.classification_map 类型无效",
        generation_trace=[
            {
                "attempt": 1,
                "status": "invalid",
                "raw_text": "first raw output",
                "error_code": "CORRECTION_GENERATOR_OUTPUT_INVALID",
            },
            {
                "attempt": 2,
                "status": "invalid",
                "raw_text": "second raw output",
                "error_code": "CORRECTION_GENERATOR_OUTPUT_INVALID",
            },
        ],
    )

    _record_baseline_correction_failure(SimpleNamespace(), row, error)

    orchestration = json.loads(row.orchestration_json)
    assert orchestration["base_projection"] == {"revision": 7}
    assert [entry["raw_text"] for entry in orchestration["generation_trace"]] == [
        "first raw output",
        "second raw output",
    ]
    assert row.status == "failed"
    assert row.error_code == "CORRECTION_GENERATOR_OUTPUT_INVALID"
