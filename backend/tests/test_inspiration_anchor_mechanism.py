"""锚点图机制的单一职责守卫测试。

这批测试是 2026-08-24 架构裁决的执行点：锚点图机制只能承载各等级锚点图片，
任何把阈值／维度／红线封顶塞回来的改动都必须在这里失败。
"""
from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from app.inspiration_anchor_contract import InspirationAnchorContractError
from app.inspiration_anchor_mechanism import (
    ANCHOR_MECHANISM_KEY,
    ANCHOR_MECHANISM_SPEC_VERSION,
    FOREIGN_MECHANISM_KEYS,
    MAX_ANCHOR_IMAGES_CEILING,
    anchor_levels_covered,
    anchor_mechanism_request,
    assert_anchor_mechanism_isolated,
    validate_anchor_mechanism,
)

_SHA = "a" * 64


def _anchor(level: str, asset_id: int, **extra: object) -> dict[str, object]:
    return {
        "level": level,
        "asset_id": asset_id,
        "mime_type": "image/jpeg",
        "sha256": _SHA,
        **extra,
    }


def _block(**overrides: object) -> dict[str, object]:
    block: dict[str, object] = {
        "spec_version": ANCHOR_MECHANISM_SPEC_VERSION,
        "enabled": True,
        "max_anchor_images": 5,
        "anchors": [_anchor("L1", 11), _anchor("L3", 33)],
    }
    block.update(overrides)
    return block


# --- 单一职责守卫：这是本模块存在的理由 ---------------------------------


@pytest.mark.parametrize("foreign_key", sorted(FOREIGN_MECHANISM_KEYS))
def test_isolation_guard_rejects_every_known_foreign_mechanism(
    foreign_key: str,
) -> None:
    """阈值／维度／红线封顶等外来机制一律不得进入锚点机制块。"""
    block = _block(**{foreign_key: {"anything": 1}})
    with pytest.raises(InspirationAnchorContractError) as excinfo:
        validate_anchor_mechanism({ANCHOR_MECHANISM_KEY: block})
    assert excinfo.value.code == "anchor_mechanism_not_isolated"
    # 报错必须指出正确归属，避免下一个人再塞回来
    assert FOREIGN_MECHANISM_KEYS[foreign_key] in str(excinfo.value)


def test_isolation_guard_lists_every_intruder_at_once() -> None:
    block = _block(score_thresholds={}, dimensions=[], redline_policy={})
    with pytest.raises(InspirationAnchorContractError) as excinfo:
        assert_anchor_mechanism_isolated(block)
    message = str(excinfo.value)
    for key in ("score_thresholds", "dimensions", "redline_policy"):
        assert key in message


def test_isolation_guard_covers_the_five_items_split_out_of_the_old_foundation() -> None:
    """历史基座混入的五样东西，每一样都必须被守卫拦住。"""
    for key in (
        "score_thresholds",
        "dimension_keys",
        "quality_rules",
        "hard_defect_exemptions",
        "boundary_policy",
    ):
        assert key in FOREIGN_MECHANISM_KEYS, f"{key} 必须在外来机制黑名单里"


def test_unknown_field_is_rejected_even_when_not_a_known_foreign_key() -> None:
    """白名单之外的字段一律拒绝，杜绝换个名字夹带规则。"""
    block = _block(sneaky_cap=20)
    with pytest.raises(InspirationAnchorContractError) as excinfo:
        validate_anchor_mechanism({ANCHOR_MECHANISM_KEY: block})
    assert excinfo.value.code == "anchor_mechanism_unknown_field"


# --- 正常路径：各等级锚点图片可自定义 -----------------------------------


def test_valid_block_is_normalised() -> None:
    result = validate_anchor_mechanism({ANCHOR_MECHANISM_KEY: _block()})
    assert result is not None
    assert result["spec_version"] == ANCHOR_MECHANISM_SPEC_VERSION
    assert result["enabled"] is True
    assert [item["level"] for item in result["anchors"]] == ["L1", "L3"]


def test_missing_block_means_mechanism_absent() -> None:
    assert validate_anchor_mechanism({}) is None
    assert validate_anchor_mechanism({ANCHOR_MECHANISM_KEY: None}) is None


