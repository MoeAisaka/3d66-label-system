"""Immutable revision storage for category-evaluation v3 mechanism artifacts."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .category_evaluation_contract import canonical_contract_hash
from .correction_contract import (
    ContractValidationError,
    assert_correction_contract_complete,
)
from .dimension_schema_registry import canonical_json
from .mechanism_profiles import (
    extract_profile_rule_mirror,
    profile_media_penalty_enabled,
    validate_mechanism_artifacts,
)
from .models import CategoryEvaluationV3Config, CategoryEvaluationV3Revision


@dataclass(frozen=True)
class RevisionArtifacts:
    display_name: str
    contract: dict[str, Any]
    classification_map: dict[str, Any]
    subcategory_dimensions: dict[str, Any]


class CategoryEvaluationV3RevisionError(ContractValidationError):
    def __init__(
        self,
        code: str,
        message: str,
        fields: list[str] | None = None,
    ) -> None:
        super().__init__(code, message, fields)


def _canonical_artifacts(
    db: Session,
    artifacts: RevisionArtifacts,
) -> tuple[str, str, str, str, str, bool]:
    profile_type = validate_mechanism_artifacts(
        artifacts.contract,
        artifacts.classification_map,
        artifacts.subcategory_dimensions,
        db=db,
    )
    contract_json = canonical_json(artifacts.contract)
    classification_map_json = canonical_json(artifacts.classification_map)
    subcategory_dimensions_json = canonical_json(
        artifacts.subcategory_dimensions
    )
    rule_mirror_json = canonical_json(
        extract_profile_rule_mirror(
            profile_type,
            artifacts.subcategory_dimensions,
            artifacts.contract,
        )
    )
    return (
        contract_json,
        classification_map_json,
        subcategory_dimensions_json,
        rule_mirror_json,
        canonical_contract_hash(artifacts.contract),
        profile_media_penalty_enabled(profile_type, artifacts.contract),
    )


def revision_bundle(revision: CategoryEvaluationV3Revision) -> dict[str, Any]:
    """Return a safe parsed bundle for later regression/package binding."""
    return {
        "category_key": revision.category_key,
        "revision": revision.revision,
        "contract_hash": revision.contract_hash,
        "contract": json.loads(revision.contract_json),
        "classification_map": json.loads(revision.classification_map_json),
        "subcategory_dimensions": json.loads(
            revision.subcategory_dimensions_json
        ),
        "dimension_deduction_rules": json.loads(
            revision.dimension_deduction_rules_json or "{}"
        ),
        "media_penalty_enabled": revision.media_penalty_enabled,
    }


def _next_revision(db: Session, category_key: str, projected_revision: int) -> int:
    maximum = db.scalar(
        select(func.max(CategoryEvaluationV3Revision.revision)).where(
            CategoryEvaluationV3Revision.category_key == category_key
        )
    )
    return max(maximum or 0, projected_revision) + 1


def _matches_runtime_projection(
    revision: CategoryEvaluationV3Revision,
    projected: CategoryEvaluationV3Config,
) -> bool:
    return (
        revision.category_key == projected.category_key
        and revision.display_name == projected.display_name
        and revision.revision == projected.revision
        and revision.status == projected.status
        and revision.contract_json == projected.contract_json
        and revision.classification_map_json == projected.classification_map_json
        and revision.subcategory_dimensions_json
        == projected.subcategory_dimensions_json
        and revision.dimension_deduction_rules_json
        == (projected.dimension_deduction_rules_json or "{}")
        and revision.media_penalty_enabled == projected.media_penalty_enabled
        and revision.contract_hash == projected.contract_hash
        and revision.created_by == projected.created_by
    )


def _revision_from_runtime_projection(
    projected: CategoryEvaluationV3Config,
    *,
    revision: int,
    parent_revision_id: int | None,
) -> CategoryEvaluationV3Revision:
    return CategoryEvaluationV3Revision(
        category_key=projected.category_key,
        display_name=projected.display_name,
        revision=revision,
        status=projected.status,
        parent_revision_id=parent_revision_id,
        contract_json=projected.contract_json,
        classification_map_json=projected.classification_map_json,
        subcategory_dimensions_json=projected.subcategory_dimensions_json,
        dimension_deduction_rules_json=(
            projected.dimension_deduction_rules_json or "{}"
        ),
        media_penalty_enabled=projected.media_penalty_enabled,
        contract_hash=projected.contract_hash,
        created_by=projected.created_by,
        created_at=projected.created_at,
        updated_at=projected.updated_at,
    )


def ensure_projected_revision(
    db: Session,
    projected: CategoryEvaluationV3Config,
) -> CategoryEvaluationV3Revision:
    """Create or attach the revision currently copied into a runtime row."""
    if projected.projected_revision_id is not None:
        existing = db.get(
            CategoryEvaluationV3Revision,
            projected.projected_revision_id,
        )
        if existing is None:
            raise RuntimeError("运行时合同 projected_revision_id 指向不存在的版本")
        if not _matches_runtime_projection(existing, projected):
            raise RuntimeError(
                "运行时合同 projected_revision_id 指向的冻结产物不一致，拒绝继续"
            )
        return existing

    existing = db.scalar(
        select(CategoryEvaluationV3Revision).where(
            CategoryEvaluationV3Revision.category_key == projected.category_key,
            CategoryEvaluationV3Revision.revision == projected.revision,
        )
    )
    if existing is not None and not _matches_runtime_projection(
        existing,
        projected,
    ):
        if existing.status == "active" and projected.status == "active":
            existing.status = "retired"
        next_revision = _next_revision(
            db,
            projected.category_key,
            projected.revision,
        )
        repaired = _revision_from_runtime_projection(
            projected,
            revision=next_revision,
            parent_revision_id=existing.id,
        )
        db.add(repaired)
        db.flush()
        projected.revision = next_revision
        projected.projected_revision_id = repaired.id
        db.flush()
        return repaired

    if existing is None:
        existing = _revision_from_runtime_projection(
            projected,
            revision=projected.revision,
            parent_revision_id=None,
        )
        db.add(existing)
        db.flush()
    projected.projected_revision_id = existing.id
    db.flush()
    return existing


def create_candidate_revision(
    db: Session,
    projected: CategoryEvaluationV3Config,
    *,
    parent_revision_id: int,
    artifacts: RevisionArtifacts,
    expected_projected_revision: int,
    expected_projected_hash: str,
    actor: str,
) -> tuple[CategoryEvaluationV3Revision, bool]:
    """Append one validated candidate without changing the runtime projection."""
    current = ensure_projected_revision(db, projected)
    if projected.revision != expected_projected_revision:
        raise CategoryEvaluationV3RevisionError(
            "projected_revision_conflict",
            "现役合同版本已变化，请刷新后重试",
        )
    if projected.contract_hash != expected_projected_hash:
        raise CategoryEvaluationV3RevisionError(
            "projected_contract_hash_conflict",
            "现役合同内容已变化，请刷新后重试",
        )

    parent = db.get(CategoryEvaluationV3Revision, parent_revision_id)
    if parent is None or parent.category_key != projected.category_key:
        raise CategoryEvaluationV3RevisionError(
            "parent_revision_conflict",
            "父版本不存在或不属于当前类目",
        )
    if parent.id != current.id:
        if parent.status != "candidate":
            raise CategoryEvaluationV3RevisionError(
                "parent_revision_conflict",
                "父版本必须是现役投影或现役投影的候选后代",
            )
        ancestor = parent
        seen: set[int] = set()
        while ancestor.id != current.id:
            if ancestor.id in seen or ancestor.parent_revision_id is None:
                raise CategoryEvaluationV3RevisionError(
                    "parent_revision_conflict",
                    "父版本不在当前现役投影的候选链上",
                )
            seen.add(ancestor.id)
            next_ancestor = db.get(
                CategoryEvaluationV3Revision,
                ancestor.parent_revision_id,
            )
            if (
                next_ancestor is None
                or next_ancestor.category_key != projected.category_key
            ):
                raise CategoryEvaluationV3RevisionError(
                    "parent_revision_conflict",
                    "父版本不在当前现役投影的候选链上",
                )
            ancestor = next_ancestor

    (
        contract_json,
        classification_map_json,
        subcategory_dimensions_json,
        rule_mirror_json,
        contract_hash,
        media_penalty_enabled,
    ) = _canonical_artifacts(db, artifacts)

    same_parent = db.scalars(
        select(CategoryEvaluationV3Revision)
        .where(
            CategoryEvaluationV3Revision.category_key == projected.category_key,
            CategoryEvaluationV3Revision.parent_revision_id == parent.id,
            CategoryEvaluationV3Revision.status == "candidate",
            CategoryEvaluationV3Revision.contract_hash == contract_hash,
        )
        .order_by(CategoryEvaluationV3Revision.id.asc())
    ).all()
    for existing in same_parent:
        artifact_match = (
            existing.display_name == artifacts.display_name
            and existing.contract_json == contract_json
            and existing.classification_map_json == classification_map_json
            and existing.subcategory_dimensions_json
            == subcategory_dimensions_json
            and existing.dimension_deduction_rules_json == rule_mirror_json
            and existing.media_penalty_enabled == media_penalty_enabled
        )
        if artifact_match:
            return existing, False
        raise CategoryEvaluationV3RevisionError(
            "candidate_revision_conflict",
            "相同父版本与合同哈希已存在内容不同的候选，请刷新后重试",
        )

    created = CategoryEvaluationV3Revision(
        category_key=projected.category_key,
        display_name=artifacts.display_name,
        revision=_next_revision(db, projected.category_key, projected.revision),
        status="candidate",
        parent_revision_id=parent.id,
        contract_json=contract_json,
        classification_map_json=classification_map_json,
        subcategory_dimensions_json=subcategory_dimensions_json,
        dimension_deduction_rules_json=rule_mirror_json,
        media_penalty_enabled=media_penalty_enabled,
        contract_hash=contract_hash,
        created_by=actor,
    )
    db.add(created)
    db.flush()
    return created, True


def sync_projected_revision(
    db: Session,
    projected: CategoryEvaluationV3Config,
    *,
    display_name: str,
    status: str,
    contract_json: str,
    classification_map_json: str,
    subcategory_dimensions_json: str,
    dimension_deduction_rules_json: str,
    media_penalty_enabled: bool,
    contract_hash: str,
    actor: str,
) -> CategoryEvaluationV3Revision:
    """Atomically append and point a known seed/runtime projection revision."""
    if projected.projected_revision_id is None:
        current = ensure_projected_revision(db, projected)
    else:
        current = db.get(
            CategoryEvaluationV3Revision,
            projected.projected_revision_id,
        )
        if current is None:
            raise RuntimeError("运行时合同 projected_revision_id 指向不存在的版本")
        if current.category_key != projected.category_key:
            raise RuntimeError(
                "运行时合同 projected_revision_id 指向其他类目的版本，拒绝继续"
            )
    unchanged = (
        projected.display_name == display_name
        and projected.status == status
        and projected.contract_json == contract_json
        and projected.classification_map_json == classification_map_json
        and projected.subcategory_dimensions_json == subcategory_dimensions_json
        and projected.dimension_deduction_rules_json
        == dimension_deduction_rules_json
        and projected.media_penalty_enabled == media_penalty_enabled
        and projected.contract_hash == contract_hash
        and projected.created_by == actor
        and _matches_runtime_projection(current, projected)
    )
    if unchanged:
        return current

    if current.status == "active" and status == "active":
        current.status = "retired"
    next_revision = _next_revision(db, projected.category_key, projected.revision)
    revision = CategoryEvaluationV3Revision(
        category_key=projected.category_key,
        display_name=display_name,
        revision=next_revision,
        status=status,
        parent_revision_id=current.id,
        contract_json=contract_json,
        classification_map_json=classification_map_json,
        subcategory_dimensions_json=subcategory_dimensions_json,
        dimension_deduction_rules_json=dimension_deduction_rules_json,
        media_penalty_enabled=media_penalty_enabled,
        contract_hash=contract_hash,
        created_by=actor,
    )
    db.add(revision)
    db.flush()

    projected.display_name = display_name
    projected.status = status
    projected.contract_json = contract_json
    projected.classification_map_json = classification_map_json
    projected.subcategory_dimensions_json = subcategory_dimensions_json
    projected.dimension_deduction_rules_json = dimension_deduction_rules_json
    projected.media_penalty_enabled = media_penalty_enabled
    projected.revision = next_revision
    projected.contract_hash = contract_hash
    projected.projected_revision_id = revision.id
    projected.created_by = actor
    db.flush()
    return revision


def activate_candidate_revision(
    db: Session,
    projected: CategoryEvaluationV3Config,
    candidate: CategoryEvaluationV3Revision,
    *,
    actor: str,
) -> CategoryEvaluationV3Revision:
    """Activate one validated direct child without creating a second revision."""
    current = ensure_projected_revision(db, projected)
    if candidate.category_key != projected.category_key:
        raise CategoryEvaluationV3RevisionError(
            "candidate_category_conflict",
            "候选 revision 不属于当前类目",
        )
    if candidate.status != "candidate":
        raise CategoryEvaluationV3RevisionError(
            "candidate_status_conflict",
            "只有候选状态的 revision 可以启用",
        )
    if candidate.parent_revision_id != current.id:
        raise CategoryEvaluationV3RevisionError(
            "projected_revision_conflict",
            "现役机制已变化，请重新执行纠偏分析",
        )
    artifacts = RevisionArtifacts(
        display_name=candidate.display_name,
        contract=json.loads(candidate.contract_json),
        classification_map=json.loads(candidate.classification_map_json),
        subcategory_dimensions=json.loads(
            candidate.subcategory_dimensions_json
        ),
    )
    (
        contract_json,
        classification_map_json,
        subcategory_dimensions_json,
        rule_mirror_json,
        contract_hash,
        media_penalty_enabled,
    ) = _canonical_artifacts(db, artifacts)
    if (
        candidate.contract_json != contract_json
        or candidate.classification_map_json != classification_map_json
        or candidate.subcategory_dimensions_json != subcategory_dimensions_json
        or candidate.dimension_deduction_rules_json != rule_mirror_json
        or candidate.contract_hash != contract_hash
        or candidate.media_penalty_enabled != media_penalty_enabled
    ):
        raise CategoryEvaluationV3RevisionError(
            "candidate_artifact_conflict",
            "候选 revision 的冻结产物校验失败",
        )

    correction_contract = artifacts.contract.get("correction_contract")
    if correction_contract is None and "nodes" in artifacts.contract:
        correction_contract = artifacts.contract
    if correction_contract is not None:
        try:
            assert_correction_contract_complete(correction_contract)
        except ContractValidationError as exc:
            raise CategoryEvaluationV3RevisionError(
                exc.code,
                str(exc),
                exc.fields,
            ) from exc

    current.status = "retired"
    candidate.status = "active"
    projected.display_name = candidate.display_name
    projected.status = "active"
    projected.contract_json = candidate.contract_json
    projected.classification_map_json = candidate.classification_map_json
    projected.subcategory_dimensions_json = (
        candidate.subcategory_dimensions_json
    )
    projected.dimension_deduction_rules_json = (
        candidate.dimension_deduction_rules_json
    )
    projected.media_penalty_enabled = candidate.media_penalty_enabled
    projected.revision = candidate.revision
    projected.contract_hash = candidate.contract_hash
    projected.projected_revision_id = candidate.id
    projected.created_by = candidate.created_by
    db.flush()
    return candidate
