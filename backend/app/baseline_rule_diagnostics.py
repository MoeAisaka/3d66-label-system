"""Aggregate per-rule hit coverage for one baseline regression run.

Tuning a scoring mechanism blind is the failure mode this module exists to
prevent.  A contract can declare dozens of deduction rules, raise their point
values, and lower the redline threshold -- and still change nothing, because the
model never reports the defects those rules key on.  Without per-rule coverage
an operator sees only "accuracy is still bad" and keeps adjusting numbers that
can never fire.

The report answers three questions directly:

* which declared rules were never hit across the whole run (dead rules),
* how many items ended up with no rule hits at all (so their score is whatever
  calling B volunteered, untouched by the rule layer),
* where the resulting scores landed relative to the contract's level scale.

Everything is derived from already-persisted snapshots; nothing is recomputed
and no model call is made.
"""

from __future__ import annotations

import json
from typing import Any, Iterable, Mapping

RULE_DIAGNOSTICS_SCHEMA = "baseline-rule-diagnostics-v1"


def _schema_definitions(track: Mapping[str, Any]) -> Iterable[Mapping[str, Any]]:
    for group_key in ("common_group", "specific_group"):
        group = track.get(group_key)
        if not isinstance(group, Mapping):
            continue
        definition = group.get("schema_definition")
        if isinstance(definition, Mapping):
            yield definition


