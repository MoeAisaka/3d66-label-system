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
import math
from copy import deepcopy
from datetime import datetime
from typing import Literal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .dimension_schema_registry import canonical_hash as _canonical_hash
from .dimension_schema_registry import canonical_json as _canonical_json
from .level_scale import LevelScaleError, is_level_enabled, resolve_level_scale
from .inspiration_anchor_contract import (
    InspirationAnchorContractError,
    validate_inspiration_anchor_contract,
)
from .inspiration_anchor_mechanism import (
    ANCHOR_MECHANISM_KEY,
    validate_anchor_mechanism,
)
from .redline_policy import (
    RedlinePolicyError,
    validate_redline_policy,
)


CATEGORY_EVALUATION_CONTRACT_VERSION = "evaluation-category-profile-v3"
TRACK_CLASSIFICATION_FORMAT_VERSION = "track-classification-v1"
COMMON_MODIFIERS_FORMAT_VERSION = "common-modifiers-v1"
COMMON_MODIFIERS_V2_FORMAT_VERSION = "common-modifiers-v2"

_TRACK_KEY_PATTERN = re.compile(r"^[a-z][a-z0-9_]{2,39}$")
_RULE_ID_PATTERN = re.compile(r"^[a-z][a-z0-9_-]{0,39}$")
_HAN_PATTERN = re.compile(r"[\u3400-\u9fff]")


def _is_finite_number(value: Any) -> bool:
    return (
        not isinstance(value, bool)
        and isinstance(value, (int, float))
        and math.isfinite(float(value))
    )


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


class BonusRule(BaseModel):
    """One operator-authored positive rule scoped to a single dimension."""

    model_config = ConfigDict(extra="forbid")

    rule_id: str
    description: str
    bonus: float = Field(gt=0, le=100)
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
            raise ValueError("加分规则 description 必须是非空中文描述")
        return value

    @field_validator("tags")
    @classmethod
    def _valid_tags(cls, value: list[str]) -> list[str]:
        normalized = [item.strip() for item in value]
        if any(not item for item in normalized):
            raise ValueError("加分规则 tags 不能包含空标签")
        if len(set(normalized)) != len(normalized):
            raise ValueError("加分规则 tags 不能重复")
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
    hit_bonus_rules: list[DeductionRuleHit] = Field(default_factory=list)


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
        "aesthetic_score",
        "final_level",
    ]
    node_path: str
    old_value: Any
    new_value: Any
    evidence: list[NodeCorrectionEvidence] = Field(default_factory=list)
    reason: str
    # 结构化归因码。自由文本无法聚合，纠偏分析要靠这里统计「哪一层最常犯哪类错」。
    reason_codes: list[str] = Field(default_factory=list)
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


