"""锚点启用与调用B正文的一致性守卫。

锚图随请求作为额外图片发出。正文若不声明这件事，模型会把锚点当成待评图的
一部分来评价，而这要跑完整整一轮、烧掉全部模型调用才会暴露。
"""
from __future__ import annotations

from app.inspiration_anchor_mechanism import (
    anchor_prompt_mismatch,
    call_b_declares_anchors,
)


def _contract(count: int = 5, enabled: bool = True) -> dict:
    levels = ["L1", "L2", "L3", "L4", "L5"][:count]
    return {
        "anchor_mechanism": {
            "spec_version": "anchor-mechanism-v1",
            "enabled": enabled,
            "max_anchor_images": 5,
            "anchors": [
                {
                    "level": level,
                    "asset_id": index + 1,
                    "mime_type": "image/jpeg",
                    "sha256": f"{index:064x}",
                }
                for index, level in enumerate(levels)
            ],
        }
    }


def test_enabled_anchors_with_silent_prompt_is_blocked() -> None:
    reason = anchor_prompt_mismatch(_contract(), "你是八维美感评估器。", "")
    assert reason is not None
    # 报错必须给出可执行信息：几张图、顺序、以及怎么修
    assert "5 张" in reason and "6 张图" in reason
    assert "最后一张才是待评图" in reason
    assert "停用锚点图机制" in reason


def test_prompt_declaring_anchors_passes() -> None:
    for body in ("前五张是锚点参照图", "先与锚图做相对比较", "anchor images come first"):
        assert anchor_prompt_mismatch(_contract(), body, "") is None


def test_declaration_in_user_prompt_also_counts() -> None:
    assert anchor_prompt_mismatch(_contract(), "八维评估", "输入含锚点参照图") is None


def test_disabled_or_absent_mechanism_never_blocks() -> None:
    assert anchor_prompt_mismatch(_contract(enabled=False), "八维评估", "") is None
    assert anchor_prompt_mismatch({}, "八维评估", "") is None
    assert anchor_prompt_mismatch(None, "八维评估", "") is None


def test_malformed_contract_is_left_to_existing_validation() -> None:
    """合同本身不合法由既有校验报错，本守卫不重复拦截。"""
    assert anchor_prompt_mismatch({"anchor_mechanism": "not-an-object"}, "x", "") is None


def test_declaration_detector_is_case_insensitive() -> None:
    assert call_b_declares_anchors("ANCHOR reference images", None) is True
    assert call_b_declares_anchors(None, None) is False