def declared_rules(subcategory_dimensions: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Flatten every deduction/bonus rule the frozen contract declares.

    Rules are keyed by ``(dimension_key, rule_id)`` because the same ``rule_id``
    may legitimately appear under several dimensions; a track that repeats a
    dimension key is folded into one entry so the report counts each rule once.
    """
    seen: dict[tuple[str, str], dict[str, Any]] = {}
    for track_key, track in sorted((subcategory_dimensions or {}).items()):
        if not isinstance(track, Mapping):
            continue
        for definition in _schema_definitions(track):
            for dimension in definition.get("dimensions") or []:
                if not isinstance(dimension, Mapping):
                    continue
                dimension_key = str(dimension.get("key") or "")
                if not dimension_key:
                    continue
                for kind, points_field in (
                    ("deduction", "deduction"),
                    ("bonus", "bonus"),
                ):
                    for rule in dimension.get(f"{kind}_rules") or []:
                        if not isinstance(rule, Mapping):
                            continue
                        rule_id = str(rule.get("rule_id") or "")
                        if not rule_id:
                            continue
                        key = (dimension_key, rule_id)
                        if key in seen:
                            seen[key].setdefault("tracks", [])
                            if track_key not in seen[key]["tracks"]:
                                seen[key]["tracks"].append(track_key)
                            continue
                        seen[key] = {
                            "dimension_key": dimension_key,
                            "dimension_label": str(dimension.get("label") or ""),
                            "rule_id": rule_id,
                            "kind": kind,
                            "description": str(rule.get("description") or ""),
                            "points": rule.get(points_field),
                            "tracks": [track_key],
                        }
    return [seen[key] for key in sorted(seen)]


def _hit_rule_ids(dimension_output: Mapping[str, Any]) -> tuple[set[str], set[str]]:
    def ids(field: str) -> set[str]:
        out: set[str] = set()
        for entry in dimension_output.get(field) or []:
            if isinstance(entry, Mapping):
                rule_id = entry.get("rule_id")
                if rule_id:
                    out.add(str(rule_id))
            elif entry:
                out.add(str(entry))
        return out

    return ids("hit_rules"), ids("hit_bonus_rules")


def _level_for_score(level_scale: Mapping[str, Any], score: Any) -> str | None:
    if not isinstance(score, (int, float)) or isinstance(score, bool):
        return None
    best: tuple[int, str] | None = None
    for level in (level_scale or {}).get("levels") or []:
        if not isinstance(level, Mapping) or level.get("enabled") is False:
            continue
        minimum = level.get("min_score")
        if not isinstance(minimum, (int, float)) or isinstance(minimum, bool):
            continue
        if score >= minimum and (best is None or minimum > best[0]):
            best = (int(minimum), str(level.get("level") or ""))
    return best[1] if best else None


def build_rule_diagnostics(
    *,
    frozen_bundle: Mapping[str, Any],
    item_snapshots: Iterable[Mapping[str, Any]],
) -> dict[str, Any]:
    """Summarise rule coverage for one run from its frozen contract and items."""
    contract = frozen_bundle.get("contract")
    contract = contract if isinstance(contract, Mapping) else {}
    subcategory_dimensions = frozen_bundle.get("subcategory_dimensions")
    subcategory_dimensions = (
        subcategory_dimensions if isinstance(subcategory_dimensions, Mapping) else {}
    )
    rules = declared_rules(subcategory_dimensions)
    counts: dict[tuple[str, str], int] = {
        (rule["dimension_key"], rule["rule_id"]): 0 for rule in rules
    }
    undeclared: dict[tuple[str, str], int] = {}

    scored_items = 0
    items_without_hits = 0
    items_with_caps = 0
    level_distribution: dict[str, int] = {}
    score_buckets: dict[str, int] = {}
    unpenalised_scores: list[float] = []

    for snapshot in item_snapshots:
        if not isinstance(snapshot, Mapping):
            continue
        scoring = snapshot.get("scoring")
        scoring = scoring if isinstance(scoring, Mapping) else {}
        output = scoring.get("dimension_deduction_output")
        output = output if isinstance(output, Mapping) else {}
        dimensions = output.get("dimensions")
        dimensions = dimensions if isinstance(dimensions, Mapping) else {}

        predicted = snapshot.get("predicted_level")
        if predicted:
            level_distribution[str(predicted)] = (
                level_distribution.get(str(predicted), 0) + 1
            )
        score = snapshot.get("authoritative_score")
        if isinstance(score, (int, float)) and not isinstance(score, bool):
            bucket = f"{int(score) // 10 * 10}-{int(score) // 10 * 10 + 9}"
            score_buckets[bucket] = score_buckets.get(bucket, 0) + 1
        if snapshot.get("cap_reasons"):
            items_with_caps += 1

        if not dimensions:
            continue
        scored_items += 1
        item_hits = 0
        for dimension_key, dimension_output in dimensions.items():
            if not isinstance(dimension_output, Mapping):
                continue
            deduction_hits, bonus_hits = _hit_rule_ids(dimension_output)
            for rule_id in deduction_hits | bonus_hits:
                key = (str(dimension_key), rule_id)
                item_hits += 1
                if key in counts:
                    counts[key] += 1
                else:
                    undeclared[key] = undeclared.get(key, 0) + 1
        if item_hits == 0:
            items_without_hits += 1
            # Score an item keeps when no rule fires -- i.e. whatever calling B
            # volunteered, passed through untouched by the rule layer.
            if isinstance(score, (int, float)) and not isinstance(score, bool):
                unpenalised_scores.append(score)

    rule_rows = [
        {**rule, "hit_count": counts[(rule["dimension_key"], rule["rule_id"])]}
        for rule in rules
    ]
    never_hit = [row for row in rule_rows if row["hit_count"] == 0]
    # Median, not mean: one outlier score should not move the figure an operator
    # uses to reason about where unpenalised items land on the level scale.
    unpenalised_score: float | None = None
    if unpenalised_scores:
        ordered = sorted(unpenalised_scores)
        middle = len(ordered) // 2
        unpenalised_score = (
            ordered[middle]
            if len(ordered) % 2
            else (ordered[middle - 1] + ordered[middle]) / 2
        )

    return {
        "schema_version": RULE_DIAGNOSTICS_SCHEMA,
        "candidate_revision_id": frozen_bundle.get("candidate_revision_id"),
        "config_revision": frozen_bundle.get("config_revision"),
        "rules": rule_rows,
        "never_hit_rule_count": len(never_hit),
        "declared_rule_count": len(rule_rows),
        "undeclared_hits": [
            {"dimension_key": key[0], "rule_id": key[1], "hit_count": value}
            for key, value in sorted(undeclared.items())
        ],
        "scored_item_count": scored_items,
        "items_without_rule_hits": items_without_hits,
        "items_with_score_caps": items_with_caps,
        "level_distribution": level_distribution,
        "score_buckets": dict(sorted(score_buckets.items())),
        "level_scale": contract.get("level_scale") or {},
        "redline_policy": {
            key: value
            for key, value in (contract.get("redline_policy") or {}).items()
            if key != "rules"
        },
        "redline_rule_count": len(
            (contract.get("redline_policy") or {}).get("rules") or []
        ),
        "rule_layer_inert": bool(scored_items) and items_without_hits == scored_items,
        "unpenalised_level": _level_for_score(
            contract.get("level_scale") or {}, unpenalised_score
        ),
    }


def diagnostics_from_run(run: Any) -> dict[str, Any]:
    """Build the report straight off a persisted regression run."""
    execution = json.loads(getattr(run, "execution_snapshot_json", None) or "{}")
    bundle = execution.get("v3_authoritative_bundle")
    bundle = bundle if isinstance(bundle, Mapping) else {}
    snapshots = [
        json.loads(item.result_snapshot_json or "{}")
        for item in getattr(run, "items", []) or []
    ]
    return build_rule_diagnostics(frozen_bundle=bundle, item_snapshots=snapshots)
