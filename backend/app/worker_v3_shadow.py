"""ADR-0033 Phase 4 worker 灰度旁挂：v3 影子评分（非侵入、默认关、best-effort）。

This module is the **only** logic the worker gains for ADR-0033 v3 shadow
scoring.  It is deliberately isolated so it can be unit-tested without importing
the heavy ``worker`` module, and so its failure surface is provably contained:

- **默认关**：``v3_shadow_enabled()`` reads ``ADR33_V3_SHADOW_ENABLED`` and is
  ``False`` unless the value is ``"1"``/``"true"`` (case-insensitive).  When the
  switch is off, ``compute_v3_shadow`` returns ``None`` and does nothing at all.
- **非侵入**：this module never touches the authoritative v1 scoring, never
  writes anything, and only issues read-only SELECTs against the already-open
  session.  The worker stores its return value in a brand-new nullable column;
  no existing ``EvaluationResult`` field is affected.
- **best-effort**：``compute_v3_shadow`` wraps the whole v3 chain in
  ``try/except`` — any exception becomes ``{"status": "error", ...}`` + a
  logged warning and is **never** re-raised, so v3 can never break or interrupt
  a real evaluation.

Grade mapping honesty (see ADR33_TASK1_DONE.md): v3's *common* dimension group
reuses the v13 space-schema keys, so common grades map faithfully from v1's
``aesthetic["dimensions"]``.  v3's *specific*-track dimensions have **no** v1
counterpart, so when a resolved track carries a non-empty specific group we do
**not** fabricate grades — we record ``status="skipped"``,
``reason="grade_mapping_unavailable"`` and leave precise mapping as a TODO.  The
redline branch needs no grades, so redline-hit images still get a full shadow
result.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import CategoryEvaluationV3Config

logger = logging.getLogger("3d66.worker.v3_shadow")

# Environment switch controlling the shadow computation. Default OFF: only the
# literal enabling tokens below turn it on; anything else (unset, "0", "", any
# other string) keeps the worker byte-for-byte identical to its current
# behaviour.
_SHADOW_ENV_VAR = "ADR33_V3_SHADOW_ENABLED"
_ENABLED_TOKENS = {"1", "true"}


def v3_shadow_enabled() -> bool:
    """Return whether the v3 shadow computation is switched on (default False).

    Reads ``ADR33_V3_SHADOW_ENABLED`` each call (no module-level caching) so a
    test or operator can flip it via ``monkeypatch.setenv`` / the process
    environment without reimporting.  Only ``"1"`` / ``"true"`` (case- and
    whitespace-insensitive) enable it; every other value — including unset —
    means off.
    """
    return os.getenv(_SHADOW_ENV_VAR, "").strip().lower() in _ENABLED_TOKENS


def _load_active_v3_config(
    db: Session, category_key: str
) -> CategoryEvaluationV3Config | None:
    """Read-only SELECT for the *active* v3 config of ``category_key``.

    Returns ``None`` when there is no row or the row is not ``active`` — the
    caller turns that into a ``skipped`` shadow payload.  Never writes.
    """
    return db.scalar(
        select(CategoryEvaluationV3Config).where(
            CategoryEvaluationV3Config.category_key == category_key,
            CategoryEvaluationV3Config.status == "active",
        )
    )


def _common_grades_from_aesthetic(
    aesthetic: Any, common_dimension_keys: list[str]
) -> dict[str, int] | None:
    """Map v1 ``aesthetic["dimensions"]`` grades onto v3's common-group keys.

    v3's common group reuses the v13 space-schema dimension keys, which are
    exactly the keys v1's 调用B fills in ``aesthetic["dimensions"][key]["grade"]``.
    Returns ``{key: grade}`` covering every common key, or ``None`` when the
    aesthetic payload is missing / malformed or any required key's grade is
    absent or not a valid 1-5 integer (fail-closed: an incomplete map is treated
    as "cannot map" rather than silently guessed).
    """
    if not isinstance(aesthetic, dict):
        return None
    dimensions = aesthetic.get("dimensions")
    if not isinstance(dimensions, dict):
        return None
    grades: dict[str, int] = {}
    for key in common_dimension_keys:
        item = dimensions.get(key)
        if not isinstance(item, dict):
            return None
        grade = item.get("grade")
        if isinstance(grade, bool) or not isinstance(grade, int) or not 1 <= grade <= 5:
            return None
        grades[key] = grade
    return grades


def _dimension_keys(group: Any) -> list[str]:
    """Best-effort extraction of a group's dimension keys (empty if malformed)."""
    if not isinstance(group, dict):
        return []
    schema = group.get("schema_definition")
    if not isinstance(schema, dict):
        return []
    dimensions = schema.get("dimensions")
    if not isinstance(dimensions, list):
        return []
    return [
        d["key"]
        for d in dimensions
        if isinstance(d, dict) and isinstance(d.get("key"), str) and d["key"]
    ]


