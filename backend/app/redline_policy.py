"""Deterministic redline pre-filter for ADR-0033 (framework-first phase).

This module is a **pure-function** contract layer: it performs no IO, no
network calls, no database access and no model calls.  Every function is
referentially transparent and produces JSON-serializable, order-stable output
for a given input, so the redline decision is fully regressible.

Scope for this phase (framework only):
- Freeze the ``redline-policy-v1`` data contract and its fail-closed validator.
- Evaluate redlines against an already-produced 调用A precheck payload.

Out of scope (later independent phases): wiring into the worker/production
path, the L-level direction migration, and touching published/consumer
contracts.  The ``hit_level`` value here is validated for legality only
(``L1``..``L5``); its "淘汰档" direction semantics are handled elsewhere.
"""

from __future__ import annotations

import re
from typing import Any

from .schema_adapter import PRODUCTION_REASON_VALUES


REDLINE_POLICY_FORMAT_VERSION = "redline-policy-v1"

_RULE_KEY_PATTERN = re.compile(r"^[a-z][a-z0-9_]{2,39}$")
_VALID_HIT_LEVELS = frozenset({"L1", "L2", "L3", "L4", "L5"})
_SUPPORTED_SIGNAL = "production_fields.reason"


class RedlinePolicyError(ValueError):
    """Raised when a redline policy is structurally or semantically invalid.

    Carries a stable ``code`` so callers can branch on the failure class
    without string matching the (localized) message.
    """

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def validate_redline_policy(policy: Any) -> None:
    """Fail-closed structural/enum/uniqueness validation of a redline policy.

    Raises ``RedlinePolicyError`` (a ``ValueError`` subclass with ``.code``)
    on any violation.  Reuses ``schema_adapter.PRODUCTION_REASON_VALUES`` for
    ``match_any`` membership rather than re-declaring the enum.
    """
    if not isinstance(policy, dict):
        raise RedlinePolicyError("policy_not_object", "红线策略必须是对象")
    if policy.get("format_version") != REDLINE_POLICY_FORMAT_VERSION:
        raise RedlinePolicyError(
            "format_version_unsupported",
            f"红线策略版本必须是 {REDLINE_POLICY_FORMAT_VERSION}",
        )

    enabled = policy.get("enabled")
    if not isinstance(enabled, bool):
        raise RedlinePolicyError("enabled_not_bool", "红线策略 enabled 必须是布尔值")

    if policy.get("hit_level") not in _VALID_HIT_LEVELS:
        raise RedlinePolicyError(
            "hit_level_invalid", "红线 hit_level 必须是 L1 至 L5 之一"
        )

    hit_score_cap = policy.get("hit_score_cap")
    if not _is_int(hit_score_cap) or not 0 <= hit_score_cap <= 100:
        raise RedlinePolicyError(
            "hit_score_cap_out_of_range", "红线 hit_score_cap 必须是 0 至 100 的整数"
        )

    rules = policy.get("rules")
    if not isinstance(rules, list):
        raise RedlinePolicyError("rules_not_list", "红线 rules 必须是数组")
    if enabled and not rules:
        raise RedlinePolicyError("rules_empty", "启用红线时 rules 不能为空")

    seen_keys: set[str] = set()
    for rule in rules:
        if not isinstance(rule, dict):
            raise RedlinePolicyError("rule_not_object", "红线规则必须是对象")

        key = rule.get("key")
        if not isinstance(key, str) or not _RULE_KEY_PATTERN.match(key):
            raise RedlinePolicyError("rule_key_invalid", "红线规则 key 不符合命名规范")
        if key in seen_keys:
            raise RedlinePolicyError("rule_key_duplicate", f"红线规则 key 重复：{key}")
        seen_keys.add(key)

        if rule.get("signal") != _SUPPORTED_SIGNAL:
            raise RedlinePolicyError(
                "signal_unsupported",
                f"本阶段红线只支持信号源 {_SUPPORTED_SIGNAL}",
            )

        match_any = rule.get("match_any")
        if not isinstance(match_any, list) or not match_any:
            raise RedlinePolicyError(
                "match_any_empty", "红线规则 match_any 必须是非空数组"
            )
        for value in match_any:
            if value not in PRODUCTION_REASON_VALUES:
                raise RedlinePolicyError(
                    "match_value_invalid", "红线 match_any 含未允许的 reason 枚举"
                )

        exemptions = rule.get("exemptions", [])
        if not isinstance(exemptions, list) or any(
            not isinstance(item, str) or not item for item in exemptions
        ):
            raise RedlinePolicyError(
                "exemptions_invalid", "红线规则 exemptions 必须是非空字符串数组或空数组"
            )


def evaluate_redlines(precheck: Any, *, policy: dict) -> dict:
    """Deterministically decide whether a precheck payload hits any redline.

    Validates ``policy`` first (fail-closed).  When the policy is disabled the
    function short-circuits to a non-eliminating result.  Matching reads only
    ``precheck["production_fields"]["reason"]`` (missing / non-list -> empty).
    A rule matches when its ``match_any`` intersects the reason list; if that
    rule carries ``exemptions`` and any exemption text also appears in the
    reason list, the rule is exempted and does not count as a hit.

    Output is a fixed-shape, JSON-serializable, input-stable dict.
    """
    validate_redline_policy(policy)

    if not policy["enabled"]:
        return {"hit": False, "hit_rules": [], "hard_reject": False}

    reasons: set[str] = set()
    production_fields = precheck.get("production_fields") if isinstance(precheck, dict) else None
    if isinstance(production_fields, dict):
        raw_reason = production_fields.get("reason")
        if isinstance(raw_reason, list):
            reasons = {item for item in raw_reason if isinstance(item, str)}

    hit_rules: list[str] = []
    for rule in policy["rules"]:
        if not reasons.intersection(rule["match_any"]):
            continue
        exemptions = rule.get("exemptions", [])
        if any(exemption in reasons for exemption in exemptions):
            continue
        key = rule["key"]
        if key not in hit_rules:
            hit_rules.append(key)

    if not hit_rules:
        return {"hit": False, "hit_rules": [], "hard_reject": False}

    return {
        "hit": True,
        "hit_rules": hit_rules,
        "hit_level": policy["hit_level"],
        "hit_score_cap": policy["hit_score_cap"],
        "hard_reject": True,
    }
