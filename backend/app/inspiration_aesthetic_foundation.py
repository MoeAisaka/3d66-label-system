"""灵感图美感分前置合同：调用B校验、冻结与纯规则定级。"""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any

from .category_evaluation_aggregator import (
    _apply_v2_hard_defect_policy,
    _score_to_level,
    _trait_to_media_key,
)
from .redline_policy import evaluate_redlines
from .subcategory_resolver import resolve_subcategory

AESTHETIC_CALL_B_VERSION = "inspiration-b-v5-anchor-calibration-evidence-20260807"
FOUNDATION_VERSION = "inspiration-aesthetic-foundation-v1"
DIMENSION_KEYS = (
    "composition_viewpoint",
    "lighting_atmosphere",
    "color_material",
    "spatial_design_furnishing",
    "visual_hierarchy",
    "detail_completion",
    "inspiration_reference",
    "presentation_integrity",
)
FORBIDDEN_FINAL_FIELDS = frozenset({
    "final_level", "level", "predicted_level", "predicted_score",
    "final_score", "published_fields", "production_fields", "tags",
})
ANCHORS = (
    {"asset_id": 2045, "level": "L1", "stored_name": "e69c40b67b2c4b7ea79d98c29d676025.png", "mime_type": "image/png", "sha256": "c5f3ef019941e01d6246316e2c0639f76750674cac1f4e5f352017e9d5d5cf92"},
    {"asset_id": 747, "level": "L2", "stored_name": "e7c1e9a1a8ba46afba105de3301b480d.jpg", "mime_type": "image/jpeg", "sha256": "feaaf40df9d305642ddb43687d08ab78d3afd8d1244f91590892583328a11c5b"},
    {"asset_id": 1263, "level": "L3", "stored_name": "9283eaef182a4b34986c57dabb7436ff.jpg", "mime_type": "image/jpeg", "sha256": "42a81a0b5dd4952fbdbad81aac3bd9798628be83536ce427b059813c831e896f"},
    {"asset_id": 601, "level": "L4", "stored_name": "9ea4c640fc8d4702a854ce63007cb287.png", "mime_type": "image/png", "sha256": "bd563a44032bf39ea58c1bf9c14f889b96de72555b0d8627e9ec65bd628b1b91"},
)


class AestheticFoundationError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def validate_aesthetic_output(payload: Any) -> dict[str, Any]:
    """严格校验调用B；任何缺失、额外维度或0值均 fail-closed。"""
    if not isinstance(payload, dict):
        raise AestheticFoundationError("payload_not_object", "调用B输出必须是JSON对象")
    forbidden = FORBIDDEN_FINAL_FIELDS.intersection(payload)
    if forbidden:
        raise AestheticFoundationError(
            "forbidden_final_fields", f"调用B不得输出最终等级/发布字段：{sorted(forbidden)}"
        )
    allowed = {"contract_version", "aesthetic_score", "dimensions", "overall_evidence", "confidence"}
    if set(payload) != allowed:
        raise AestheticFoundationError("top_level_shape_invalid", "调用B顶层字段不符合冻结合同")
    if payload.get("contract_version") != FOUNDATION_VERSION:
        raise AestheticFoundationError("contract_version_invalid", "调用B合同版本不匹配")
    score = payload.get("aesthetic_score")
    if not _is_int(score) or not 0 <= score <= 100:
        raise AestheticFoundationError("aesthetic_score_invalid", "aesthetic_score必须是0至100整数")
    dimensions = payload.get("dimensions")
    if not isinstance(dimensions, dict) or set(dimensions) != set(DIMENSION_KEYS):
        raise AestheticFoundationError("dimensions_shape_invalid", "调用B必须且只能输出冻结八维")
    normalized_dimensions: dict[str, Any] = {}
    for key in DIMENSION_KEYS:
        item = dimensions[key]
        if not isinstance(item, dict) or set(item) != {"grade", "evidence", "shortcomings"}:
            raise AestheticFoundationError("dimension_shape_invalid", f"{key}维度字段不完整")
        grade = item.get("grade")
        if not _is_int(grade) or not 1 <= grade <= 5:
            raise AestheticFoundationError("dimension_grade_invalid", f"{key}.grade必须为1至5整数，禁止0值")
        evidence = item.get("evidence")
        shortcomings = item.get("shortcomings")
        if not isinstance(evidence, list) or not evidence or not all(isinstance(x, str) and x.strip() for x in evidence):
            raise AestheticFoundationError("dimension_evidence_invalid", f"{key}.evidence必须含非空逐维证据")
        if not isinstance(shortcomings, list) or not all(isinstance(x, str) and x.strip() for x in shortcomings):
            raise AestheticFoundationError("dimension_shortcomings_invalid", f"{key}.shortcomings必须为字符串数组")
        normalized_dimensions[key] = {
            "grade": grade,
            "evidence": [x.strip() for x in evidence],
            "shortcomings": [x.strip() for x in shortcomings],
        }
    overall = payload.get("overall_evidence")
    confidence = payload.get("confidence")
    if not isinstance(overall, list) or not overall or not all(isinstance(x, str) and x.strip() for x in overall):
        raise AestheticFoundationError("overall_evidence_invalid", "overall_evidence必须含可见证据")
    if isinstance(confidence, bool) or not isinstance(confidence, (int, float)) or not 0 <= float(confidence) <= 1:
        raise AestheticFoundationError("confidence_invalid", "confidence必须在0至1")
    return {
        "contract_version": FOUNDATION_VERSION,
        "aesthetic_score": score,
        "dimensions": normalized_dimensions,
        "overall_evidence": [x.strip() for x in overall],
        "confidence": float(confidence),
    }


