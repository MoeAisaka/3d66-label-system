from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping, Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from .audit import canonical_json
from .models import (
    ScriptDefinition,
    ScriptVersion,
    WorkflowDefinition,
    WorkflowVersion,
)
from .queue_scheduler import QUEUE_CLASSES
from .script_registry import ValidationErrorItem, ValidationReport


STANDARD_STEP_TYPES = frozenset(
    {
        "connector",
        "identity",
        "transform",
        "model_call",
        "rule_eval",
        "human_task",
        "release_gate",
        "projection",
        "reconcile",
        "feedback",
    }
)
CONDITION_OPERATORS = frozenset(
    {"eq", "neq", "in", "not_in", "exists", "gte", "gt", "lte", "lt"}
)
WORKFLOW_VERSION_STATUSES = frozenset(
    {"draft", "validating", "active", "deprecated", "retired", "blocked"}
)
ALLOWED_TRANSITIONS = {
    "draft": frozenset({"validating", "retired"}),
    "validating": frozenset({"active", "blocked", "retired"}),
    "active": frozenset({"deprecated", "blocked"}),
    "deprecated": frozenset({"retired", "blocked"}),
    "blocked": frozenset({"validating", "retired"}),
    "retired": frozenset(),
}


class WorkflowRegistryError(ValueError):
    def __init__(self, code: str, message: str, *, status_code: int = 400):
        super().__init__(message)
        self.code = code
        self.status_code = status_code


def _error(
    errors: list[ValidationErrorItem],
    *,
    path: str,
    code: str,
    message: str,
) -> None:
    errors.append(ValidationErrorItem(path=path, code=code, message=message))


def _script_reference(
    db: Session,
    reference: Any,
    *,
    path: str,
    step_type: str | None,
    errors: list[ValidationErrorItem],
) -> ScriptVersion | None:
    if not isinstance(reference, str) or "@" not in reference:
        _error(
            errors,
            path=path,
            code="script_reference_invalid",
            message="脚本版本引用必须使用 script_key@version",
        )
        return None
    script_key, version_name = reference.rsplit("@", 1)
    definition = db.scalar(
        select(ScriptDefinition).where(ScriptDefinition.script_key == script_key)
    )
    if definition is None:
        _error(
            errors,
            path=path,
            code="script_definition_unknown",
            message=f"未知脚本定义：{script_key}",
        )
        return None
    version = db.scalar(
        select(ScriptVersion).where(
            ScriptVersion.script_definition_id == definition.id,
            ScriptVersion.version == version_name,
        )
    )
    if version is None:
        _error(
            errors,
            path=path,
            code="script_version_unknown",
            message=f"未知脚本版本：{reference}",
        )
        return None
    if definition.status != "active" or version.status != "active":
        _error(
            errors,
            path=path,
            code="script_version_unavailable",
            message=f"脚本版本不可用于新工作流：{reference}",
        )
    if step_type is not None:
        try:
            allowed_types = json.loads(definition.step_types_json)
        except json.JSONDecodeError:
            allowed_types = []
        if step_type not in allowed_types:
            _error(
                errors,
                path=path,
                code="script_step_type_incompatible",
                message=f"{reference} 不支持步骤类型 {step_type}",
            )
    return version


def _validate_condition(
    condition: Any,
    *,
    path: str,
    step_keys: set[str],
    errors: list[ValidationErrorItem],
    depth: int = 0,
) -> None:
    if depth > 8:
        _error(
            errors,
            path=path,
            code="condition_depth_exceeded",
            message="条件嵌套深度不能超过 8",
        )
        return
    if not isinstance(condition, Mapping):
        _error(
            errors,
            path=path,
            code="condition_object_required",
            message="条件必须是对象",
        )
        return
    compound = [key for key in ("all", "any", "not") if key in condition]
    if compound:
        if len(compound) != 1 or len(condition) != 1:
            _error(
                errors,
                path=path,
                code="condition_shape_invalid",
                message="复合条件只能声明 all、any 或 not 之一",
            )
            return
        key = compound[0]
        value = condition[key]
        children: Sequence[Any]
        if key == "not":
            children = [value]
        elif isinstance(value, list) and value:
            children = value
        else:
            _error(
                errors,
                path=f"{path}.{key}",
                code="condition_children_required",
                message=f"{key} 必须包含非空条件数组",
            )
            return
        for index, child in enumerate(children):
            _validate_condition(
                child,
                path=f"{path}.{key}[{index}]",
                step_keys=step_keys,
                errors=errors,
                depth=depth + 1,
            )
        return

    operator = condition.get("op")
    if operator not in CONDITION_OPERATORS:
        _error(
            errors,
            path=f"{path}.op",
            code="condition_operator_unsupported",
            message="条件操作符不在允许列表",
        )
    reference = condition.get("path")
    if not isinstance(reference, str) or not reference.startswith("steps."):
        _error(
            errors,
            path=f"{path}.path",
            code="condition_path_invalid",
            message="条件路径必须引用前序步骤输出",
        )
        return
    parts = reference.split(".")
    if len(parts) < 4 or parts[1] not in step_keys or parts[2] != "output":
        _error(
            errors,
            path=f"{path}.path",
            code="condition_path_unknown",
            message="条件路径引用了未知步骤或非输出字段",
        )


