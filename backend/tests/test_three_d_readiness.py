from __future__ import annotations

import pytest

from app.three_d_readiness import (
    ThreeDReadinessError,
    build_three_d_readiness_manifest,
    readiness_manifest_hash,
    validate_three_d_readiness_manifest,
)


def test_readiness_manifest_is_pending_and_has_no_external_effects() -> None:
    manifest = build_three_d_readiness_manifest()

    assert manifest.schema_version == "3d-su-readiness-v1"
    assert manifest.category_key == "model_3d_su"
    assert manifest.status == "pending_external_signoff"
    assert manifest.external_effects.model_dump() == {
        "connect_real_source": False,
        "execute_sql": False,
        "request_permissions": False,
        "write_database": False,
        "call_model": False,
        "publish_labels": False,
        "deploy": False,
    }


def test_readiness_manifest_freezes_identity_fields_permissions_and_probe_hash() -> None:
    manifest = build_three_d_readiness_manifest()

    assert manifest.identity.table_name == "aliyun_3d66_dw.dim_res_info_union"
    assert manifest.identity.key_fields == ("res_type", "ll_id")
    assert manifest.identity.accepted_res_types == (1, 6)
    assert manifest.identity.window_required is True
    assert manifest.identity.approval_state == "pending"
    assert len(manifest.identity.probe_hash) == 64
    assert manifest.permissions.allowed == ("SELECT", "DESCRIBE")
    assert {"DOWNLOAD", "UPDATE", "ALTER", "DROP", "INSERT", "DELETE"} <= set(
        manifest.permissions.denied
    )


def test_readiness_manifest_freezes_platform_fields_extensions_and_quality_gates() -> None:
    manifest = build_three_d_readiness_manifest()

    assert set(manifest.fields.platform_field_keys) == {
        "space",
        "object",
        "style",
        "material",
        "structural_features",
        "architectural_element",
        "soft_decoration",
        "hard_decoration",
        "color",
        "title",
    }
    assert all(
        field.startswith("category.model_3d_su.")
        for field in manifest.fields.category_extensions
    )
    assert manifest.fields.variants == ("whole", "single")
    assert manifest.fields.min_precision == 0.8
    assert manifest.fields.min_recall == 0.7
    assert manifest.fields.owner_signoff_required is True


def test_readiness_manifest_freezes_golden_set_floor_and_revision_policy() -> None:
    manifest = build_three_d_readiness_manifest()

    assert manifest.golden_set.minimum_count == 100
    assert {"3D", "SU", "whole", "single", "L1", "L2", "L3", "L4", "L5"} <= set(
        manifest.golden_set.required_strata
    )
    assert manifest.golden_set.locked_revision_required is True
    assert manifest.golden_set.truth_change_policy == "new_revision_only"


def test_readiness_manifest_hash_is_stable() -> None:
    first = build_three_d_readiness_manifest()
    second = build_three_d_readiness_manifest()

    assert readiness_manifest_hash(first) == readiness_manifest_hash(second)
    assert len(readiness_manifest_hash(first)) == 64


def test_readiness_rejects_ready_status_without_signed_evidence() -> None:
    payload = build_three_d_readiness_manifest().model_dump(mode="json")
    payload["status"] = "ready_for_real_ingress"

    with pytest.raises(ThreeDReadinessError, match="签认"):
        validate_three_d_readiness_manifest(payload)


def test_readiness_rejects_weaker_gates_or_overbroad_permissions() -> None:
    weak_gate = build_three_d_readiness_manifest().model_dump(mode="json")
    weak_gate["fields"]["min_precision"] = 0.79
    with pytest.raises(ThreeDReadinessError, match="Precision"):
        validate_three_d_readiness_manifest(weak_gate)

    overbroad = build_three_d_readiness_manifest().model_dump(mode="json")
    overbroad["permissions"]["allowed"].append("DOWNLOAD")
    with pytest.raises(ThreeDReadinessError, match="SELECT/DESCRIBE"):
        validate_three_d_readiness_manifest(overbroad)


def test_readiness_rejects_non_platform_extension_namespace() -> None:
    payload = build_three_d_readiness_manifest().model_dump(mode="json")
    payload["fields"]["category_extensions"].append("semantic.private_3d_field")

    with pytest.raises(ThreeDReadinessError, match="category.model_3d_su"):
        validate_three_d_readiness_manifest(payload)