class CategoryEvaluationPromptBindingError(ValueError):
    """Candidate contract and executable Prompt versions disagree."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def bind_category_evaluation_prompt_versions(
    contract: dict[str, Any],
    *,
    prompt_a_version: str,
    prompt_b_version: str | None,
) -> dict[str, Any]:
    """Copy a candidate contract and bind it to one executable Prompt pair."""

    bound = deepcopy(contract)
    bound["prompt_bindings"] = {
        "call_a_version": prompt_a_version,
        "call_b_version": prompt_b_version,
    }
    foundation = bound.get("aesthetic_foundation")
    if isinstance(foundation, dict):
        foundation["call_b_version"] = prompt_b_version
    return bound


def validate_category_evaluation_prompt_bindings(
    contract: Any,
    *,
    prompt_a_version: str,
    prompt_b_version: str | None,
) -> None:
    """Fail closed unless a candidate contract names its executable Prompt pair."""

    if not isinstance(contract, dict):
        raise CategoryEvaluationPromptBindingError(
            "prompt_binding_contract_invalid",
            "候选合同不是可核验对象",
        )
    bindings = contract.get("prompt_bindings")
    if not isinstance(bindings, dict):
        raise CategoryEvaluationPromptBindingError(
            "prompt_bindings_missing",
            "候选合同缺少 Prompt 绑定",
        )
    if (
        bindings.get("call_a_version") != prompt_a_version
        or bindings.get("call_b_version") != prompt_b_version
    ):
        raise CategoryEvaluationPromptBindingError(
            "prompt_bindings_mismatch",
            "候选合同声明的 A/B Prompt 版本与执行策略不一致",
        )
    foundation = contract.get("aesthetic_foundation")
    if (
        isinstance(foundation, dict)
        and foundation.get("call_b_version") != prompt_b_version
    ):
        raise CategoryEvaluationPromptBindingError(
            "aesthetic_foundation_prompt_binding_mismatch",
            "候选美感前置合同声明的调用 B 版本与执行策略不一致",
        )


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


def _validate_track_adjustments(value: Any, *, track_keys: set[str]) -> None:
    """Validate optional operator-authored fixed track score adjustments."""
    if value is None:
        return
    if not isinstance(value, dict):
        raise CategoryEvaluationContractError(
            "track_adjustments_not_object", "track_adjustments 必须是对象"
        )
    for track_key, adjustment in value.items():
        if track_key not in track_keys:
            raise CategoryEvaluationContractError(
                "track_adjustment_track_unknown",
                f"track_adjustments 包含未知赛道：{track_key}",
            )
        if not isinstance(adjustment, dict):
            raise CategoryEvaluationContractError(
                "track_adjustment_invalid",
                f"赛道 {track_key} 的固定调整必须是对象",
            )
        keys = set(adjustment)
        if not keys or not keys <= {"deduction", "bonus"}:
            raise CategoryEvaluationContractError(
                "track_adjustment_invalid",
                f"赛道 {track_key} 的固定调整只能声明 deduction/bonus",
            )
        for name in keys:
            amount = adjustment[name]
            if not _is_finite_number(amount) or not 0 <= float(amount) <= 100:
                raise CategoryEvaluationContractError(
                    "track_adjustment_value_invalid",
                    f"赛道 {track_key} 的 {name} 必须是 0 至 100 的有限数值",
                )


def _validate_hard_defect_penalty(value: Any) -> None:
    if value is None:
        return
    if not isinstance(value, dict):
        raise CategoryEvaluationContractError(
            "hard_defect_penalty_not_object", "hard_defect_penalty 必须是对象"
        )
    if not isinstance(value.get("enabled", True), bool):
        raise CategoryEvaluationContractError(
            "hard_defect_penalty_enabled",
            "hard_defect_penalty.enabled 必须是布尔值",
        )
    per_hit = value.get("per_hit")
    if not _is_finite_number(per_hit) or not 0 <= float(per_hit) <= 100:
        raise CategoryEvaluationContractError(
            "hard_defect_penalty_value",
            "hard_defect_penalty.per_hit 必须是 0 至 100 的有限数值",
        )
    source = value.get("source", "hard_defects")
    if source not in {"hard_defects", "image_defects", "both"}:
        raise CategoryEvaluationContractError(
            "hard_defect_penalty_source",
            "hard_defect_penalty.source 必须是 hard_defects、image_defects 或 both",
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
    if media is not None and not isinstance(media, dict):
        raise CategoryEvaluationContractError(
            "media_penalty_not_object", "media_type_penalty 必须是对象"
        )
    if isinstance(media, dict):
        enabled = media.get("enabled", True)
        if not isinstance(enabled, bool):
            raise CategoryEvaluationContractError(
                "media_penalty_enabled", "media_type_penalty.enabled 必须是布尔值"
            )
        penalties = media.get("penalties")
        if enabled and (not isinstance(penalties, dict) or not penalties):
            raise CategoryEvaluationContractError(
                "media_penalty_keys",
                "启用的 media_type_penalty.penalties 必须是非空对象",
            )
        if penalties is not None and not isinstance(penalties, dict):
            raise CategoryEvaluationContractError(
                "media_penalty_keys", "media_type_penalty.penalties 必须是对象"
            )
        penalties = penalties or {}
        for name, value in penalties.items():
            if not isinstance(name, str) or not name.strip():
                raise CategoryEvaluationContractError(
                    "media_penalty_key_invalid", "媒介降权 key 必须是非空字符串"
                )
            if not _is_finite_number(value) or not -100 <= float(value) <= 0:
                raise CategoryEvaluationContractError(
                    "media_penalty_value",
                    f"media_type_penalty.penalties.{name} 必须是 -100 至 0 的有限数值",
                )
        baseline = media.get("baseline")
        if enabled and baseline not in penalties:
            raise CategoryEvaluationContractError(
                "media_penalty_baseline", "启用的媒介降权 baseline 必须引用 penalties 中的 key"
            )
        if baseline in penalties and penalties[baseline] != 0:
            raise CategoryEvaluationContractError(
                "media_penalty_baseline_nonzero", "基准媒介的降权必须为 0"
            )
        for field in ("fallback",):
            value = media.get(field)
            if value is not None and value not in penalties:
                raise CategoryEvaluationContractError(
                    "media_penalty_reference_unknown",
                    f"media_type_penalty.{field} 必须引用 penalties 中的 key",
                )
        aliases = media.get("aliases", {})
        if not isinstance(aliases, dict):
            raise CategoryEvaluationContractError(
                "media_penalty_aliases", "media_type_penalty.aliases 必须是对象"
            )
        for alias, target in aliases.items():
            if (
                not isinstance(alias, str)
                or not alias.strip()
                or not isinstance(target, str)
                or target not in penalties
            ):
                raise CategoryEvaluationContractError(
                    "media_penalty_aliases",
                    "media_type_penalty.aliases 必须把非空字符串映射到 penalties key",
                )

    veto = block.get("high_score_veto")
    if veto is not None and not isinstance(veto, dict):
        raise CategoryEvaluationContractError(
            "veto_not_object", "high_score_veto 必须是对象"
        )
    if veto is None:
        return
    veto_enabled = veto.get("enabled", True)
    if not isinstance(veto_enabled, bool):
        raise CategoryEvaluationContractError(
            "veto_enabled", "high_score_veto.enabled 必须是布尔值"
        )
    # Disabled optional primitives carry no execution requirements. This lets a
    # new contract choose per-hit penalties without manufacturing a legacy cap.
    if not veto_enabled:
        return
    if format_version == COMMON_MODIFIERS_V2_FORMAT_VERSION:
        policy_version = veto.get("policy_version")
        if not isinstance(policy_version, str) or not policy_version.strip():
            raise CategoryEvaluationContractError(
                "veto_policy_version", "high_score_veto.policy_version 必须是非空字符串"
            )
        tiers = veto.get("tiers")
        if not isinstance(tiers, dict) or not tiers:
            raise CategoryEvaluationContractError(
                "veto_tiers_invalid", "启用的 high_score_veto.tiers 必须是非空对象"
            )
        for tier_key, tier in tiers.items():
            if not isinstance(tier_key, str) or not tier_key.strip() or not isinstance(tier, dict):
                raise CategoryEvaluationContractError(
                    "veto_tier_invalid", "硬伤 tier key 必须是非空字符串且 tier 必须是对象"
                )
            action = tier.get("action")
            cap_to = tier.get("cap_to")
            if action == "record_only":
                if cap_to is not None:
                    raise CategoryEvaluationContractError(
                        "veto_tier_invalid", f"硬伤 tier {tier_key} 为 record_only 时不得配置 cap_to"
                    )
            elif action != "cap" or not _is_int(cap_to) or not 0 <= cap_to <= 100:
                raise CategoryEvaluationContractError(
                    "veto_tier_invalid", f"硬伤 tier {tier_key} 必须声明 cap 或 record_only 动作"
                )
            target_band = tier.get("target_band")
            if target_band is not None and (
                not isinstance(target_band, str) or not target_band.strip()
            ):
                raise CategoryEvaluationContractError(
                    "veto_tier_invalid", f"硬伤 tier {tier_key}.target_band 必须是非空字符串"
                )
        rules = veto.get("rules")
        if not isinstance(rules, list):
            raise CategoryEvaluationContractError(
                "veto_rules_invalid", "high_score_veto.rules 必须是数组"
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
        if escalation is not None and (
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


def dimension_rule_mode(
    dimension: Any,
) -> Literal["grade_fallback", "deduction_v1", "bonus_cap_v2"]:
    """Select scoring mode from raw field presence without adding defaults."""
    if not isinstance(dimension, dict):
        return "grade_fallback"
    if "bonus_rules" in dimension or "dimension_score_cap" in dimension:
        return "bonus_cap_v2"
    if "deduction_rules" in dimension or "dimension_deduction_cap" in dimension:
        return "deduction_v1"
    return "grade_fallback"


def validate_dimension_deduction_cap(
    value: Any, *, dimension_key: str
) -> None:
    """Validate the maximum cumulative deduction for one dimension.

    The field is intentionally independent from ``dimension_score_cap``:
    the former limits how many points can be removed by hit rules, while the
    latter limits the resulting positive dimension score.
    """
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(float(value))
        or not 0 <= float(value) <= 100
    ):
        raise CategoryEvaluationContractError(
            "dimension_deduction_cap_invalid",
            f"维度 {dimension_key} 的 dimension_deduction_cap 必须是 0 至 100 的有限数值",
        )


def validate_dimension_rules(dimension: Any, *, dimension_key: str) -> None:
    """Validate a dimension's raw rule mode while preserving legacy behavior."""
    mode = dimension_rule_mode(dimension)
    if mode == "grade_fallback":
        return
    if "dimension_deduction_cap" in dimension:
        validate_dimension_deduction_cap(
            dimension["dimension_deduction_cap"], dimension_key=dimension_key
        )
    if mode == "deduction_v1":
        validate_deduction_rules(
            dimension.get("deduction_rules"), dimension_key=dimension_key
        )
        return

    cap = dimension.get("dimension_score_cap")
    if (
        isinstance(cap, bool)
        or not isinstance(cap, (int, float))
        or not math.isfinite(float(cap))
        or not 0 <= float(cap) <= 100
    ):
        raise CategoryEvaluationContractError(
            "dimension_score_cap_invalid",
            f"维度 {dimension_key} 的 dimension_score_cap 必须是 0 至 100 的有限数值",
        )

    if "deduction_rules" not in dimension:
        raise CategoryEvaluationContractError(
            "deduction_rules_missing",
            f"维度 {dimension_key} 的 bonus-cap-v2 必须显式包含 deduction_rules 数组",
        )
    if "bonus_rules" not in dimension:
        raise CategoryEvaluationContractError(
            "bonus_rules_missing",
            f"维度 {dimension_key} 的 bonus-cap-v2 必须显式包含 bonus_rules 数组",
        )
    deduction_rules = dimension["deduction_rules"]
    bonus_rules = dimension["bonus_rules"]
    if not isinstance(deduction_rules, list):
        raise CategoryEvaluationContractError(
            "deduction_rules_invalid",
            f"维度 {dimension_key} 的 deduction_rules 必须是数组",
        )
    if not isinstance(bonus_rules, list):
        raise CategoryEvaluationContractError(
            "bonus_rules_invalid",
            f"维度 {dimension_key} 的 bonus_rules 必须是数组",
        )
    if not deduction_rules and not bonus_rules:
        raise CategoryEvaluationContractError(
            "rules_empty",
            f"维度 {dimension_key} 的扣分规则与加分规则不能同时为空",
        )

    seen: set[str] = set()
    for kind, rules, model in (
        ("扣分", deduction_rules, DeductionRule),
        ("加分", bonus_rules, BonusRule),
    ):
        for index, raw_rule in enumerate(rules):
            try:
                rule = model.model_validate(raw_rule)
            except ValueError as exc:
                raise CategoryEvaluationContractError(
                    f"{kind == '扣分' and 'deduction' or 'bonus'}_rule_invalid",
                    f"维度 {dimension_key} 的第 {index + 1} 条{kind}规则无效：{exc}",
                ) from exc
            if rule.rule_id in seen:
                raise CategoryEvaluationContractError(
                    "rule_id_duplicate",
                    f"维度 {dimension_key} 的扣分/加分 rule_id 重复：{rule.rule_id}",
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
    track_keys = {
        track["key"]
        for track in contract["track_classification"]["tracks"]
        if isinstance(track, dict) and isinstance(track.get("key"), str)
    }
    _validate_track_adjustments(
        contract.get("track_adjustments"), track_keys=track_keys
    )
    _validate_hard_defect_penalty(
        contract["common_modifiers"].get("hard_defect_penalty")
    )

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

    if "aesthetic_foundation" in contract:
        foundation = contract["aesthetic_foundation"]
        if not isinstance(foundation, dict) or "anchors" not in foundation:
            raise CategoryEvaluationContractError(
                "aesthetic_foundation.anchor_contract_invalid",
                "美感前置合同缺少冻结锚图",
            )
        try:
            validate_inspiration_anchor_contract(foundation["anchors"])
        except InspirationAnchorContractError as exc:
            raise CategoryEvaluationContractError(
                f"aesthetic_foundation.{exc.code}", str(exc)
            ) from exc

    # 锚点图机制（``anchor-mechanism-v1``）：只承载各等级锚点图片。
    # 阈值归 level_scale、维度归 Call B、红线封顶归 Call A 与 redline_policy；
    # validate_anchor_mechanism 内的隔离守卫会拒绝任何混入的外来机制。
    if ANCHOR_MECHANISM_KEY in contract:
        try:
            validate_anchor_mechanism(contract)
        except InspirationAnchorContractError as exc:
            raise CategoryEvaluationContractError(
                f"{ANCHOR_MECHANISM_KEY}.{exc.code}", str(exc)
            ) from exc


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


def resolve_scoring_capabilities(
    contract: dict[str, Any],
    subcategory_dimensions: dict[str, Any],
) -> dict[str, Any]:
    """Resolve declared scoring primitives into one auditable execution plan."""
    if not isinstance(contract, dict) or not isinstance(subcategory_dimensions, dict):
        raise CategoryEvaluationContractError(
            "scoring_capabilities_invalid", "评分能力解析需要合同和赛道维度对象"
        )
    from .dimension_composition import validate_subcategory_dimensions
    from .dimension_deduction_bridge import rule_scoring_mode

    track_modes: dict[str, str] = {}
    for track_key, config in subcategory_dimensions.items():
        if not isinstance(config, dict):
            raise CategoryEvaluationContractError(
                "scoring_capabilities_invalid",
                f"赛道 {track_key} 的维度配置必须是对象",
            )
        validate_subcategory_dimensions(config)
        try:
            raw_mode = rule_scoring_mode(config)
        except Exception as exc:
            raise CategoryEvaluationContractError(
                "scoring_capability_mode_mixed",
                f"赛道 {track_key} 的维度规则模式不一致：{exc}",
            ) from exc
        track_modes[track_key] = (
            "rule_deduction"
            if raw_mode == "deduction_v1"
            else "bonus_cap_v2"
            if raw_mode == "bonus_cap_v2"
            else "grade_fallback"
        )
    modes = set(track_modes.values())
    mode = next(iter(modes), "grade_fallback") if len(modes) == 1 else "per_track"
    primitives = ["redline"]
    if contract.get("track_adjustments"):
        primitives.append("track_adjustment")
    if "rule_deduction" in modes:
        primitives.append("dimension_rule_deduction")
    if "bonus_cap_v2" in modes:
        primitives.append("dimension_rule_bonus_cap")
    media = (contract.get("common_modifiers") or {}).get("media_type_penalty")
    if isinstance(media, dict) and media.get("enabled", True):
        primitives.append("media_penalty")
    hard_penalty = (contract.get("common_modifiers") or {}).get(
        "hard_defect_penalty"
    )
    if isinstance(hard_penalty, dict) and hard_penalty.get("enabled", True):
        primitives.append("hard_defect_penalty")
    veto = (contract.get("common_modifiers") or {}).get("high_score_veto")
    if isinstance(veto, dict) and veto.get("enabled", True):
        primitives.append("hard_defect_veto")
    primitives.append("level_scale")
    return {
        "format_version": "scoring-capabilities-v1",
        "execution_mode": mode,
        "track_modes": track_modes,
        "primitives": primitives,
    }
