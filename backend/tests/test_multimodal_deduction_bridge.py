from __future__ import annotations

import asyncio

from app.dimension_deduction_bridge import (
    FALLBACK_WARNING,
    call_multimodal_for_dimension_deductions,
)
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
        return Response({"dimensions": dimensions, "overall_note": "已逐条核验"})


def test_bridge_normalizes_rule_hits_and_uses_chinese_prompt() -> None:
    config = build_inspiration_subcategory_dimensions()["class_one"]
    client = Client()
    output = asyncio.run(
        call_multimodal_for_dimension_deductions(
            "image.jpg", config, client=client, mime_type="image/jpeg", precheck={}
        )
    )
    assert output["warning"] is None
    assert output["dimensions"][0]["hit_rules"][0]["evidence"] == "图中主体偏移"
    assert "不打1-5分" in client.system
    assert "扣" in client.user


def test_bridge_provider_failure_returns_empty_hits_and_warning() -> None:
    config = build_inspiration_subcategory_dimensions()["class_one"]
    output = asyncio.run(
        call_multimodal_for_dimension_deductions(
            "image.jpg", config, client=Client(fail=True), mime_type="image/jpeg"
        )
    )
    assert output["warning"] == FALLBACK_WARNING
    assert all(item["hit_rules"] == [] for item in output["dimensions"])
