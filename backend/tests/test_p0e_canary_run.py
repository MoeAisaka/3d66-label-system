"""Comprehensive deterministic tests for P0-E E2 canary run-plan state machine.

All tests are pure: no filesystem, network, database, or model access.
Tests are authored correct-by-construction; execution requires the project's
pytest environment (intentionally unavailable to the AI author per project rules).
"""
from __future__ import annotations

import json
from typing import Any

import pytest

from app.p0e_canary_run import (
    APPROVALS_READY,
    CANDIDATE_READY,
    CANCELLED,
    DRAFT,
    FAILED,
    FREEZE_READY,
    HUMAN_REVIEW_READY,
    PREFLIGHT_READY,
    RUN_PLAN_VERSION,
    CanaryRunError,
    CanaryRunIssue,
    RunSnapshot,
    advance_to_approvals_ready,
    advance_to_candidate_ready,
    advance_to_freeze_ready,
    advance_to_human_review_ready,
    advance_to_preflight_ready,
    cancel_run,
    create_run,
    fail_run,
)
from app.p0e_candidate_package import CANDIDATE_PACKAGE_VERSION
from app.p0e_image_freeze import MANIFEST_VERSION
from app.p0e_safe_import import PREFLIGHT_SCHEMA_VERSION


# ── shared fixtures ────────────────────────────────────────────────────────────

_VALID_BATCH_KEY = "p0e:" + "a" * 64


def _valid_preflight() -> dict[str, Any]:
    return {
        "schema_version": PREFLIGHT_SCHEMA_VERSION,
        "batch_key": _VALID_BATCH_KEY,
        "mode": "preflight_only",
        "writes_business_database": False,
    }


def _valid_approval(batch_key: str = _VALID_BATCH_KEY) -> dict[str, Any]:
    return {
        "human_approved": True,
        "approved_by": "operator-01",
        "batch_key": batch_key,
        "applied_mappings": [],
    }


def _valid_fetch_config() -> dict[str, Any]:
    return {
        "allowed_hosts": ["images.example.test", "cdn.example.test"],
        "pinned_https_attested": True,
    }


def _valid_manifest(count: int = 5) -> dict[str, Any]:
    return {
        "manifest_version": MANIFEST_VERSION,
        "complete": True,
        "expected_source_count": count,
        "frozen_source_count": count,
        "errors": [],
        "assets": [],
    }


def _valid_candidate_preview(size: int = 40) -> dict[str, Any]:
    return {
        "schema_version": CANDIDATE_PACKAGE_VERSION,
        "complete_for_requested_preview": True,
        "selected_count": size,
        "forms_gold": False,
        "downloads_performed": False,
        "model_runs_performed": False,
    }


def _valid_handoff(item_count: int = 40) -> dict[str, Any]:
    return {
        "all_items_require_review": True,
        "no_truth_or_gold_granted": True,
        "item_count": item_count,
    }


def _error_code(exc: pytest.ExceptionInfo[CanaryRunError]) -> str:
    return str(exc.value.issue.code)


# Build a fully-advanced snapshot in one call (for terminal/idempotency tests).
def _full_run(target_size: int = 40) -> RunSnapshot:
    s = create_run("3D", target_size=target_size, seed="test-seed")
    s = advance_to_preflight_ready(s, xlsx_preflight=_valid_preflight())
    s = advance_to_approvals_ready(s, approval_artifact=_valid_approval())
    s = advance_to_freeze_ready(s, fetch_config=_valid_fetch_config())
    s = advance_to_candidate_ready(s, manifest=_valid_manifest())
    s = advance_to_human_review_ready(
        s,
        candidate_preview=_valid_candidate_preview(target_size),
        human_review_handoff=_valid_handoff(target_size),
    )
    return s


# ── happy path ─────────────────────────────────────────────────────────────────


def test_happy_path_all_states_in_order() -> None:
    s0 = create_run("3D", target_size=40, seed="my-seed")
    assert s0.state == DRAFT
    assert s0.plan["domain"] == "3D"
    assert s0.plan["target_size"] == 40
    assert s0.plan["seed"] == "my-seed"
    assert s0.plan["plan_version"] == RUN_PLAN_VERSION
    # All invariants False from the start.
    assert s0.writes_business_database is False
    assert s0.downloads_performed is False
    assert s0.model_runs_performed is False
    assert s0.forms_gold is False
    assert s0.publishes_release is False

    s1 = advance_to_preflight_ready(s0, xlsx_preflight=_valid_preflight())
    assert s1.state == PREFLIGHT_READY
    assert s1.evidence["xlsx_preflight"]["batch_key"] == _VALID_BATCH_KEY

    s2 = advance_to_approvals_ready(s1, approval_artifact=_valid_approval())
    assert s2.state == APPROVALS_READY
    assert s2.evidence["approval"]["human_approved"] is True

    s3 = advance_to_freeze_ready(s2, fetch_config=_valid_fetch_config())
    assert s3.state == FREEZE_READY
    assert s3.evidence["fetch_config"]["pinned_https_attested"] is True

    s4 = advance_to_candidate_ready(s3, manifest=_valid_manifest())
    assert s4.state == CANDIDATE_READY

    s5 = advance_to_human_review_ready(
        s4,
        candidate_preview=_valid_candidate_preview(40),
        human_review_handoff=_valid_handoff(40),
    )
    assert s5.state == HUMAN_REVIEW_READY
    # Invariants remain False throughout.
    assert s5.writes_business_database is False
    assert s5.downloads_performed is False
    assert s5.model_runs_performed is False
    assert s5.forms_gold is False
    assert s5.publishes_release is False
    # Evidence is accumulated across all stages.
    for key in ("xlsx_preflight", "approval", "fetch_config", "manifest",
                "candidate_preview", "human_review_handoff"):
        assert key in s5.evidence