def _topological_order(
    step_keys: list[str],
    edges: list[tuple[str, str]],
) -> list[str] | None:
    incoming = {key: 0 for key in step_keys}
    outgoing = {key: [] for key in step_keys}
    for source, target in edges:
        if source in outgoing and target in incoming:
            outgoing[source].append(target)
            incoming[target] += 1
    ready = sorted(key for key, count in incoming.items() if count == 0)
    order: list[str] = []
    while ready:
        current = ready.pop(0)
        order.append(current)
        for target in sorted(outgoing[current]):
            incoming[target] -= 1
            if incoming[target] == 0:
                ready.append(target)
                ready.sort()
    return order if len(order) == len(step_keys) else None


def validate_workflow_manifest(
    db: Session,
    manifest: Mapping[str, Any],
) -> ValidationReport:
    errors: list[ValidationErrorItem] = []
    if manifest.get("schema_version") != "workflow-v1":
        _error(
            errors,
            path="schema_version",
            code="workflow_schema_version_unsupported",
            message="工作流 Schema 必须为 workflow-v1",
        )
    queue_class = manifest.get("queue_class")
    if queue_class not in QUEUE_CLASSES:
        _error(
            errors,
            path="queue_class",
            code="queue_class_unsupported",
            message="工作流只能使用既有五队列",
        )

    steps = manifest.get("steps")
    if not isinstance(steps, list) or not steps:
        _error(
            errors,
            path="steps",
            code="workflow_steps_required",
            message="工作流至少包含一个步骤",
        )
        steps = []
    step_keys: list[str] = []
    seen: set[str] = set()
    for index, step in enumerate(steps):
        path = f"steps[{index}]"
        if not isinstance(step, Mapping):
            _error(
                errors,
                path=path,
                code="workflow_step_object_required",
                message="步骤必须是对象",
            )
            continue
        key = step.get("key")
        if not isinstance(key, str) or not key.strip():
            _error(
                errors,
                path=f"{path}.key",
                code="workflow_step_key_invalid",
                message="步骤 key 不能为空",
            )
            continue
        if key in seen:
            _error(
                errors,
                path=f"{path}.key",
                code="workflow_step_key_duplicate",
                message=f"步骤 key 重复：{key}",
            )
        seen.add(key)
        step_keys.append(key)
        step_type = step.get("type")
        if step_type not in STANDARD_STEP_TYPES:
            _error(
                errors,
                path=f"{path}.type",
                code="workflow_step_type_unsupported",
                message="未知标准步骤类型",
            )
        _script_reference(
            db,
            step.get("script_version"),
            path=f"{path}.script_version",
            step_type=step_type if isinstance(step_type, str) else None,
            errors=errors,
        )
        for schema_key in ("input_schema", "output_schema"):
            schema = step.get(schema_key)
            if not isinstance(schema, Mapping) or schema.get("type") != "object":
                _error(
                    errors,
                    path=f"{path}.{schema_key}",
                    code="workflow_step_schema_invalid",
                    message=f"{schema_key} 根类型必须为 object",
                )

    edges_value = manifest.get("edges")
    if not isinstance(edges_value, list):
        _error(
            errors,
            path="edges",
            code="workflow_edges_array_required",
            message="edges 必须是数组",
        )
        edges_value = []
    parsed_edges: list[tuple[str, str]] = []
    step_key_set = set(step_keys)
    for index, edge in enumerate(edges_value):
        path = f"edges[{index}]"
        if not isinstance(edge, Mapping):
            _error(
                errors,
                path=path,
                code="workflow_edge_object_required",
                message="依赖边必须是对象",
            )
            continue
        source = edge.get("from")
        target = edge.get("to")
        if source not in step_key_set or target not in step_key_set:
            _error(
                errors,
                path=path,
                code="workflow_edge_step_unknown",
                message="依赖边引用了未知步骤",
            )
            continue
        parsed_edges.append((str(source), str(target)))
        if "condition" in edge:
            _validate_condition(
                edge["condition"],
                path=f"{path}.condition",
                step_keys=step_key_set,
                errors=errors,
            )

    order = _topological_order(step_keys, parsed_edges)
    if step_keys and order is None:
        _error(
            errors,
            path="edges",
            code="workflow_cycle",
            message="工作流依赖图必须是 DAG",
        )
    elif len(step_keys) > 1:
        incoming = {key: 0 for key in step_keys}
        outgoing = {key: 0 for key in step_keys}
        for source, target in parsed_edges:
            outgoing[source] += 1
            incoming[target] += 1
        if sum(value == 0 for value in incoming.values()) != 1:
            _error(
                errors,
                path="edges",
                code="workflow_entry_not_unique",
                message="工作流必须有且只有一个入口步骤",
            )
        if sum(value == 0 for value in outgoing.values()) != 1:
            _error(
                errors,
                path="edges",
                code="workflow_terminal_not_unique",
                message="工作流必须有且只有一个终点步骤",
            )

    for schema_key in ("input_schema", "output_schema"):
        schema = manifest.get(schema_key)
        if not isinstance(schema, Mapping) or schema.get("type") != "object":
            _error(
                errors,
                path=schema_key,
                code="workflow_schema_invalid",
                message=f"{schema_key} 根类型必须为 object",
            )
    resource_policy = manifest.get("resource_policy")
    if not isinstance(resource_policy, Mapping):
        _error(
            errors,
            path="resource_policy",
            code="resource_policy_invalid",
            message="resource_policy 必须是对象",
        )

    deduplicated: list[ValidationErrorItem] = []
    seen_errors: set[tuple[str, str]] = set()
    for item in errors:
        key = (item.path, item.code)
        if key not in seen_errors:
            seen_errors.add(key)
            deduplicated.append(item)
    return ValidationReport(ok=not deduplicated, errors=tuple(deduplicated))