def test_any_level_subset_is_allowed_so_operators_can_start_small() -> None:
    """不强制五档齐全——运营可以只配 L1 和 L5 就开始调试。"""
    block = _block(anchors=[_anchor("L5", 55)])
    result = validate_anchor_mechanism({ANCHOR_MECHANISM_KEY: block})
    assert result is not None
    assert [item["level"] for item in result["anchors"]] == ["L5"]


def test_anchors_are_sorted_by_level_then_asset_for_deterministic_prompts() -> None:
    block = _block(
        max_anchor_images=5,
        anchors=[_anchor("L4", 9), _anchor("L1", 7), _anchor("L1", 3)],
    )
    result = validate_anchor_mechanism({ANCHOR_MECHANISM_KEY: block})
    assert result is not None
    assert [(i["level"], i["asset_id"]) for i in result["anchors"]] == [
        ("L1", 3),
        ("L1", 7),
        ("L4", 9),
    ]


def test_replacing_an_anchor_is_allowed() -> None:
    """自定义的核心：换掉某一等级的图片必须被接受。"""
    before = validate_anchor_mechanism({ANCHOR_MECHANISM_KEY: _block()})
    after = validate_anchor_mechanism(
        {ANCHOR_MECHANISM_KEY: _block(anchors=[_anchor("L1", 99), _anchor("L3", 33)])}
    )
    assert before is not None and after is not None
    assert before["anchors"][0]["asset_id"] == 11
    assert after["anchors"][0]["asset_id"] == 99


def test_note_is_trimmed_and_optional() -> None:
    block = _block(anchors=[_anchor("L2", 5, note="  参考构图  ")])
    result = validate_anchor_mechanism({ANCHOR_MECHANISM_KEY: block})
    assert result is not None
    assert result["anchors"][0]["note"] == "参考构图"

    blank = _block(anchors=[_anchor("L2", 5, note="   ")])
    result_blank = validate_anchor_mechanism({ANCHOR_MECHANISM_KEY: blank})
    assert result_blank is not None
    assert "note" not in result_blank["anchors"][0]


def test_anchor_levels_covered_reports_in_level_order() -> None:
    block = _block(
        max_anchor_images=5,
        anchors=[_anchor("L4", 4), _anchor("L1", 1), _anchor("L4", 40)],
    )
    assert anchor_levels_covered(block) == ["L1", "L4"]
    assert anchor_levels_covered(None) == []
    assert anchor_levels_covered({"anchors": "nope"}) == []


# --- 形状校验 fail-closed ------------------------------------------------


def test_enabled_mechanism_requires_at_least_one_anchor() -> None:
    with pytest.raises(InspirationAnchorContractError) as excinfo:
        validate_anchor_mechanism({ANCHOR_MECHANISM_KEY: _block(anchors=[])})
    assert excinfo.value.code == "anchor_mechanism_invalid"


def test_disabled_mechanism_may_have_no_anchors() -> None:
    result = validate_anchor_mechanism(
        {ANCHOR_MECHANISM_KEY: _block(enabled=False, anchors=[])}
    )
    assert result is not None
    assert result["enabled"] is False
    assert result["anchors"] == []


def test_duplicate_asset_is_rejected() -> None:
    block = _block(anchors=[_anchor("L1", 7), _anchor("L2", 7)])
    with pytest.raises(InspirationAnchorContractError) as excinfo:
        validate_anchor_mechanism({ANCHOR_MECHANISM_KEY: block})
    assert "重复" in str(excinfo.value)


def test_anchor_count_over_max_is_rejected() -> None:
    block = _block(
        max_anchor_images=1, anchors=[_anchor("L1", 1), _anchor("L2", 2)]
    )
    with pytest.raises(InspirationAnchorContractError):
        validate_anchor_mechanism({ANCHOR_MECHANISM_KEY: block})


def test_max_anchor_images_ceiling_is_enforced() -> None:
    block = _block(max_anchor_images=MAX_ANCHOR_IMAGES_CEILING + 1)
    with pytest.raises(InspirationAnchorContractError):
        validate_anchor_mechanism({ANCHOR_MECHANISM_KEY: block})