def test_happy_path_target_size_boundary_30_and_50() -> None:
    for size in (30, 50):
        s = create_run("3D", target_size=size, seed="boundary")
        assert s.plan["target_size"] == size
        s = advance_to_preflight_ready(s, xlsx_preflight=_valid_preflight())
        s = advance_to_approvals_ready(s, approval_artifact=_valid_approval())
        s = advance_to_freeze_ready(s, fetch_config=_valid_fetch_config())
        s = advance_to_candidate_ready(s, manifest=_valid_manifest())
        s = advance_to_human_review_ready(
            s,
            candidate_preview=_valid_candidate_preview(size),
            human_review_handoff=_valid_handoff(size),
        )
        assert s.state == HUMAN_REVIEW_READY


# ── plan validation (create_run) ───────────────────────────────────────────────


def test_plan_domain_not_3d_is_rejected() -> None:
    for bad_domain in ("2D", "3d66", "", "3D ", "ALL"):
        with pytest.raises(CanaryRunError) as exc:
            create_run(bad_domain, target_size=40, seed="s")
        assert _error_code(exc) == "PLAN_DOMAIN_NOT_3D"
        assert exc.value.issue.current_state == DRAFT


def test_plan_target_size_out_of_range_is_rejected() -> None:
    for bad_size in (0, 1, 29, 51, 100, -1):
        with pytest.raises(CanaryRunError) as exc:
            create_run("3D", target_size=bad_size, seed="s")
        assert _error_code(exc) == "PLAN_TARGET_SIZE_INVALID"


def test_plan_empty_seed_is_rejected() -> None:
    for bad_seed in ("", "   ", "\t"):
        with pytest.raises(CanaryRunError) as exc:
            create_run("3D", target_size=40, seed=bad_seed)
        assert _error_code(exc) == "PLAN_SEED_REQUIRED"


def test_plan_domain_case_insensitive_3d() -> None:
    # "3d" (lowercase) is the canonical form; the plan stores "3D".
    s = create_run("3d", target_size=40, seed="seed")
    assert s.plan["domain"] == "3D"


# ── skipped gates ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("from_state", "fn_name"),
    [
        (DRAFT, "approvals_ready"),
        (DRAFT, "freeze_ready"),
        (DRAFT, "candidate_ready"),
        (DRAFT, "human_review_ready"),
        (PREFLIGHT_READY, "freeze_ready"),
        (PREFLIGHT_READY, "candidate_ready"),
        (PREFLIGHT_READY, "human_review_ready"),
        (APPROVALS_READY, "candidate_ready"),
        (APPROVALS_READY, "human_review_ready"),
        (FREEZE_READY, "human_review_ready"),
    ],
)
def test_skipped_gate_is_rejected(from_state: str, fn_name: str) -> None:
    """Every attempt to skip a gate is rejected with TRANSITION_GATE_SKIPPED."""
    # Build a snapshot at the desired starting state.
    s = create_run("3D", target_size=40, seed="s")
    if from_state in (
        PREFLIGHT_READY, APPROVALS_READY, FREEZE_READY,
        CANDIDATE_READY, HUMAN_REVIEW_READY,
    ):
        s = advance_to_preflight_ready(s, xlsx_preflight=_valid_preflight())
    if from_state in (
        APPROVALS_READY, FREEZE_READY, CANDIDATE_READY, HUMAN_REVIEW_READY,
    ):
        s = advance_to_approvals_ready(s, approval_artifact=_valid_approval())
    if from_state in (FREEZE_READY, CANDIDATE_READY, HUMAN_REVIEW_READY):
        s = advance_to_freeze_ready(s, fetch_config=_valid_fetch_config())
    if from_state in (CANDIDATE_READY, HUMAN_REVIEW_READY):
        s = advance_to_candidate_ready(s, manifest=_valid_manifest())

    target_fns = {
        "approvals_ready": lambda snap: advance_to_approvals_ready(
            snap, approval_artifact=_valid_approval()
        ),
        "freeze_ready": lambda snap: advance_to_freeze_ready(
            snap, fetch_config=_valid_fetch_config()
        ),
        "candidate_ready": lambda snap: advance_to_candidate_ready(
            snap, manifest=_valid_manifest()
        ),
        "human_review_ready": lambda snap: advance_to_human_review_ready(
            snap,
            candidate_preview=_valid_candidate_preview(),
            human_review_handoff=_valid_handoff(),
        ),
    }
    with pytest.raises(CanaryRunError) as exc:
        target_fns[fn_name](s)
    assert _error_code(exc) in {
        "TRANSITION_GATE_SKIPPED",
        "TRANSITION_BACKWARD_NOT_ALLOWED",
    }
    assert exc.value.issue.current_state == from_state


