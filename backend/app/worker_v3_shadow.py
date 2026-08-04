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
counterpart.

ADR-0033 Task 1b closes that gap with a **dedicated shadow 调用B**: rather than
fabricate the specific-dimension grades, the worker issues a separate,
best-effort, switch-gated model call (``fetch_v3_specific_grades``) that grades
*only* those specific dimensions, and feeds the result back into
``compute_v3_shadow`` via ``specific_grades_by_track``.  When that shadow call is
absent / fails / returns an incomplete map, ``compute_v3_shadow`` records
``status="skipped"``, ``reason="specific_grade_shadow_unavailable"`` (fail-closed:
never guess).  The redline branch needs no grades, so redline-hit images still
get a full shadow result.

category_key alignment (TODO for OpenClaw): the worker feeds
``current_job.category_key`` straight into ``compute_v3_shadow`` /
``resolve_specific_shadow_targets``.  The active v3 config is keyed by
``CategoryEvaluationV3Config.category_key`` (e.g. ``"inspiration_image"``).  If
the job's ``category_key`` naming does not equal the v3 config key, the lookup
falls through to ``no_active_v3_config`` and the shadow is simply skipped — this
module deliberately does **not** guess a mapping.  Aligning the two (very likely
via an explicit ``{job_category_key -> v3_config_key}`` alias table) is left for
OpenClaw to confirm; add that table here and resolve through it once the mapping
is settled.
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
    return [d["key"] for d in _dimension_defs(group)]


def _dimension_defs(group: Any) -> list[dict[str, Any]]:
    """Best-effort extraction of a group's ``{key, label}`` dimension defs.

    Empty on any malformed shape.  ``label`` falls back to ``key`` when absent so
    the shadow prompt always has something human-readable to describe.
    """
    if not isinstance(group, dict):
        return []
    schema = group.get("schema_definition")
    if not isinstance(schema, dict):
        return []
    dimensions = schema.get("dimensions")
    if not isinstance(dimensions, list):
        return []
    defs: list[dict[str, Any]] = []
    for d in dimensions:
        if not isinstance(d, dict):
            continue
        key = d.get("key")
        if not isinstance(key, str) or not key:
            continue
        label = d.get("label")
        defs.append({"key": key, "label": label if isinstance(label, str) and label else key})
    return defs


def _extract_specific_grades(
    parsed: Any, expected_keys: list[str]
) -> dict[str, int] | None:
    """Parse a shadow 调用B ``parsed`` payload into ``{key: grade}`` (fail-closed).

    Expects ``{"dimensions": {key: {"grade": 1..5, ...}}}`` (the shape
    ``build_specific_dimension_shadow_prompt`` asks for).  Returns a complete map
    covering **every** ``expected_keys`` entry with a valid 1-5 int grade, or
    ``None`` when the payload is malformed / any expected key is missing or its
    grade is absent / non-integer / out of range.  An incomplete map is treated
    as "unavailable" rather than silently partial (fail-closed, mirrors
    ``_common_grades_from_aesthetic``).
    """
    if not isinstance(parsed, dict):
        return None
    dimensions = parsed.get("dimensions")
    if not isinstance(dimensions, dict):
        return None
    grades: dict[str, int] = {}
    for key in expected_keys:
        item = dimensions.get(key)
        if not isinstance(item, dict):
            return None
        grade = item.get("grade")
        if isinstance(grade, bool) or not isinstance(grade, int) or not 1 <= grade <= 5:
            return None
        grades[key] = grade
    return grades


def resolve_specific_shadow_targets(
    db: Session, category_key: Any, precheck: Any, *, enabled: bool
) -> dict[str, Any] | None:
    """Read-only: decide whether/what a v3 specific-dimension 影子调用B should grade.

    Runs the same pure redline/track resolution as ``compute_v3_shadow`` but stops
    *before* any scoring, so the worker can learn — without a model call — the
    resolved ``track_key`` and its specific-dimension defs.  The worker then only
    issues the extra shadow 调用B when this returns a non-empty target.

    Returns:
    - ``None`` when the switch is off, there is no active/parseable config, the
      precheck hits a redline, or the resolved track carries no specific
      dimensions — in every one of those cases no shadow 调用B is needed (either
      the shadow is skipped upstream, or the redline/common-only path already
      produces a faithful result without specific grades).
    - ``{"track_key": str, "specific_dims": [{"key","label"}, ...]}`` when the
      resolved track has a non-empty specific group that a shadow 调用B should
      grade.

    NEVER raises: any failure degrades to ``None`` (the worker then skips the
    extra call and ``compute_v3_shadow`` records the fail-closed skip).  Pure
    read: only SELECTs, no writes, no model calls.
    """
    if not enabled:
        return None
    try:
        if not isinstance(category_key, str) or not category_key:
            return None
        config = _load_active_v3_config(db, category_key)
        if config is None:
            return None

        import json

        contract = json.loads(config.contract_json or "{}")
        classification_map = json.loads(config.classification_map_json or "{}")
        subcategory_dimensions = json.loads(config.subcategory_dimensions_json or "{}")

        from .redline_policy import evaluate_redlines
        from .subcategory_resolver import resolve_subcategory

        redline = evaluate_redlines(precheck, policy=contract["redline_policy"])
        if redline.get("hit"):
            return None

        resolved = resolve_subcategory(
            precheck,
            classification_map=classification_map,
            track_classification=contract["track_classification"],
        )
        track_key = resolved["track_key"]
        track_config = subcategory_dimensions.get(track_key)
        if not isinstance(track_config, dict):
            return None
        specific_dims = _dimension_defs(track_config.get("specific_group"))
        if not specific_dims:
            return None
        return {"track_key": track_key, "specific_dims": specific_dims}
    except Exception as exc:  # noqa: BLE001 — best-effort: never break the worker
        logger.warning(
            "ADR-0033 v3 specific-shadow target resolution failed (best-effort): %s",
            exc,
        )
        return None