def canonical_foundation(payload: Any) -> str:
    return json.dumps(validate_aesthetic_output(payload), ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def foundation_sha256(payload: Any) -> str:
    return hashlib.sha256(canonical_foundation(payload).encode("utf-8")).hexdigest()


def anchor_samples(upload_dir: Path, target: Path, target_mime: str | None) -> list[tuple[str, Path, str | None]]:
    samples: list[tuple[str, Path, str | None]] = []
    for anchor in ANCHORS:
        path = upload_dir / anchor["stored_name"]
        if not path.is_file():
            raise AestheticFoundationError("anchor_missing", f"锚图asset {anchor['asset_id']}不存在")
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest != anchor["sha256"]:
            raise AestheticFoundationError("anchor_hash_mismatch", f"锚图asset {anchor['asset_id']}哈希不匹配")
        samples.append((f"Owner锚图 {anchor['level']}（asset {anchor['asset_id']}）", path, anchor["mime_type"]))
    samples.append(("待评图片（禁止把锚图等级直接当作输出）", target, target_mime))
    return samples


def build_prompt() -> str:
    dims = "、".join(DIMENSION_KEYS)
    output_example = {
        "contract_version": FOUNDATION_VERSION,
        "aesthetic_score": 80,
        "dimensions": {
            key: {
                "grade": 3,
                "evidence": ["必须填写至少一条待评图可见证据"],
                "shortcomings": [],
            }
            for key in DIMENSION_KEYS
        },
        "overall_evidence": ["必须填写至少一条整体可见证据"],
        "confidence": 0.8,
    }
    return f"""你是3d66灵感图美感基础评分器。四张Owner锚图依次代表L1、L2、L3、L4的相对美感参照；第五张才是待评图片。
锚点必须按下面的可见内容和质量差异理解，不能只记等级名称：
- 2045/L1：螺旋楼梯具有独特空间结构，木构与自然场景、光线、材质关系完整，达到媒体发布成熟度。
- 747/L2：紫绿床品的纹理和缝线清楚，产品近景稳定，但主要停留在局部床品，空间与叙事不足。
- 1263/L3：荷花与祝福文字的主体和颜色可辨，但文字压住核心、层次较平、形式常见。
- 601/L4：居中灯笼记录照的主体关系简单，拍摄和设计信息普通，灵感参考价值有限。
清晰且无明显硬伤不等于高分；普通、完整、清楚的记录图通常仍应与L3/L4锚比较。
75分边界表示L2与L3的最低分界：只有整体质量明确不低于747/L2锚才可给75分或更高；接近或低于1263/L3锚应低于75分。
90分边界同理只保留给明确达到2045/L1媒体发布成熟度的图片。
只判断待评图片的视觉美感基础，不执行赛道扣分、红线、封顶、发布标签或最终等级。
必须依据可见证据并做相邻锚点比较，输出0-100连续整数 aesthetic_score；禁止因为画面清晰或无硬伤自动上调。
固定八维：{dims}。
每个维度grade只能为1、2、3、4、5；不得输出0、null、缺失或额外维度。即使不典型也必须按可见质量判断。
只输出一个严格JSON对象，顶层字段必须且只能是 contract_version、aesthetic_score、dimensions、overall_evidence、confidence。
contract_version固定为{FOUNDATION_VERSION}。dimensions每项必须且只能含grade、evidence、shortcomings；evidence必须非空。
严禁输出final_level、level、predicted_level、predicted_score、final_score、production_fields、published_fields、tags或任何最终等级/发布字段。
下面是必须逐字段填满的完整JSON结构实例；占位文字必须替换为待评图可见事实，任何维度的evidence不得为空：
{json.dumps(output_example, ensure_ascii=False)}"""


def _validated_quality_rules(contract: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """读取新revision专用质量规则；形状漂移时拒绝继续定级。"""
    block = contract.get("aesthetic_foundation")
    if not isinstance(block, dict):
        raise AestheticFoundationError("quality_rules_missing", "缺少美感前置质量规则")
    soft_cap = block.get("casual_snapshot_soft_cap")
    if not isinstance(soft_cap, dict) or set(soft_cap) != {
        "key", "signal", "match_any", "cap_to"
    }:
        raise AestheticFoundationError("soft_cap_invalid", "随手拍软封顶规则形状非法")
    if (
        soft_cap.get("key") != "casual_snapshot_soft_cap"
        or soft_cap.get("signal") != "production_fields.reason"
        or soft_cap.get("match_any") != ["是随手拍"]
        or not _is_int(soft_cap.get("cap_to"))
        or not 0 <= soft_cap["cap_to"] <= 100
    ):
        raise AestheticFoundationError("soft_cap_invalid", "随手拍软封顶规则内容非法")
    exemptions = block.get("hard_defect_exemptions")
    if not isinstance(exemptions, list) or len(exemptions) != 1:
        raise AestheticFoundationError("defect_exemptions_invalid", "硬伤豁免规则必须唯一且不可缺失")
    exemption = exemptions[0]
    if not isinstance(exemption, dict) or set(exemption) != {
        "key", "source", "defect_key", "evidence_contains_any", "foundation_requirements"
    }:
        raise AestheticFoundationError("defect_exemption_invalid", "硬伤豁免规则形状非法")
    requirements = exemption.get("foundation_requirements")
    if (
        exemption.get("key") != "subject_obscuring_brand_wordmark"
        or exemption.get("source") != "image_defects"
        or exemption.get("defect_key") != "subject_obscuring_watermark"
        or exemption.get("evidence_contains_any") != ["品牌文字", "品牌字样"]
        or not isinstance(requirements, dict)
        or set(requirements) != {"detail_completion", "presentation_integrity"}
    ):
        raise AestheticFoundationError("defect_exemption_invalid", "品牌字样豁免规则内容非法")
    for key, requirement in requirements.items():
        if (
            not isinstance(requirement, dict)
            or set(requirement) != {"min_grade", "shortcomings_empty"}
            or not _is_int(requirement.get("min_grade"))
            or not 1 <= requirement["min_grade"] <= 5
            or requirement.get("shortcomings_empty") is not True
            or key not in DIMENSION_KEYS
        ):
            raise AestheticFoundationError("defect_exemption_invalid", "品牌字样豁免的维度约束非法")
    return soft_cap, exemptions


def _reason_values(precheck: dict[str, Any]) -> list[str]:
    value = (precheck.get("production_fields") or {}).get("reason")
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [item for item in value if isinstance(item, str)]
    return []


def _precheck_after_narrow_exemptions(
    precheck: dict[str, Any],
    foundation: dict[str, Any],
    exemptions: list[dict[str, Any]],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """只在A证据与B完整性双重佐证时排除品牌字样误报；输入保持只读。"""
    adjusted = copy.deepcopy(precheck)
    applied: list[dict[str, Any]] = []
    evidence_text = json.dumps(
        precheck.get("decisive_evidence"), ensure_ascii=False, sort_keys=True
    )
    for exemption in exemptions:
        source = exemption["source"]
        defect_key = exemption["defect_key"]
        defects = adjusted.get(source)
        if not isinstance(defects, list) or defect_key not in defects:
            continue
        if not any(token in evidence_text for token in exemption["evidence_contains_any"]):
            continue
        qualified = True
        for dimension_key, requirement in exemption["foundation_requirements"].items():
            dimension = foundation["dimensions"][dimension_key]
            if dimension["grade"] < requirement["min_grade"]:
                qualified = False
            if requirement["shortcomings_empty"] and dimension["shortcomings"]:
                qualified = False
        if not qualified:
            continue
        adjusted[source] = [item for item in defects if item != defect_key]
        applied.append({
            "rule": "hard_defect_exemption",
            "key": exemption["key"],
            "defect_key": defect_key,
        })
    return adjusted, applied


def apply_aesthetic_v3_rules(
    *, contract: dict[str, Any], classification_map: dict[str, Any],
    precheck: dict[str, Any], foundation: dict[str, Any] | None,
    _test_mutate_after_freeze: bool = False,
) -> dict[str, Any]:
    """以冻结美感分为只读输入应用现行赛道/媒介/硬伤/红线规则。"""
    redline = evaluate_redlines(precheck, policy=contract["redline_policy"])
    if redline.get("hit"):
        return {
            "engine_version": "inspiration-aesthetic-v3-engine-v2",
            "score": min(20, int(redline.get("hit_score_cap") or 20)), "level": "L5",
            "raw_level": "L5", "hard_reject": True,
            "hit_rules": list(redline.get("hit_rules") or []), "caps": ["redline"],
            "inspiration_aesthetic_score": None,
            "foundation_before_rules": None, "foundation_after_rules": None,
            "dimension_scoring_mode": "aesthetic_foundation",
        }
    if foundation is None:
        raise AestheticFoundationError("foundation_missing", "非红线样本缺少调用B美感基础结果")
    normalized = validate_aesthetic_output(foundation)
    soft_cap, exemptions = _validated_quality_rules(contract)
    frozen = canonical_foundation(normalized)
    working = copy.deepcopy(normalized)
    if _test_mutate_after_freeze:
        working["aesthetic_score"] -= 1
    score = normalized["aesthetic_score"]
    thresholds = contract["aesthetic_foundation"]["score_thresholds"]
    raw_level = _score_to_level(score, thresholds)
    resolved = resolve_subcategory(
        precheck, classification_map=classification_map,
        track_classification=contract["track_classification"],
    )
    tracks = {item["key"]: item for item in contract["track_classification"]["tracks"]}
    track = tracks[resolved["track_key"]]
    caps: list[dict[str, Any]] = []
    final_score = min(score, int(track["track_cap"]))
    if final_score != score:
        caps.append({"rule": "track_cap", "cap_to": int(track["track_cap"])})
    modifiers = contract["common_modifiers"]
    media = modifiers["media_type_penalty"]
    if media.get("enabled"):
        media_key, uncertain = _trait_to_media_key(precheck)
        penalty = int(media["penalties"].get(media_key, 0))
        final_score = max(0, final_score + penalty)
        if penalty:
            caps.append({"rule": "media_penalty", "media_key": media_key, "delta": penalty, "uncertain": uncertain})
    if any(reason in soft_cap["match_any"] for reason in _reason_values(precheck)):
        capped = min(final_score, int(soft_cap["cap_to"]))
        if capped != final_score:
            caps.append({"rule": soft_cap["key"], "cap_to": int(soft_cap["cap_to"])})
        final_score = capped
    veto = modifiers["high_score_veto"]
    if "tiers" in veto:
        policy_precheck, applied_exemptions = _precheck_after_narrow_exemptions(
            precheck, normalized, exemptions
        )
        caps.extend(applied_exemptions)
        after, action = _apply_v2_hard_defect_policy(precheck=policy_precheck, veto=veto, score=float(final_score))
        if int(after) != final_score:
            caps.append({"rule": "hard_defect_severity", **action})
        final_score = int(after)
    after = canonical_foundation(working)
    if frozen != after:
        raise AestheticFoundationError("foundation_pollution", "分层污染：v3规则改写了前置美感分或八维证据")
    return {
        "engine_version": "inspiration-aesthetic-v3-engine-v2",
        "score": final_score,
        "level": _score_to_level(final_score, thresholds),
        "raw_level": raw_level, "hard_reject": False, "hit_rules": [],
        "caps": caps, "track_key": resolved["track_key"],
        "inspiration_aesthetic_score": score,
        "inspiration_aesthetic_level": raw_level,
        "foundation_before_rules": frozen, "foundation_after_rules": after,
        "foundation_sha256": foundation_sha256(normalized),
        "dimensions": normalized["dimensions"],
        "confidence": normalized["confidence"],
        "dimension_scoring_mode": "aesthetic_foundation",
    }