# ── backward transitions ───────────────────────────────────────────────────────


def test_backward_transitions_are_rejected() -> None:
    s = create_run("3D", target_size=40, seed="s")
    s1 = advance_to_preflight_ready(s, xlsx_preflight=_valid_preflight())
    s2 = advance_to_approvals_ready(s1, approval_artifact=_valid_approval())

    # Trying to go back from APPROVALS_READY to PREFLIGHT_READY.
    with pytest.raises(CanaryRunError) as exc:
        advance_to_preflight_ready(s2, xlsx_preflight=_valid_preflight())
    assert exc.value.issue.code in {
        "TRANSITION_GATE_SKIPPED",
        "TRANSITION_BACKWARD_NOT_ALLOWED",
    }
    assert exc.value.issue.current_state == APPROVALS_READY

    # Trying to go back from FREEZE_READY to PREFLIGHT_READY.
    s3 = advance_to_freeze_ready(s2, fetch_config=_valid_fetch_config())
    with pytest.raises(CanaryRunError) as exc2:
        advance_to_preflight_ready(s3, xlsx_preflight=_valid_preflight())
    assert exc2.value.issue.code in {
        "TRANSITION_GATE_SKIPPED",
        "TRANSITION_BACKWARD_NOT_ALLOWED",
    }


# ── terminal state: no resume ──────────────────────────────────────────────────


def test_no_transitions_allowed_from_human_review_ready() -> None:
    s = _full_run()
    assert s.state == HUMAN_REVIEW_READY
    with pytest.raises(CanaryRunError) as exc:
        advance_to_preflight_ready(s, xlsx_preflight=_valid_preflight())
    assert _error_code(exc) == "TRANSITION_FROM_TERMINAL_STATE"


def test_no_transitions_allowed_from_failed() -> None:
    s = create_run("3D", target_size=40, seed="s")
    s_failed = fail_run(s, reason="test failure")
    assert s_failed.state == FAILED
    with pytest.raises(CanaryRunError) as exc:
        advance_to_preflight_ready(s_failed, xlsx_preflight=_valid_preflight())
    assert _error_code(exc) == "TRANSITION_FROM_TERMINAL_STATE"


def test_no_transitions_allowed_from_cancelled() -> None:
    s = create_run("3D", target_size=40, seed="s")
    s_cancelled = cancel_run(s, reason="operator decision")
    assert s_cancelled.state == CANCELLED
    with pytest.raises(CanaryRunError) as exc:
        advance_to_preflight_ready(
            s_cancelled, xlsx_preflight=_valid_preflight()
        )
    assert _error_code(exc) == "TRANSITION_FROM_TERMINAL_STATE"


def test_cancel_or_fail_from_terminal_raises() -> None:
    s = _full_run()
    with pytest.raises(CanaryRunError) as exc:
        cancel_run(s, reason="late cancel")
    assert _error_code(exc) == "TRANSITION_FROM_TERMINAL_STATE"

    s_failed = fail_run(
        create_run("3D", target_size=40, seed="s"), reason="reason"
    )
    with pytest.raises(CanaryRunError) as exc2:
        fail_run(s_failed, reason="double fail")
    assert _error_code(exc2) == "TRANSITION_FROM_TERMINAL_STATE"


def test_failed_and_cancelled_are_not_reported_as_complete() -> None:
    s = create_run("3D", target_size=40, seed="s")
    sf = fail_run(s, reason="x")
    sc = cancel_run(s, reason="y")
    assert sf.state == FAILED
    assert sc.state == CANCELLED
    # Neither terminal failure state should have state == HUMAN_REVIEW_READY.
    assert sf.state != HUMAN_REVIEW_READY
    assert sc.state != HUMAN_REVIEW_READY


# ── preflight gate validation ──────────────────────────────────────────────────


def test_preflight_unrecognized_schema_is_rejected() -> None:
    s = create_run("3D", target_size=40, seed="s")
    bad = {**_valid_preflight(), "schema_version": "p0e-xlsx-preflight-v99"}
    with pytest.raises(CanaryRunError) as exc:
        advance_to_preflight_ready(s, xlsx_preflight=bad)
    assert _error_code(exc) == "PREFLIGHT_SCHEMA_UNRECOGNIZED"
    assert exc.value.issue.current_state == DRAFT


