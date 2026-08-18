"""Thin adapters shared by baseline, incremental, and candidate correction lanes."""

from __future__ import annotations

import json
from copy import deepcopy
from types import SimpleNamespace
from typing import Any, Literal, Mapping

from .evaluation_v3_pipeline import recompute_qualified_v3
from .worker_v3_authoritative import build_v3_authoritative_scoring


CorrectionLane = Literal["baseline", "incremental", "candidate"]
EvidenceRoute = Literal["A", "B", "V3", "A+B"]
_EXECUTABLE_INPUT_KEYS = {
    "code",
    "expression",
    "executable",
    "javascript",
    "python",
    "rule_code",
    "threshold_override",
}


def _json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return deepcopy(dict(value))
    if not isinstance(value, str):
        return {}
    try:
        parsed = json.loads(value or "{}")
    except (TypeError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def correction_lane_for_run(run: Any) -> CorrectionLane:
    table_name = str(
        getattr(run, "__tablename__", "")
        or getattr(type(run), "__tablename__", "")
    )
    if table_name == "evaluation_production_runs":
        return "incremental"
    if table_name == "prompt_regression_runs":
        return "candidate"
    if table_name == "baseline_regression_runs":
        execution = _json_object(getattr(run, "execution_snapshot_json", None))
        correction_context = execution.get("correction_context")
        if isinstance(correction_context, Mapping) and correction_context.get(
            "candidate_revision_id"
        ):
            return "candidate"
        return "baseline"
    raise ValueError("无法识别纠偏运行类型")


def _contains_executable_input(value: Any) -> bool:
    if isinstance(value, Mapping):
        return any(
            str(key).lower() in _EXECUTABLE_INPUT_KEYS
            or _contains_executable_input(item)
            for key, item in value.items()
        )
    if isinstance(value, list):
        return any(_contains_executable_input(item) for item in value)
    return False


def route_human_evidence(report: Mapping[str, Any]) -> EvidenceRoute:
    if _contains_executable_input(report):
        raise ValueError("人工纠偏不能提交规则代码或阈值覆盖")
    raw_layers = report.get("affected_layers")
    if not isinstance(raw_layers, list):
        raw_nodes = report.get("nodes")
        raw_layers = (
            [node.get("layer") for node in raw_nodes if isinstance(node, Mapping)]
            if isinstance(raw_nodes, list)
            else []
        )
    layers = {
        str(layer)
        for layer in raw_layers
        if str(layer) in {"A", "B", "V3"}
    }
    if layers == {"A"}:
        return "A"
    if layers == {"B"}:
        return "B"
    if layers == {"V3"}:
        return "A+B"
    return "A+B"


def wrap_evaluation_item(
    run: Any,
    evaluation: Any,
    *,
    item_id: int,
    result_snapshot_json: str,
) -> SimpleNamespace:
    """Present lane-specific results through the shared item interface."""

    return SimpleNamespace(
        id=item_id,
        run_id=getattr(run, "id", None),
        run=run,
        asset_id=getattr(evaluation, "asset_id", None),
        evaluation_id=getattr(evaluation, "id", None),
        evaluation=evaluation,
        result_snapshot_json=result_snapshot_json,
    )


def recompute_v3_from_correction(
    *,
    v3_context: dict[str, Any],
    precheck: dict[str, Any],
    dimension_output: dict[str, Any] | None,
    track_key: str | None = None,
) -> dict[str, Any]:
    replayed = recompute_qualified_v3(
        v3_context=v3_context,
        precheck=precheck,
        dimension_output=dimension_output,
        track_key=track_key,
    )
    scoring = build_v3_authoritative_scoring(replayed, precheck=precheck)
    scoring.pop("_dimension_deduction_raw_payload", None)
    scoring["v3_context"] = deepcopy(v3_context)
    scoring["v3_config_revision"] = v3_context.get("config_revision")
    return {
        "precheck": deepcopy(precheck),
        "dimension_output": deepcopy(
            replayed.get("dimension_deduction_output", dimension_output)
        ),
        "scoring": scoring,
    }
