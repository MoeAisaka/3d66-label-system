"""灵感图质量规则机制（可配置版）。

本模块只负责两件事，且必须只负责这两件事：

1. 随手拍限分（snapshot_limit）——识别到"随手拍"一类信号时，把总分压到某个上限。
2. 硬伤例外名单（defect_exceptions）——满足佐证条件时，让某个硬伤不触发降级。

设计要点
--------
运营用**人话字段**配置（"当判定理由包含…"、"最高只能得几分"），装载器负责翻译成
执行层 ``inspiration_aesthetic_foundation`` 已经在消费的内部形状。因此执行层的
评分逻辑一行不改，风险面只落在翻译层。

刻意不复用 ``_validated_quality_rules``
---------------------------------------
旧校验把内容写死了：``match_any`` 必须精确等于 ``["是随手拍"]``、豁免必须恰好 1 条、
``key`` 必须叫 ``subject_obscuring_brand_wordmark``、关键词必须精确等于
``["品牌文字", "品牌字样"]``。运营改任何一处都会被拒——那正是本模块要解开的锁。
所以这里自带装载器，只做结构与取值域校验，不锁定业务内容。

单一职责（切记）
----------------
本机制**只管上面两项**。锚点图片归 ``inspiration_anchor_mechanism``、分档切点归合同
顶层 ``level_thresholds`` / ``level_scale``、八维定义归 ``dimensions``、红线归
``redline_policy``。任何外来机制键混进本块都会被 ``assert_quality_rules_isolated``
拦下——混进来会让调试失去可归因性，``inspiration_aesthetic_foundation`` 就是把锚图、
阈值、维度、封顶揉在一起的反面样本。
"""

from __future__ import annotations

import copy
from typing import Any

CONTRACT_BLOCK_KEY = "quality_rules"

# 本块允许出现的顶层字段——白名单，杜绝改个名字夹带外来机制。
_ALLOWED_BLOCK_FIELDS = frozenset({
    "enabled",
    "snapshot_limit",
    "defect_exceptions",
    "notes",
})

_ALLOWED_SNAPSHOT_FIELDS = frozenset({
    "enabled",
    "name",
    "signal",
    "when_reason_contains",
    "max_score",
    "max_level",
    "dimension_ceilings",
})

_ALLOWED_EXCEPTION_FIELDS = frozenset({
    "name",
    "defect",
    "defect_source",
    "when_evidence_contains",
    "require_dimensions",
})

_ALLOWED_REQUIREMENT_FIELDS = frozenset({
    "dimension",
    "min_grade",
    "no_shortcomings",
})

# 不属于本机制的键——一旦出现就报错，并直接告诉它该去哪。
_FOREIGN_KEYS: dict[str, str] = {
    "score_thresholds": "应放在合同顶层 level_thresholds",
    "level_thresholds": "应放在合同顶层，不属于质量规则块",
    "level_scale": "应放在合同顶层 level_scale",
    "bands": "分档切点应放在合同顶层 level_thresholds",
    "anchors": "锚点图片应放在 anchor_mechanism 块",
    "anchor_samples": "锚点图片应放在 anchor_mechanism 块",
    "anchor_mechanism": "锚点机制是独立块，不可嵌套在质量规则内",
    "dimensions": "八维定义应放在合同顶层 dimensions",
    "dimension_keys": "八维定义应放在合同顶层 dimensions",
    "dimension_weights": "维度权重应放在合同顶层 dimensions",
    "redline_policy": "红线策略应放在合同顶层 redline_policy",
    "redlines": "红线规则应放在合同顶层 redline_policy",
    "boundary_policy": "边界策略语义未定，且后端零实现，不得在此声明",
    "prompt_template": "提示词应放在提示词管理，不属于质量规则块",
    "call_b_version": "版本标识应放在合同顶层",
    "calibration_status": "标定状态应放在合同顶层",
    "aesthetic_foundation": "旧基座是被替代对象，不可嵌套",
}