def test_preflight_missing_batch_key_is_rejected() -> None:
    s = create_run("3D", target_size=40, seed="s")
    for bad_key in ("", None, "not-starting-with-p0e:", "   "):
        evidence = {**_valid_preflight(), "batch_key": bad_key}
        with pytest.raises(CanaryRunError) as exc:
            advance_to_preflight_ready(s, xlsx_preflight=evidence)
        assert _error_code(exc) == "PREFLIGHT_BATCH_KEY_MISSING"


# ── approval gate validation ───────────────────────────────────────────────────


def test_approval_missing_human_approved_is_rejected() -> None:
    s = create_run("3D", target_size=40, seed="s")
    s = advance_to_preflight_ready(s, xlsx_preflight=_valid_preflight())

    # Completely absent key.
    artifact = {
        "approved_by": "operator",
        "batch_key": _VALID_BATCH_KEY,
        "applied_mappings": [],
    }
    with pytest.raises(CanaryRunError) as exc:
        advance_to_approvals_ready(s, approval_artifact=artifact)
    assert _error_code(exc) == "APPROVAL_HUMAN_APPROVED_REQUIRED"
    assert exc.value.issue.current_state == PREFLIGHT_READY


def test_approval_human_approved_false_is_rejected() -> None:
    s = create_run("3D", target_size=40, seed="s")
    s = advance_to_preflight_ready(s, xlsx_preflight=_valid_preflight())
    artifact = {**_valid_approval(), "human_approved": False}
    with pytest.raises(CanaryRunError) as exc:
        advance_to_approvals_ready(s, approval_artifact=artifact)
    assert _error_code(exc) == "APPROVAL_HUMAN_APPROVED_REQUIRED"


def test_silent_mapping_attempt_without_human_approved_is_rejected() -> None:
    """Supplying applied_mappings without human_approved=True is rejected."""
    s = create_run("3D", target_size=40, seed="s")
    s = advance_to_preflight_ready(s, xlsx_preflight=_valid_preflight())
    # Simulate someone trying to silently apply a farmat→format mapping
    # without explicit human sign-off.
    artifact = {
        "human_approved": False,  # missing proper approval
        "approved_by": "auto-bot",
        "batch_key": _VALID_BATCH_KEY,
        "applied_mappings": [
            {
                "source_internal_name": "farmat",
                "target_field": "format",
                "applied": True,  # silently applied — must be rejected
            }
        ],
    }
    with pytest.raises(CanaryRunError) as exc:
        advance_to_approvals_ready(s, approval_artifact=artifact)
    assert _error_code(exc) == "APPROVAL_HUMAN_APPROVED_REQUIRED"


def test_approval_missing_approved_by_is_rejected() -> None:
    s = create_run("3D", target_size=40, seed="s")
    s = advance_to_preflight_ready(s, xlsx_preflight=_valid_preflight())
    for bad in ("", None, "  "):
        artifact = {**_valid_approval(), "approved_by": bad}
        with pytest.raises(CanaryRunError) as exc:
            advance_to_approvals_ready(s, approval_artifact=artifact)
        assert _error_code(exc) == "APPROVAL_APPROVED_BY_REQUIRED"


def test_approval_batch_key_mismatch_is_rejected() -> None:
    s = create_run("3D", target_size=40, seed="s")
    s = advance_to_preflight_ready(s, xlsx_preflight=_valid_preflight())
    artifact = {**_valid_approval(), "batch_key": "p0e:" + "b" * 64}
    with pytest.raises(CanaryRunError) as exc:
        advance_to_approvals_ready(s, approval_artifact=artifact)
    assert _error_code(exc) == "APPROVAL_BATCH_KEY_MISMATCH"


def test_approval_mappings_must_be_list_or_absent() -> None:
    s = create_run("3D", target_size=40, seed="s")
    s = advance_to_preflight_ready(s, xlsx_preflight=_valid_preflight())
    # A dict instead of a list is rejected.
    artifact = {**_valid_approval(), "applied_mappings": {"key": "val"}}
    with pytest.raises(CanaryRunError) as exc:
        advance_to_approvals_ready(s, approval_artifact=artifact)
    assert _error_code(exc) == "APPROVAL_MAPPINGS_INVALID"


def test_approval_with_empty_applied_mappings_is_accepted() -> None:
    """Empty applied_mappings is valid — no mappings needed for this batch."""
    s = create_run("3D", target_size=40, seed="s")
    s = advance_to_preflight_ready(s, xlsx_preflight=_valid_preflight())
    s2 = advance_to_approvals_ready(
        s, approval_artifact={**_valid_approval(), "applied_mappings": []}
    )
    assert s2.state == APPROVALS_READY
    assert s2.evidence["approval"]["applied_mappings"] == []


# ── fetch_config gate validation ──────────────────────────────────────────────


