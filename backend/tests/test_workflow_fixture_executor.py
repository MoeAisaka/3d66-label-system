from __future__ import annotations

import importlib
import importlib.util


def _fixture_module():
    assert importlib.util.find_spec("app.workflow_fixture_executor") is not None, (
        "fixture executor module is missing"
    )
    return importlib.import_module("app.workflow_fixture_executor")


def test_identity_transform_and_noop_are_deterministic() -> None:
    fixture = _fixture_module()
    identity = fixture.execute_fixture(
        "identity",
        {"content_key": "3d:1:42", "title": "A"},
        {"fixture": "identity"},
        attempt_no=1,
    )
    assert identity.output_manifest["content_key"] == "3d:1:42"

    transformed = fixture.execute_fixture(
        "transform",
        {"content_key": "3d:1:42", "title": "A"},
        {"fixture": "transform", "mapping": {"asset_key": "content_key"}},
        attempt_no=1,
    )
    assert transformed.output_manifest == {"asset_key": "3d:1:42"}

    noop = fixture.execute_fixture(
        "noop",
        {"content_key": "3d:1:42"},
        {"fixture": "noop"},
        attempt_no=1,
    )
    assert noop.output_manifest["fixture"] == "noop"
    assert noop.output_hash == fixture.hash_manifest(noop.output_manifest)


def test_fail_once_has_no_external_execution_surface() -> None:
    fixture = _fixture_module()
    try:
        fixture.execute_fixture(
            "fail_once",
            {"content_key": "3d:1:42"},
            {"fixture": "fail_once", "source": "print('unsafe')"},
            attempt_no=1,
        )
    except fixture.FixtureExecutionError as exc:
        assert exc.code == "arbitrary_code_field_forbidden"
    else:
        raise AssertionError("unsafe fixture manifest was accepted")

    try:
        fixture.execute_fixture(
            "fail_once",
            {"content_key": "3d:1:42"},
            {"fixture": "fail_once"},
            attempt_no=1,
        )
    except fixture.FixtureExecutionError as exc:
        assert exc.code == "FIXTURE_FAIL_ONCE"
    else:
        raise AssertionError("fail_once did not fail its first attempt")

    success = fixture.execute_fixture(
        "fail_once",
        {"content_key": "3d:1:42"},
        {"fixture": "fail_once"},
        attempt_no=2,
    )
    assert success.output_manifest["fixture"] == "fail_once"

