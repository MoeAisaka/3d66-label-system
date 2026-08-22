"""Rebase a diverged candidate revision onto the current active revision.

A candidate may only be activated when it is a direct child of the active
revision.  Once someone publishes a sibling branch, every candidate hanging off
the old branch becomes unreleasable: replaying it as-is would silently drop
whatever the newly published revision introduced.

This module answers "what would that candidate look like if it had been built on
today's active revision" by three-way merging the candidate's own changes onto
the active artifacts, using their nearest common ancestor as the base.  It only
computes artifacts -- creating the new candidate stays with
``create_candidate_revision`` so parent-chain rules, canonicalisation and
idempotency keep their single owner.

Divergences the merge cannot decide are reported as conflicts rather than
guessed: a conflict means a human has to say which side wins.
"""

from __future__ import annotations

from typing import Any, Mapping

from sqlalchemy.orm import Session

from .models import CategoryEvaluationV3Revision

ARTIFACT_KEYS = ("contract", "classification_map", "subcategory_dimensions")

_MISSING = object()


class CandidateRebaseError(ValueError):
    """Raised when a rebase cannot be computed at all."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _ancestry(
    db: Session, revision: CategoryEvaluationV3Revision
) -> list[CategoryEvaluationV3Revision]:
    """Return revision -> root, stopping on cycles or foreign categories."""
    chain: list[CategoryEvaluationV3Revision] = []
    seen: set[int] = set()
    cursor: CategoryEvaluationV3Revision | None = revision
    while cursor is not None and cursor.id not in seen:
        seen.add(cursor.id)
        chain.append(cursor)
        if cursor.parent_revision_id is None:
            break
        parent = db.get(CategoryEvaluationV3Revision, cursor.parent_revision_id)
        if parent is None or parent.category_key != revision.category_key:
            break
        cursor = parent
    return chain


def nearest_common_ancestor(
    db: Session,
    candidate: CategoryEvaluationV3Revision,
    active: CategoryEvaluationV3Revision,
) -> CategoryEvaluationV3Revision | None:
    """Find the newest revision present in both ancestries."""
    active_ids = {revision.id for revision in _ancestry(db, active)}
    for revision in _ancestry(db, candidate):
        if revision.id in active_ids:
            return revision
    return None


def _merge(
    base: Any,
    ours: Any,
    theirs: Any,
    path: str,
    conflicts: list[dict[str, str]],
    adopted: list[str],
) -> Any:
    """Replay base->theirs edits on top of ours.

    ``ours`` is the active revision (what is live today) and ``theirs`` is the
    candidate being rebased, so "adopted" entries are the candidate's own
    contribution.  Lists are compared atomically: element-wise merging of
    rule arrays would silently interleave two orderings.
    """
    if isinstance(base, Mapping) and isinstance(ours, Mapping) and isinstance(theirs, Mapping):
        merged = dict(ours)
        for key in sorted(set(base) | set(ours) | set(theirs)):
            child = f"{path}.{key}" if path else key
            base_value = base.get(key, _MISSING)
            our_value = ours.get(key, _MISSING)
            their_value = theirs.get(key, _MISSING)

            if their_value is _MISSING and base_value is not _MISSING:
                # The candidate removed this key.
                if our_value is _MISSING:
                    continue
                if our_value == base_value:
                    merged.pop(key, None)
                    adopted.append(f"删除 {child}")
                else:
                    conflicts.append({
                        "path": child,
                        "reason": "候选删除了该键，但现役版本改动过它",
                    })
                continue

            if base_value is _MISSING and our_value is _MISSING:
                # Only the candidate has it.
                merged[key] = their_value
                adopted.append(f"新增 {child}")
                continue

            if base_value is _MISSING and their_value is _MISSING:
                # Only the active revision has it -- keep it untouched.
                continue

            if base_value is _MISSING:
                # Both sides introduced the key independently.
                if our_value != their_value:
                    conflicts.append({
                        "path": child,
                        "reason": "现役版本与候选各自新增了该键且内容不同",
                    })
                continue

            if our_value is _MISSING:
                # The active revision removed it.
                if their_value != base_value:
                    conflicts.append({
                        "path": child,
                        "reason": "现役版本删除了该键，但候选改动过它",
                    })
                continue

            merged[key] = _merge(
                base_value, our_value, their_value, child, conflicts, adopted
            )
        return merged

    if theirs == base:
        return ours
    if ours == base:
        adopted.append(f"采纳 {path}")
        return theirs
    if ours == theirs:
        return ours
    conflicts.append({
        "path": path or ".",
        "reason": "现役版本与候选都改动了该值且结果不同",
    })
    return ours


def rebase_candidate_artifacts(
    *,
    base: Mapping[str, Any],
    active: Mapping[str, Any],
    candidate: Mapping[str, Any],
) -> dict[str, Any]:
    """Three-way merge one candidate's artifacts onto the active artifacts.

    Each mapping holds the keys in :data:`ARTIFACT_KEYS`.  Returns the merged
    artifacts plus the adopted-change log and any conflicts; callers must refuse
    to persist the result when ``conflicts`` is non-empty.
    """
    merged: dict[str, Any] = {}
    conflicts: list[dict[str, str]] = []
    adopted: list[str] = []
    for key in ARTIFACT_KEYS:
        merged[key] = _merge(
            base.get(key) or {},
            active.get(key) or {},
            candidate.get(key) or {},
            key,
            conflicts,
            adopted,
        )
    return {
        "schema_version": "candidate-rebase-v1",
        "artifacts": merged,
        "adopted_changes": adopted,
        "conflicts": conflicts,
    }


def _artifacts(revision: CategoryEvaluationV3Revision) -> dict[str, Any]:
    import json

    return {
        "contract": json.loads(revision.contract_json or "{}"),
        "classification_map": json.loads(revision.classification_map_json or "{}"),
        "subcategory_dimensions": json.loads(
            revision.subcategory_dimensions_json or "{}"
        ),
    }


def plan_candidate_rebase(
    db: Session,
    *,
    candidate: CategoryEvaluationV3Revision,
    active: CategoryEvaluationV3Revision,
) -> dict[str, Any]:
    """Compute the rebase of ``candidate`` onto ``active``, without persisting.

    Raises :class:`CandidateRebaseError` when the request makes no sense at all
    (wrong status, foreign category, no shared history).  A candidate that is
    already a direct child needs no rebase and says so.
    """
    if candidate.category_key != active.category_key:
        raise CandidateRebaseError(
            "candidate_category_conflict", "候选修订不属于当前类目"
        )
    if candidate.status != "candidate":
        raise CandidateRebaseError(
            "candidate_status_conflict", "只有候选状态的修订可以变基"
        )
    if candidate.id == active.id:
        raise CandidateRebaseError(
            "candidate_is_active", "该修订已是现役版本，无需变基"
        )
    if candidate.parent_revision_id == active.id:
        return {
            "schema_version": "candidate-rebase-v1",
            "needed": False,
            "reason": "候选已直接挂在现役版本之上，无需变基",
            "base_revision_id": active.id,
            "artifacts": _artifacts(candidate),
            "adopted_changes": [],
            "conflicts": [],
        }

    base = nearest_common_ancestor(db, candidate, active)
    if base is None:
        raise CandidateRebaseError(
            "no_common_ancestor",
            "候选与现役版本没有共同祖先，无法自动变基",
        )

    result = rebase_candidate_artifacts(
        base=_artifacts(base),
        active=_artifacts(active),
        candidate=_artifacts(candidate),
    )
    result.update({
        "needed": True,
        "base_revision_id": base.id,
        "base_revision": base.revision,
        "source_revision_id": candidate.id,
        "source_revision": candidate.revision,
        "onto_revision_id": active.id,
        "onto_revision": active.revision,
    })
    return result


