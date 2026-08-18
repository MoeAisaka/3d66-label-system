from __future__ import annotations

import json
from types import SimpleNamespace

from app.correction_view import build_correction_view, legacy_correction_view_from_archived_fields


def _item(archive: dict) -> SimpleNamespace:
    run = SimpleNamespace(
        id=41,
        category_key="inspiration_image",
        __tablename__="baseline_regression_runs",
        correction_contract_json=None,
        correction_contract_hash=None,
        execution_snapshot_json=json.dumps(
            {"active_contract_that_must_not_be_read": {"nodes": [{"node_key": "must-not-read"}]}},
            ensure_ascii=False,
        ),
    )
    evaluation = SimpleNamespace(
        id=51,
        review_revision=2,
        correction_history_json="[]",
        precheck_json=json.dumps({"production_fields": {"title": "归档标题"}}, ensure_ascii=False),
        aesthetic_json="{}",
        scoring_json=json.dumps({"score": 70, "level": "L2"}, ensure_ascii=False),
        level="L2",
        score=70,
        reviews=[],
    )
    snapshot = {
        "archived_correction_fields": archive,
        "stage_a": {"production_fields": {"title": "归档标题"}},
        "predicted_level": "L2",
        "authoritative_score": 70,
    }
    return SimpleNamespace(
        id=61,
        run_id=41,
        run=run,
        evaluation_id=51,
        evaluation=evaluation,
        result_snapshot_json=json.dumps(snapshot, ensure_ascii=False),
    )


def _complete_node() -> dict:
    return {
        "node_key": "call_a.title",
        "layer": "A",
        "path": "call_a.title",
        "label": "素材标题",
        "description": "历史归档的素材标题纠偏字段",
        "type": "text",
        "semantic_version": "1",
        "compatibility_key": "production-field:title",
        "required": True,
        "evidence": {"description": "请提供图片证据", "required": False},
        "value": "归档标题",
    }


def test_complete_archived_subset_is_editable_without_active_fallback() -> None:
    view = legacy_correction_view_from_archived_fields(
        _item({"expected_nodes": ["call_a.title"], "nodes": [_complete_node()]})
    )

    assert view["snapshot_status"] == "legacy_compatible"
    assert view["read_only"] is False
    assert view["snapshot_source"] == "archived_item_fields"
    assert [node["node_key"] for node in view["nodes"]] == ["call_a.title"]
    assert view["nodes"][0]["editable"] is True
    assert view["nodes"][0]["model_value"] == "归档标题"
    assert view["nodes"][0].get("human_value") is None


def test_incomplete_archive_is_explicitly_read_only_and_lists_unavailable_nodes() -> None:
    incomplete = _complete_node()
    incomplete.pop("description")
    view = legacy_correction_view_from_archived_fields(
        _item(
            {
                "expected_nodes": ["call_a.title", "v3.final_level"],
                "nodes": [incomplete],
            }
        )
    )

    assert view["snapshot_status"] == "legacy_read_only"
    assert view["read_only"] is True
    assert "v3.final_level" in view["unavailable_nodes"]
    assert any("description" in item for item in view["unavailable_nodes"])
    assert all(node["editable"] is False for node in view["nodes"])


def test_build_view_uses_item_archive_when_run_has_no_contract() -> None:
    item = _item({"expected_nodes": ["call_a.title"], "nodes": [_complete_node()]})
    view = build_correction_view(None, run=item.run, item=item)

    assert view["snapshot_source"] == "archived_item_fields"
    assert view["contract"]["contract_hash"]
    assert [node["node_key"] for node in view["nodes"]] == ["call_a.title"]