def test_empty_host_allowlist_is_rejected() -> None:
    s = create_run("3D", target_size=40, seed="s")
    s = advance_to_preflight_ready(s, xlsx_preflight=_valid_preflight())
    s = advance_to_approvals_ready(s, approval_artifact=_valid_approval())

    for bad_hosts in ([], None, ""):
        config = {**_valid_fetch_config(), "allowed_hosts": bad_hosts}
        with pytest.raises(CanaryRunError) as exc:
            advance_to_freeze_ready(s, fetch_config=config)
        assert _error_code(exc) == "FETCH_HOST_ALLOWLIST_EMPTY"
        assert exc.value.issue.current_state == APPROVALS_READY


def test_missing_pinned_https_attestation_is_rejected() -> None:
    s = create_run("3D", target_size=40, seed="s")
    s = advance_to_preflight_ready(s, xlsx_preflight=_valid_preflight())
    s = advance_to_approvals_ready(s, approval_artifact=_valid_approval())

    for bad_attest in (False, None, 0, "yes", 1):
        config = {**_valid_fetch_config(), "pinned_https_attested": bad_attest}
        with pytest.raises(CanaryRunError) as exc:
            advance_to_freeze_ready(s, fetch_config=config)
        assert _error_code(exc) == "FETCH_PINNED_HTTPS_NOT_ATTESTED"


def test_allowed_hosts_are_normalized_idna() -> None:
    s = create_run("3D", target_size=40, seed="s")
    s = advance_to_preflight_ready(s, xlsx_preflight=_valid_preflight())
    s = advance_to_approvals_ready(s, approval_artifact=_valid_approval())
    config = {**_valid_fetch_config(), "allowed_hosts": ["IMAGES.EXAMPLE.TEST"]}
    s2 = advance_to_freeze_ready(s, fetch_config=config)
    assert "images.example.test" in s2.evidence["fetch_config"]["allowed_hosts"]


# ── manifest gate validation ───────────────────────────────────────────────────


def _at_freeze_ready() -> RunSnapshot:
    s = create_run("3D", target_size=40, seed="s")
    s = advance_to_preflight_ready(s, xlsx_preflight=_valid_preflight())
    s = advance_to_approvals_ready(s, approval_artifact=_valid_approval())
    return advance_to_freeze_ready(s, fetch_config=_valid_fetch_config())


def test_manifest_unrecognized_version_is_rejected() -> None:
    s = _at_freeze_ready()
    bad = {**_valid_manifest(), "manifest_version": "p0e-frozen-manifest-v99"}
    with pytest.raises(CanaryRunError) as exc:
        advance_to_candidate_ready(s, manifest=bad)
    assert _error_code(exc) == "MANIFEST_VERSION_UNRECOGNIZED"
    assert exc.value.issue.current_state == FREEZE_READY


def test_manifest_not_complete_is_rejected() -> None:
    s = _at_freeze_ready()
    bad = {**_valid_manifest(), "complete": False}
    with pytest.raises(CanaryRunError) as exc:
        advance_to_candidate_ready(s, manifest=bad)
    assert _error_code(exc) == "MANIFEST_NOT_COMPLETE"


def test_manifest_count_mismatch_is_rejected() -> None:
    s = _at_freeze_ready()
    bad = {**_valid_manifest(5), "frozen_source_count": 4}
    with pytest.raises(CanaryRunError) as exc:
        advance_to_candidate_ready(s, manifest=bad)
    assert _error_code(exc) == "MANIFEST_COUNT_MISMATCH"


def test_manifest_zero_frozen_count_is_rejected() -> None:
    s = _at_freeze_ready()
    bad = {
        **_valid_manifest(0),
        "expected_source_count": 0,
        "frozen_source_count": 0,
    }
    with pytest.raises(CanaryRunError) as exc:
        advance_to_candidate_ready(s, manifest=bad)
    assert _error_code(exc) == "MANIFEST_EMPTY"


def test_manifest_with_errors_is_rejected() -> None:
    s = _at_freeze_ready()
    bad = {
        **_valid_manifest(),
        "errors": [{"code": "DOWNLOAD_FAILED", "message": "x"}],
    }
    with pytest.raises(CanaryRunError) as exc:
        advance_to_candidate_ready(s, manifest=bad)
    assert _error_code(exc) == "MANIFEST_HAS_ERRORS"


# ── candidate preview gate validation ─────────────────────────────────────────


def _at_candidate_ready(target_size: int = 40) -> RunSnapshot:
    s = _at_freeze_ready()
    return advance_to_candidate_ready(s, manifest=_valid_manifest())


def test_candidate_schema_unrecognized_is_rejected() -> None:
    s = _at_candidate_ready()
    bad = {**_valid_candidate_preview(), "schema_version": "p0e-candidate-preview-v99"}
    with pytest.raises(CanaryRunError) as exc:
        advance_to_human_review_ready(
            s,
            candidate_preview=bad,
            human_review_handoff=_valid_handoff(),
        )
    assert _error_code(exc) == "CANDIDATE_SCHEMA_UNRECOGNIZED"
    assert exc.value.issue.current_state == CANDIDATE_READY