_VALID_LEVELS = ("L1", "L2", "L3", "L4", "L5")
_VALID_DEFECT_SOURCES = ("image_defects", "content_defects")


class QualityRulesError(ValueError):
    """质量规则机制配置非法。"""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def assert_quality_rules_isolated(block: Any) -> None:
    """守卫单一职责：本块只准出现质量规则自己的字段。

    这不是注释约束，是机器可查的红线。混进外来机制会让调试失去可归因性。
    """
    if not isinstance(block, dict):
        raise QualityRulesError("quality_rules_not_object", "质量规则块必须是对象")

    for key, belongs_to in _FOREIGN_KEYS.items():
        if key in block:
            raise QualityRulesError(
                "quality_rules_foreign_key",
                f"质量规则块不得包含 {key}（{belongs_to}）",
            )

    unknown = sorted(set(block) - _ALLOWED_BLOCK_FIELDS)
    if unknown:
        raise QualityRulesError(
            "quality_rules_unknown_field",
            f"质量规则块出现未知字段：{', '.join(unknown)}",
        )

    snapshot = block.get("snapshot_limit")
    if snapshot is not None:
        if not isinstance(snapshot, dict):
            raise QualityRulesError("snapshot_limit_not_object", "随手拍限分必须是对象")
        for key, belongs_to in _FOREIGN_KEYS.items():
            if key in snapshot:
                raise QualityRulesError(
                    "quality_rules_foreign_key",
                    f"随手拍限分不得包含 {key}（{belongs_to}）",
                )
        unknown = sorted(set(snapshot) - _ALLOWED_SNAPSHOT_FIELDS)
        if unknown:
            raise QualityRulesError(
                "snapshot_limit_unknown_field",
                f"随手拍限分出现未知字段：{', '.join(unknown)}",
            )

    exceptions = block.get("defect_exceptions")
    if exceptions is not None:
        if not isinstance(exceptions, list):
            raise QualityRulesError("defect_exceptions_not_list", "硬伤例外名单必须是列表")
        for index, item in enumerate(exceptions):
            if not isinstance(item, dict):
                raise QualityRulesError(
                    "defect_exception_not_object", f"硬伤例外第 {index + 1} 条必须是对象"
                )
            for key, belongs_to in _FOREIGN_KEYS.items():
                if key in item:
                    raise QualityRulesError(
                        "quality_rules_foreign_key",
                        f"硬伤例外第 {index + 1} 条不得包含 {key}（{belongs_to}）",
                    )
            unknown = sorted(set(item) - _ALLOWED_EXCEPTION_FIELDS)
            if unknown:
                raise QualityRulesError(
                    "defect_exception_unknown_field",
                    f"硬伤例外第 {index + 1} 条出现未知字段：{', '.join(unknown)}",
                )


