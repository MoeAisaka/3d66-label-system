"""ADR-0033 v3 evaluation contract definition and validation.

Pure **definition + validation** only.  This module performs no IO, no
network, no database and no model calls. Workers consume validated persisted contracts.

The v3 contract (``evaluation-category-profile-v3``) is carried as a ``dict``
and extends v2 with three additional blocks — ``redline_policy`` (delegated to
``redline_policy.validate_redline_policy``), ``track_classification``
(``track-classification-v1``) and ``common_modifiers`` (``common-modifiers-v1``).
v2 fields are intentionally not redefined here.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Literal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .dimension_schema_registry import canonical_hash as _canonical_hash
from .dimension_schema_registry import canonical_json as _canonical_json
from .level_scale import LevelScaleError, is_level_enabled, resolve_level_scale
from .redline_policy import (
    RedlinePolicyError,
    validate_redline_policy,
)


CATEGORY_EVALUATION_CONTRACT_VERSION = "evaluation-category-profile-v3"
TRACK_CLASSIFICATION_FORMAT_VERSION = "track-classification-v1"
COMMON_MODIFIERS_FORMAT_VERSION = "common-modifiers-v1"
COMMON_MODIFIERS_V2_FORMAT_VERSION = "common-modifiers-v2"

_TRACK_KEY_PATTERN = re.compile(r"^[a-z][a-z0-9_]{2,39}$")
_MEDIA_PENALTY_KEYS = frozenset({"real_photo", "render_3d", "ai_image", "other"})
_RULE_ID_PATTERN = re.compile(r"^[a-z][a-z0-9_-]{0,39}$")
_HAN_PATTERN = re.compile(r"[\u3400-\u9fff]")


class DeductionRule(BaseModel):
    """One operator-authored rule scoped to a single dimension."""

    model_config = ConfigDict(extra="forbid")

    rule_id: str
    description: str
    deduction: float = Field(gt=0, le=100)
    tags: list[str] = Field(default_factory=list, max_length=12)

    @field_validator("rule_id")
    @classmethod
    def _valid_rule_id(cls, value: str) -> str:
        value = value.strip()
        if not _RULE_ID_PATTERN.fullmatch(value):
            raise ValueError("rule_id 必须以小写字母开头，只含小写字母、数字、_、-")
        return value

    @field_validator("description")
    @classmethod
    def _valid_chinese_description(cls, value: str) -> str:
        value = value.strip()
        if not value or not _HAN_PATTERN.search(value):
            raise ValueError("扣分规则 description 必须是非空中文描述")
        return value

    @field_validator("tags")
    @classmethod
    def _valid_tags(cls, value: list[str]) -> list[str]:
        normalized = [item.strip() for item in value]
        if any(not item for item in normalized):
            raise ValueError("扣分规则 tags 不能包含空标签")
        if len(set(normalized)) != len(normalized):
            raise ValueError("扣分规则 tags 不能重复")
        return normalized


class DeductionRuleHit(BaseModel):
    """Calling-B judgment for one configured rule."""

    model_config = ConfigDict(extra="forbid")

    rule_id: str
    confidence: Literal["high", "medium", "low"]
    evidence: str

    @field_validator("evidence")
    @classmethod
    def _valid_evidence(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("规则命中 evidence 不能为空")
        return value


class DimensionDeductionOutput(BaseModel):
    """Normalized calling-B output for one dimension."""

    model_config = ConfigDict(extra="forbid")

    dimension_key: str
    hit_rules: list[DeductionRuleHit] = Field(default_factory=list)


class NodeCorrectionEvidence(BaseModel):
    """Per-rule evidence delta carried by a node correction."""

    model_config = ConfigDict(extra="forbid")

    rule_id: str
    old_confidence: Literal["high", "medium", "low"] | None = None
    new_confidence: Literal["high", "medium", "low"] | None = None
    old_evidence: str = ""
    new_evidence: str = ""


class NodeCorrection(BaseModel):
    """Append-only correction event over the deterministic v3 scoring graph."""

    model_config = ConfigDict(extra="forbid")

    correction_key: str | None = None
    node_type: Literal[
        "call_a_field",
        "precheck_field",
        "redline",
        "track",
        "dimension_rule",
        "final_level",
    ]
    node_path: str
    old_value: Any
    new_value: Any
    evidence: list[NodeCorrectionEvidence] = Field(default_factory=list)
    reason: str
    corrector: str
    corrector_confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    corrector_policy: str | None = None
    corrected_at: datetime
    downstream_recomputed: bool


class CategoryEvaluationContractError(ValueError):
    """Raised when a v3 evaluation contract is invalid.

    Carries a stable ``code`` for programmatic branching independent of the
    (localized) message text.
    """

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _validate_track_classification(block: Any) -> None:
    if not isinstance(block, dict):
        raise CategoryEvaluationContractError(
            "track_classification_not_object", "track_classification 必须是对象"
        )
    if block.get("format_version") != TRACK_CLASSIFICATION_FORMAT_VERSION:
        raise CategoryEvaluationContractError(
            "track_classification_version",
            f"track_classification 版本必须是 {TRACK_CLASSIFICATION_FORMAT_VERSION}",
        )

    tracks = block.get("tracks")
    if not isinstance(tracks, list) or not tracks:
        raise CategoryEvaluationContractError(
            "tracks_empty", "track_classification.tracks 必须是非空数组"
        )

    seen_keys: set[str] = set()
    for track in tracks:
        if not isinstance(track, dict):
            raise CategoryEvaluationContractError(
                "track_not_object", "赛道定义必须是对象"
            )
        key = track.get("key")
        if not isinstance(key, str) or not _TRACK_KEY_PATTERN.match(key):
            raise CategoryEvaluationContractError(
                "track_key_invalid", "赛道 key 不符合命名规范"
            )
        if key in seen_keys:
            raise CategoryEvaluationContractError(
                "track_key_duplicate", f"赛道 key 重复：{key}"
            )
        seen_keys.add(key)

        if not isinstance(track.get("label"), str) or not track["label"].strip():
            raise CategoryEvaluationContractError(
                "track_label_invalid", "赛道 label 必须是非空字符串"
            )

        base_score = track.get("base_score")
        dimension_max = track.get("dimension_max")
        track_cap = track.get("track_cap")
        for name, value in (
            ("base_score", base_score),
            ("dimension_max", dimension_max),
            ("track_cap", track_cap),
        ):
            if not _is_int(value) or not 0 <= value <= 100:
                raise CategoryEvaluationContractError(
                    "track_score_out_of_range",
                    f"赛道 {name} 必须是 0 至 100 的整数",
                )
        if not base_score + dimension_max <= track_cap <= 100:
            raise CategoryEvaluationContractError(
                "track_cap_inconsistent",
                "赛道必须满足 base_score+dimension_max<=track_cap<=100",
            )

        ref = track.get("dimension_schema_ref")
        if not isinstance(ref, dict):
            raise CategoryEvaluationContractError(
                "track_schema_ref_invalid", "赛道 dimension_schema_ref 必须是对象"
            )
        for field in ("schema_key", "version"):
            if not isinstance(ref.get(field), str) or not ref[field].strip():
                raise CategoryEvaluationContractError(
                    "track_schema_ref_invalid",
                    f"赛道 dimension_schema_ref.{field} 必须是非空字符串",
                )

    default_track = block.get("default_track")
    if default_track not in seen_keys:
        raise CategoryEvaluationContractError(
            "default_track_unknown", "default_track 必须是已定义的赛道 key"
        )


def _validate_common_modifiers(block: Any) -> None:
    if not isinstance(block, dict):
        raise CategoryEvaluationContractError(
            "common_modifiers_not_object", "common_modifiers 必须是对象"
        )
    format_version = block.get("format_version")
    if format_version not in {COMMON_MODIFIERS_FORMAT_VERSION, COMMON_MODIFIERS_V2_FORMAT_VERSION}:
        raise CategoryEvaluationContractError(
            "common_modifiers_version",
            "common_modifiers 版本不受支持",
        )

    media = block.get("media_type_penalty")
    if not isinstance(media, dict):
        raise CategoryEvaluationContractError(
            "media_penalty_not_object", "media_type_penalty 必须是对象"
        )
    enabled = media.get("enabled", True)
    if not isinstance(enabled, bool):
        raise CategoryEvaluationContractError(
            "media_penalty_enabled", "media_type_penalty.enabled 必须是布尔值"
        )
    penalties = media.get("penalties")
    if not isinstance(penalties, dict) or set(penalties) != _MEDIA_PENALTY_KEYS:
        raise CategoryEvaluationContractError(
            "media_penalty_keys",
            "media_type_penalty.penalties 必须且只能包含四个媒介键",
        )
    for name, value in penalties.items():
        if not _is_int(value) or value > 0:
            raise CategoryEvaluationContractError(
                "media_penalty_value",
                f"media_type_penalty.penalties.{name} 必须是 <=0 的整数",
            )
    baseline = media.get("baseline")
    if baseline not in _MEDIA_PENALTY_KEYS:
        raise CategoryEvaluationContractError(
            "media_penalty_baseline", "media_type_penalty.baseline 必须是允许的媒介键"
        )
    if penalties[baseline] != 0:
        raise CategoryEvaluationContractError(
            "media_penalty_baseline_nonzero", "基准媒介的降权必须为 0"
        )

    veto = block.get("high_score_veto")
    if not isinstance(veto, dict):
        raise CategoryEvaluationContractError(
            "veto_not_object", "high_score_veto 必须是对象"
        )
    veto_enabled = veto.get("enabled", True)
    if not isinstance(veto_enabled, bool):
        raise CategoryEvaluationContractError(
            "veto_enabled", "high_score_veto.enabled 必须是布尔值"
        )
    if format_version == COMMON_MODIFIERS_V2_FORMAT_VERSION:
        policy_version = veto.get("policy_version")
        if not isinstance(policy_version, str) or not policy_version.strip():
            raise CategoryEvaluationContractError(
                "veto_policy_version", "high_score_veto.policy_version 必须是非空字符串"
            )
        tiers = veto.get("tiers")
        if not isinstance(tiers, dict) or set(tiers) != {"A", "B", "record_only"}:
            raise CategoryEvaluationContractError(
                "veto_tiers_invalid", "high_score_veto.tiers 必须定义 A、B、record_only"
            )
        for tier_key, tier in tiers.items():
            if not isinstance(tier, dict):
                raise CategoryEvaluationContractError(
                    "veto_tier_invalid", f"硬伤 tier {tier_key} 必须是对象"
                )
            action = tier.get("action")
            cap_to = tier.get("cap_to")
            if tier_key == "record_only":
                if action != "record_only" or cap_to is not None:
                    raise CategoryEvaluationContractError(
                        "veto_tier_invalid", "record_only 必须只记录且不得配置 cap_to"
                    )
            elif action != "cap" or not _is_int(cap_to) or not 0 <= cap_to <= 100:
                raise CategoryEvaluationContractError(
                    "veto_tier_invalid", f"硬伤 tier {tier_key} 必须配置 0 至 100 的 cap_to"
                )
            if not isinstance(tier.get("target_band"), str) or not tier["target_band"].strip():
                raise CategoryEvaluationContractError(
                    "veto_tier_invalid", f"硬伤 tier {tier_key}.target_band 必须是非空字符串"
                )
        rules = veto.get("rules")
        if not isinstance(rules, list) or not rules:
            raise CategoryEvaluationContractError(
                "veto_rules_invalid", "high_score_veto.rules 必须是非空数组"
            )
        seen_keys: set[str] = set()
        for rule in rules:
            if not isinstance(rule, dict):
                raise CategoryEvaluationContractError(
                    "veto_rule_invalid", "high_score_veto.rules 每项必须是对象"
                )
            key = rule.get("key")
            source = rule.get("source")
            kind = rule.get("kind", "defect")
            severity = rule.get("severity")
            if not isinstance(key, str) or not key.strip() or key in seen_keys:
                raise CategoryEvaluationContractError(
                    "veto_rule_invalid", "硬伤 key 必须非空且不可重复"
                )
            seen_keys.add(key)
            if source not in {"hard_defects", "image_defects"}:
                raise CategoryEvaluationContractError(
                    "veto_rule_source", f"硬伤 {key} 的 source 不受支持"
                )
            if kind == "modifier":
                if severity != "inherit" or rule.get("inherits_strongest") is not True:
                    raise CategoryEvaluationContractError(
                        "veto_modifier_invalid", f"修饰符 {key} 必须继承最强 tier"
                    )
            elif kind != "defect" or severity not in tiers:
                raise CategoryEvaluationContractError(
                    "veto_rule_severity", f"硬伤 {key} 的 severity 不受支持"
                )
            if not isinstance(rule.get("description"), str) or not rule["description"].strip():
                raise CategoryEvaluationContractError(
                    "veto_rule_invalid", f"硬伤 {key} 缺少说明"
                )
        escalation = veto.get("escalation")
        if (
            not isinstance(escalation, dict)
            or escalation.get("source_tier") not in tiers
            or escalation.get("target_tier") not in tiers
            or not _is_int(escalation.get("minimum_distinct_hits"))
            or escalation["minimum_distinct_hits"] < 2
        ):
            raise CategoryEvaluationContractError(
                "veto_escalation_invalid", "high_score_veto.escalation 无效"
            )
        return
    threshold = veto.get("threshold")
    cap_to = veto.get("cap_to")
    for name, value in (("threshold", threshold), ("cap_to", cap_to)):
        if not _is_int(value) or not 0 <= value <= 100:
            raise CategoryEvaluationContractError(
                "veto_out_of_range",
                f"high_score_veto.{name} 必须是 0 至 100 的整数",
            )
    if not cap_to < threshold:
        raise CategoryEvaluationContractError(
            "veto_inconsistent", "high_score_veto.cap_to 必须小于 threshold"
        )
    rules = veto.get("rules")
    if rules is not None:
        if not isinstance(rules, list) or not rules:
            raise CategoryEvaluationContractError(
                "veto_rules_invalid", "high_score_veto.rules 必须是非空数组"
            )
        seen_keys: set[str] = set()
        for rule in rules:
            if not isinstance(rule, dict):
                raise CategoryEvaluationContractError(
                    "veto_rule_invalid", "high_score_veto.rules 每项必须是对象"
                )
            key = rule.get("key")
            description = rule.get("description")
            if not isinstance(key, str) or not key.strip():
                raise CategoryEvaluationContractError(
                    "veto_rule_invalid", "高分硬伤 key 必须是非空字符串"
                )
            if not isinstance(description, str) or not description.strip():
                raise CategoryEvaluationContractError(
                    "veto_rule_invalid", "高分硬伤 description 必须是非空中文说明"
                )
            if key in seen_keys:
                raise CategoryEvaluationContractError(
                    "veto_rule_duplicate", f"高分硬伤 key 重复：{key}"
                )
            seen_keys.add(key)


def validate_deduction_rules(rules: Any, *, dimension_key: str) -> None:
    """Validate one dimension's optional rule list.

    Missing ``deduction_rules`` is legal and selects the deprecated grade-point
    fallback.  A present list must be non-empty, structurally valid and unique
    by ``rule_id``.  This helper is shared by the config validator and bridge.
    """
    if rules is None:
        return
    if not isinstance(rules, list) or not rules:
        raise CategoryEvaluationContractError(
            "deduction_rules_empty",
            f"维度 {dimension_key} 的 deduction_rules 必须是非空数组",
        )
    seen: set[str] = set()
    for index, raw_rule in enumerate(rules):
        try:
            rule = DeductionRule.model_validate(raw_rule)
        except ValueError as exc:
            raise CategoryEvaluationContractError(
                "deduction_rule_invalid",
                f"维度 {dimension_key} 的第 {index + 1} 条扣分规则无效：{exc}",
            ) from exc
        if rule.rule_id in seen:
            raise CategoryEvaluationContractError(
                "deduction_rule_duplicate",
                f"维度 {dimension_key} 的 rule_id 重复：{rule.rule_id}",
            )
        seen.add(rule.rule_id)


def validate_category_evaluation_contract(contract: Any) -> None:
    """Fail-closed validation of a v3 contract, block by block.

    Delegates the ``redline_policy`` block to task 1's validator and re-raises
    its ``RedlinePolicyError`` as ``CategoryEvaluationContractError`` so callers
    of this contract see a single error type with a stable ``.code``.
    """
    if not isinstance(contract, dict):
        raise CategoryEvaluationContractError(
            "contract_not_object", "评测合同必须是对象"
        )
    if contract.get("schema_version") != CATEGORY_EVALUATION_CONTRACT_VERSION:
        raise CategoryEvaluationContractError(
            "schema_version_unsupported",
            f"评测合同版本必须是 {CATEGORY_EVALUATION_CONTRACT_VERSION}",
        )

    for block_key in ("redline_policy", "track_classification", "common_modifiers"):
        if block_key not in contract:
            raise CategoryEvaluationContractError(
                "block_missing", f"评测合同缺少 {block_key} 区块"
            )

    try:
        validate_redline_policy(contract["redline_policy"])
    except RedlinePolicyError as exc:
        raise CategoryEvaluationContractError(
            f"redline_policy.{exc.code}", str(exc)
        ) from exc

    _validate_track_classification(contract["track_classification"])
    _validate_common_modifiers(contract["common_modifiers"])

    try:
        level_scale = resolve_level_scale(contract)
    except LevelScaleError as exc:
        raise CategoryEvaluationContractError(
            f"level_scale.{exc.code}", str(exc)
        ) from exc
    hit_level = contract["redline_policy"]["hit_level"]
    if not is_level_enabled(hit_level, level_scale):
        raise CategoryEvaluationContractError(
            "level_scale.redline_level_disabled",
            f"红线 hit_level {hit_level} 已被当前类目关闭",
        )


def canonical_contract_hash(contract: dict[str, Any]) -> str:
    """Stable sha256 hex of the canonical JSON of a contract.

    Reuses ``dimension_schema_registry.canonical_json`` / ``canonical_hash``
    (sort_keys, compact separators, ensure_ascii=False), so key order is
    irrelevant and structurally equivalent contracts hash identically.
    """
    return _canonical_hash(contract)


def canonical_contract_json(contract: dict[str, Any]) -> str:
    """Canonical JSON string of a contract (key-order independent)."""
    return _canonical_json(contract)