def create_workflow_definition(
    db: Session,
    *,
    workflow_key: str,
    name: str,
    description: str,
    owner: str,
    allowed_categories: list[str],
    created_by: str,
) -> WorkflowDefinition:
    existing = db.scalar(
        select(WorkflowDefinition).where(
            WorkflowDefinition.workflow_key == workflow_key
        )
    )
    if existing is not None:
        raise WorkflowRegistryError(
            "workflow_definition_duplicate",
            "工作流定义键已存在",
            status_code=409,
        )
    row = WorkflowDefinition(
        workflow_key=workflow_key,
        name=name,
        description=description,
        owner=owner,
        allowed_categories_json=canonical_json(allowed_categories),
        status="active",
        created_by=created_by,
    )
    db.add(row)
    db.flush()
    return row


def create_workflow_version(
    db: Session,
    *,
    definition: WorkflowDefinition,
    version: str,
    manifest: Mapping[str, Any],
    created_by: str,
) -> WorkflowVersion:
    if definition.status != "active":
        raise WorkflowRegistryError(
            "workflow_definition_retired",
            "已退休工作流定义不能创建新版本",
            status_code=409,
        )
    existing = db.scalar(
        select(WorkflowVersion).where(
            WorkflowVersion.workflow_definition_id == definition.id,
            WorkflowVersion.version == version,
        )
    )
    if existing is not None:
        raise WorkflowRegistryError(
            "workflow_version_duplicate",
            "工作流版本已存在",
            status_code=409,
        )
    report = validate_workflow_manifest(db, manifest)
    if not report.ok:
        raise WorkflowRegistryError(
            "workflow_manifest_invalid",
            canonical_json(report.as_dict()),
        )
    canonical_hash = hashlib.sha256(canonical_json(manifest).encode("utf-8")).hexdigest()
    row = WorkflowVersion(
        workflow_definition_id=definition.id,
        version=version,
        status="draft",
        workflow_schema_version="workflow-v1",
        step_manifest_json=canonical_json(manifest["steps"]),
        edge_manifest_json=canonical_json(manifest["edges"]),
        input_schema_json=canonical_json(manifest["input_schema"]),
        output_schema_json=canonical_json(manifest["output_schema"]),
        resource_policy_json=canonical_json(
            {
                **dict(manifest["resource_policy"]),
                "queue_class": manifest["queue_class"],
            }
        ),
        canonical_hash=canonical_hash,
        validation_report_json="{}",
        created_by=created_by,
    )
    db.add(row)
    db.flush()
    return row