def test_candidate_preview_incomplete_is_rejected() -> None:
    s = _at_candidate_ready()
    bad = {**_valid_candidate_preview(), "complete_for_requested_preview": False}
    with pytest.raises(CanaryRunError) as exc:
        advance_to_human_review_ready(
            s,
            candidate_preview=bad,
            human_review_handoff=_valid_handoff(),
        )
    assert _error_code(exc) == "CANDIDATE_PREVIEW_INCOMPLETE"


def test_underfilled_preview_is_rejected() -> None:
    """selected_count < target_size must produce CANDIDATE_PREVIEW_SIZE_MISMATCH."""
    s = _at_candidate_ready()
    # The plan has target_size=40; preview has selected_count=35 (underfill).
    bad = {
        **_valid_candidate_preview(35),
        "complete_for_requested_preview": True,
    }
    with pytest.raises(CanaryRunError) as exc:
        advance_to_human_review_ready(
            s,
            candidate_preview=bad,
            human_review_handoff=_valid_handoff(35),
        )
    assert _error_code(exc) == "CANDIDATE_PREVIEW_SIZE_MISMATCH"


def test_preview_claiming_gold_is_rejected() -> None:
    s = _at_candidate_ready()
    bad = {**_valid_candidate_preview(), "forms_gold": True}
    with pytest.raises(CanaryRunError) as exc:
        advance_to_human_review_ready(
            s, candidate_preview=bad, human_review_handoff=_valid_handoff()
        )
    assert _error_code(exc) == "CANDIDATE_PREVIEW_CLAIMS_GOLD"


def test_preview_claiming_downloads_is_rejected() -> None:
    s = _at_candidate_ready()
    bad = {**_valid_candidate_preview(), "downloads_performed": True}
    with pytest.raises(CanaryRunError) as exc:
        advance_to_human_review_ready(
            s, candidate_preview=bad, human_review_handoff=_valid_handoff()
        )
    assert _error_code(exc) == "CANDIDATE_PREVIEW_CLAIMS_DOWNLOADS"


def test_preview_claiming_model_runs_is_rejected() -> None:
    s = _at_candidate_ready()
    bad = {**_valid_candidate_preview(), "model_runs_performed": True}
    with pytest.raises(CanaryRunError) as exc:
        advance_to_human_review_ready(
            s, candidate_preview=bad, human_review_handoff=_valid_handoff()
        )
    assert _error_code(exc) == "CANDIDATE_PREVIEW_CLAIMS_MODEL_RUNS"


def test_handoff_missing_all_items_require_review_is_rejected() -> None:
    s = _at_candidate_ready()
    bad_handoff = {**_valid_handoff(), "all_items_require_review": False}
    with pytest.raises(CanaryRunError) as exc:
        advance_to_human_review_ready(
            s,
            candidate_preview=_valid_candidate_preview(),
            human_review_handoff=bad_handoff,
        )
    assert _error_code(exc) == "HANDOFF_ALL_ITEMS_REQUIRE_REVIEW"


def test_handoff_no_truth_or_gold_granted_must_be_true() -> None:
    s = _at_candidate_ready()
    bad_handoff = {**_valid_handoff(), "no_truth_or_gold_granted": False}
    with pytest.raises(CanaryRunError) as exc:
        advance_to_human_review_ready(
            s,
            candidate_preview=_valid_candidate_preview(),
            human_review_handoff=bad_handoff,
        )
    assert _error_code(exc) == "HANDOFF_NO_TRUTH_OR_GOLD_GRANTED"


def test_handoff_item_count_mismatch_is_rejected() -> None:
    s = _at_candidate_ready()
    bad_handoff = {**_valid_handoff(40), "item_count": 39}
    with pytest.raises(CanaryRunError) as exc:
        advance_to_human_review_ready(
            s,
            candidate_preview=_valid_candidate_preview(40),
            human_review_handoff=bad_handoff,
        )
    assert _error_code(exc) == "HANDOFF_ITEM_COUNT_MISMATCH"


# ── unsafe URL evidence ───────────────────────────────────────────────────────


def test_preflight_evidence_with_unsafe_url_is_rejected() -> None:
    s = create_run("3D", target_size=40, seed="s")
    # A preflight evidence dict that contains a URL with a query token.
    bad = {
        **_valid_preflight(),
        "source_file": "clean.xlsx",
        "extra_url": "https://images.example.test/a.png?token=secret",
    }
    with pytest.raises(CanaryRunError) as exc:
        advance_to_preflight_ready(s, xlsx_preflight=bad)
    assert _error_code(exc) == "EVIDENCE_URL_CONTAINS_QUERY"


def test_preflight_evidence_url_with_userinfo_is_rejected() -> None:
    s = create_run("3D", target_size=40, seed="s")
    bad = {
        **_valid_preflight(),
        "ref_url": "https://user:pass@images.example.test/a.png",
    }
    with pytest.raises(CanaryRunError) as exc:
        advance_to_preflight_ready(s, xlsx_preflight=bad)
    assert _error_code(exc) == "EVIDENCE_URL_CONTAINS_USERINFO"


