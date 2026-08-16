from __future__ import annotations

import pytest

from app.asset_identity import (
    AssetIdentityError,
    IdentityVerificationEvidence,
    resolve_three_d_su_identity,
)


def _verified() -> IdentityVerificationEvidence:
    return IdentityVerificationEvidence(
        source_system="aliyun_3d66_dw",
        key_fields=("res_type", "ll_id"),
        status="verified",
        evidence_hash="a" * 64,
    )


def test_verified_3d_identity_builds_deterministic_content_key() -> None:
    result = resolve_three_d_su_identity(
        source_system="aliyun_3d66_dw",
        payload={"res_type": 1, "ll_id": "12345", "res_id": "r-9"},
        verification=_verified(),
    )
    assert result.content_key == "aliyun_3d66_dw:1:12345"
    assert result.identity_status == "verified"
    assert len(result.identity_hash) == 64


def test_unverified_identity_never_builds_content_key() -> None:
    result = resolve_three_d_su_identity(
        source_system="aliyun_3d66_dw",
        payload={"res_type": 6, "ll_id": "su-1"},
        verification=None,
    )
    assert result.content_key is None
    assert result.identity_status == "pending_verification"


@pytest.mark.parametrize("res_type", [0, 2, 9, "1"])
def test_unsupported_or_untyped_res_type_is_rejected(res_type) -> None:
    with pytest.raises(AssetIdentityError, match="res_type"):
        resolve_three_d_su_identity(
            source_system="aliyun_3d66_dw",
            payload={"res_type": res_type, "ll_id": "123"},
            verification=_verified(),
        )


def test_conflict_evidence_blocks_identity() -> None:
    evidence = _verified().model_copy(update={"status": "conflict"})
    with pytest.raises(AssetIdentityError, match="冲突"):
        resolve_three_d_su_identity(
            source_system="aliyun_3d66_dw",
            payload={"res_type": 1, "ll_id": "123"},
            verification=evidence,
        )
