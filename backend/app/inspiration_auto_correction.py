"""Golden-set calibration and auditable automatic correction for inspiration v3.

The module deliberately separates truth ingestion from prediction correction:
human filename ratings are frozen once in ``BaselineSetItem`` snapshots, while
automatic decisions only consume a calibration table learned from a stable
training partition.  Per-item expected truth is never passed to the decision
function.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import asdict, dataclass
from typing import Any, Iterable, Mapping

from sqlalchemy import func, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from .audit import append_audit_event
from .baseline_regression import (
    LEVELS,
    baseline_set_fingerprint,
    canonical_json,
    compute_level_metrics,
)
from .models import (
    Asset,
    BaselineRegressionItem,
    BaselineRegressionRun,
    BaselineSet,
    BaselineSetItem,
    OptimizationCaseQueue,
)
from .node_correction_api import CorrectNodeRequest, apply_node_correction


GOLDEN_SET_NAME = "灵感图人工评级黄金集-20260724-v2"
TRUTH_SOURCE = "灵感图人工评级集-20260724"
AUTO_CORRECTOR = "auto-corrector-v1"
AUTO_CORRECTOR_POLICY = "level-confusion-calibration-v1"
RATING_TO_LEVEL = {
    "好": "L1",
    "中等": "L2",
    "中差": "L3",
    "极差": "L4",
    "过滤": "L5",
}
EXPECTED_RATING_DISTRIBUTION = {
    "好": 188,
    "中等": 622,
    "中差": 811,
    "极差": 237,
    "过滤": 427,
}
_RATING_PATTERN = re.compile(r"(?:^|/|_)(好|中等|中差|极差|过滤)_")


@dataclass(frozen=True)
class AutoCorrectionPolicy:
    confidence_threshold: float = 0.85
    minimum_support: int = 30
    coverage_rate: float = 0.10
    calibration_fraction: float = 0.70
    maximum_level_shift: int = 1
    version: str = AUTO_CORRECTOR_POLICY

    def validate(self) -> None:
        for name, value in (
            ("confidence_threshold", self.confidence_threshold),
            ("coverage_rate", self.coverage_rate),
            ("calibration_fraction", self.calibration_fraction),
        ):
            if not 0.0 <= value <= 1.0:
                raise ValueError(f"{name} 必须在 0 和 1 之间")
        if self.minimum_support < 1:
            raise ValueError("minimum_support 必须至少为 1")
        if self.maximum_level_shift < 1 or self.maximum_level_shift > 4:
            raise ValueError("maximum_level_shift 必须在 1 和 4 之间")


def rating_from_original_name(original_name: str) -> str | None:
    match = _RATING_PATTERN.search(original_name or "")
    return match.group(1) if match else None


def _stable_fraction(namespace: str, value: int) -> float:
    digest = hashlib.sha256(f"{namespace}:{value}".encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big") / float(2**64)


def is_calibration_asset(asset_id: int, fraction: float) -> bool:
    return _stable_fraction("inspiration-calibration-v1", asset_id) < fraction


def ensure_inspiration_golden_set(
    db: Session,
    *,
    name: str = GOLDEN_SET_NAME,
    truth_source: str = TRUTH_SOURCE,
    created_by: str = AUTO_CORRECTOR,
    expected_distribution: Mapping[str, int] | None = EXPECTED_RATING_DISTRIBUTION,
) -> tuple[BaselineSet, dict[str, Any]]:
    """Create or validate the immutable inspiration golden baseline set.

    Cross-category asset references are intentional: the original asset row is
    left untouched and its source category is frozen in the item snapshot.  A
    baseline run later creates ``EvaluationJob.category_key=inspiration_image``.
    """

    # Asset deletion is a history-preserving soft delete (binary_retained=True).
    # The human corpus is frozen by asset id and must not silently shrink when
    # an operator hides an already-rated row from the normal asset list.
    assets = db.scalars(select(Asset).order_by(Asset.id.asc())).all()
    candidates: list[tuple[Asset, str, str]] = []
    candidate_distribution = {rating: 0 for rating in RATING_TO_LEVEL}
    for asset in assets:
        rating = rating_from_original_name(asset.original_name)
        if rating is None:
            continue
        level = RATING_TO_LEVEL[rating]
        candidates.append((asset, rating, level))
        candidate_distribution[rating] += 1
    if not candidates:
        raise ValueError("未找到文件名含人工评级前缀的灵感图资产")
    excluded: list[tuple[Asset, str, str]] = []
    if expected_distribution is None:
        selected = candidates
    else:
        selected = []
        remaining = dict(expected_distribution)
        for candidate in candidates:
            rating = candidate[1]
            if remaining.get(rating, 0) > 0:
                selected.append(candidate)
                remaining[rating] -= 1
            else:
                excluded.append(candidate)
        if any(remaining.values()):
            raise ValueError(
                "人工评级候选不足以满足冻结口径："
                f"expected={dict(expected_distribution)}, actual={candidate_distribution}"
            )
    distribution = {rating: 0 for rating in RATING_TO_LEVEL}
    for _asset, rating, _level in selected:
        distribution[rating] += 1

    manifest = [
        {
            "asset_id": asset.id,
            "asset_sha256": asset.sha256,
            "expected_level": level,
        }
        for asset, _rating, level in selected
    ]
    fingerprint = baseline_set_fingerprint(
        manifest, category_key="inspiration_image"
    )
    existing = db.scalar(select(BaselineSet).where(BaselineSet.name == name))
    if existing is not None:
        if existing.category_key != "inspiration_image":
            raise ValueError("同名黄金集的 category_key 不是 inspiration_image")
        if existing.fingerprint != fingerprint:
            raise ValueError("同名黄金集已存在，但人工真值指纹不一致；禁止原地改写")
        existing_count = db.scalar(
            select(func.count(BaselineSetItem.id)).where(
                BaselineSetItem.baseline_set_id == existing.id
            )
        )
        if existing_count != len(selected):
            raise ValueError("同名黄金集条目数与当前人工真值不一致")
        return existing, {
            "created": False,
            "idempotent": True,
            "item_count": len(selected),
            "distribution": distribution,
            "fingerprint": fingerprint,
            "candidate_distribution": candidate_distribution,
            "excluded_candidate_ids": [asset.id for asset, _rating, _level in excluded],
        }

    baseline_set = BaselineSet(
        category_key="inspiration_image",
        name=name,
        description=(
            "图片级人工真值；仅由文件名人工评级前缀生成。"
            "原始 asset.category_key 保持不变。"
        ),
        default_expected_level="L3",
        fingerprint=fingerprint,
        created_by=created_by,
    )
    db.add(baseline_set)
    db.flush()
    for asset, rating, level in selected:
        snapshot = {
            "schema_version": "baseline-asset-v1",
            "asset_id": asset.id,
            "category_key": "inspiration_image",
            "asset_source_category_key": asset.category_key,
            "name": asset.original_name,
            "sha256": asset.sha256,
            "mime_type": asset.mime_type,
            "size_bytes": asset.size_bytes,
            "width": asset.width,
            "height": asset.height,
            "source_package_id": None,
            "expected_level_source": "human_filename_rating",
            "human_rating": rating,
            "truth_updated_by": truth_source,
            "truth_source": truth_source,
            "created_at": asset.created_at.isoformat(),
        }
        db.add(
            BaselineSetItem(
                baseline_set_id=baseline_set.id,
                asset_id=asset.id,
                source_package_id=None,
                expected_level=level,
                asset_snapshot_json=canonical_json(snapshot),
            )
        )
        if len(db.new) >= 500:
            db.flush()
    db.flush()
    persisted_count = db.scalar(
        select(func.count(BaselineSetItem.id)).where(
            BaselineSetItem.baseline_set_id == baseline_set.id
        )
    )
    if persisted_count != len(selected):
        raise ValueError(
            f"黄金集落库条目不完整：expected={len(selected)}, actual={persisted_count}"
        )
    append_audit_event(
        db,
        category="baseline_regression",
        action="inspiration_golden_set_created",
        subject_type="baseline_set",
        subject_id=baseline_set.id,
        actor=created_by,
        payload={
            "category_key": "inspiration_image",
            "item_count": len(selected),
            "distribution": distribution,
            "fingerprint": fingerprint,
            "truth_updated_by": truth_source,
            "asset_category_mutations": 0,
            "candidate_distribution": candidate_distribution,
            "excluded_candidate_ids": [asset.id for asset, _rating, _level in excluded],
        },
        event_key=f"inspiration-golden-set:{fingerprint}",
    )
    db.commit()
    db.refresh(baseline_set)
    return baseline_set, {
        "created": True,
        "idempotent": False,
        "item_count": len(selected),
        "distribution": distribution,
        "fingerprint": fingerprint,
        "candidate_distribution": candidate_distribution,
        "excluded_candidate_ids": [asset.id for asset, _rating, _level in excluded],
    }


def _wilson_lower_bound(successes: int, total: int, *, z: float = 1.96) -> float:
    if total <= 0:
        return 0.0
    proportion = successes / total
    denominator = 1.0 + z * z / total
    centre = proportion + z * z / (2.0 * total)
    margin = z * math.sqrt(
        (proportion * (1.0 - proportion) + z * z / (4.0 * total)) / total
    )
    return max(0.0, (centre - margin) / denominator)


def fit_level_calibration(
    rows: Iterable[Mapping[str, Any]],
    *,
    policy: AutoCorrectionPolicy,
) -> dict[str, Any]:
    """Fit predicted-level to human-level mapping on a stable train partition."""

    policy.validate()
    counts = {predicted: {expected: 0 for expected in LEVELS} for predicted in LEVELS}
    training_count = 0
    for row in rows:
        asset_id = int(row["asset_id"])
        expected = str(row.get("expected_level") or "")
        predicted = str(row.get("predicted_level") or "")
        if (
            expected not in LEVELS
            or predicted not in LEVELS
            or not is_calibration_asset(asset_id, policy.calibration_fraction)
        ):
            continue
        counts[predicted][expected] += 1
        training_count += 1

    mappings: dict[str, Any] = {}
    for predicted in LEVELS:
        distribution = counts[predicted]
        support = sum(distribution.values())
        target = min(
            LEVELS,
            key=lambda level: (
                -distribution[level],
                abs(LEVELS.index(level) - LEVELS.index(predicted)),
                LEVELS.index(level),
            ),
        )
        successes = distribution[target]
        mappings[predicted] = {
            "predicted_level": predicted,
            "target_level": target,
            "support": support,
            "successes": successes,
            "empirical_confidence": successes / support if support else 0.0,
            "confidence_lower_bound": _wilson_lower_bound(successes, support),
            "expected_distribution": distribution,
        }
    frozen = {
        "schema_version": "inspiration-level-calibration-v1",
        "policy": asdict(policy),
        "training_count": training_count,
        "mappings": mappings,
    }
    frozen["calibration_hash"] = hashlib.sha256(
        canonical_json(frozen).encode("utf-8")
    ).hexdigest()
    return frozen


def decide_level_correction(
    *,
    asset_id: int,
    predicted_level: str | None,
    calibration: Mapping[str, Any],
    policy: AutoCorrectionPolicy,
) -> dict[str, Any]:
    """Decide without access to the evaluated item's expected human level."""

    policy.validate()
    if predicted_level not in LEVELS:
        return {"action": "manual_review", "reason": "prediction_missing"}
    mapping = (calibration.get("mappings") or {}).get(predicted_level)
    if not isinstance(mapping, Mapping):
        return {"action": "manual_review", "reason": "mapping_missing"}
    target = str(mapping.get("target_level") or "")
    support = int(mapping.get("support") or 0)
    confidence = float(mapping.get("confidence_lower_bound") or 0.0)
    if target == predicted_level:
        return {
            "action": "keep",
            "reason": "calibrated_level_unchanged",
            "target_level": target,
            "confidence": confidence,
            "support": support,
        }
    shift = abs(LEVELS.index(target) - LEVELS.index(predicted_level))
    if (
        support < policy.minimum_support
        or confidence < policy.confidence_threshold
        or shift > policy.maximum_level_shift
    ):
        return {
            "action": "manual_review",
            "reason": "confidence_gate_not_met",
            "target_level": target,
            "confidence": confidence,
            "support": support,
            "shift": shift,
        }
    if _stable_fraction("inspiration-auto-coverage-v1", asset_id) >= policy.coverage_rate:
        return {
            "action": "holdback",
            "reason": "coverage_holdback",
            "target_level": target,
            "confidence": confidence,
            "support": support,
            "shift": shift,
        }
    return {
        "action": "auto_apply",
        "reason": "high_confidence_calibration",
        "target_level": target,
        "confidence": confidence,
        "support": support,
        "shift": shift,
    }


