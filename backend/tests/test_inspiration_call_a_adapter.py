from app.schema_adapter import adapt_inspiration_call_a_precheck


def _custom_redline_policy() -> dict:
    return {
        "format_version": "redline-policy-v1",
        "enabled": True,
        "hit_level": "L5",
        "hit_score_cap": 20,
        "rules": [
            {
                "key": "transparent_checkerboard",
                "signal": "production_fields.reason",
                "match_any": ["透明棋盘格"],
                "exemptions": [],
                "enabled": True,
            }
        ],
    }


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


def _valid_decisive_payload() -> dict:
    return {
        "redline_triggered": {
            "screenshot": False,
            "casual_photo": False,
            "text_heavy": False,
            "qr_code_heavy": False,
        },
        "reason": [],
        "hard_defects": ["fake_material"],
        "image_defects": ["corner_small_watermark"],
        "decisive_evidence": {
            "redline_triggered": {
                "screenshot": [],
                "casual_photo": [],
                "text_heavy": [],
                "qr_code_heavy": [],
            },
            "hard_defects": [
                {"key": "fake_material", "evidence": "桌面反光与纹理方向矛盾"}
            ],
            "image_defects": [
                {"key": "corner_small_watermark", "evidence": "右下角有小型品牌水印"}
            ],
        },
        "decision_status": "complete",
        "uncertain_fields": [],
        "track_classification": "class_one",
        "track_confidence": 0.91,
        "media_type": "real_photo",
        "media_confidence": 0.8,
        "trait": "实景照片",
        "primary_category": "建筑设计",
        "classification_confidence": 0.87,
    }


def test_decisive_signals_preserve_native_fields_and_validate_bidirectionally() -> None:
    adapted = adapt_inspiration_call_a_precheck(_valid_decisive_payload())
    assert adapted["hard_defects"] == ["fake_material"]
    assert adapted["image_defects"] == ["corner_small_watermark"]
    assert adapted["production_fields"]["reason"] == []
    assert adapted["production_fields"]["image_defects"] == "有水印"
    assert adapted["decisive_signal_validation"] == {"status": "valid", "reasons": []}
    assert adapted["needs_review"] is False


def test_decisive_signal_conflict_is_fail_closed() -> None:
    raw = _valid_decisive_payload()
    raw["redline_triggered"]["screenshot"] = True
    raw["decisive_evidence"]["redline_triggered"]["screenshot"] = ["浏览器栏可见"]
    adapted = adapt_inspiration_call_a_precheck(raw)
    assert adapted["decisive_signal_validation"]["status"] == "needs_review"
    assert adapted["needs_review"] is True
    assert "redline_reason_conflict:screenshot" in adapted["review_reasons"]


def test_missing_mandatory_decisive_fields_are_not_defaulted_safe() -> None:
    raw = _valid_decisive_payload()
    del raw["image_defects"]
    del raw["decisive_evidence"]
    adapted = adapt_inspiration_call_a_precheck(raw)
    assert adapted["decisive_signal_validation"]["status"] == "needs_review"
    assert "missing:image_defects" in adapted["review_reasons"]
    assert "missing:decisive_evidence" in adapted["review_reasons"]


def test_hit_without_matching_evidence_needs_review() -> None:
    raw = _valid_decisive_payload()
    raw["decisive_evidence"]["hard_defects"] = []
    adapted = adapt_inspiration_call_a_precheck(raw)
    assert adapted["decisive_signal_validation"]["status"] == "needs_review"
    assert "missing_evidence:hard_defects:fake_material" in adapted["review_reasons"]


def test_known_real_photo_modifier_without_a_defect_needs_review() -> None:
    raw = _valid_decisive_payload()
    raw["hard_defects"] = ["known_real_photo_defect"]
    raw["decisive_evidence"]["hard_defects"] = [
        {
            "key": "known_real_photo_defect",
            "evidence": "已确认实拍来源，但未定位其他硬伤",
        }
    ]
    adapted = adapt_inspiration_call_a_precheck(raw)
    assert adapted["decisive_signal_validation"]["status"] == "needs_review"
    assert (
        "modifier_without_defect:known_real_photo_defect"
        in adapted["review_reasons"]
    )


def test_missing_redline_evidence_key_is_not_defaulted_safe() -> None:
    raw = _valid_decisive_payload()
    del raw["decisive_evidence"]["redline_triggered"]["screenshot"]
    adapted = adapt_inspiration_call_a_precheck(raw)
    assert adapted["decisive_signal_validation"]["status"] == "needs_review"
    assert "invalid:evidence:redline:screenshot" in adapted["review_reasons"]


def test_contract_declared_redline_key_is_audited_without_platform_code_change() -> None:
    raw = _valid_decisive_payload()
    raw["redline_triggered"] = {"transparent_checkerboard": True}
    raw["reason"] = ["透明棋盘格"]
    raw["decisive_evidence"]["redline_triggered"] = {
        "transparent_checkerboard": ["主体外区域显示透明棋盘格"],
    }

    try:
        adapted = adapt_inspiration_call_a_precheck(
            raw,
            redline_policy=_custom_redline_policy(),
        )
    except TypeError as exc:
        raise AssertionError("调用 A 适配器必须读取冻结红线合同") from exc

    assert adapted["production_fields"]["reason"] == ["透明棋盘格"]
    assert adapted["redline_signal_validation"] == {"status": "valid", "reasons": []}
    assert adapted["non_redline_signal_validation"] == {
        "status": "valid",
        "reasons": [],
    }
    assert adapted["decisive_signal_validation"] == {"status": "valid", "reasons": []}


def test_custom_redline_missing_evidence_is_separate_from_non_redline_validity() -> None:
    raw = _valid_decisive_payload()
    raw["redline_triggered"] = {"transparent_checkerboard": True}
    raw["reason"] = ["透明棋盘格"]
    raw["decisive_evidence"]["redline_triggered"] = {
        "transparent_checkerboard": [],
    }

    try:
        adapted = adapt_inspiration_call_a_precheck(
            raw,
            redline_policy=_custom_redline_policy(),
        )
    except TypeError as exc:
        raise AssertionError("调用 A 适配器必须读取冻结红线合同") from exc

    assert adapted["redline_signal_validation"]["status"] == "needs_review"
    assert adapted["non_redline_signal_validation"] == {
        "status": "valid",
        "reasons": [],
    }
    assert (
        "missing_evidence:redline:transparent_checkerboard"
        in adapted["redline_signal_validation"]["reasons"]
    )
