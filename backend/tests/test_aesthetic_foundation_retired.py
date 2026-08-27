"""旧锚点（aesthetic_foundation）退役守卫。

锚点参照图的现行载体是 anchor_mechanism；旧块把八维、阈值、封顶与四张硬编码
锚图糅在一起，无编辑入口也无法归因调试，已停止演进。守卫只拦新建候选，
历史修订与历史评测结果必须仍可读取重放。
"""
from __future__ import annotations

import json

import pytest

from app.category_evaluation_v3_revisions import (
    CategoryEvaluationV3RevisionError,
    _reject_retired_aesthetic_foundation,
)


def test_new_contract_introducing_retired_anchor_block_is_rejected() -> None:
    """父版本没有该块时新引入 → 拦截。"""
    with pytest.raises(CategoryEvaluationV3RevisionError) as excinfo:
        _reject_retired_aesthetic_foundation(
            {"aesthetic_foundation": {"anchors": [{"asset_id": 2045, "level": "L1"}]}},
            json.dumps({"level_scale": {"levels": []}}),
        )
    assert excinfo.value.code == "aesthetic_foundation_retired"
    # 报错必须指出替代方案，否则运营不知道该配哪里
    assert "anchor_mechanism" in str(excinfo.value)


def test_inherited_anchor_block_from_parent_is_allowed() -> None:
    """父版本本就带该块 → 放行。

    否则从历史投影（灵感图现役 rev9 仍带旧锚点）派生任何候选都会被拦死，
    包括只想改阈值的无关改动。
    """
    legacy = {"aesthetic_foundation": {"anchors": [{"asset_id": 2045, "level": "L1"}]}}
    _reject_retired_aesthetic_foundation(dict(legacy), json.dumps(legacy))


def test_anchor_mechanism_contract_passes() -> None:
    """现行锚点机制不受影响。"""
    _reject_retired_aesthetic_foundation({
        "anchor_mechanism": {
            "spec_version": "anchor-mechanism-v1",
            "enabled": True,
            "anchors": [{"level": "L1", "asset_id": 24}],
        },
    })


def test_contract_without_any_anchor_block_passes() -> None:
    _reject_retired_aesthetic_foundation({"level_scale": {"levels": []}})


def test_non_mapping_contract_is_ignored() -> None:
    """守卫只管形状合法的合同，其余交给既有校验，避免重复报错。"""
    _reject_retired_aesthetic_foundation(None)
    _reject_retired_aesthetic_foundation("not-a-contract")


def test_historical_revisions_remain_readable() -> None:
    """历史四锚合同仍能被解析——退役只拦新建，不动读取路径。"""
    from app.inspiration_aesthetic_foundation import ANCHORS
    from app.inspiration_anchor_contract import validate_inspiration_anchor_contract

    # 直接用平台内置的历史四锚（R4 时期冻结的那组）验证读取路径未受影响
    normalized = validate_inspiration_anchor_contract([dict(a) for a in ANCHORS])
    assert [a["level"] for a in normalized] == ["L1", "L2", "L3", "L4"]