def _validate_snapshot_limit(snapshot: dict[str, Any]) -> dict[str, Any] | None:
    """校验并翻译随手拍限分为执行层内部形状。"""
    if snapshot.get("enabled") is False:
        return None

    reasons = snapshot.get("when_reason_contains")
    if not isinstance(reasons, list) or not reasons:
        raise QualityRulesError(
            "snapshot_limit_reasons_empty", "随手拍限分至少要配一个判定理由关键词"
        )
    for item in reasons:
        if not isinstance(item, str) or not item.strip():
            raise QualityRulesError(
                "snapshot_limit_reason_invalid", "判定理由关键词必须是非空文本"
            )

    signal = snapshot.get("signal") or "production_fields.reason"
    if not isinstance(signal, str) or not signal.strip():
        raise QualityRulesError("snapshot_limit_signal_invalid", "信号来源必须是非空文本")

    max_score = snapshot.get("max_score")
    max_level = snapshot.get("max_level")
    if max_score is None and max_level is None:
        raise QualityRulesError(
            "snapshot_limit_target_missing", "随手拍限分要么配最高分数，要么配最高等级"
        )
    if max_score is not None and max_level is not None:
        raise QualityRulesError(
            "snapshot_limit_target_conflict", "最高分数与最高等级只能配一个"
        )

    normalized: dict[str, Any] = {
        "key": "casual_snapshot_soft_cap",
        "signal": signal,
        "match_any": [str(item) for item in reasons],
    }

    if max_score is not None:
        if not isinstance(max_score, int) or isinstance(max_score, bool):
            raise QualityRulesError("snapshot_limit_score_invalid", "最高分数必须是整数")
        if not 0 <= max_score <= 100:
            raise QualityRulesError(
                "snapshot_limit_score_range", "最高分数必须在 0 到 100 之间"
            )
        normalized["cap_to"] = max_score
        return normalized

    if max_level not in _VALID_LEVELS:
        raise QualityRulesError(
            "snapshot_limit_level_invalid",
            f"最高等级必须是 {'/'.join(_VALID_LEVELS)} 之一",
        )
    normalized["cap_to_level"] = max_level

    ceilings = snapshot.get("dimension_ceilings")
    if ceilings is None:
        ceilings = {}
    if not isinstance(ceilings, dict):
        raise QualityRulesError(
            "snapshot_limit_ceilings_invalid", "维度分上限必须是对象"
        )
    normalized_ceilings: dict[str, int] = {}
    for dimension_key, limit in ceilings.items():
        if not isinstance(dimension_key, str) or not dimension_key.strip():
            raise QualityRulesError(
                "snapshot_limit_ceiling_key_invalid", "维度分上限的维度名必须是非空文本"
            )
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 5:
            raise QualityRulesError(
                "snapshot_limit_ceiling_value_invalid",
                f"维度 {dimension_key} 的分上限必须是 1 到 5 的整数",
            )
        normalized_ceilings[dimension_key] = limit

    normalized["filter_escalation"] = {
        "cap_to_level": max_level,
        "dimensions_at_most": normalized_ceilings,
    }
    return normalized


def _validate_defect_exception(item: dict[str, Any], index: int) -> dict[str, Any]:
    """校验并翻译单条硬伤例外为执行层内部形状。"""
    position = f"硬伤例外第 {index + 1} 条"

    name = item.get("name")
    if not isinstance(name, str) or not name.strip():
        raise QualityRulesError(
            "defect_exception_name_invalid", f"{position}要有名字，便于运营辨认"
        )

    defect = item.get("defect")
    if not isinstance(defect, str) or not defect.strip():
        raise QualityRulesError(
            "defect_exception_defect_invalid", f"{position}要指明豁免哪个硬伤"
        )

    source = item.get("defect_source") or "image_defects"
    if source not in _VALID_DEFECT_SOURCES:
        raise QualityRulesError(
            "defect_exception_source_invalid",
            f"{position}的硬伤来源必须是 {'/'.join(_VALID_DEFECT_SOURCES)} 之一",
        )

    evidence = item.get("when_evidence_contains")
    if not isinstance(evidence, list) or not evidence:
        raise QualityRulesError(
            "defect_exception_evidence_empty", f"{position}至少要配一个佐证关键词"
        )
    for token in evidence:
        if not isinstance(token, str) or not token.strip():
            raise QualityRulesError(
                "defect_exception_evidence_invalid", f"{position}的佐证关键词必须是非空文本"
            )

    requirements = item.get("require_dimensions")
    if not isinstance(requirements, list) or not requirements:
        raise QualityRulesError(
            "defect_exception_requirements_empty",
            f"{position}至少要配一条维度门槛，避免无条件豁免",
        )

    normalized_requirements: dict[str, Any] = {}
    for requirement in requirements:
        if not isinstance(requirement, dict):
            raise QualityRulesError(
                "defect_exception_requirement_invalid", f"{position}的维度门槛必须是对象"
            )
        unknown = sorted(set(requirement) - _ALLOWED_REQUIREMENT_FIELDS)
        if unknown:
            raise QualityRulesError(
                "defect_exception_requirement_unknown_field",
                f"{position}的维度门槛出现未知字段：{', '.join(unknown)}",
            )
        dimension_key = requirement.get("dimension")
        if not isinstance(dimension_key, str) or not dimension_key.strip():
            raise QualityRulesError(
                "defect_exception_requirement_dimension_invalid",
                f"{position}的维度门槛要指明维度",
            )
        if dimension_key in normalized_requirements:
            raise QualityRulesError(
                "defect_exception_requirement_duplicate",
                f"{position}的维度 {dimension_key} 重复配置",
            )
        min_grade = requirement.get("min_grade")
        if (
            not isinstance(min_grade, int)
            or isinstance(min_grade, bool)
            or not 1 <= min_grade <= 5
        ):
            raise QualityRulesError(
                "defect_exception_requirement_grade_invalid",
                f"{position}的维度 {dimension_key} 最低档位必须是 1 到 5 的整数",
            )
        no_shortcomings = requirement.get("no_shortcomings", False)
        if not isinstance(no_shortcomings, bool):
            raise QualityRulesError(
                "defect_exception_requirement_flag_invalid",
                f"{position}的维度 {dimension_key} 的「不能有缺点」必须是真假值",
            )
        normalized_requirements[dimension_key] = {
            "min_grade": min_grade,
            "shortcomings_empty": no_shortcomings,
        }

    return {
        "key": name.strip(),
        "source": source,
        "defect_key": defect.strip(),
        "evidence_contains_any": [str(token) for token in evidence],
        "foundation_requirements": normalized_requirements,
    }


