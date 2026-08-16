from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Mapping

from sqlalchemy import select

from .audit import canonical_json
from .database import SessionLocal
from .models import (
    ModelConfig,
    ProductionStepAttempt,
    QueueSchedulerState,
    ScriptVersion,
)
from .workflow_runtime import (
    claim_next_runtime_step,
    complete_runtime_step,
    fail_runtime_step,
    recover_expired_runtime_steps,
)


FORBIDDEN_FIELDS = frozenset({"source", "code", "command", "shell", "sql", "script"})


class FixtureExecutionError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class FixtureResult:
    output_manifest: dict[str, Any]
    output_hash: str


def hash_manifest(value: Mapping[str, Any]) -> str:
    import hashlib

    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _reject_forbidden(value: Any, *, path: str = "$") -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            if str(key).lower() in FORBIDDEN_FIELDS:
                raise FixtureExecutionError(
                    "arbitrary_code_field_forbidden",
                    f"fixture manifest 包含禁止字段：{path}.{key}",
                )
            _reject_forbidden(child, path=f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _reject_forbidden(child, path=f"{path}[{index}]")


def _lookup_path(input_manifest: Mapping[str, Any], path: str) -> Any:
    current: Any = input_manifest
    for part in path.split("."):
        if not isinstance(current, Mapping) or part not in current:
            raise FixtureExecutionError(
                "fixture_input_path_missing",
                f"fixture 输入字段不存在：{path}",
            )
        current = current[part]
    return current


def execute_fixture(
    kind: str,
    input_manifest: Mapping[str, Any],
    manifest: Mapping[str, Any],
    *,
    attempt_no: int,
) -> FixtureResult:
    _reject_forbidden(manifest)
    fixture_kind = manifest.get("fixture", kind)
    if fixture_kind != kind:
        raise FixtureExecutionError(
            "fixture_kind_mismatch",
            "步骤执行器与脚本 manifest 的 fixture 类型不一致",
        )
    if kind not in {"identity", "transform", "route", "noop", "fail_once"}:
        raise FixtureExecutionError(
            "fixture_kind_unsupported",
            f"未知 deterministic fixture：{kind}",
        )

    if kind == "identity":
        output = dict(input_manifest)
    elif kind == "transform":
        mapping = manifest.get("mapping")
        if not isinstance(mapping, Mapping):
            raise FixtureExecutionError(
                "fixture_mapping_required",
                "transform fixture 必须声明 mapping 对象",
            )
        output: dict[str, Any] = {}
        for output_key, source in mapping.items():
            if not isinstance(output_key, str) or not output_key.strip():
                raise FixtureExecutionError(
                    "fixture_mapping_key_invalid",
                    "transform mapping 的输出字段必须是非空字符串",
                )
            if isinstance(source, str):
                output[output_key] = _lookup_path(input_manifest, source)
            else:
                output[output_key] = source
    elif kind == "route":
        branch_field = manifest.get("branch_field", "branch")
        branch = _lookup_path(input_manifest, branch_field)
        branches = manifest.get("branches", {})
        if not isinstance(branches, Mapping):
            raise FixtureExecutionError(
                "fixture_branches_required",
                "route fixture 必须声明 branches 对象",
            )
        output = {
            "branch": branch,
            "target": branches.get(str(branch), manifest.get("default_target")),
        }
    elif kind == "fail_once":
        if attempt_no == 1:
            raise FixtureExecutionError(
                "FIXTURE_FAIL_ONCE",
                "确定性 fixture 首次尝试失败，用于验证恢复",
            )
        output = dict(input_manifest)
        output["fixture"] = "fail_once"
    else:
        output = {
            "fixture": "noop",
            "input_hash": hash_manifest(input_manifest),
        }

    return FixtureResult(output_manifest=output, output_hash=hash_manifest(output))


def process_runtime_step_once(
    worker_id: str,
    *,
    global_limit: int | None = None,
) -> bool:
    claim_db = SessionLocal()
    try:
        resolved_limit = global_limit
        if resolved_limit is None:
            scheduler_state = claim_db.get(QueueSchedulerState, 1)
            if scheduler_state is not None:
                resolved_limit = scheduler_state.global_limit
            else:
                model = claim_db.scalar(
                    select(ModelConfig)
                    .where(ModelConfig.active.is_(True))
                    .order_by(ModelConfig.id.asc())
                )
                resolved_limit = model.max_concurrency if model is not None else 1
        attempt_id = claim_next_runtime_step(
            claim_db,
            worker_id,
            global_limit=max(1, resolved_limit),
        )
        claim_db.commit()
        if attempt_id is None:
            return False
        claimed = claim_db.get(ProductionStepAttempt, attempt_id)
        if claimed is None or not claimed.lease_token:
            return False
        lease_token = claimed.lease_token
        input_manifest = json.loads(claimed.input_manifest_json)
        script = claim_db.get(ScriptVersion, claimed.script_version_id)
        if script is None:
            return False
        script_manifest = json.loads(script.manifest_json)
        attempt_no = claimed.attempt_no
    finally:
        claim_db.close()

    try:
        fixture_kind = str(script_manifest.get("fixture", claimed.step_type))
        result = execute_fixture(
            fixture_kind,
            input_manifest,
            script_manifest,
            attempt_no=attempt_no,
        )
    except FixtureExecutionError as exc:
        failure_db = SessionLocal()
        try:
            fail_runtime_step(
                failure_db,
                attempt_id,
                lease_token,
                exc.code,
                str(exc),
                retryable=True,
            )
            failure_db.commit()
        finally:
            failure_db.close()
        return True

    complete_db = SessionLocal()
    try:
        complete_runtime_step(
            complete_db,
            attempt_id,
            lease_token,
            result.output_manifest,
        )
        complete_db.commit()
    finally:
        complete_db.close()
    return True


def recover_runtime_once() -> int:
    db = SessionLocal()
    try:
        recovered = recover_expired_runtime_steps(db)
        db.commit()
        return recovered
    finally:
        db.close()
