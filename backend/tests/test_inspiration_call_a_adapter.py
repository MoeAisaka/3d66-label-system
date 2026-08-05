from app.schema_adapter import adapt_inspiration_call_a_precheck


def test_inspiration_call_a_is_projected_to_v3_facts_without_truth_or_score() -> None:
    raw = {
        "redline_triggered": {
            "screenshot": True,
            "casual_photo": False,
            "text_heavy": True,
            "qr_code_heavy": False,
        },
        "hard_defects": ["fake_material"],
        "track_classification": "class_one",
        "track_confidence": 0.91,
        "media_type": "3d_render",
        "media_confidence": 0.8,
        "primary_category": "建筑设计",
        "classification_confidence": 0.87,
    }
    adapted = adapt_inspiration_call_a_precheck(raw)
    assert adapted["classification"] == {
        "scope_status": "in_scope",
        "primary_category": "建筑设计",
        "primary_confidence": 0.87,
    }
    assert adapted["production_fields"] == {
        "reason": ["是截图", "有大面积文字说明"],
        "trait": "3D数字效果图",
    }
    assert adapted["hard_defects"] == ["fake_material"]
    assert "score" not in adapted["production_fields"]


def test_standard_precheck_is_not_rewritten() -> None:
    raw = {"classification": {"scope_status": "in_scope"}}
    assert adapt_inspiration_call_a_precheck(raw) is raw