def load_quality_rules(
    contract: dict[str, Any],
) -> tuple[dict[str, Any] | None, list[dict[str, Any]]] | None:
    """从合同装载质量规则，翻译成执行层内部形状。

    返回 ``None`` 表示合同没有本块（调用方应回落旧路径）；
    返回 ``(soft_cap, exemptions)``，与执行层既有消费形状一致。
    """
    if not isinstance(contract, dict):
        raise QualityRulesError("contract_not_object", "合同必须是对象")

    block = contract.get(CONTRACT_BLOCK_KEY)
    if block is None:
        return None

    assert_quality_rules_isolated(block)

    if block.get("enabled") is False:
        return None, []

    snapshot = block.get("snapshot_limit")
    normalized_snapshot: dict[str, Any] | None = None
    if isinstance(snapshot, dict):
        normalized_snapshot = _validate_snapshot_limit(snapshot)

    exceptions = block.get("defect_exceptions") or []
    normalized_exceptions = [
        _validate_defect_exception(item, index) for index, item in enumerate(exceptions)
    ]

    seen_keys: set[str] = set()
    for item in normalized_exceptions:
        if item["key"] in seen_keys:
            raise QualityRulesError(
                "defect_exception_name_duplicate", f"硬伤例外名字重复：{item['key']}"
            )
        seen_keys.add(item["key"])

    return normalized_snapshot, normalized_exceptions


def validate_quality_rules_block(contract: dict[str, Any]) -> None:
    """合同保存前校验：只做校验，不返回装载结果。"""
    load_quality_rules(contract)


def default_quality_rules_block() -> dict[str, Any]:
    """给前端与种子数据用的默认块，语义等价于当前生产配置。"""
    return copy.deepcopy({
        "enabled": True,
        "snapshot_limit": {
            "enabled": True,
            "name": "随手拍限分",
            "signal": "production_fields.reason",
            "when_reason_contains": ["是随手拍"],
            "max_score": 59,
        },
        "defect_exceptions": [
            {
                "name": "subject_obscuring_brand_wordmark",
                "defect": "subject_obscuring_watermark",
                "defect_source": "image_defects",
                "when_evidence_contains": ["品牌文字", "品牌字样"],
                "require_dimensions": [
                    {
                        "dimension": "detail_completion",
                        "min_grade": 4,
                        "no_shortcomings": True,
                    },
                    {
                        "dimension": "presentation_integrity",
                        "min_grade": 4,
                        "no_shortcomings": True,
                    },
                ],
            }
        ],
    })
