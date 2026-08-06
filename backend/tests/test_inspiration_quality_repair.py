from __future__ import annotations

import copy

from app.inspiration_aesthetic_foundation import (
    AESTHETIC_CALL_B_VERSION,
    DIMENSION_KEYS,
    apply_aesthetic_v3_rules,
    build_prompt,
)
from app.inspiration_category_seed import (
    INSPIRATION_SEED_VERSION,
    build_inspiration_classification_map,
    build_inspiration_v3_contract,
)


def _foundation(score: int = 88) -> dict:
    return {
        "contract_version": "inspiration-aesthetic-foundation-v1",
        "aesthetic_score": score,
        "dimensions": {
            key: {
                "grade": 3,
                "evidence": [f"{key} 可见证据"],
                "shortcomings": [f"{key} 可见不足"],
            }
            for key in DIMENSION_KEYS
        },
        "overall_evidence": ["整体可见证据"],
        "confidence": 0.82,
    }


def _precheck(*, reason: str = "无红线", track: str = "建筑设计") -> dict:
    return {
        "classification": {
            "scope_status": "in_scope",
            "primary_category": track,
            "primary_confidence": 0.95,
        },
        "production_fields": {"reason": [reason], "trait": "实景照片"},
        "hard_defects": [],
        "image_defects": [],
        "decisive_signal_validation": {"status": "valid"},
    }


def _apply(precheck: dict, foundation: dict | None) -> dict:
    return apply_aesthetic_v3_rules(
        contract=build_inspiration_v3_contract(),
        classification_map=build_inspiration_classification_map(),
        precheck=precheck,
        foundation=foundation,
    )


def test_l3_recall_prompt_restores_detailed_anchor_calibration() -> None:
    prompt = build_prompt()
    assert AESTHETIC_CALL_B_VERSION == (
        "inspiration-b-v5-anchor-calibration-evidence-20260807"
    )
    assert INSPIRATION_SEED_VERSION == "inspiration-category-seed-v7-quality-gates"
    assert "2045/L1：螺旋楼梯" in prompt
    assert "747/L2：紫绿床品" in prompt
    assert "1263/L3：荷花与祝福文字" in prompt
    assert "601/L4：居中灯笼记录照" in prompt
    assert "清晰且无明显硬伤不等于高分" in prompt
    assert "75分边界" in prompt
    assert '"contract_version": "inspiration-aesthetic-foundation-v1"' in prompt
    assert '"evidence": ["必须填写至少一条待评图可见证据"]' in prompt


def test_asset_390_casual_blurry_grayish_is_l5_redline() -> None:
    item = _precheck(reason="是随手拍")
    item["hard_defects"] = ["blurry_grayish"]
    result = _apply(item, None)
    assert result["hard_reject"] is True
    assert result["level"] == "L5"
    assert result["hit_rules"] == ["casual_snapshot"]


def test_non_redline_casual_snapshot_is_soft_capped_to_l4() -> None:
    result = _apply(_precheck(reason="是随手拍"), _foundation(61))
    assert result["hard_reject"] is False
    assert result["inspiration_aesthetic_score"] == 61
    assert result["score"] == 59
    assert result["level"] == "L4"
    assert {"rule": "casual_snapshot_soft_cap", "cap_to": 59} in result["caps"]


def _watermark_precheck(evidence: str) -> dict:
    item = _precheck(track="产品设计")
    item["image_defects"] = ["subject_obscuring_watermark"]
    item["decisive_evidence"] = {
        "image_defects": {"subject_obscuring_watermark": evidence}
    }
    return item


def _clean_brand_wordmark_foundation() -> dict:
    payload = _foundation(88)
    for key in ("detail_completion", "presentation_integrity"):
        payload["dimensions"][key] = {
            "grade": 4,
            "evidence": [f"{key} 主体完整清晰"],
            "shortcomings": [],
        }
    return payload


def test_asset_747_brand_wordmark_is_narrowly_exempt_from_tier_a_cap() -> None:
    item = _watermark_precheck(
        "画面中下偏下位置的白色品牌文字TEKLA位于床品面料上"
    )
    before = copy.deepcopy(item)
    result = _apply(item, _clean_brand_wordmark_foundation())
    assert result["hard_reject"] is False
    assert result["inspiration_aesthetic_score"] == 88
    assert result["score"] == 80
    assert result["level"] == "L2"
    assert {
        "rule": "hard_defect_exemption",
        "key": "subject_obscuring_brand_wordmark",
        "defect_key": "subject_obscuring_watermark",
    } in result["caps"]
    assert item == before


def test_true_subject_obscuring_watermark_still_keeps_tier_a_cap() -> None:
    payload = _clean_brand_wordmark_foundation()
    payload["dimensions"]["detail_completion"]["shortcomings"] = [
        "半透明商业水印破坏视觉连贯性"
    ]
    result = _apply(
        _watermark_precheck("半透明版权水印覆盖主体内容"),
        payload,
    )
    assert result["hard_reject"] is False
    assert result["score"] == 20
    assert result["level"] == "L4"
    assert any(cap.get("rule") == "hard_defect_severity" for cap in result["caps"])
    assert not any(cap.get("rule") == "hard_defect_exemption" for cap in result["caps"])