async def fetch_v3_specific_grades(
    client: Any,
    image_path: Any,
    mime_type: Any,
    track_key: str,
    specific_dims: list[dict[str, Any]],
    *,
    enabled: bool,
) -> dict[str, Any] | None:
    """Issue the extra v3 specific-dimension 影子调用B (best-effort, switch-gated).

    This is the **only** place the shadow path performs an *additional* model
    call, and it is fully contained:

    - **默认关**：returns ``None`` immediately when ``enabled`` is False — no model
      call is ever issued while the switch is off.
    - **best-effort**：the whole ``client.chat_json`` + parse is wrapped in
      ``try/except``; any exception (network, timeout, bad JSON, ...) is logged as
      a warning and returned as ``{"status": "error", ...}`` — **never** re-raised,
      so the authoritative evaluation is unaffected.

    Returns:
    - ``None`` when disabled, or when there are no specific dimensions to grade.
    - ``{"status": "ok", "track_key", "grades": {key: 1..5}}`` when 调用B returns a
      complete, valid grade map for every specific dimension.
    - ``{"status": "error", ...}`` on any failure or incomplete/invalid map.
    """
    if not enabled:
        return None
    try:
        expected_keys = [
            d["key"]
            for d in specific_dims
            if isinstance(d, dict) and isinstance(d.get("key"), str) and d["key"]
        ]
        if not expected_keys:
            return None

        # Deferred import: keep the prompt builder lazy so an import failure
        # degrades to a shadow "error", never a worker crash.
        from .worker_v3_shadow_prompt import build_specific_dimension_shadow_prompt

        system_prompt, user_prompt = build_specific_dimension_shadow_prompt(
            track_key, specific_dims
        )
        response = await client.chat_json(
            system_prompt,
            user_prompt,
            image_path=image_path,
            mime_type=mime_type,
        )
        grades = _extract_specific_grades(getattr(response, "parsed", None), expected_keys)
        if grades is None:
            return {
                "status": "error",
                "error": "specific_grade_shadow_parse_incomplete",
                "track_key": track_key,
            }
        return {"status": "ok", "track_key": track_key, "grades": grades}
    except Exception as exc:  # noqa: BLE001 — best-effort: never break the worker
        logger.warning(
            "ADR-0033 v3 specific-dimension shadow 调用B failed (best-effort): %s", exc
        )
        return {"status": "error", "error": str(exc)[:500], "track_key": track_key}


def compute_v3_shadow(
    db: Session,
    category_key: Any,
    precheck: Any,
    aesthetic: Any,
    *,
    enabled: bool,
    specific_grades_by_track: dict[str, dict[str, int]] | None = None,
) -> dict[str, Any] | None:
    """Compute the ADR-0033 v3 shadow payload for one image (best-effort, pure-read).

    ``specific_grades_by_track`` (Task 1b) optionally supplies the per-track v3
    *specific*-dimension grades produced by the dedicated shadow 调用B
    (``fetch_v3_specific_grades``).  When a non-redline track carries specific
    dimensions, those grades **must** be present and complete here; otherwise the
    payload is ``status="skipped"``, ``reason="specific_grade_shadow_unavailable"``
    (fail-closed: this function never fabricates specific grades).

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
        resolved_specific_grades: dict[str, dict[str, int]] = dict(
            specific_grades_by_track or {}
        )

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
                # Task 1b: v3 specific-track dimensions (spatial_originality, ...)
                # have no v1 调用B counterpart, so they arrive via the dedicated
                # shadow 调用B (``fetch_v3_specific_grades``) as
                # ``specific_grades_by_track``.  Still fail-closed: if the shadow
                # call is absent / incomplete for any specific key, skip rather
                # than fabricate.
                provided = resolved_specific_grades.get(track_key)
                if not isinstance(provided, dict) or any(
                    key not in provided for key in specific_keys
                ):
                    return {
                        "status": "skipped",
                        "reason": "specific_grade_shadow_unavailable",
                        "detail": (
                            f"track {track_key} 的特有维度 {sorted(specific_keys)} "
                            f"影子调用B grade 缺失/不完整，未硬猜"
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

        specific_grades_arg = resolved_specific_grades

        result = evaluate_one(
            contract=contract,
            classification_map=classification_map,
            subcategory_dimensions=subcategory_dimensions,
            precheck=precheck if isinstance(precheck, dict) else {},
            common_grades_by_track=common_grades_by_track,
            specific_grades_by_track=specific_grades_arg,
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

