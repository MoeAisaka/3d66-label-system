from __future__ import annotations

import json
from copy import deepcopy
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.dimension_schema_registry import canonical_hash
from app.regression import (
    compare_paired_results,
    result_fields,
    reviewed_truth_snapshot,
)


def _definition(*, label_suffix: str = "") -> dict:
    keys = (
        "presentation_integrity",
        "visual_hierarchy",
        "inspiration_reference",
    )
    return {
        "format_version": "dimension-schema-definition-v1",
        "dimensions": [
            {
                "key": key,
                "label": f"{key}{label_suffix}",
                "aggregation_role": "score",
            }
            for key in keys
        ],
        "output_contract": {
            "dimension_output_keys": list(keys),
            "unknown_key_policy": "reject",
        },
    }


def _result(
    result_id: int,
    *,
    definition: dict,
    schema_id: int = 11,
    schema_version: str = "candidate-v1",
    strategy_version: str = "strategy-bundle-v2",
) -> SimpleNamespace:
    dimensions = {
        dimension["key"]: {"grade": 3}
        for dimension in definition["dimensions"]
    }
    dimensions["legacy_extra_dimension"] = {"grade": 5}
    snapshot = {"schema_version": strategy_version}
    if strategy_version == "strategy-bundle-v2":
        snapshot.update(
            {
                "resolved_dimension_schema_id": schema_id,
                "resolved_dimension_schema_key": "dimension.candidate",
                "resolved_dimension_schema_version": schema_version,
                "resolved_dimension_schema_hash": canonical_hash(definition),
                "resolved_dimensions_snapshot": deepcopy(definition),
            }
        )
    return SimpleNamespace(
        id=result_id,
        asset_id=1,
        strategy_bundle_id=result_id,
        strategy_snapshot_json=json.dumps(snapshot, ensure_ascii=False),
        precheck_json=json.dumps(
            {
                "classification": {
                    "scope_status": "in_scope",
                    "primary_category": "测试素材",
                },
                "image_quality": {"quality_severity": "normal"},
                "media_form": {},
            },
            ensure_ascii=False,
        ),
        aesthetic_json=json.dumps(
            {
                "dimensions": dimensions,
                "decision_rules": {
                    "hard_gate_triggered": False,
                    "hard_gate_target": "none",
                    "level_cap": "none",
                },
            },
            ensure_ascii=False,
        ),
        scoring_json=json.dumps(
            {"formal": True, "caps": []},
            ensure_ascii=False,
        ),
        level="L3",
        score=65.0,
        needs_review=False,
        reviews=[],
        review_stage="completed",
        review_revision=0,
    )


def _approve(result: SimpleNamespace) -> None:
    result.reviews = [
        SimpleNamespace(
            id=21,
            reviewer_name="reviewer",
            decision="approved",
            corrected_level=None,
            corrected_score=None,
            corrections_json="[]",
            note="confirmed",
            created_at=datetime.now(timezone.utc),
        )
    ]


def test_result_contract_uses_only_bound_dimension_keys() -> None:
    definition = _definition()
    fields = result_fields(_result(1, definition=definition))

    assert tuple(fields["dimensions"]) == (
        "presentation_integrity",
        "visual_hierarchy",
        "inspiration_reference",
    )
    assert "legacy_extra_dimension" not in fields["dimensions"]
    assert fields["dimension_schema"] == {
        "binding_version": "dimension-truth-binding-v1",
        "schema_id": 11,
        "schema_key": "dimension.candidate",
        "version": "candidate-v1",
        "canonical_hash": canonical_hash(definition),
        "definition": definition,
    }


def test_result_contract_maps_legacy_alias_to_bound_dimension_key() -> None:
    definition = _definition()
    result = _result(1, definition=definition)
    aesthetic = json.loads(result.aesthetic_json)
    aesthetic["dimensions"]["contemporary_relevance"] = (
        aesthetic["dimensions"].pop("inspiration_reference")
    )
    result.aesthetic_json = json.dumps(aesthetic, ensure_ascii=False)

    fields = result_fields(result)

    assert fields["dimensions"]["inspiration_reference"] == 3
    assert "contemporary_relevance" not in fields["dimensions"]


def test_reviewed_truth_v2_freezes_full_dimension_identity() -> None:
    definition = _definition()
    result = _result(1, definition=definition)
    _approve(result)

    snapshot, _review = reviewed_truth_snapshot(result, "stable_control")

    assert snapshot["schema_version"] == "paired-truth-v2"
    assert snapshot["truth"]["dimension_schema"]["definition"] == definition
    assert (
        snapshot["truth"]["dimension_schema"]["canonical_hash"]
        == canonical_hash(definition)
    )


def test_paired_regression_rejects_different_dimension_semantics() -> None:
    baseline_definition = _definition()
    candidate_definition = _definition(label_suffix="-changed")
    baseline = _result(1, definition=baseline_definition)
    candidate = _result(
        2,
        definition=candidate_definition,
        schema_id=12,
        schema_version="candidate-v2",
    )
    _approve(baseline)
    truth_snapshot, _review = reviewed_truth_snapshot(
        baseline,
        "stable_control",
    )

    comparison = compare_paired_results(
        truth_snapshot=truth_snapshot,
        role="stable_control",
        baseline=baseline,
        candidate=candidate,
    )

    assert comparison["passed"] is False
    assert comparison["dimension_schema_mismatches"] == [
        "candidate.dimension_schema.schema_id",
        "candidate.dimension_schema.version",
        "candidate.dimension_schema.canonical_hash",
    ]
    assert comparison["failure_reasons"][0]["code"] == (
        "dimension_schema_mismatch"
    )


def test_paired_regression_rejects_tampered_truth_definition() -> None:
    definition = _definition()
    baseline = _result(1, definition=definition)
    _approve(baseline)
    truth_snapshot, _review = reviewed_truth_snapshot(
        baseline,
        "stable_control",
    )
    truth_snapshot["truth"]["dimension_schema"]["definition"][
        "dimensions"
    ][0]["label"] = "tampered"

    with pytest.raises(
        ValueError,
        match="无法复算",
    ):
        compare_paired_results(
            truth_snapshot=truth_snapshot,
            role="stable_control",
            baseline=baseline,
            candidate=_result(2, definition=definition),
        )


def test_v1_review_truth_remains_readable_without_schema_identity() -> None:
    result = _result(
        1,
        definition=_definition(),
        strategy_version="strategy-bundle-v1",
    )
    _approve(result)

    snapshot, _review = reviewed_truth_snapshot(result, "stable_control")

    assert snapshot["schema_version"] == "paired-truth-v1"
    assert "dimension_schema" not in snapshot["truth"]