def _raw_rows(run: BaselineRegressionRun) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in run.items:
        snapshot = json.loads(item.result_snapshot_json or "{}")
        rows.append(
            {
                "item": item,
                "asset_id": item.asset_id,
                "expected_level": item.expected_level,
                "predicted_level": snapshot.get("predicted_level"),
                "status": item.status,
            }
        )
    return rows


def _enqueue_manual_review(
    db: Session,
    *,
    run: BaselineRegressionRun,
    item: BaselineRegressionItem,
    decision: Mapping[str, Any],
) -> bool:
    if item.evaluation_id is None:
        return False
    snapshot = json.loads(item.result_snapshot_json or "{}")
    prompt_version = (
        (snapshot.get("versions") or {}).get("prompt_b")
        or (snapshot.get("versions") or {}).get("prompt_a")
        or run.strategy_bundle.prompt_b_version
        or run.strategy_bundle.prompt_a_version
        or "unknown"
    )
    case = {
        "schema_version": "optimization-case-v1",
        "source": "auto_correction_low_confidence",
        "baseline_run_id": run.id,
        "baseline_item_id": item.id,
        "asset_id": item.asset_id,
        "evaluation_id": item.evaluation_id,
        "auto_correction": dict(decision),
    }
    result = db.execute(
        sqlite_insert(OptimizationCaseQueue)
        .values(
            category_key=run.category_key,
            idempotency_key=f"auto-corrector-review:{run.id}:{item.id}",
            evaluation_id=item.evaluation_id,
            final_review_id=None,
            source_type="baseline_regression",
            source_event_id=None,
            baseline_regression_item_id=item.id,
            prompt_version=str(prompt_version),
            severity="P2",
            case_json=canonical_json(case),
            status="pending",
        )
        .on_conflict_do_nothing()
    )
    return bool(result.rowcount)