def compute_v3_shadow(
    db: Session,
    category_key: Any,
    precheck: Any,
    aesthetic: Any,
    *,
    enabled: bool,
) -> dict[str, Any] | None:
    """Compute the ADR-0033 v3 shadow payload for one image (best-effort, pure-read).

    Returns:
    - ``None`` when the switch is off (the worker leaves the shadow column NULL —
      byte-for-byte identical to today).
    - a JSON-serializable status dict otherwise, one of:
      ``{"status": "skipped", "reason": ...}``,
      ``{"status": "ok", "engine": "adr33-v3", "config_revision": ..., "result": ...}``,
      ``{"status": "error", "error": ...}``.

    NEVER raises: every failure inside the v3 chain is caught, logged as a
    warning, and returned as ``status="error"``.  Only issues read-only SELECTs;
    writes nothing.  The authoritative v1 scoring is not read or mutated here.
    """
    if not enabled:
        return None
    try:
        # Deferred import: the seed pulls in the whole framework stack, so keep
        # it lazy and inside the guarded block — an import failure degrades to a
        # shadow "error", never a worker crash.
        from .inspiration_category_seed import evaluate_one

        if not isinstance(category_key, str) or not category_key:
            return {"status": "skipped", "reason": "no_category_key"}

        config = _load_active_v3_config(db, category_key)
        if config is None:
            return {"status": "skipped", "reason": "no_active_v3_config"}

        import json

        contract = json.loads(config.contract_json or "{}")
        classification_map = json.loads(config.classification_map_json or "{}")
        subcategory_dimensions = json.loads(
            config.subcategory_dimensions_json or "{}"
        )

        # Decide the grade-mapping feasibility BEFORE calling evaluate_one:
        #   * A redline hit short-circuits scoring and ignores grades entirely,
        #     so a shadow result is fully faithful with empty grade maps.
        #   * Otherwise the resolved track's *specific* dimensions have no v1
        #     counterpart, so if that track carries a non-empty specific group we
        #     refuse to fabricate grades and skip (grade_mapping_unavailable).
        from .redline_policy import evaluate_redlines
        from .subcategory_resolver import resolve_subcategory

        redline = evaluate_redlines(precheck, policy=contract["redline_policy"])
        common_grades_by_track: dict[str, dict[str, int]] = {}
        specific_grades_by_track: dict[str, dict[str, int]] = {}

        if not redline.get("hit"):
            resolved = resolve_subcategory(
                precheck,
                classification_map=classification_map,
                track_classification=contract["track_classification"],
            )
            track_key = resolved["track_key"]
            track_config = subcategory_dimensions.get(track_key)
            if not isinstance(track_config, dict):
                return {
                    "status": "skipped",
                    "reason": "missing_track_config",
                    "detail": f"track {track_key} 无 subcategory_dimensions 配置",
                }
            specific_keys = _dimension_keys(track_config.get("specific_group"))
            if specific_keys:
                # v3 specific-track dimensions (spatial_originality, ...) are not
                # produced by v1's 调用B; do not guess. Precise mapping is a TODO.
                return {
                    "status": "skipped",
                    "reason": "grade_mapping_unavailable",
                    "detail": (
                        f"track {track_key} 含 v1 无对应的特有维度 "
                        f"{sorted(specific_keys)}，未硬猜 grade，留待精确映射"
                    ),
                }
            common_keys = _dimension_keys(track_config.get("common_group"))
            common_grades = _common_grades_from_aesthetic(aesthetic, common_keys)
            if common_keys and common_grades is None:
                return {
                    "status": "skipped",
                    "reason": "grade_mapping_unavailable",
                    "detail": (
                        f"track {track_key} 的共性维度 grade 无法从 aesthetic 完整映射"
                    ),
                }
            if common_grades:
                common_grades_by_track[track_key] = common_grades

        result = evaluate_one(
            contract=contract,
            classification_map=classification_map,
            subcategory_dimensions=subcategory_dimensions,
            precheck=precheck if isinstance(precheck, dict) else {},
            common_grades_by_track=common_grades_by_track,
            specific_grades_by_track=specific_grades_by_track,
        )
        return {
            "status": "ok",
            "engine": "adr33-v3",
            "config_revision": config.revision,
            "result": result,
        }
    except Exception as exc:  # noqa: BLE001 — best-effort: never break the worker
        logger.warning("ADR-0033 v3 shadow scoring failed (best-effort): %s", exc)
        return {"status": "error", "error": str(exc)[:500]}

