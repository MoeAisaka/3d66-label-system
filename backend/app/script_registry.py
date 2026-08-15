from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Mapping

from sqlalchemy.orm import Session

from .models import ScriptVersion


SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
FORBIDDEN_EXECUTABLE_FIELDS = frozenset(
    {"source", "code", "command", "shell", "sql", "script"}
)
SCRIPT_VERSION_STATUSES = frozenset(
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


@dataclass(frozen=True)
class ValidationErrorItem:
    path: str
    code: str
    message: str


@dataclass(frozen=True)
class ValidationReport:
    ok: bool
    errors: tuple[ValidationErrorItem, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "errors": [
                {"path": item.path, "code": item.code, "message": item.message}
                for item in self.errors
            ],
        }


class ScriptRegistryError(ValueError):
    def __init__(self, code: str, message: str, *, status_code: int = 400):
        super().__init__(message)
        self.code = code
        self.status_code = status_code


def _walk_forbidden_fields(
    value: Any,
    *,
    path: str = "$",
) -> list[ValidationErrorItem]:
    errors: list[ValidationErrorItem] = []
    if isinstance(value, Mapping):
        for key, child in value.items():
            child_path = f"{path}.{key}"
            if str(key).lower() in FORBIDDEN_EXECUTABLE_FIELDS:
                errors.append(
                    ValidationErrorItem(
                        path=child_path,
                        code="arbitrary_code_field_forbidden",
                        message="受控脚本版本不能包含任意代码或命令字段",
                    )
                )
            errors.extend(_walk_forbidden_fields(child, path=child_path))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            errors.extend(_walk_forbidden_fields(child, path=f"{path}[{index}]"))
    return errors


def _mapping_field(
    payload: Mapping[str, Any],
    key: str,
    errors: list[ValidationErrorItem],
) -> Mapping[str, Any] | None:
    value = payload.get(key)
    if not isinstance(value, Mapping):
        errors.append(
            ValidationErrorItem(
                path=key,
                code="object_required",
                message=f"{key} 必须是对象",
            )
        )
        return None
    return value


def validate_script_version_payload(
    payload: Mapping[str, Any],
) -> ValidationReport:
    errors = _walk_forbidden_fields(payload)

    if payload.get("executor_kind") != "deterministic_fixture":
        errors.append(
            ValidationErrorItem(
                path="executor_kind",
                code="executor_kind_unsupported",
                message="本阶段只允许 deterministic_fixture 执行器",
            )
        )

    artifact_sha256 = payload.get("artifact_sha256")
    if not isinstance(artifact_sha256, str) or not SHA256_PATTERN.fullmatch(
        artifact_sha256
    ):
        errors.append(
            ValidationErrorItem(
                path="artifact_sha256",
                code="artifact_sha256_invalid",
                message="artifact_sha256 必须是 64 位小写十六进制",
            )
        )

    manifest = _mapping_field(payload, "manifest", errors)
    input_schema = _mapping_field(payload, "input_schema", errors)
    output_schema = _mapping_field(payload, "output_schema", errors)
    _mapping_field(payload, "retry_policy", errors)
    _mapping_field(payload, "estimated_cost", errors)

    for key, schema in (("input_schema", input_schema), ("output_schema", output_schema)):
        if schema is not None and schema.get("type") != "object":
            errors.append(
                ValidationErrorItem(
                    path=f"{key}.type",
                    code="schema_root_object_required",
                    message=f"{key} 根类型必须为 object",
                )
            )

    if manifest is not None and not manifest:
        errors.append(
            ValidationErrorItem(
                path="manifest",
                code="manifest_empty",
                message="manifest 不能为空",
            )
        )

    permissions = payload.get("required_permissions")
    if not isinstance(permissions, list) or any(
        not isinstance(item, str) or not item.strip() for item in permissions
    ):
        errors.append(
            ValidationErrorItem(
                path="required_permissions",
                code="permissions_invalid",
                message="required_permissions 必须是非空字符串数组或空数组",
            )
        )

    idempotency_template = payload.get("idempotency_template")
    if not isinstance(idempotency_template, str) or not idempotency_template.strip():
        errors.append(
            ValidationErrorItem(
                path="idempotency_template",
                code="idempotency_template_required",
                message="必须声明幂等键模板",
            )
        )

    for key, minimum, maximum in (
        ("timeout_seconds", 1, 3600),
        ("max_attempts", 1, 5),
        ("concurrency_limit", 1, 1000),
    ):
        value = payload.get(key)
        if not isinstance(value, int) or isinstance(value, bool) or not (
            minimum <= value <= maximum
        ):
            errors.append(
                ValidationErrorItem(
                    path=key,
                    code=f"{key}_out_of_range",
                    message=f"{key} 必须位于 {minimum}..{maximum}",
                )
            )

    deduplicated: list[ValidationErrorItem] = []
    seen: set[tuple[str, str]] = set()
    for item in errors:
        key = (item.path, item.code)
        if key not in seen:
            seen.add(key)
            deduplicated.append(item)
    return ValidationReport(ok=not deduplicated, errors=tuple(deduplicated))


def persisted_script_payload(version: ScriptVersion) -> dict[str, Any]:
    return {
        "display_name": version.display_name,
        "executor_kind": version.executor_kind,
        "artifact_sha256": version.artifact_sha256,
        "manifest": json.loads(version.manifest_json),
        "input_schema": json.loads(version.input_schema_json),
        "output_schema": json.loads(version.output_schema_json),
        "required_permissions": json.loads(version.required_permissions_json),
        "idempotency_template": version.idempotency_template,
        "timeout_seconds": version.timeout_seconds,
        "max_attempts": version.max_attempts,
        "retry_policy": json.loads(version.retry_policy_json),
        "concurrency_limit": version.concurrency_limit,
        "rate_limit_key": version.rate_limit_key,
        "estimated_cost": json.loads(version.estimated_cost_json),
    }


def transition_script_version(
    db: Session,
    version_id: int,
    target: str,
    *,
    actor: str,
) -> ScriptVersion:
    version = db.get(ScriptVersion, version_id)
    if version is None:
        raise ScriptRegistryError(
            "script_version_not_found",
            "脚本版本不存在",
            status_code=404,
        )
    if target not in SCRIPT_VERSION_STATUSES:
        raise ScriptRegistryError(
            "script_status_invalid",
            "未知脚本版本状态",
        )
    allowed = ALLOWED_TRANSITIONS[version.status]
    if target not in allowed:
        raise ScriptRegistryError(
            "script_transition_invalid",
            f"脚本版本不能从 {version.status} 转为 {target}",
            status_code=409,
        )
    if target == "active":
        try:
            report = json.loads(version.validation_report_json)
        except json.JSONDecodeError as exc:
            raise ScriptRegistryError(
                "script_validation_report_invalid",
                "脚本版本校验报告损坏",
                status_code=409,
            ) from exc
        if report.get("ok") is not True:
            raise ScriptRegistryError(
                "script_validation_required",
                "脚本版本必须先通过校验",
                status_code=409,
            )
    version.status = target
    if target != "blocked":
        version.blocked_reason = ""
    db.flush()
    return version

