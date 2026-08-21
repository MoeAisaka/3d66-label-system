from __future__ import annotations

import asyncio
from copy import deepcopy
import hashlib

import pytest

from app.dimension_deduction_bridge import (
    BONUS_CAP_BRIDGE_VERSION,
    DEDUCTION_PROMPT_TEMPLATE_VERSION,
    DimensionDeductionBridgeError,
    FALLBACK_WARNING,
    call_multimodal_for_dimension_deductions,
    compose_rule_deductions,
    empty_deduction_output,
    foundation_required,
    normalize_dimension_deduction_output,
)
from app.b_aesthetic_foundation import BAestheticFoundationError
from app.inspiration_category_seed import build_inspiration_subcategory_dimensions


class Response:
    def __init__(self, parsed: dict) -> None:
        self.parsed = parsed
        self.raw_payload = {"provider": parsed}


class Client:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.system = ""
        self.user = ""

    async def chat_json(self, system: str, user: str, **_kwargs) -> Response:
        self.system, self.user = system, user
        if self.fail:
            raise TimeoutError("timeout")
        config = build_inspiration_subcategory_dimensions()["class_one"]
        dimensions = []
        for dimension in config["common_group"]["schema_definition"]["dimensions"]:
            hits = []
            if not dimensions:
                rule = dimension["deduction_rules"][0]
                hits = [{"rule_id": rule["rule_id"], "confidence": "high", "evidence": "图中主体偏移"}]
            dimensions.append({"dimension_key": dimension["key"], "hit_rules": hits})
        return Response(
            {
                "aesthetic_score": 88,
                "aesthetic_evidence": ["整体画面基础美感可见"],
                "aesthetic_confidence": 0.8,
                "dimensions": dimensions,
                "overall_note": "已逐条核验",
            }
        )


def _bonus_cap_config() -> dict:
    config = deepcopy(build_inspiration_subcategory_dimensions()["class_one"])
    dimensions = config["common_group"]["schema_definition"]["dimensions"]
    for dimension in dimensions:
        dimension["dimension_score_cap"] = 100
        dimension["bonus_rules"] = []
    dimensions[0]["bonus_rules"] = [
        {
            "rule_id": "composition_clear",
            "description": "构图层级清晰完整",
            "bonus": 8,
            "tags": ["构图"],
        }
    ]
    return config


class BonusCapClient:
    def __init__(self, config: dict, *, fail: bool = False) -> None:
        self.config = config
        self.fail = fail
        self.system = ""
        self.user = ""

    async def chat_json(self, system: str, user: str, **_kwargs) -> Response:
        self.system, self.user = system, user
        if self.fail:
            raise TimeoutError("timeout")
        dimensions = {}
        for index, dimension in enumerate(
            self.config["common_group"]["schema_definition"]["dimensions"]
        ):
            dimensions[dimension["key"]] = {
                "hit_rules": [],
                "hit_bonus_rules": (
                    [
                        {
                            "rule_id": "composition_clear",
                            "confidence": "high",
                            "evidence": "构图层级清晰",
                        }
                    ]
                    if index == 0
                    else []
                ),
            }
        return Response(
            {
                "aesthetic_score": 88,
                "aesthetic_evidence": ["整体画面基础美感可见"],
                "aesthetic_confidence": 0.8,
                "dimensions": dimensions,
                "overall_note": "已核验正负规则",
            }
        )


def test_bridge_normalizes_rule_hits_and_uses_chinese_prompt() -> None:
    config = build_inspiration_subcategory_dimensions()["class_one"]
    client = Client()
    output = asyncio.run(
        call_multimodal_for_dimension_deductions(
            "image.jpg", config, client=client, mime_type="image/jpeg", precheck={}
        )
    )
    assert output["warning"] is None
    first = next(iter(output["dimensions"].values()))
    assert first["hit_rules"][0]["evidence"] == "图中主体偏移"
    assert "aesthetic_score" in client.system
    assert "扣" in client.user
    assert output["prompt_identity"] == {
        "template_version": DEDUCTION_PROMPT_TEMPLATE_VERSION,
        "system_sha256": hashlib.sha256(client.system.encode("utf-8")).hexdigest(),
        "user_sha256": hashlib.sha256(client.user.encode("utf-8")).hexdigest(),
    }


