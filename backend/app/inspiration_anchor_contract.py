"""Pure validation for version-frozen inspiration Owner image anchors."""
from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from pathlib import PurePosixPath, PureWindowsPath
from typing import Any


ANCHOR_LEVELS = ("L1", "L2", "L3", "L4", "L5")
ANCHOR_PUBLIC_ITEM_KEYS = frozenset({
    "asset_id", "level", "mime_type", "sha256",
})
ANCHOR_LEGACY_ITEM_KEYS = ANCHOR_PUBLIC_ITEM_KEYS | frozenset({"stored_name"})
ANCHOR_MIME_TYPES = frozenset({"image/jpeg", "image/png"})
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


class InspirationAnchorContractError(ValueError):
    """A stable, fail-closed error emitted for bad frozen anchor metadata."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def is_safe_inspiration_anchor_stored_name(value: object) -> bool:
    """Accept one basename that is safe on both POSIX and Windows workers."""
    if not isinstance(value, str) or not value or value in {".", ".."}:
        return False
    if "/" in value or "\\" in value:
        return False
    posix_path = PurePosixPath(value)
    windows_path = PureWindowsPath(value)
    return (
        not posix_path.is_absolute()
        and not windows_path.is_absolute()
        and not windows_path.drive
        and posix_path.name == value
        and windows_path.name == value
    )


def validate_inspiration_anchor_contract(
    anchors: Any,
) -> tuple[dict[str, object], ...]:
    """Normalize the only supported four- or five-anchor contract shape.

    The first four levels are mandatory and ordered. A candidate may append L5;
    it cannot replace, reorder, or silently omit an existing Owner anchor.
    """
    if not isinstance(anchors, Sequence) or isinstance(anchors, (str, bytes)):
        raise InspirationAnchorContractError("anchor_contract_invalid", "锚图合同必须是数组")
    if len(anchors) not in {4, 5}:
        raise InspirationAnchorContractError("anchor_contract_invalid", "锚图合同只能包含L1至L4或L1至L5")

    item_key_sets = [
        set(raw_anchor)
        for raw_anchor in anchors
        if isinstance(raw_anchor, Mapping)
    ]
    if len(item_key_sets) != len(anchors) or any(
        keys not in {ANCHOR_PUBLIC_ITEM_KEYS, ANCHOR_LEGACY_ITEM_KEYS}
        for keys in item_key_sets
    ):
        raise InspirationAnchorContractError("anchor_contract_invalid", "锚图字段不符合冻结合同")
    public_contract = all(keys == ANCHOR_PUBLIC_ITEM_KEYS for keys in item_key_sets)
    legacy_contract = all(keys == ANCHOR_LEGACY_ITEM_KEYS for keys in item_key_sets)
    if not (public_contract or legacy_contract):
        raise InspirationAnchorContractError(
            "anchor_contract_invalid", "锚图合同不得混用公开与内部存储名字段"
        )
    if legacy_contract and len(anchors) != 4:
        raise InspirationAnchorContractError(
            "anchor_contract_invalid", "带内部存储名的旧锚图合同只能包含L1至L4"
        )
    if public_contract and len(anchors) != 5:
        raise InspirationAnchorContractError(
            "anchor_contract_invalid", "公开锚图合同仅允许用于含L5的五锚候选"
        )

    normalized: list[dict[str, object]] = []
    expected_levels = ANCHOR_LEVELS[:len(anchors)]
    seen_asset_ids: set[int] = set()
    for index, raw_anchor in enumerate(anchors):
        assert isinstance(raw_anchor, Mapping)
        asset_id = raw_anchor.get("asset_id")
        level = raw_anchor.get("level")
        mime_type = raw_anchor.get("mime_type")
        digest = raw_anchor.get("sha256")
        if not _is_int(asset_id) or asset_id < 1 or asset_id in seen_asset_ids:
            raise InspirationAnchorContractError("anchor_contract_invalid", "锚图asset_id非法或重复")
        if level != expected_levels[index]:
            raise InspirationAnchorContractError("anchor_contract_invalid", "锚图等级必须从L1连续排序")
        stored_name = raw_anchor.get("stored_name")
        if stored_name is not None and not is_safe_inspiration_anchor_stored_name(stored_name):
            raise InspirationAnchorContractError("anchor_contract_invalid", "锚图存储名非法")
        if mime_type not in ANCHOR_MIME_TYPES:
            raise InspirationAnchorContractError("anchor_contract_invalid", "锚图MIME类型非法")
        if not isinstance(digest, str) or not SHA256_PATTERN.fullmatch(digest):
            raise InspirationAnchorContractError("anchor_contract_invalid", "锚图SHA-256非法")
        seen_asset_ids.add(asset_id)
        normalized_anchor: dict[str, object] = {
            "asset_id": asset_id,
            "level": level,
            "mime_type": mime_type,
            "sha256": digest,
        }
        if stored_name is not None:
            normalized_anchor["stored_name"] = stored_name
        normalized.append(normalized_anchor)
    return tuple(normalized)