@pytest.mark.parametrize(
    "bad",
    [
        {"level": "L9", "asset_id": 1, "mime_type": "image/jpeg", "sha256": _SHA},
        {"level": "L1", "asset_id": 0, "mime_type": "image/jpeg", "sha256": _SHA},
        {"level": "L1", "asset_id": True, "mime_type": "image/jpeg", "sha256": _SHA},
        {"level": "L1", "asset_id": 1, "mime_type": "image/gif", "sha256": _SHA},
        {"level": "L1", "asset_id": 1, "mime_type": "image/jpeg", "sha256": "XYZ"},
        {"level": "L1", "asset_id": 1, "mime_type": "image/jpeg", "sha256": _SHA.upper()},
    ],
)
def test_malformed_anchor_entries_fail_closed(bad: dict[str, object]) -> None:
    with pytest.raises(InspirationAnchorContractError):
        validate_anchor_mechanism({ANCHOR_MECHANISM_KEY: _block(anchors=[bad])})


def test_wrong_spec_version_is_rejected() -> None:
    with pytest.raises(InspirationAnchorContractError) as excinfo:
        validate_anchor_mechanism(
            {ANCHOR_MECHANISM_KEY: _block(spec_version="anchor-mechanism-v0")}
        )
    assert excinfo.value.code == "anchor_mechanism_spec_version_invalid"


@pytest.mark.parametrize("bad_block", ["nope", 5, [], True])
def test_non_object_block_is_rejected(bad_block: object) -> None:
    with pytest.raises(InspirationAnchorContractError):
        validate_anchor_mechanism({ANCHOR_MECHANISM_KEY: bad_block})


def test_anchors_must_be_a_list() -> None:
    with pytest.raises(InspirationAnchorContractError):
        validate_anchor_mechanism({ANCHOR_MECHANISM_KEY: _block(anchors="L1")})


# --- 执行层装配：anchor_mechanism_request --------------------------------


class _FakeAsset:
    def __init__(self, asset_id: int, path: Path) -> None:
        self.id = asset_id
        self._path = path


def _write(tmp_path: Path, name: str, payload: bytes) -> tuple[Path, str]:
    path = tmp_path / name
    path.write_bytes(payload)
    return path, hashlib.sha256(payload).hexdigest()


def _resolver(asset: object) -> Path:
    return asset._path  # type: ignore[attr-defined]


def _request(
    contract: dict[str, object],
    target: Path,
    assets: dict[int, object],
) -> tuple[list[tuple[str, Path, str | None]], int] | None:
    return anchor_mechanism_request(
        contract,
        target,
        "image/jpeg",
        assets_by_id=assets,
        asset_path_resolver=_resolver,
    )


def test_request_returns_none_when_mechanism_absent_disabled_or_empty(
    tmp_path: Path,
) -> None:
    target, _ = _write(tmp_path, "target.jpg", b"target")
    assert _request({}, target, {}) is None
    assert (
        _request(
            {ANCHOR_MECHANISM_KEY: _block(enabled=False, anchors=[])}, target, {}
        )
        is None
    )


def test_request_appends_target_after_anchors(tmp_path: Path) -> None:
    """锚点图在前、待评图片在最后——顺序决定模型怎么读这批图。"""
    a1, h1 = _write(tmp_path, "a1.jpg", b"anchor-one")
    a2, h2 = _write(tmp_path, "a2.jpg", b"anchor-two")
    target, _ = _write(tmp_path, "target.jpg", b"target")
    assets = {11: _FakeAsset(11, a1), 33: _FakeAsset(33, a2)}
    contract = {
        ANCHOR_MECHANISM_KEY: _block(
            anchors=[_anchor("L1", 11, sha256=h1), _anchor("L3", 33, sha256=h2)]
        )
    }

    result = _request(contract, target, assets)
    assert result is not None
    samples, count = result
    assert count == 3
    assert [path for _, path, _ in samples] == [a1, a2, target]
    assert "L1" in samples[0][0] and "L3" in samples[1][0]
    assert "待评图片" in samples[2][0]


