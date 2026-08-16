"""Deterministic 3D/SU source identity resolution without persistence or IO."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class AssetIdentityError(ValueError):
    """Raised when source identity inputs cannot be resolved safely."""


class IdentityVerificationEvidence(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    source_system: str
    key_fields: tuple[Literal["res_type", "ll_id"], ...]
    status: Literal["verified", "conflict"]
    evidence_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


class ResolvedAssetIdentity(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    source_system: str
    res_type: Literal[1, 6]
    ll_id: str
    res_id: str | None
    content_key: str | None
    identity_status: Literal["pending_verification", "verified"]
    identity_hash: str


def resolve_three_d_su_identity(
    *,
    source_system: str,
    payload: Mapping[str, Any],
    verification: IdentityVerificationEvidence | None,
) -> ResolvedAssetIdentity:
    """Resolve an asset key only when matching uniqueness evidence is verified."""

    normalized_source = str(source_system).strip()
    if not normalized_source or len(normalized_source) > 120:
        raise AssetIdentityError("source_system 必须填写且长度不超过 120")

    raw_res_type = payload.get("res_type")
    if isinstance(raw_res_type, bool) or not isinstance(raw_res_type, int):
        raise AssetIdentityError("res_type 必须是整数 1 或 6")
    if raw_res_type not in (1, 6):
        raise AssetIdentityError("res_type 只支持 1（3D）或 6（SU）")

    raw_ll_id = payload.get("ll_id")
    ll_id = "" if raw_ll_id is None else str(raw_ll_id).strip()
    if not ll_id or len(ll_id) > 160:
        raise AssetIdentityError("ll_id 必须填写且长度不超过 160")

    raw_res_id = payload.get("res_id")
    res_id = None if raw_res_id is None else str(raw_res_id).strip()
    if res_id == "":
        res_id = None
    if res_id is not None and len(res_id) > 160:
        raise AssetIdentityError("res_id 长度不能超过 160")

    identity_payload = {
        "source_system": normalized_source,
        "res_type": raw_res_type,
        "ll_id": ll_id,
        "res_id": res_id,
    }
    canonical = json.dumps(
        identity_payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    identity_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    if verification is None:
        return ResolvedAssetIdentity(
            **identity_payload,
            content_key=None,
            identity_status="pending_verification",
            identity_hash=identity_hash,
        )
    if verification.source_system != normalized_source:
        raise AssetIdentityError("身份签认 source_system 与事件不一致")
    if verification.key_fields != ("res_type", "ll_id"):
        raise AssetIdentityError("身份签认 key_fields 必须为 res_type + ll_id")
    if verification.status == "conflict":
        raise AssetIdentityError("源身份唯一性存在冲突")

    return ResolvedAssetIdentity(
        **identity_payload,
        content_key=f"{normalized_source}:{raw_res_type}:{ll_id}",
        identity_status="verified",
        identity_hash=identity_hash,
    )