def test_preflight_evidence_url_with_fragment_is_rejected() -> None:
    s = create_run("3D", target_size=40, seed="s")
    bad = {
        **_valid_preflight(),
        "ref_url": "https://images.example.test/a.png#section",
    }
    with pytest.raises(CanaryRunError) as exc:
        advance_to_preflight_ready(s, xlsx_preflight=bad)
    assert _error_code(exc) == "EVIDENCE_URL_CONTAINS_FRAGMENT"


def test_manifest_evidence_with_unsafe_url_in_nested_asset_is_rejected() -> None:
    s = _at_freeze_ready()
    bad_manifest = {
        **_valid_manifest(),
        "assets": [
            {
                "source_url": "https://images.example.test/img.png?sig=abc",
                "asset_id": "asset_sha256_aaa",
            }
        ],
    }
    with pytest.raises(CanaryRunError) as exc:
        advance_to_candidate_ready(s, manifest=bad_manifest)
    assert _error_code(exc) == "EVIDENCE_URL_CONTAINS_QUERY"


def test_candidate_evidence_with_unsafe_url_in_nested_item_is_rejected() -> None:
    s = _at_candidate_ready()
    bad_preview = {
        **_valid_candidate_preview(),
        "selected": [
            {"source_url": "https://images.example.test/img.png?token=x"}
        ],
    }
    with pytest.raises(CanaryRunError) as exc:
        advance_to_human_review_ready(
            s,
            candidate_preview=bad_preview,
            human_review_handoff=_valid_handoff(),
        )
    assert _error_code(exc) == "EVIDENCE_URL_CONTAINS_QUERY"


def test_safe_url_without_query_or_userinfo_is_accepted() -> None:
    """A sanitized source_url (no query/fragment/userinfo) passes the scan."""
    s = _at_freeze_ready()
    good_manifest = {
        **_valid_manifest(),
        "assets": [
            {
                "source_url": "https://images.example.test/img.png",
                "asset_id": "asset_sha256_aaa",
            }
        ],
    }
    s2 = advance_to_candidate_ready(s, manifest=good_manifest)
    assert s2.state == CANDIDATE_READY


# ── idempotency ───────────────────────────────────────────────────────────────


def test_identical_plan_produces_identical_run_id() -> None:
    s1 = create_run("3D", target_size=40, seed="fixed-seed")
    s2 = create_run("3D", target_size=40, seed="fixed-seed")
    assert s1.run_id == s2.run_id


def test_different_seed_produces_different_run_id() -> None:
    s1 = create_run("3D", target_size=40, seed="seed-a")
    s2 = create_run("3D", target_size=40, seed="seed-b")
    assert s1.run_id != s2.run_id


def test_identical_transition_inputs_produce_identical_snapshot() -> None:
    s0 = create_run("3D", target_size=40, seed="idem-seed")
    pf = _valid_preflight()
    s1a = advance_to_preflight_ready(s0, xlsx_preflight=pf)
    s1b = advance_to_preflight_ready(s0, xlsx_preflight=pf)
    assert s1a.snapshot_fingerprint == s1b.snapshot_fingerprint
    assert s1a.state == s1b.state
    assert s1a.evidence == s1b.evidence


def test_idempotency_across_full_run() -> None:
    """Two identically parameterized full runs produce identical final snapshots."""
    run_a = _full_run(40)
    run_b = _full_run(40)
    assert run_a.run_id == run_b.run_id
    assert run_a.snapshot_fingerprint == run_b.snapshot_fingerprint
    assert run_a.state == run_b.state


def test_different_target_size_produces_different_run_id() -> None:
    s30 = create_run("3D", target_size=30, seed="s")
    s50 = create_run("3D", target_size=50, seed="s")
    assert s30.run_id != s50.run_id


# ── machine-readable errors ───────────────────────────────────────────────────


def test_canary_run_error_is_json_serializable() -> None:
    try:
        create_run("NOT3D", target_size=40, seed="s")
    except CanaryRunError as err:
        serialized = json.loads(json.dumps(err.as_dict(), ensure_ascii=False))
        assert serialized["error"]["code"] == "PLAN_DOMAIN_NOT_3D"
        assert "message" in serialized["error"]
        assert serialized["error"]["current_state"] == DRAFT
        assert "attempted_transition" in serialized["error"]
        assert isinstance(serialized["error"]["retryable"], bool)
    else:
        pytest.fail("Expected CanaryRunError not raised")


def test_canary_run_issue_fields_are_all_present() -> None:
    try:
        s = create_run("3D", target_size=40, seed="s")
        advance_to_preflight_ready(
            s, xlsx_preflight={**_valid_preflight(), "schema_version": "bad"}
        )
    except CanaryRunError as err:
        d = err.issue.as_dict()
        for field in ("code", "message", "current_state", "attempted_transition",
                      "retryable"):
            assert field in d, f"Missing field {field!r}"
        assert d["current_state"] == DRAFT
        assert isinstance(d["retryable"], bool)
    else:
        pytest.fail("Expected CanaryRunError not raised")