def test_request_allows_what_the_frozen_validator_rejects(tmp_path: Path) -> None:
    """回归守卫：拆分要换来的正是这三种自定义能力。

    旧的 Owner 锚图冻结校验（validate_inspiration_anchor_contract）实测会拒绝
    「只配两档」「换掉某一档的图」「乱序」，而运营自定义必须支持这三种。
    """
    a1, h1 = _write(tmp_path, "a1.jpg", b"one")
    a2, h2 = _write(tmp_path, "a2.jpg", b"two")
    swapped, h_swapped = _write(tmp_path, "swapped.jpg", b"replacement")
    target, _ = _write(tmp_path, "target.jpg", b"target")
    assets = {
        11: _FakeAsset(11, a1),
        33: _FakeAsset(33, a2),
        99: _FakeAsset(99, swapped),
    }

    # 只配两档
    partial = {
        ANCHOR_MECHANISM_KEY: _block(
            anchors=[_anchor("L1", 11, sha256=h1), _anchor("L3", 33, sha256=h2)]
        )
    }
    assert _request(partial, target, assets) is not None

    # 换掉 L3 的图
    replaced = {
        ANCHOR_MECHANISM_KEY: _block(
            anchors=[_anchor("L1", 11, sha256=h1), _anchor("L3", 99, sha256=h_swapped)]
        )
    }
    result = _request(replaced, target, assets)
    assert result is not None
    assert result[0][1][1] == swapped

    # 乱序录入（规范化后按等级排序，不报错）
    unordered = {
        ANCHOR_MECHANISM_KEY: _block(
            anchors=[_anchor("L3", 33, sha256=h2), _anchor("L1", 11, sha256=h1)]
        )
    }
    ordered = _request(unordered, target, assets)
    assert ordered is not None
    assert [path for _, path, _ in ordered[0]] == [a1, a2, target]


def test_request_rejects_missing_asset(tmp_path: Path) -> None:
    target, _ = _write(tmp_path, "target.jpg", b"target")
    contract = {ANCHOR_MECHANISM_KEY: _block(anchors=[_anchor("L1", 77)])}
    with pytest.raises(InspirationAnchorContractError) as excinfo:
        _request(contract, target, {})
    assert excinfo.value.code == "anchor_asset_missing"


def test_request_rejects_hash_mismatch(tmp_path: Path) -> None:
    """内容身份守卫：图片被换掉但合同没更新哈希，必须失败而不是静默用新图。"""
    a1, _ = _write(tmp_path, "a1.jpg", b"original")
    target, _ = _write(tmp_path, "target.jpg", b"target")
    contract = {
        ANCHOR_MECHANISM_KEY: _block(anchors=[_anchor("L1", 11, sha256="b" * 64)])
    }
    with pytest.raises(InspirationAnchorContractError) as excinfo:
        _request(contract, target, {11: _FakeAsset(11, a1)})
    assert excinfo.value.code == "anchor_hash_mismatch"


def test_request_rejects_missing_file(tmp_path: Path) -> None:
    target, _ = _write(tmp_path, "target.jpg", b"target")
    absent = tmp_path / "gone.jpg"
    contract = {ANCHOR_MECHANISM_KEY: _block(anchors=[_anchor("L1", 11)])}
    with pytest.raises(InspirationAnchorContractError) as excinfo:
        _request(contract, target, {11: _FakeAsset(11, absent)})
    assert excinfo.value.code == "anchor_missing"


def test_request_wraps_resolver_failure(tmp_path: Path) -> None:
    target, _ = _write(tmp_path, "target.jpg", b"target")
    contract = {ANCHOR_MECHANISM_KEY: _block(anchors=[_anchor("L1", 11)])}

    def boom(_asset: object) -> Path:
        raise RuntimeError("NAS 不可达")

    with pytest.raises(InspirationAnchorContractError) as excinfo:
        anchor_mechanism_request(
            contract,
            target,
            "image/jpeg",
            assets_by_id={11: _FakeAsset(11, tmp_path / "x.jpg")},
            asset_path_resolver=boom,
        )
    assert excinfo.value.code == "anchor_source_unavailable"


def test_request_puts_note_into_the_label(tmp_path: Path) -> None:
    a1, h1 = _write(tmp_path, "a1.jpg", b"one")
    target, _ = _write(tmp_path, "target.jpg", b"target")
    contract = {
        ANCHOR_MECHANISM_KEY: _block(
            anchors=[_anchor("L1", 11, sha256=h1, note="构图最干净")]
        )
    }
    result = _request(contract, target, {11: _FakeAsset(11, a1)})
    assert result is not None
    assert "构图最干净" in result[0][0][0]
