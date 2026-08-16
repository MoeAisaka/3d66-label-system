from fastapi.testclient import TestClient

from app.main import app, current_user
from app.mechanism_profiles import (
    IMAGE_PROFILE,
    PROPOSAL_PROFILE,
    describe_mechanism_profile,
    mechanism_profile_catalog,
)
from app.models import User
from app.three_d_profile import THREE_D_PROFILE


def test_supported_profile_exposes_version_capabilities_and_editor_route() -> None:
    summary = describe_mechanism_profile(
        {"profile_type": IMAGE_PROFILE, "category_key": "space_image"}
    )

    assert summary.profile_type == IMAGE_PROFILE
    assert summary.version == "v1"
    assert summary.capabilities == (
        "structured_editor",
        "candidate_validation",
        "candidate_execution",
        "workflow_incremental",
        "workflow_stock",
    )
    assert summary.editor_route == "image-rule"
    assert summary.read_only_fallback is False
    assert summary.can_execute is True


def test_unknown_profile_is_read_only_and_cannot_execute() -> None:
    summary = describe_mechanism_profile(
        {"profile_type": "future-3d-v99", "category_key": "3d_model"}
    )

    assert summary.version == "v99"
    assert summary.capabilities == ()
    assert summary.editor_route is None
    assert summary.read_only_fallback is True
    assert summary.editable is False
    assert summary.can_execute is False


def test_catalog_enables_combined_3d_su_profile_and_keeps_future_su_slot_closed() -> None:
    catalog = {item["profile_type"]: item for item in mechanism_profile_catalog()}

    assert catalog[IMAGE_PROFILE]["can_execute"] is True
    assert catalog[PROPOSAL_PROFILE]["can_execute"] is True
    assert catalog[THREE_D_PROFILE] == {
        "profile_type": THREE_D_PROFILE,
        "version": "v1",
        "capabilities": [
            "structured_editor",
            "candidate_validation",
            "candidate_execution",
            "workflow_incremental",
            "workflow_stock",
        ],
        "editor_route": "three-d",
        "read_only_fallback": False,
        "editable": True,
        "can_execute": True,
    }
    assert catalog["future-su-controlled-v1"]["can_execute"] is False


def test_profile_catalog_api_exposes_safe_extension_metadata() -> None:
    user = User(username="profile-reader", password_hash="unused")
    app.dependency_overrides[current_user] = lambda: user
    try:
        response = TestClient(app).get(
            "/api/category-evaluation/v3-config/profiles"
        )
        assert response.status_code == 200, response.text
        catalog = {
            item["profile_type"]: item for item in response.json()["items"]
        }
        assert catalog[IMAGE_PROFILE]["editor_route"] == "image-rule"
        assert catalog[THREE_D_PROFILE]["editor_route"] == "three-d"
        assert catalog["future-su-controlled-v1"]["can_execute"] is False
    finally:
        app.dependency_overrides.clear()