def persisted_workflow_manifest(row: WorkflowVersion) -> dict[str, Any]:
    resource_policy = json.loads(row.resource_policy_json)
    queue_class = resource_policy.pop("queue_class", None)
    return {
        "schema_version": row.workflow_schema_version,
        "steps": json.loads(row.step_manifest_json),
        "edges": json.loads(row.edge_manifest_json),
        "queue_class": queue_class,
        "input_schema": json.loads(row.input_schema_json),
        "output_schema": json.loads(row.output_schema_json),
        "resource_policy": resource_policy,
    }


def transition_workflow_version(
    db: Session,
    version_id: int,
    target: str,
    *,
    actor: str,
) -> WorkflowVersion:
    row = db.get(WorkflowVersion, version_id)
    if row is None:
        raise WorkflowRegistryError(
            "workflow_version_not_found",
            "工作流版本不存在",
            status_code=404,
        )
    if target not in WORKFLOW_VERSION_STATUSES:
        raise WorkflowRegistryError("workflow_status_invalid", "未知工作流版本状态")
    if target not in ALLOWED_TRANSITIONS[row.status]:
        raise WorkflowRegistryError(
            "workflow_transition_invalid",
            f"工作流版本不能从 {row.status} 转为 {target}",
            status_code=409,
        )
    if target == "active":
        try:
            report = json.loads(row.validation_report_json)
        except json.JSONDecodeError as exc:
            raise WorkflowRegistryError(
                "workflow_validation_report_invalid",
                "工作流校验报告损坏",
                status_code=409,
            ) from exc
        if report.get("ok") is not True:
            raise WorkflowRegistryError(
                "workflow_validation_required",
                "工作流版本必须先通过校验",
                status_code=409,
            )
    row.status = target
    db.flush()
    return row


def canonical_workflow_snapshot(
    db: Session,
    workflow_version_id: int,
    runtime_context: Mapping[str, Any],
) -> tuple[dict[str, Any], str]:
    row = db.get(WorkflowVersion, workflow_version_id)
    if row is None:
        raise WorkflowRegistryError(
            "workflow_version_not_found",
            "工作流版本不存在",
            status_code=404,
        )
    manifest = persisted_workflow_manifest(row)
    scripts: dict[str, dict[str, Any]] = {}
    for step in manifest["steps"]:
        reference = step["script_version"]
        script_key, version_name = reference.rsplit("@", 1)
        definition = db.scalar(
            select(ScriptDefinition).where(
                ScriptDefinition.script_key == script_key
            )
        )
        if definition is None:
            raise WorkflowRegistryError(
                "script_definition_unknown",
                f"冻结快照时找不到脚本：{reference}",
                status_code=409,
            )
        version = db.scalar(
            select(ScriptVersion).where(
                ScriptVersion.script_definition_id == definition.id,
                ScriptVersion.version == version_name,
            )
        )
        if version is None:
            raise WorkflowRegistryError(
                "script_version_unknown",
                f"冻结快照时找不到脚本版本：{reference}",
                status_code=409,
            )
        scripts[reference] = {
            "script_key": script_key,
            "version": version_name,
            "artifact_sha256": version.artifact_sha256,
            "executor_kind": version.executor_kind,
            "manifest": json.loads(version.manifest_json),
            "input_schema": json.loads(version.input_schema_json),
            "output_schema": json.loads(version.output_schema_json),
            "timeout_seconds": version.timeout_seconds,
            "max_attempts": version.max_attempts,
            "retry_policy": json.loads(version.retry_policy_json),
            "concurrency_limit": version.concurrency_limit,
        }
    snapshot = {
        "schema_version": "production-run-snapshot-v1",
        "workflow": {
            "workflow_definition_id": row.workflow_definition_id,
            "workflow_version_id": row.id,
            "version": row.version,
            "canonical_hash": row.canonical_hash,
            "manifest": manifest,
        },
        "scripts": [scripts[key] for key in sorted(scripts)],
        "runtime_context": dict(runtime_context),
    }
    encoded = canonical_json(snapshot)
    return snapshot, hashlib.sha256(encoded.encode("utf-8")).hexdigest()