def apply_auto_correction_to_run(
    db: Session,
    *,
    run: BaselineRegressionRun,
    policy: AutoCorrectionPolicy | None = None,
) -> dict[str, Any]:
    policy = policy or AutoCorrectionPolicy()
    if run.category_key != "inspiration_image":
        raise ValueError("自动纠偏器仅允许 inspiration_image 基线回归")
    if run.status not in {"completed", "partial_failed"}:
        raise ValueError("基线回归尚未完成")
    rows = _raw_rows(run)
    calibration = fit_level_calibration(rows, policy=policy)
    counts = {action: 0 for action in ("auto_apply", "manual_review", "holdback", "keep")}
    created_manual_cases = 0
    applied: list[dict[str, Any]] = []
    for row in rows:
        item = row["item"]
        decision = decide_level_correction(
            asset_id=item.asset_id,
            predicted_level=row["predicted_level"],
            calibration=calibration,
            policy=policy,
        )
        action = decision["action"]
        counts[action] += 1
        if action == "manual_review":
            created_manual_cases += int(
                _enqueue_manual_review(
                    db, run=run, item=item, decision=decision
                )
            )
            continue
        if action != "auto_apply" or item.evaluation is None:
            continue
        payload = CorrectNodeRequest(
            correction_key=(
                f"{AUTO_CORRECTOR}:{run.id}:{item.evaluation_id}:"
                f"{calibration['calibration_hash'][:16]}"
            ),
            node_type="final_level",
            node_path="final_level",
            old_value=row["predicted_level"],
            new_value=decision["target_level"],
            evidence=[],
            reason=(
                "人工黄金集校准："
                f"support={decision['support']}, "
                f"wilson_lower={decision['confidence']:.4f}"
            ),
        )
        apply_node_correction(
            db,
            result=item.evaluation,
            payload=payload,
            corrector=AUTO_CORRECTOR,
            corrector_confidence=decision["confidence"],
            corrector_policy=policy.version,
        )
        applied.append(
            {
                "baseline_item_id": item.id,
                "evaluation_id": item.evaluation_id,
                "asset_id": item.asset_id,
                "from": row["predicted_level"],
                "to": decision["target_level"],
                "confidence": decision["confidence"],
            }
        )
    append_audit_event(
        db,
        category="baseline_regression",
        action="auto_correction_applied",
        subject_type="baseline_regression_run",
        subject_id=run.id,
        actor=AUTO_CORRECTOR,
        payload={
            "policy": asdict(policy),
            "calibration_hash": calibration["calibration_hash"],
            "decision_counts": counts,
            "created_manual_cases": created_manual_cases,
            "applied_count": len(applied),
        },
        event_key=(
            f"auto-corrector:{run.id}:{calibration['calibration_hash']}:"
            f"{policy.coverage_rate}"
        ),
    )
    db.commit()
    return {
        "schema_version": "inspiration-auto-correction-run-v1",
        "run_id": run.id,
        "policy": asdict(policy),
        "calibration": calibration,
        "decision_counts": counts,
        "created_manual_cases": created_manual_cases,
        "applied": applied,
        "drift": build_drift_report(run, policy=policy),
    }