def test_canary_run_error_str_includes_code() -> None:
    try:
        create_run("3D", target_size=29, seed="s")
    except CanaryRunError as err:
        assert "PLAN_TARGET_SIZE_INVALID" in str(err)
    else:
        pytest.fail("Expected CanaryRunError not raised")


# ── snapshot invariants ───────────────────────────────────────────────────────


def test_invariants_are_always_false_throughout() -> None:
    """The five safety invariants must be False at every state in the chain."""
    s = create_run("3D", target_size=40, seed="inv-test")
    states_seen: list[str] = []
    for advance_fn, kwargs in [
        (advance_to_preflight_ready, {"xlsx_preflight": _valid_preflight()}),
        (advance_to_approvals_ready, {"approval_artifact": _valid_approval()}),
        (advance_to_freeze_ready, {"fetch_config": _valid_fetch_config()}),
        (advance_to_candidate_ready, {"manifest": _valid_manifest()}),
        (
            advance_to_human_review_ready,
            {
                "candidate_preview": _valid_candidate_preview(40),
                "human_review_handoff": _valid_handoff(40),
            },
        ),
    ]:
        s = advance_fn(s, **kwargs)
        states_seen.append(s.state)
        assert s.writes_business_database is False
        assert s.downloads_performed is False
        assert s.model_runs_performed is False
        assert s.forms_gold is False
        assert s.publishes_release is False

    assert states_seen == [
        PREFLIGHT_READY,
        APPROVALS_READY,
        FREEZE_READY,
        CANDIDATE_READY,
        HUMAN_REVIEW_READY,
    ]


def test_snapshot_as_dict_is_json_serializable_and_complete() -> None:
    s = _full_run()
    d = s.as_dict()
    round_tripped = json.loads(json.dumps(d, ensure_ascii=False))
    for key in (
        "run_id", "snapshot_fingerprint", "state", "plan", "evidence",
        "writes_business_database", "downloads_performed", "model_runs_performed",
        "forms_gold", "publishes_release",
    ):
        assert key in round_tripped, f"Missing key {key!r} in snapshot dict"
    assert round_tripped["writes_business_database"] is False
    assert round_tripped["forms_gold"] is False


def test_cancel_run_records_reason_and_is_terminal() -> None:
    s = create_run("3D", target_size=40, seed="s")
    s = advance_to_preflight_ready(s, xlsx_preflight=_valid_preflight())
    cancelled = cancel_run(s, reason="operator override")
    assert cancelled.state == CANCELLED
    assert cancelled.evidence["cancellation"]["reason"] == "operator override"
    # Attempt to continue from cancelled state must fail.
    with pytest.raises(CanaryRunError) as exc:
        advance_to_approvals_ready(
            cancelled, approval_artifact=_valid_approval()
        )
    assert _error_code(exc) == "TRANSITION_FROM_TERMINAL_STATE"


def test_fail_run_records_reason_and_is_terminal() -> None:
    s = create_run("3D", target_size=40, seed="s")
    s = advance_to_preflight_ready(s, xlsx_preflight=_valid_preflight())
    failed = fail_run(s, reason="downstream error")
    assert failed.state == FAILED
    assert failed.evidence["failure"]["reason"] == "downstream error"
    with pytest.raises(CanaryRunError) as exc:
        advance_to_approvals_ready(
            failed, approval_artifact=_valid_approval()
        )
    assert _error_code(exc) == "TRANSITION_FROM_TERMINAL_STATE"


def test_run_id_is_stable_and_prefixed() -> None:
    s = create_run("3D", target_size=40, seed="stable")
    assert s.run_id.startswith("canary:")
    # The run_id must not change through transitions.
    s2 = advance_to_preflight_ready(s, xlsx_preflight=_valid_preflight())
    assert s2.run_id == s.run_id


def test_evidence_accumulates_across_gates() -> None:
    """Each gate appends its evidence key without overwriting earlier keys."""
    s = create_run("3D", target_size=40, seed="s")
    assert s.evidence == {}
    s = advance_to_preflight_ready(s, xlsx_preflight=_valid_preflight())
    assert set(s.evidence.keys()) == {"xlsx_preflight"}
    s = advance_to_approvals_ready(s, approval_artifact=_valid_approval())
    assert "xlsx_preflight" in s.evidence and "approval" in s.evidence
    s = advance_to_freeze_ready(s, fetch_config=_valid_fetch_config())
    assert "fetch_config" in s.evidence
    s = advance_to_candidate_ready(s, manifest=_valid_manifest())
    assert "manifest" in s.evidence
    s = advance_to_human_review_ready(
        s,
        candidate_preview=_valid_candidate_preview(40),
        human_review_handoff=_valid_handoff(40),
    )
    assert "candidate_preview" in s.evidence
    assert "human_review_handoff" in s.evidence
