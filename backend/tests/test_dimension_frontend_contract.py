from __future__ import annotations

import json
from types import SimpleNamespace

from app.dimension_schema_registry import canonical_hash
from app.main import _evaluation_dimension_schema_payload


def _result(
    *,
    definition: dict | None = None,
    strategy_version: str | None = "strategy-bundle-v2",
) -> SimpleNamespace:
    snapshot: dict = {}
    if strategy_version is not None:
        snapshot["schema_version"] = strategy_version
    if definition is not None and strategy_version == "strategy-bundle-v2":
        snapshot.update(
            {
                "resolved_dimension_schema_id": 91,
                "resolved_dimension_schema_key": "dimension.non-eight",
                "resolved_dimension_schema_version": "test-v1",
                "resolved_dimension_schema_hash": canonical_hash(definition),
                "resolved_dimensions_snapshot": definition,
            }
        )
    return SimpleNamespace(
        strategy_snapshot_json=(
            json.dumps(snapshot, ensure_ascii=False)
            if strategy_version is not None
            else None
        ),
        aesthetic_json=json.dumps(
            {
                "scoring_profile": "space_aesthetic_v1.3",
                "dimensions": {
                    "clarity": {"grade": 4},
                    "utility": {"grade": 3},
                },
            },
            ensure_ascii=False,
        ),
    )


def test_frontend_payload_exposes_non_eight_bound_contract() -> None:
    definition = {
        "format_version": "dimension-schema-definition-v1",
        "dimensions": [
            {"key": "clarity", "label": "清晰度"},
            {"key": "novelty", "label": "新鲜度"},
            {"key": "utility", "label": "可用性"},
        ],
        "output_contract": {
            "dimension_output_keys": [
                "clarity",
                "novelty",
                "utility",
            ],
            "unknown_key_policy": "reject",
        },
    }

    payload = _evaluation_dimension_schema_payload(
        _result(definition=definition)
    )

    assert payload == {
        "status": "resolved",
        "schema_id": 91,
        "schema_key": "dimension.non-eight",
        "version": "test-v1",
        "canonical_hash": canonical_hash(definition),
        "legacy_derived": False,
        "dimension_keys": ["clarity", "novelty", "utility"],
        "definition": definition,
        "error": None,
    }


def test_frontend_payload_keeps_legacy_results_readable() -> None:
    payload = _evaluation_dimension_schema_payload(
        _result(strategy_version=None)
    )

    assert payload["status"] == "resolved"
    assert payload["schema_id"] is None
    assert payload["legacy_derived"] is True
    assert len(payload["dimension_keys"]) == 8
    assert (
        payload["definition"]["compatibility_revision"]
        == "active_v1_3"
    )


def test_frontend_payload_fails_closed_for_invalid_contract() -> None:
    payload = _evaluation_dimension_schema_payload(
        _result(strategy_version="unknown")
    )

    assert payload["status"] == "invalid"
    assert payload["definition"] is None
    assert payload["dimension_keys"] == ["clarity", "utility"]
    assert "不受支持" in payload["error"]