def build_drift_report(
    run: BaselineRegressionRun,
    *,
    policy: AutoCorrectionPolicy | None = None,
) -> dict[str, Any]:
    policy = policy or AutoCorrectionPolicy()
    rows = _raw_rows(run)
    before_rows: list[dict[str, Any]] = []
    after_rows: list[dict[str, Any]] = []
    holdout_before: list[dict[str, Any]] = []
    holdout_after: list[dict[str, Any]] = []
    fixed = 0
    introduced = 0
    for row in rows:
        item = row["item"]
        after_level = item.evaluation.level if item.evaluation is not None else None
        before = {
            "status": row["status"],
            "expected_level": row["expected_level"],
            "predicted_level": row["predicted_level"],
        }
        after = {**before, "predicted_level": after_level}
        before_rows.append(before)
        after_rows.append(after)
        if not is_calibration_asset(item.asset_id, policy.calibration_fraction):
            holdout_before.append(before)
            holdout_after.append(after)
        was_correct = row["predicted_level"] == row["expected_level"]
        is_correct = after_level == row["expected_level"]
        fixed += int(not was_correct and is_correct)
        introduced += int(was_correct and not is_correct)
    return {
        "schema_version": "inspiration-auto-correction-drift-v1",
        "run_id": run.id,
        "before": compute_level_metrics(before_rows),
        "after": compute_level_metrics(after_rows),
        "holdout_before": compute_level_metrics(holdout_before),
        "holdout_after": compute_level_metrics(holdout_after),
        "fixed_errors": fixed,
        "introduced_errors": introduced,
        "independent_holdout": True,
        "truth_source": TRUTH_SOURCE,
    }