def test_bridge_provider_failure_returns_empty_hits_and_warning() -> None:
    config = build_inspiration_subcategory_dimensions()["class_one"]
    output = asyncio.run(
        call_multimodal_for_dimension_deductions(
            "image.jpg", config, client=Client(fail=True), mime_type="image/jpeg"
        )
    )
    assert output["warning"] == FALLBACK_WARNING
    assert all(item["hit_rules"] == [] for item in output["dimensions"].values())
    identity = output["prompt_identity"]
    assert identity["template_version"] == DEDUCTION_PROMPT_TEMPLATE_VERSION
    assert len(identity["system_sha256"]) == 64
    assert len(identity["user_sha256"]) == 64


def test_bonus_cap_bridge_normalizes_both_rule_arrays_and_prompt() -> None:
    config = _bonus_cap_config()
    client = BonusCapClient(config)
    output = asyncio.run(
        call_multimodal_for_dimension_deductions(
            "image.jpg", config, client=client, mime_type="image/jpeg"
        )
    )
    assert output["bridge_version"] == BONUS_CAP_BRIDGE_VERSION
    first = next(iter(output["dimensions"].values()))
    assert first["hit_rules"] == []
    assert first["hit_bonus_rules"][0]["rule_id"] == "composition_clear"
    assert "扣分规则" in client.user
    assert "加分规则" in client.user


def test_bonus_cap_provider_failure_returns_both_empty_arrays_and_warning() -> None:
    config = _bonus_cap_config()
    output = asyncio.run(
        call_multimodal_for_dimension_deductions(
            "image.jpg",
            config,
            client=BonusCapClient(config, fail=True),
            mime_type="image/jpeg",
        )
    )
    assert output["warning"] == FALLBACK_WARNING
    assert all(
        item == {"hit_rules": [], "hit_bonus_rules": []}
        for item in output["dimensions"].values()
    )


def test_bonus_cap_bridge_rejects_duplicate_provider_hits() -> None:
    config = _bonus_cap_config()
    first = config["common_group"]["schema_definition"]["dimensions"][0]
    payload = {
        "aesthetic_score": 88,
        "aesthetic_evidence": ["整体画面基础美感可见"],
        "aesthetic_confidence": 0.8,
        "dimensions": {
            dimension["key"]: {"hit_rules": [], "hit_bonus_rules": []}
            for dimension in config["common_group"]["schema_definition"]["dimensions"]
        }
    }
    hit = {
        "rule_id": first["bonus_rules"][0]["rule_id"],
        "confidence": "high",
        "evidence": "同一规则重复返回",
    }
    payload["dimensions"][first["key"]]["hit_bonus_rules"] = [hit, hit]
    with pytest.raises(DimensionDeductionBridgeError) as excinfo:
        normalize_dimension_deduction_output(payload, config)
    assert excinfo.value.code == "rule_duplicate"


# --- Call-B failure must stay fail-open for contracts that never declared the
# --- foundation, while "B answered but omitted the score" stays fail-closed.


def _undeclared_config() -> dict:
    config = deepcopy(build_inspiration_subcategory_dimensions()["class_one"])
    config.pop("b_aesthetic_foundation", None)
    return config


def _answered_without_score(config: dict) -> dict:
    """A payload the provider really returned, minus any aesthetic score."""
    return {
        "dimensions": {
            key: {"hit_rules": []}
            for key in empty_deduction_output(config)["dimensions"]
        },
        "overall_note": "已核验规则",
    }


def test_undeclared_contract_degrades_when_call_b_itself_fails() -> None:
    config = _undeclared_config()
    assert foundation_required(config) is True
    fallback = empty_deduction_output(config, warning=FALLBACK_WARNING)

    composed = compose_rule_deductions(
        config=config,
        dimension_output=fallback,
        require_foundation=foundation_required(config),
    )

    assert composed.get("aesthetic_score") is None


def test_undeclared_contract_fails_closed_when_call_b_omits_score() -> None:
    config = _undeclared_config()

    with pytest.raises(BAestheticFoundationError):
        compose_rule_deductions(
            config=config,
            dimension_output=_answered_without_score(config),
            require_foundation=foundation_required(config),
        )


def test_declared_contract_stays_strict_even_on_provider_failure() -> None:
    config = deepcopy(build_inspiration_subcategory_dimensions()["class_one"])
    assert isinstance(config.get("b_aesthetic_foundation"), dict)
    fallback = empty_deduction_output(config, warning=FALLBACK_WARNING)

    with pytest.raises(BAestheticFoundationError):
        compose_rule_deductions(
            config=config,
            dimension_output=fallback,
            require_foundation=foundation_required(config),
        )
