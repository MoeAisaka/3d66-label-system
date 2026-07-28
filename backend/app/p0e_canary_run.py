"""P0-E E2: deterministic canary run-plan and state-machine orchestrator.

This module is a pure orchestration layer over the E0/E1 primitives.  It
contains NO filesystem access, NO network calls, NO database writes, NO model
invocations, and NO Gold formation.  Every transition is a deterministic pure
function over caller-supplied evidence dicts.

State machine (monotonic, no skipping gates):
    draft
    → preflight_ready
    → approvals_ready
    → freeze_ready
    → candidate_ready
    → human_review_ready   (terminal success)
    → failed               (terminal, any state)
    → cancelled            (terminal, any state)

Terminal states are irreversible within E2; no resume is offered.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from typing import Any
from urllib.parse import urlsplit

from .p0e_candidate_package import CANDIDATE_PACKAGE_VERSION
from .p0e_image_freeze import MANIFEST_VERSION
from .p0e_safe_import import PREFLIGHT_SCHEMA_VERSION


# ── versioning ────────────────────────────────────────────────────────────────

RUN_PLAN_VERSION = "p0e-canary-run-v1"

_RECOGNIZED_PREFLIGHT_SCHEMAS: frozenset[str] = frozenset(
    {PREFLIGHT_SCHEMA_VERSION}
)
_RECOGNIZED_MANIFEST_VERSIONS: frozenset[str] = frozenset({MANIFEST_VERSION})
_RECOGNIZED_CANDIDATE_SCHEMAS: frozenset[str] = frozenset(
    {CANDIDATE_PACKAGE_VERSION}
)

# ── state constants ───────────────────────────────────────────────────────────

DRAFT = "draft"
PREFLIGHT_READY = "preflight_ready"
APPROVALS_READY = "approvals_ready"
FREEZE_READY = "freeze_ready"
CANDIDATE_READY = "candidate_ready"
HUMAN_REVIEW_READY = "human_review_ready"
FAILED = "failed"
CANCELLED = "cancelled"

# States from which further forward transitions are impossible in E2.
_TERMINAL_STATES: frozenset[str] = frozenset(
    {HUMAN_REVIEW_READY, FAILED, CANCELLED}
)

# The strict linear gate order.
_GATE_ORDER: tuple[str, ...] = (
    DRAFT,
    PREFLIGHT_READY,
    APPROVALS_READY,
    FREEZE_READY,
    CANDIDATE_READY,
    HUMAN_REVIEW_READY,
)
_GATE_INDEX: dict[str, int] = {s: i for i, s in enumerate(_GATE_ORDER)}

# ── error types ───────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class CanaryRunIssue:
    """Machine-readable rejection with stable fields."""

    code: str
    message: str
    current_state: str
    attempted_transition: str
    retryable: bool = False

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


class CanaryRunError(ValueError):
    """Raised when a transition is rejected; carries a CanaryRunIssue."""

    def __init__(self, issue: CanaryRunIssue) -> None:
        super().__init__(f"{issue.code}: {issue.message}")
        self.issue = issue

    def as_dict(self) -> dict[str, Any]:
        return {"error": self.issue.as_dict()}


# ── snapshot ──────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class RunSnapshot:
    """Immutable point-in-time state of a canary run.

    All invariants are explicit fields so callers never have to infer them.
    """

    run_id: str
    snapshot_fingerprint: str
    state: str
    plan: dict[str, Any]
    evidence: dict[str, Any]
    # Explicit invariants — always False for E2 orchestration.
    writes_business_database: bool
    downloads_performed: bool
    model_runs_performed: bool
    forms_gold: bool
    publishes_release: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "snapshot_fingerprint": self.snapshot_fingerprint,
            "state": self.state,
            "plan": self.plan,
            "evidence": self.evidence,
            "writes_business_database": self.writes_business_database,
            "downloads_performed": self.downloads_performed,
            "model_runs_performed": self.model_runs_performed,
            "forms_gold": self.forms_gold,
            "publishes_release": self.publishes_release,
        }


# ── internal helpers ──────────────────────────────────────────────────────────


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _fingerprint(value: Any) -> str:
    return hashlib.sha256(
        _canonical_json(value).encode("utf-8")
    ).hexdigest()


def _reject(
    code: str,
    message: str,
    *,
    current_state: str,
    attempted_transition: str,
    retryable: bool = False,
) -> None:
    raise CanaryRunError(
        CanaryRunIssue(
            code=code,
            message=message,
            current_state=current_state,
            attempted_transition=attempted_transition,
            retryable=retryable,
        )
    )


def _check_url_sanitized(
    url_string: str,
    *,
    current_state: str,
    attempted_transition: str,
) -> None:
    """Reject any URL that contains query, fragment, or userinfo."""
    try:
        parts = urlsplit(url_string)
    except ValueError:
        return  # not a parseable URL, not our concern here
    if parts.scheme.casefold() not in {"http", "https"}:
        return  # not an HTTP URL, skip
    if parts.username is not None or parts.password is not None:
        _reject(
            "EVIDENCE_URL_CONTAINS_USERINFO",
            "证据中包含带有 userinfo 的 URL，已拒绝以防凭据泄漏。",
            current_state=current_state,
            attempted_transition=attempted_transition,
        )
    if parts.query:
        _reject(
            "EVIDENCE_URL_CONTAINS_QUERY",
            "证据中包含带有 query 参数的 URL，已拒绝以防令牌泄漏。",
            current_state=current_state,
            attempted_transition=attempted_transition,
        )
    if parts.fragment:
        _reject(
            "EVIDENCE_URL_CONTAINS_FRAGMENT",
            "证据中包含带有 fragment 的 URL，已拒绝。",
            current_state=current_state,
            attempted_transition=attempted_transition,
        )


def _scan_for_unsafe_urls(
    node: Any,
    *,
    current_state: str,
    attempted_transition: str,
) -> None:
    """Recursively scan evidence for unsafe URL strings."""
    if isinstance(node, str):
        if node.startswith(("http://", "https://")):
            _check_url_sanitized(
                node,
                current_state=current_state,
                attempted_transition=attempted_transition,
            )
    elif isinstance(node, dict):
        for value in node.values():
            _scan_for_unsafe_urls(
                value,
                current_state=current_state,
                attempted_transition=attempted_transition,
            )
    elif isinstance(node, (list, tuple)):
        for item in node:
            _scan_for_unsafe_urls(
                item,
                current_state=current_state,
                attempted_transition=attempted_transition,
            )


def _assert_state(
    snapshot: RunSnapshot,
    expected: str,
    attempted_transition: str,
) -> None:
    """Guard: snapshot must be in the expected state."""
    if snapshot.state in _TERMINAL_STATES:
        _reject(
            "TRANSITION_FROM_TERMINAL_STATE",
            f"运行已处于终止状态 {snapshot.state!r}，E2 不支持从终止状态恢复。",
            current_state=snapshot.state,
            attempted_transition=attempted_transition,
        )
    if snapshot.state != expected:
        # Check if this looks like a backward or skipped-gate attempt.
        current_idx = _GATE_INDEX.get(snapshot.state, -1)
        expected_idx = _GATE_INDEX.get(expected, -1)
        if current_idx > expected_idx >= 0:
            code = "TRANSITION_BACKWARD_NOT_ALLOWED"
            msg = f"状态机不允许回退：当前 {snapshot.state!r}，请求 {expected!r}。"
        else:
            code = "TRANSITION_GATE_SKIPPED"
            msg = (
                f"状态机不允许跳跃门控：当前 {snapshot.state!r}，"
                f"必须先到达 {expected!r} 才能执行此转换。"
            )
        _reject(
            code,
            msg,
            current_state=snapshot.state,
            attempted_transition=attempted_transition,
        )


def _normalize_host_for_allowlist(
    host: str,
    *,
    current_state: str,
    attempted_transition: str,
) -> str:
    candidate = host.strip().rstrip(".")
    if not candidate:
        _reject(
            "EVIDENCE_HOST_ALLOWLIST_EMPTY_ENTRY",
            "允许主机名列表包含空项。",
            current_state=current_state,
            attempted_transition=attempted_transition,
        )
    try:
        return candidate.encode("idna").decode("ascii").casefold()
    except UnicodeError:
        _reject(
            "EVIDENCE_HOST_ALLOWLIST_INVALID",
            f"允许主机名列表包含无效域名：{candidate[:80]!r}。",
            current_state=current_state,
            attempted_transition=attempted_transition,
        )
    raise AssertionError("unreachable")


def _make_snapshot(
    *,
    run_id: str,
    state: str,
    plan: dict[str, Any],
    evidence: dict[str, Any],
) -> RunSnapshot:
    fingerprint = _fingerprint({"plan": plan, "evidence": evidence})
    return RunSnapshot(
        run_id=run_id,
        snapshot_fingerprint=fingerprint,
        state=state,
        plan=plan,
        evidence=evidence,
        writes_business_database=False,
        downloads_performed=False,
        model_runs_performed=False,
        forms_gold=False,
        publishes_release=False,
    )


# ── public API: plan creation ─────────────────────────────────────────────────


def create_run(
    domain: str,
    *,
    target_size: int,
    seed: str,
) -> RunSnapshot:
    """Create a new run in the DRAFT state; no evidence consumed yet.

    Parameters are validated immediately so callers discover invalid plans
    before advancing any gates.
    """
    attempted = "create_run"
    current = DRAFT

    if str(domain).casefold() != "3d":
        _reject(
            "PLAN_DOMAIN_NOT_3D",
            f"P0-E E2 运行计划只允许 3D 域；收到 {domain!r}。",
            current_state=current,
            attempted_transition=attempted,
        )
    if not (30 <= target_size <= 50):
        _reject(
            "PLAN_TARGET_SIZE_INVALID",
            f"目标数量必须在 30 到 50 之间；收到 {target_size}。",
            current_state=current,
            attempted_transition=attempted,
        )
    if not str(seed).strip():
        _reject(
            "PLAN_SEED_REQUIRED",
            "运行计划必须提供非空 seed。",
            current_state=current,
            attempted_transition=attempted,
        )

    plan: dict[str, Any] = {
        "plan_version": RUN_PLAN_VERSION,
        "domain": "3D",
        "target_size": target_size,
        "seed": str(seed).strip(),
    }
    run_id = "canary:" + _fingerprint(plan)
    return _make_snapshot(
        run_id=run_id,
        state=DRAFT,
        plan=plan,
        evidence={},
    )


# ── public API: gate transitions ──────────────────────────────────────────────


def advance_to_preflight_ready(
    snapshot: RunSnapshot,
    *,
    xlsx_preflight: dict[str, Any],
) -> RunSnapshot:
    """DRAFT → PREFLIGHT_READY.

    Validates the E1 XLSX preflight output: recognized schema version and a
    non-empty batch key are required.  Nothing about the spreadsheet content is
    re-parsed here; the orchestrator trusts but verifies the contract fields.
    """
    attempted = f"{DRAFT}→{PREFLIGHT_READY}"
    _assert_state(snapshot, DRAFT, attempted)

    schema = xlsx_preflight.get("schema_version", "")
    if schema not in _RECOGNIZED_PREFLIGHT_SCHEMAS:
        _reject(
            "PREFLIGHT_SCHEMA_UNRECOGNIZED",
            f"XLSX 预检 schema_version 不被识别：{schema!r}。",
            current_state=snapshot.state,
            attempted_transition=attempted,
        )
    batch_key = str(xlsx_preflight.get("batch_key") or "").strip()
    if not batch_key or not batch_key.startswith("p0e:"):
        _reject(
            "PREFLIGHT_BATCH_KEY_MISSING",
            "XLSX 预检缺少有效的 batch_key（必须以 'p0e:' 开头）。",
            current_state=snapshot.state,
            attempted_transition=attempted,
        )
    _scan_for_unsafe_urls(
        xlsx_preflight,
        current_state=snapshot.state,
        attempted_transition=attempted,
    )

    evidence = dict(snapshot.evidence)
    evidence["xlsx_preflight"] = {
        "schema_version": schema,
        "batch_key": batch_key,
    }
    return _make_snapshot(
        run_id=snapshot.run_id,
        state=PREFLIGHT_READY,
        plan=snapshot.plan,
        evidence=evidence,
    )


def advance_to_approvals_ready(
    snapshot: RunSnapshot,
    *,
    approval_artifact: dict[str, Any],
) -> RunSnapshot:
    """PREFLIGHT_READY → APPROVALS_READY.

    Requires an explicit, human-signed approval artifact.  Mappings are never
    inferred; any applied_mappings entry must have been explicitly confirmed by
    a human.  An artifact that lacks human_approved=True, or that has no
    approved_by identity, or whose batch_key does not match the preflight is
    rejected unconditionally.
    """
    attempted = f"{PREFLIGHT_READY}→{APPROVALS_READY}"
    _assert_state(snapshot, PREFLIGHT_READY, attempted)

    if approval_artifact.get("human_approved") is not True:
        _reject(
            "APPROVAL_HUMAN_APPROVED_REQUIRED",
            "审批件缺少显式的 human_approved=True 标记；映射不得静默应用或推断。",
            current_state=snapshot.state,
            attempted_transition=attempted,
        )
    approved_by = str(approval_artifact.get("approved_by") or "").strip()
    if not approved_by:
        _reject(
            "APPROVAL_APPROVED_BY_REQUIRED",
            "审批件缺少 approved_by 字段（审批人身份标识）。",
            current_state=snapshot.state,
            attempted_transition=attempted,
        )
    preflight_batch_key = snapshot.evidence.get("xlsx_preflight", {}).get(
        "batch_key", ""
    )
    artifact_batch_key = str(approval_artifact.get("batch_key") or "").strip()
    if artifact_batch_key != preflight_batch_key:
        _reject(
            "APPROVAL_BATCH_KEY_MISMATCH",
            "审批件的 batch_key 与预检批次键不一致。",
            current_state=snapshot.state,
            attempted_transition=attempted,
        )
    applied_mappings = approval_artifact.get("applied_mappings")
    if applied_mappings is not None and not isinstance(applied_mappings, list):
        _reject(
            "APPROVAL_MAPPINGS_INVALID",
            "审批件的 applied_mappings 必须为列表（可为空）。",
            current_state=snapshot.state,
            attempted_transition=attempted,
        )

    evidence = dict(snapshot.evidence)
    evidence["approval"] = {
        "human_approved": True,
        "approved_by": approved_by,
        "batch_key": artifact_batch_key,
        "applied_mappings": list(applied_mappings) if applied_mappings else [],
    }
    return _make_snapshot(
        run_id=snapshot.run_id,
        state=APPROVALS_READY,
        plan=snapshot.plan,
        evidence=evidence,
    )


def advance_to_freeze_ready(
    snapshot: RunSnapshot,
    *,
    fetch_config: dict[str, Any],
) -> RunSnapshot:
    """APPROVALS_READY → FREEZE_READY.

    Requires a non-empty explicit allowed-host set (normalized) and a pinned
    HTTPS transport attestation.  Generic HTTP is insufficient: the caller must
    explicitly attest that IP-pinned HTTPS is available (pinned_https_attested
    must be exactly True, not just truthy).
    """
    attempted = f"{APPROVALS_READY}→{FREEZE_READY}"
    _assert_state(snapshot, APPROVALS_READY, attempted)

    raw_hosts = fetch_config.get("allowed_hosts")
    if not isinstance(raw_hosts, (list, tuple, set, frozenset)) or not raw_hosts:
        _reject(
            "FETCH_HOST_ALLOWLIST_EMPTY",
            "获取配置必须提供至少一个显式允许主机名。",
            current_state=snapshot.state,
            attempted_transition=attempted,
        )
    normalized_hosts: list[str] = []
    for host in raw_hosts:
        normalized_hosts.append(
            _normalize_host_for_allowlist(
                str(host),
                current_state=snapshot.state,
                attempted_transition=attempted,
            )
        )

    if fetch_config.get("pinned_https_attested") is not True:
        _reject(
            "FETCH_PINNED_HTTPS_NOT_ATTESTED",
            "获取配置未显式证明固定 IP HTTPS 传输可用（pinned_https_attested 必须为 True）。",
            current_state=snapshot.state,
            attempted_transition=attempted,
        )

    evidence = dict(snapshot.evidence)
    evidence["fetch_config"] = {
        "allowed_hosts": sorted(set(normalized_hosts)),
        "pinned_https_attested": True,
    }
    return _make_snapshot(
        run_id=snapshot.run_id,
        state=FREEZE_READY,
        plan=snapshot.plan,
        evidence=evidence,
    )


def advance_to_candidate_ready(
    snapshot: RunSnapshot,
    *,
    manifest: dict[str, Any],
) -> RunSnapshot:
    """FREEZE_READY → CANDIDATE_READY.

    Validates the E1 frozen manifest: recognized version, complete=True,
    expected_source_count == frozen_source_count, no errors, non-empty asset
    set.  Scans manifest evidence for unsafe URLs.
    """
    attempted = f"{FREEZE_READY}→{CANDIDATE_READY}"
    _assert_state(snapshot, FREEZE_READY, attempted)

    mv = manifest.get("manifest_version", "")
    if mv not in _RECOGNIZED_MANIFEST_VERSIONS:
        _reject(
            "MANIFEST_VERSION_UNRECOGNIZED",
            f"manifest_version 不被识别：{mv!r}。",
            current_state=snapshot.state,
            attempted_transition=attempted,
        )
    if manifest.get("complete") is not True:
        _reject(
            "MANIFEST_NOT_COMPLETE",
            "manifest 未标记为 complete；不完整或失败的清单不得推进冻结门控。",
            current_state=snapshot.state,
            attempted_transition=attempted,
        )
    expected = manifest.get("expected_source_count")
    frozen = manifest.get("frozen_source_count")
    if not isinstance(expected, int) or not isinstance(frozen, int):
        _reject(
            "MANIFEST_COUNT_INVALID",
            "manifest 缺少有效的 expected_source_count 或 frozen_source_count。",
            current_state=snapshot.state,
            attempted_transition=attempted,
        )
    if expected != frozen:
        _reject(
            "MANIFEST_COUNT_MISMATCH",
            f"manifest expected_source_count ({expected}) 与 frozen_source_count ({frozen}) 不一致。",
            current_state=snapshot.state,
            attempted_transition=attempted,
        )
    if frozen < 1:
        _reject(
            "MANIFEST_EMPTY",
            "manifest 没有冻结任何来源资产。",
            current_state=snapshot.state,
            attempted_transition=attempted,
        )
    errors = manifest.get("errors") or []
    if errors:
        _reject(
            "MANIFEST_HAS_ERRORS",
            f"manifest 包含 {len(errors)} 个错误项，不得推进。",
            current_state=snapshot.state,
            attempted_transition=attempted,
        )
    _scan_for_unsafe_urls(
        manifest,
        current_state=snapshot.state,
        attempted_transition=attempted,
    )

    evidence = dict(snapshot.evidence)
    evidence["manifest"] = {
        "manifest_version": mv,
        "complete": True,
        "expected_source_count": expected,
        "frozen_source_count": frozen,
    }
    return _make_snapshot(
        run_id=snapshot.run_id,
        state=CANDIDATE_READY,
        plan=snapshot.plan,
        evidence=evidence,
    )


def advance_to_human_review_ready(
    snapshot: RunSnapshot,
    *,
    candidate_preview: dict[str, Any],
    human_review_handoff: dict[str, Any],
) -> RunSnapshot:
    """CANDIDATE_READY → HUMAN_REVIEW_READY.

    Validates the E1 candidate preview and an explicit human-review handoff
    artifact.  The preview must be complete, sized exactly to plan.target_size,
    and must assert forms_gold=False, downloads_performed=False, and
    model_runs_performed=False.  The handoff must explicitly record that every
    selected item requires review and that no truth or Gold status is granted.
    """
    attempted = f"{CANDIDATE_READY}→{HUMAN_REVIEW_READY}"
    _assert_state(snapshot, CANDIDATE_READY, attempted)

    cv = candidate_preview.get("schema_version", "")
    if cv not in _RECOGNIZED_CANDIDATE_SCHEMAS:
        _reject(
            "CANDIDATE_SCHEMA_UNRECOGNIZED",
            f"候选包 schema_version 不被识别：{cv!r}。",
            current_state=snapshot.state,
            attempted_transition=attempted,
        )
    if candidate_preview.get("complete_for_requested_preview") is not True:
        _reject(
            "CANDIDATE_PREVIEW_INCOMPLETE",
            "候选包未完成所请求的预览数量，不得推进人工审核门控。",
            current_state=snapshot.state,
            attempted_transition=attempted,
        )
    target_size: int = snapshot.plan["target_size"]
    selected_count = candidate_preview.get("selected_count")
    if selected_count != target_size:
        _reject(
            "CANDIDATE_PREVIEW_SIZE_MISMATCH",
            (
                f"候选包 selected_count ({selected_count}) 与运行计划 "
                f"target_size ({target_size}) 不一致。"
            ),
            current_state=snapshot.state,
            attempted_transition=attempted,
        )
    if candidate_preview.get("forms_gold") is not False:
        _reject(
            "CANDIDATE_PREVIEW_CLAIMS_GOLD",
            "候选包 forms_gold 必须为 False；候选预览不得声称已形成 Gold。",
            current_state=snapshot.state,
            attempted_transition=attempted,
        )
    if candidate_preview.get("downloads_performed") is not False:
        _reject(
            "CANDIDATE_PREVIEW_CLAIMS_DOWNLOADS",
            "候选包 downloads_performed 必须为 False；E2 编排本身不执行下载。",
            current_state=snapshot.state,
            attempted_transition=attempted,
        )
    if candidate_preview.get("model_runs_performed") is not False:
        _reject(
            "CANDIDATE_PREVIEW_CLAIMS_MODEL_RUNS",
            "候选包 model_runs_performed 必须为 False；E2 编排本身不调用模型。",
            current_state=snapshot.state,
            attempted_transition=attempted,
        )
    _scan_for_unsafe_urls(
        candidate_preview,
        current_state=snapshot.state,
        attempted_transition=attempted,
    )

    if human_review_handoff.get("all_items_require_review") is not True:
        _reject(
            "HANDOFF_ALL_ITEMS_REQUIRE_REVIEW",
            "人工审核交接件必须显式声明 all_items_require_review=True。",
            current_state=snapshot.state,
            attempted_transition=attempted,
        )
    if human_review_handoff.get("no_truth_or_gold_granted") is not True:
        _reject(
            "HANDOFF_NO_TRUTH_OR_GOLD_GRANTED",
            "人工审核交接件必须显式声明 no_truth_or_gold_granted=True。",
            current_state=snapshot.state,
            attempted_transition=attempted,
        )
    handoff_item_count = human_review_handoff.get("item_count")
    if handoff_item_count != selected_count:
        _reject(
            "HANDOFF_ITEM_COUNT_MISMATCH",
            (
                f"人工审核交接件 item_count ({handoff_item_count}) 与候选包 "
                f"selected_count ({selected_count}) 不一致。"
            ),
            current_state=snapshot.state,
            attempted_transition=attempted,
        )

    evidence = dict(snapshot.evidence)
    evidence["candidate_preview"] = {
        "schema_version": cv,
        "complete_for_requested_preview": True,
        "selected_count": selected_count,
        "forms_gold": False,
        "downloads_performed": False,
        "model_runs_performed": False,
    }
    evidence["human_review_handoff"] = {
        "all_items_require_review": True,
        "no_truth_or_gold_granted": True,
        "item_count": selected_count,
    }
    return _make_snapshot(
        run_id=snapshot.run_id,
        state=HUMAN_REVIEW_READY,
        plan=snapshot.plan,
        evidence=evidence,
    )


# ── terminal transitions ───────────────────────────────────────────────────────


def cancel_run(
    snapshot: RunSnapshot,
    *,
    reason: str,
) -> RunSnapshot:
    """Move any non-terminal run to CANCELLED (irreversible in E2)."""
    if snapshot.state in _TERMINAL_STATES:
        _reject(
            "TRANSITION_FROM_TERMINAL_STATE",
            f"运行已处于终止状态 {snapshot.state!r}，无法取消。",
            current_state=snapshot.state,
            attempted_transition="cancel_run",
        )
    evidence = dict(snapshot.evidence)
    evidence["cancellation"] = {"reason": str(reason).strip() or "unspecified"}
    return _make_snapshot(
        run_id=snapshot.run_id,
        state=CANCELLED,
        plan=snapshot.plan,
        evidence=evidence,
    )


def fail_run(
    snapshot: RunSnapshot,
    *,
    reason: str,
) -> RunSnapshot:
    """Move any non-terminal run to FAILED (irreversible in E2)."""
    if snapshot.state in _TERMINAL_STATES:
        _reject(
            "TRANSITION_FROM_TERMINAL_STATE",
            f"运行已处于终止状态 {snapshot.state!r}，无法标记为失败。",
            current_state=snapshot.state,
            attempted_transition="fail_run",
        )
    evidence = dict(snapshot.evidence)
    evidence["failure"] = {"reason": str(reason).strip() or "unspecified"}
    return _make_snapshot(
        run_id=snapshot.run_id,
        state=FAILED,
        plan=snapshot.plan,
        evidence=evidence,
    )
