"""Fact-driven orchestration for producing immutable EvaluationPackages."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Callable, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .audit import append_audit_event
from .correction_contract import (
    correction_contract_hash,
    freeze_contract_from_execution_snapshot,
)
from .database import get_db
from .evaluation_packages import (
    EvaluationPackageCreateRequest,
    TERMINAL_REGRESSION_STATUSES,
    create_evaluation_package,
)
from .models import (
    AutomationOptimizationRun,
    AutomationPolicy,
    EvaluationJob,
    EvaluationPackage,
    EvaluationProductionRun,
    EvaluationResult,
    MaterialPackage,
    OptimizationCaseQueue,
    PromptRegressionRun,
    ReviewPanel,
    User,
)
from .optimization_automation import (
    automation_lifecycle_status,
    configured_optimization_adapter,
)
from .regression import reconcile_automation_review_states


PRODUCTION_STATUSES = (
    "preparing",
    "queued",
    "evaluating",
    "first_review",
    "optimizing",
    "regressing",
    "awaiting_review",
    "approved",
    "rejected",
    "published",
    "blocked",
    "failed",
    "archived",
)
FINAL_PACKAGE_STATUS_MAP = {
    "validating": ("regressing", "regression"),
    "awaiting_review": ("awaiting_review", "second_review"),
    "approved": ("approved", "release"),
    "rejected": ("rejected", "second_review"),
    "published": ("published", "release"),
    "archived": ("archived", "release"),
}

EnqueueCallback = Callable[[Session, list[int], str], dict[str, Any]]


class EvaluationProductionRunCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    material_package_id: int = Field(ge=1)
    category_key: str = Field(pattern=r"^[a-z][a-z0-9_]{2,39}$")
    workflow_kind: Literal["incremental", "stock"] = "incremental"
    idempotency_key: str = Field(min_length=1, max_length=160)

    @field_validator("idempotency_key")
    @classmethod
    def normalize_key(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("幂等键不得为空白")
        return normalized


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _loads_object(value: str | None) -> dict[str, Any]:
    try:
        parsed = json.loads(value or "{}")
    except (TypeError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _loads_list(value: str | None) -> list[Any]:
    try:
        parsed = json.loads(value or "[]")
    except (TypeError, json.JSONDecodeError):
        return []
    return parsed if isinstance(parsed, list) else []


def _request_hash(payload: EvaluationProductionRunCreateRequest) -> str:
    definition = {
        "schema_version": "evaluation-production-request-v1",
        "material_package_id": payload.material_package_id,
        "category_key": payload.category_key,
        "workflow_kind": payload.workflow_kind,
        "idempotency_key": payload.idempotency_key,
    }
    return hashlib.sha256(_canonical_json(definition).encode("utf-8")).hexdigest()


def _validate_frozen_configuration(snapshot_json: str, category_key: str) -> dict[str, Any]:
    snapshot = _loads_object(snapshot_json)
    pipeline = snapshot.get("pipeline_config")
    allowed_mimes = snapshot.get("allowed_mime_types")
    if (
        snapshot.get("schema_version") != "evaluation-category-profile-v2"
        or snapshot.get("category_key") != category_key
        or not isinstance(snapshot.get("profile_id"), int)
        or not isinstance(snapshot.get("prompt_a_id"), int)
        or not isinstance(snapshot.get("model_config_id"), int)
        or not isinstance(allowed_mimes, list)
        or not allowed_mimes
        or not isinstance(pipeline, dict)
        or pipeline.get("schema_version") != "category-pipeline-v1"
        or not str(snapshot.get("rubric_version") or "").strip()
    ):
        raise HTTPException(status_code=409, detail="类目冻结方案不完整，暂不能开始评测")
    prompt_mode = pipeline.get("prompt_mode")
    if prompt_mode == "ab" and not isinstance(snapshot.get("prompt_b_id"), int):
        raise HTTPException(status_code=409, detail="类目冻结方案缺少 B 阶段评测定义")
    if prompt_mode == "single" and snapshot.get("prompt_b_id") is not None:
        raise HTTPException(status_code=409, detail="类目冻结方案的单次评测定义不一致")
    return snapshot


def _active_package_asset_ids(package: MaterialPackage) -> list[int]:
    asset_ids: list[int] = []
    seen: set[int] = set()
    for item in package.items:
        if item.asset.status == "deleted" or item.asset_id in seen:
            continue
        seen.add(item.asset_id)
        asset_ids.append(item.asset_id)
    return asset_ids


def _batch_jobs(db: Session, run: EvaluationProductionRun) -> tuple[list[EvaluationJob], list[EvaluationJob]]:
    jobs = db.scalars(
        select(EvaluationJob)
        .where(EvaluationJob.batch_key == run.batch_key)
        .order_by(EvaluationJob.technical_attempt.asc(), EvaluationJob.id.asc())
    ).all()
    latest_by_root: dict[int, EvaluationJob] = {}
    for job in jobs:
        root_id = job.root_job_id or job.id
        previous = latest_by_root.get(root_id)
        if previous is None or (job.technical_attempt, job.id) > (
            previous.technical_attempt,
            previous.id,
        ):
            latest_by_root[root_id] = job
    return jobs, list(latest_by_root.values())


def _job_facts(db: Session, run: EvaluationProductionRun) -> dict[str, Any]:
    jobs, latest = _batch_jobs(db, run)
    counts = {
        "total": len(latest),
        "queued": 0,
        "processing": 0,
        "completed": 0,
        "failed": 0,
    }
    for job in latest:
        if job.status in {"queued", "paused"}:
            counts["queued"] += 1
        elif job.status == "completed":
            counts["completed"] += 1
        elif job.status in {"failed", "canceled", "cancelled"}:
            counts["failed"] += 1
        else:
            counts["processing"] += 1

    results = db.scalars(
        select(EvaluationResult)
        .where(EvaluationResult.job_id.in_([job.id for job in jobs] or [-1]))
        .order_by(EvaluationResult.id.asc())
    ).all()
    job_by_id = {job.id: job for job in jobs}
    result_by_root: dict[int, EvaluationResult] = {}
    for result in results:
        job = job_by_id.get(result.job_id)
        if job is not None:
            result_by_root[job.root_job_id or job.id] = result
    result_rows = list(result_by_root.values())
    result_ids = [result.id for result in result_rows]
    panels = db.scalars(
        select(ReviewPanel).where(
            ReviewPanel.evaluation_id.in_(result_ids or [-1])
        )
    ).all()
    panel_by_evaluation = {panel.evaluation_id: panel for panel in panels}
    pending_review_ids: list[int] = []
    for result in result_rows:
        panel = panel_by_evaluation.get(result.id)
        if panel is not None and panel.status != "completed":
            pending_review_ids.append(result.id)
        elif result.needs_review and result.review_stage != "completed":
            pending_review_ids.append(result.id)
    return {
        "jobs": jobs,
        "latest_jobs": latest,
        "job_ids": [job.id for job in jobs],
        "counts": counts,
        "results": result_rows,
        "result_ids": result_ids,
        "panels": panels,
        "pending_review_ids": pending_review_ids,
    }


def _blocker(
    code: str,
    title: str,
    message: str,
    *,
    action_label: str,
    action_href: str,
    api_path: str | None = None,
    api_method: str | None = None,
) -> dict[str, Any]:
    return {
        "code": code,
        "title": title,
        "message": message,
        "fix": {
            "label": action_label,
            "href": action_href,
            "api_path": api_path,
            "api_method": api_method,
        },
    }


def _automation_result_ids(automation: AutomationOptimizationRun) -> list[int]:
    result = _loads_object(automation.result_json)
    raw_ids = result.get("regression_ids")
    if not isinstance(raw_ids, list) or not all(
        isinstance(value, int) and value > 0 for value in raw_ids
    ):
        return []
    return list(dict.fromkeys(raw_ids))


def _persist_facts(
    db: Session,
    run: EvaluationProductionRun,
    *,
    status: str,
    stage: str,
    blockers: list[dict[str, Any]],
    actor: str,
    error_code: str = "",
    error_message: str = "",
    job_ids: list[int] | None = None,
) -> None:
    now = datetime.now(timezone.utc)
    blockers_json = _canonical_json(blockers)
    job_ids_json = _canonical_json(job_ids) if job_ids is not None else run.job_ids_json
    changed = any(
        (
            run.status != status,
            run.current_stage != stage,
            run.blockers_json != blockers_json,
            run.error_code != error_code,
            run.error_message != error_message,
            run.job_ids_json != job_ids_json,
        )
    )
    previous_status = run.status
    run.status = status
    run.current_stage = stage
    run.blockers_json = blockers_json
    run.error_code = error_code
    run.error_message = error_message
    run.job_ids_json = job_ids_json
    run.last_reconciled_at = now
    run.updated_at = now
    if status in {"rejected", "published", "archived"} and run.finished_at is None:
        run.finished_at = now
    if status == "archived" and run.archived_at is None:
        run.archived_at = now
    if changed:
        run.audit_revision += 1
        append_audit_event(
            db,
            category="evaluation_production",
            action="reconciled",
            subject_type="evaluation_production_run",
            subject_id=run.id,
            actor=actor,
            payload={
                "revision": run.audit_revision,
                "from_status": previous_status,
                "to_status": status,
                "current_stage": stage,
                "blocker_codes": [item["code"] for item in blockers],
                "automation_run_id": run.automation_run_id,
                "regression_run_id": run.regression_run_id,
                "evaluation_package_id": run.evaluation_package_id,
            },
            event_key=f"evaluation-production:{run.id}:revision:{run.audit_revision}",
        )


def _mirror_final_package(
    db: Session,
    run: EvaluationProductionRun,
    package: EvaluationPackage,
    *,
    actor: str,
    job_ids: list[int],
) -> None:
    status, stage = FINAL_PACKAGE_STATUS_MAP.get(
        package.status, ("blocked", "second_review")
    )
    blockers: list[dict[str, Any]] = []
    if package.status == "validating":
        blockers.append(
            _blocker(
                "final_package_still_validating",
                "回归证据仍在收口",
                "最终评测包尚未进入二审，系统不会提前批准或发布。",
                action_label="查看回归",
                action_href="/workflow/optimization/paired-regression",
            )
        )
    _persist_facts(
        db,
        run,
        status=status,
        stage=stage,
        blockers=blockers,
        actor=actor,
        job_ids=job_ids,
    )


def reconcile_production_run(
    db: Session,
    run: EvaluationProductionRun,
    *,
    actor: str,
) -> EvaluationProductionRun:
    job_facts = _job_facts(db, run)
    job_ids = job_facts["job_ids"]
    if run.evaluation_package_id is not None:
        package = db.get(EvaluationPackage, run.evaluation_package_id)
        if package is None:
            _persist_facts(
                db,
                run,
                status="failed",
                stage="second_review",
                blockers=[
                    _blocker(
                        "final_package_missing",
                        "最终评测包记录缺失",
                        "关联记录无法读取，请由管理员检查审计记录。",
                        action_label="查看审计",
                        action_href="/workflow/governance/audit",
                    )
                ],
                actor=actor,
                error_code="final_package_missing",
                error_message="最终评测包关联记录缺失",
                job_ids=job_ids,
            )
        else:
            _mirror_final_package(db, run, package, actor=actor, job_ids=job_ids)
        db.commit()
        db.refresh(run)
        return run

    counts = job_facts["counts"]
    if counts["failed"]:
        _persist_facts(
            db,
            run,
            status="failed",
            stage="evaluation",
            blockers=[
                _blocker(
                    "evaluation_jobs_failed",
                    "部分素材评测未完成",
                    "系统已保留成功结果和失败记录；请在任务页处理后再刷新。",
                    action_label="打开评测任务",
                    action_href="/workflow/materials/jobs",
                )
            ],
            actor=actor,
            error_code="evaluation_jobs_failed",
            error_message="部分评测任务已失败或取消",
            job_ids=job_ids,
        )
        db.commit()
        db.refresh(run)
        return run
    if counts["total"] == 0:
        _persist_facts(
            db,
            run,
            status="failed",
            stage="evaluation",
            blockers=[
                _blocker(
                    "evaluation_jobs_missing",
                    "评测任务记录缺失",
                    "本次生产记录没有找到对应任务，请由管理员检查审计记录。",
                    action_label="查看审计",
                    action_href="/workflow/governance/audit",
                )
            ],
            actor=actor,
            error_code="evaluation_jobs_missing",
            error_message="评测任务记录缺失",
            job_ids=job_ids,
        )
        db.commit()
        db.refresh(run)
        return run
    if counts["completed"] < counts["total"]:
        status = "queued" if counts["completed"] == 0 and counts["processing"] == 0 else "evaluating"
        _persist_facts(
            db,
            run,
            status=status,
            stage="evaluation",
            blockers=[],
            actor=actor,
            job_ids=job_ids,
        )
        db.commit()
        db.refresh(run)
        return run
    if len(job_facts["results"]) != counts["total"]:
        _persist_facts(
            db,
            run,
            status="failed",
            stage="evaluation",
            blockers=[
                _blocker(
                    "evaluation_results_missing",
                    "评测结果尚未完整落库",
                    "任务已经结束但结果数量不一致，请在任务页检查。",
                    action_label="打开评测任务",
                    action_href="/workflow/materials/jobs",
                )
            ],
            actor=actor,
            error_code="evaluation_results_missing",
            error_message="完成任务与评测结果数量不一致",
            job_ids=job_ids,
        )
        db.commit()
        db.refresh(run)
        return run
    if job_facts["pending_review_ids"]:
        _persist_facts(
            db,
            run,
            status="first_review",
            stage="first_review",
            blockers=[],
            actor=actor,
            job_ids=job_ids,
        )
        db.commit()
        db.refresh(run)
        return run

    cases = db.scalars(
        select(OptimizationCaseQueue)
        .where(
            OptimizationCaseQueue.source_type == "human_review",
            OptimizationCaseQueue.category_key == run.category_key,
            OptimizationCaseQueue.evaluation_id.in_(job_facts["result_ids"] or [-1]),
        )
        .order_by(OptimizationCaseQueue.id.asc())
    ).all()
    if not cases:
        _persist_facts(
            db,
            run,
            status="blocked",
            stage="optimization",
            blockers=[
                _blocker(
                    "no_correction_for_optimization",
                    "没有可用于自动改进的纠偏",
                    "一审没有形成纠偏案例，现有链路无法据此生成新版候选；系统不会伪造回归或最终包。",
                    action_label="查看一审结果",
                    action_href="/workflow/review/completed",
                )
            ],
            actor=actor,
            job_ids=job_ids,
        )
        db.commit()
        db.refresh(run)
        return run

    case_run_ids = sorted(
        {
            case.automation_run_id
            for case in cases
            if case.automation_run_id is not None
        }
    )
    if run.automation_run_id is None and len(case_run_ids) == 1:
        run.automation_run_id = case_run_ids[0]
    elif run.automation_run_id is None and len(case_run_ids) > 1:
        _persist_facts(
            db,
            run,
            status="blocked",
            stage="optimization",
            blockers=[
                _blocker(
                    "multiple_automation_runs",
                    "纠偏被分入多个自动改进批次",
                    "当前生产记录无法自动选择唯一候选，请在候选页人工确认。",
                    action_label="查看候选",
                    action_href="/workflow/optimization/candidates",
                )
            ],
            actor=actor,
            job_ids=job_ids,
        )
        db.commit()
        db.refresh(run)
        return run

    if run.automation_run_id is None:
        policy = db.get(AutomationPolicy, 1)
        blockers: list[dict[str, Any]] = []
        if policy is None or not policy.enabled:
            blockers.append(
                _blocker(
                    "automation_disabled",
                    "自动改进尚未开启",
                    "纠偏案例已经保存；开启自动改进后可从原位置继续。",
                    action_label="打开自动改进",
                    action_href="/workflow/optimization/automation",
                    api_path="/api/automation-runs/consume",
                    api_method="POST",
                )
            )
        elif policy.dry_run:
            blockers.append(
                _blocker(
                    "automation_dry_run",
                    "自动改进仍处于安全试跑",
                    "安全试跑不会生成真实候选或回归，请由管理员完成启用检查。",
                    action_label="打开自动改进",
                    action_href="/workflow/optimization/automation",
                )
            )
        else:
            adapter = configured_optimization_adapter(db, category_key=run.category_key)
            if adapter is None:
                blockers.append(
                    _blocker(
                        "optimizer_configuration_incomplete",
                        "自动改进方案尚未就绪",
                        "优化模型配置不完整，请由管理员补齐后继续。",
                        action_label="打开自动改进",
                        action_href="/workflow/optimization/automation",
                    )
                )
            else:
                try:
                    adapter.bind_base_prompt(db, version=cases[0].prompt_version)
                    adapter.prepare_regression_binding(
                        db,
                        base_prompt=adapter.base_prompt,
                        category_key=run.category_key,
                    )
                except ValueError:
                    blockers.append(
                        _blocker(
                            "regression_binding_missing",
                            "黄金样本回归方案尚未就绪",
                            "缺少同类目的锁定黄金样本与三类验证分组，不能生成可二审候选。",
                            action_label="查看黄金样本与回归",
                            action_href="/workflow/optimization/paired-regression",
                        )
                    )
        _persist_facts(
            db,
            run,
            status="blocked" if blockers else "optimizing",
            stage="optimization",
            blockers=blockers,
            actor=actor,
            job_ids=job_ids,
        )
        db.commit()
        db.refresh(run)
        return run

    automation = db.get(AutomationOptimizationRun, run.automation_run_id)
    if automation is None:
        _persist_facts(
            db,
            run,
            status="failed",
            stage="optimization",
            blockers=[
                _blocker(
                    "automation_run_missing",
                    "自动改进记录缺失",
                    "关联批次无法读取，请由管理员检查审计记录。",
                    action_label="查看审计",
                    action_href="/workflow/governance/audit",
                )
            ],
            actor=actor,
            error_code="automation_run_missing",
            error_message="自动改进关联记录缺失",
            job_ids=job_ids,
        )
        db.commit()
        db.refresh(run)
        return run
    if automation.category_key != run.category_key:
        _persist_facts(
            db,
            run,
            status="failed",
            stage="optimization",
            blockers=[
                _blocker(
                    "automation_category_mismatch",
                    "自动改进批次类目冲突",
                    "关联的自动改进批次属于其他类目，系统已停止继续生产。",
                    action_label="查看审计",
                    action_href="/workflow/governance/audit",
                )
            ],
            actor=actor,
            error_code="automation_category_mismatch",
            error_message="自动改进批次与生产记录类目不一致",
            job_ids=job_ids,
        )
        db.commit()
        db.refresh(run)
        return run
    if automation.dry_run or automation.status == "planned":
        _persist_facts(
            db,
            run,
            status="blocked",
            stage="optimization",
            blockers=[
                _blocker(
                    "automation_not_real",
                    "本次只完成了安全试跑",
                    "安全试跑不产生真实候选，不能进入回归或创建最终评测包。",
                    action_label="打开自动改进",
                    action_href="/workflow/optimization/automation",
                )
            ],
            actor=actor,
            job_ids=job_ids,
        )
        db.commit()
        db.refresh(run)
        return run
    if automation.status in {"failed", "cancelled"}:
        _persist_facts(
            db,
            run,
            status="failed",
            stage="optimization",
            blockers=[
                _blocker(
                    "automation_failed",
                    "自动改进未完成",
                    "纠偏与失败记录已经保留，请在自动改进页处理后继续。",
                    action_label="打开自动改进",
                    action_href="/workflow/optimization/automation",
                )
            ],
            actor=actor,
            error_code="automation_failed",
            error_message="自动改进执行失败",
            job_ids=job_ids,
        )
        db.commit()
        db.refresh(run)
        return run
    if automation.status in {"awaiting_executor", "processing"}:
        _persist_facts(
            db,
            run,
            status="optimizing",
            stage="optimization",
            blockers=[],
            actor=actor,
            job_ids=job_ids,
        )
        db.commit()
        db.refresh(run)
        return run

    regression_ids = _automation_result_ids(automation)
    if len(regression_ids) != 1:
        _persist_facts(
            db,
            run,
            status="blocked",
            stage="regression",
            blockers=[
                _blocker(
                    "regression_selection_required",
                    "无法自动确定唯一回归",
                    "自动改进没有生成唯一候选回归，请在候选页核对。",
                    action_label="查看候选",
                    action_href="/workflow/optimization/candidates",
                )
            ],
            actor=actor,
            job_ids=job_ids,
        )
        db.commit()
        db.refresh(run)
        return run
    if run.regression_run_id is None:
        run.regression_run_id = regression_ids[0]
    elif run.regression_run_id != regression_ids[0]:
        _persist_facts(
            db,
            run,
            status="failed",
            stage="regression",
            blockers=[
                _blocker(
                    "regression_identity_changed",
                    "回归关联发生冲突",
                    "已冻结的回归身份与自动改进结果不一致，请检查审计记录。",
                    action_label="查看审计",
                    action_href="/workflow/governance/audit",
                )
            ],
            actor=actor,
            error_code="regression_identity_changed",
            error_message="回归身份关联冲突",
            job_ids=job_ids,
        )
        db.commit()
        db.refresh(run)
        return run

    regression = db.get(PromptRegressionRun, run.regression_run_id)
    if regression is None:
        _persist_facts(
            db,
            run,
            status="failed",
            stage="regression",
            blockers=[
                _blocker(
                    "regression_missing",
                    "回归记录缺失",
                    "关联回归无法读取，请由管理员检查审计记录。",
                    action_label="查看审计",
                    action_href="/workflow/governance/audit",
                )
            ],
            actor=actor,
            error_code="regression_missing",
            error_message="回归关联记录缺失",
            job_ids=job_ids,
        )
        db.commit()
        db.refresh(run)
        return run
    if regression.status not in TERMINAL_REGRESSION_STATUSES:
        _persist_facts(
            db,
            run,
            status="regressing",
            stage="regression",
            blockers=[],
            actor=actor,
            job_ids=job_ids,
        )
        db.commit()
        db.refresh(run)
        return run

    reconcile_automation_review_states(db)
    db.flush()
    try:
        package, _duplicate = create_evaluation_package(
            db,
            payload=EvaluationPackageCreateRequest(
                package_key=(
                    f"production-run:{run.id}:regression:{regression.id}"
                ),
                category_key=run.category_key,
                regression_run_id=regression.id,
                automation_run_id=automation.id,
            ),
            actor="evaluation-production-reconcile",
        )
    except HTTPException:
        db.rollback()
        run = db.get(EvaluationProductionRun, run.id)
        if run is None:
            raise
        run.automation_run_id = automation.id
        run.regression_run_id = regression.id
        _persist_facts(
            db,
            run,
            status="blocked",
            stage="regression",
            blockers=[
                _blocker(
                    "final_package_evidence_incomplete",
                    "最终包证据尚未齐备",
                    "真实回归已经结束，但冻结最终包所需的黄金集或候选身份仍不完整。",
                    action_label="查看回归",
                    action_href="/workflow/optimization/paired-regression",
                )
            ],
            actor=actor,
            job_ids=job_ids,
        )
        db.commit()
        db.refresh(run)
        return run

    run = db.get(EvaluationProductionRun, run.id)
    if run is None:
        raise RuntimeError("评测生产记录在最终包创建后丢失")
    run.evaluation_package_id = package.id
    _mirror_final_package(db, run, package, actor=actor, job_ids=job_ids)
    db.commit()
    db.refresh(run)
    return run


def _stage_label(stage: str) -> str:
    return {
        "preparing": "准备运行",
        "evaluation": "模型评测",
        "first_review": "一审纠偏",
        "optimization": "自动改进",
        "regression": "黄金集回归",
        "second_review": "二审决定",
        "release": "人工发布",
    }.get(stage, "核对进度")


def _progress(run: EvaluationProductionRun, facts: dict[str, Any]) -> dict[str, Any]:
    counts = facts["counts"]
    total = counts["total"]
    completed = counts["completed"]
    evaluation_ratio = completed / total if total else 0.0
    percent = {
        "preparing": 2,
        "evaluation": round(5 + evaluation_ratio * 30),
        "first_review": 45,
        "optimization": 62,
        "regression": 82,
        "second_review": 92,
        "release": 96,
    }.get(run.current_stage, 0)
    if run.status == "published":
        percent = 100
    elif run.status in {"rejected", "archived"}:
        percent = max(percent, 92)
    return {
        "percent": max(0, min(100, percent)),
        "current_step": _stage_label(run.current_stage),
        "completed_jobs": completed,
        "total_jobs": total,
    }


def _timeline(run: EvaluationProductionRun, facts: dict[str, Any]) -> list[dict[str, Any]]:
    definitions = [
        ("queued", "进入评测队列", "preparing"),
        ("evaluation", "模型评测", "evaluation"),
        ("first_review", "一审纠偏", "first_review"),
        ("optimization", "自动改进", "optimization"),
        ("regression", "黄金集回归", "regression"),
        ("second_review", "最终包二审", "second_review"),
        ("release", "人工发布", "release"),
    ]
    order = {stage: index for index, (_key, _label, stage) in enumerate(definitions)}
    active = order.get(run.current_stage, 0)
    job_times = [job.finished_at for job in facts["latest_jobs"] if job.finished_at]
    panel_times = [panel.completed_at for panel in facts["panels"] if panel.completed_at]
    automation = run.automation_run
    regression = run.regression_run
    package = run.evaluation_package
    completed_times = {
        "queued": run.created_at,
        "evaluation": max(job_times, default=None),
        "first_review": max(panel_times, default=None),
        "optimization": automation.finished_at if automation else None,
        "regression": regression.finished_at if regression else None,
        "second_review": package.reviewed_at if package else None,
        "release": package.published_at if package else None,
    }
    rows: list[dict[str, Any]] = []
    for index, (key, label, stage) in enumerate(definitions):
        if index < active:
            state = "completed"
        elif index > active:
            state = "pending"
        elif run.status == "failed":
            state = "failed"
        elif run.status in {"blocked", "rejected", "archived"}:
            state = "blocked"
        elif run.status == "published" and key == "release":
            state = "completed"
        else:
            state = "current"
        rows.append(
            {
                "key": key,
                "label": label,
                "status": state,
                "completed_at": completed_times.get(key),
            }
        )
    return rows


def _next_step(run: EvaluationProductionRun, blockers: list[dict[str, Any]]) -> str:
    if blockers:
        return blockers[0]["message"]
    return {
        "queued": "评测任务已经排队，系统会自动开始处理。",
        "evaluating": "模型正在处理素材，完成后会自动汇总需要一审的结果。",
        "first_review": "请完成待一审结果；最终纠偏会自动进入改进队列。",
        "optimizing": "纠偏已经进入自动改进，系统会继续生成候选。",
        "regressing": "候选正在用锁定黄金样本回归，完成后才会冻结最终包。",
        "awaiting_review": "最终评测包已经冻结，请二审人员查看证据并决定。",
        "approved": "二审已批准；仍需由有权限的人员明确发布。",
        "rejected": "二审已拒绝，本次证据会保留，不会自动发布。",
        "published": "最终评测包已经人工发布。",
        "archived": "本次记录已归档并保留完整历史。",
        "failed": "请按阻塞入口处理失败项，修复后重新核对即可继续。",
        "blocked": "请先处理当前阻塞项，系统不会跳过真实证据。",
    }.get(run.status, "系统正在核对本次生产记录。")


def production_run_payload(db: Session, run: EvaluationProductionRun) -> dict[str, Any]:
    facts = _job_facts(db, run)
    blockers = [item for item in _loads_list(run.blockers_json) if isinstance(item, dict)]
    package = run.evaluation_package
    automation = run.automation_run
    regression = run.regression_run
    actions = [item.get("fix") for item in blockers if isinstance(item.get("fix"), dict)]
    if run.status == "first_review":
        actions.append({"label": "前往一审", "href": "/workflow/review/low-confidence"})
    elif run.status == "awaiting_review" and package is not None:
        actions.append({"label": "打开二审评测包", "href": f"/workflow/releases/packages/{package.id}"})
    elif run.status == "approved" and package is not None:
        actions.append({"label": "查看并发布", "href": f"/workflow/releases/packages/{package.id}"})
    return {
        "id": run.id,
        "idempotency_key": run.idempotency_key,
        "status": run.status,
        "current_stage": run.current_stage,
        "current_stage_label": _stage_label(run.current_stage),
        "material_package_id": run.material_package_id,
        "material_package": {
            "id": run.material_package.id,
            "name": run.material_package.name,
            "package_key": run.material_package.package_key,
            "active_asset_count": len(_active_package_asset_ids(run.material_package)),
        },
        "category_key": run.category_key,
        "workflow_kind": run.workflow_kind,
        "category": {
            "key": run.category_key,
            "name": _loads_object(run.category_profile_snapshot_json).get("display_name") or run.category_key,
            "configuration_hash": run.category_profile_hash,
        },
        "job_ids": [int(value) for value in _loads_list(run.job_ids_json) if isinstance(value, int)],
        "job_counts": facts["counts"],
        "pending_first_review_count": len(facts["pending_review_ids"]),
        "pending_first_review_ids": [
            int(value) for value in facts["pending_review_ids"] if isinstance(value, int)
        ],
        "progress": _progress(run, facts),
        "automation_run_id": run.automation_run_id,
        "automation": (
            {
                "id": automation.id,
                "status": automation.status,
                "lifecycle_status": automation_lifecycle_status(automation.status),
                "dry_run": automation.dry_run,
                "href": "/workflow/optimization/automation",
            }
            if automation is not None
            else None
        ),
        "regression_run_id": run.regression_run_id,
        "regression": (
            {
                "id": regression.id,
                "status": regression.status,
                "recommendation": regression.recommendation,
                "completed": regression.completed,
                "total": regression.total,
                "href": "/workflow/optimization/paired-regression",
            }
            if regression is not None
            else None
        ),
        "evaluation_package_id": run.evaluation_package_id,
        "evaluation_package": (
            {
                "id": package.id,
                "status": package.status,
                "href": f"/workflow/releases/packages/{package.id}",
            }
            if package is not None
            else None
        ),
        "blockers": blockers,
        "fix_actions": actions,
        "ai_next_step": _next_step(run, blockers),
        "timeline": _timeline(run, facts),
        "error": (
            {"code": run.error_code, "message": run.error_message}
            if run.error_code
            else None
        ),
        "audit": {
            "revision": run.audit_revision,
            "last_reconciled_at": run.last_reconciled_at,
        },
        "created_by": run.created_by,
        "created_at": run.created_at,
        "updated_at": run.updated_at,
        "started_at": run.started_at,
        "finished_at": run.finished_at,
        "archived_at": run.archived_at,
    }


def create_production_run(
    db: Session,
    *,
    payload: EvaluationProductionRunCreateRequest,
    actor: str,
    enqueue: EnqueueCallback,
) -> tuple[EvaluationProductionRun, bool]:
    request_hash = _request_hash(payload)
    existing = db.scalar(
        select(EvaluationProductionRun).where(
            EvaluationProductionRun.idempotency_key == payload.idempotency_key
        )
    )
    if existing is not None:
        if existing.request_hash != request_hash:
            raise HTTPException(status_code=409, detail="相同幂等键对应的生产请求已变化")
        return reconcile_production_run(db, existing, actor=actor), True

    package = db.get(MaterialPackage, payload.material_package_id)
    if package is None or package.status == "deleted":
        raise HTTPException(status_code=404, detail="素材包不存在或已删除")
    if package.category_key != payload.category_key:
        raise HTTPException(status_code=422, detail="素材包与所选类目队列不一致")
    asset_ids = _active_package_asset_ids(package)
    if not asset_ids:
        raise HTTPException(status_code=409, detail="素材包没有可用素材，不能开始评测")
    if any(
        item.asset.status != "deleted" and item.asset.category_key != payload.category_key
        for item in package.items
    ):
        raise HTTPException(status_code=422, detail="素材包包含其他类目的素材")

    queued = enqueue(db, asset_ids, payload.category_key)
    snapshot_json = str(queued.get("category_profile_snapshot") or "")
    snapshot = _validate_frozen_configuration(snapshot_json, payload.category_key)
    normalized_snapshot = _canonical_json(snapshot)
    profile_hash = hashlib.sha256(normalized_snapshot.encode("utf-8")).hexdigest()
    correction_contract = freeze_contract_from_execution_snapshot(
        category_key=payload.category_key,
        execution_snapshot=snapshot,
    )
    job_ids = queued.get("job_ids")
    batch_key = queued.get("batch_key")
    if (
        not isinstance(job_ids, list)
        or len(job_ids) != len(asset_ids)
        or not all(isinstance(value, int) for value in job_ids)
        or not isinstance(batch_key, str)
        or not batch_key
    ):
        raise HTTPException(status_code=409, detail="评测队列未返回完整任务身份")
    run = EvaluationProductionRun(
        idempotency_key=payload.idempotency_key,
        request_hash=request_hash,
        material_package_id=package.id,
        category_key=payload.category_key,
        workflow_kind=payload.workflow_kind,
        category_profile_snapshot_json=normalized_snapshot,
        category_profile_hash=profile_hash,
        correction_contract_json=_canonical_json(correction_contract),
        correction_contract_hash=correction_contract_hash(correction_contract),
        job_ids_json=_canonical_json(job_ids),
        batch_key=batch_key,
        status="queued",
        current_stage="evaluation",
        blockers_json="[]",
        created_by=actor,
    )
    db.add(run)
    try:
        db.flush()
        append_audit_event(
            db,
            category="evaluation_production",
            action="created",
            subject_type="evaluation_production_run",
            subject_id=run.id,
            actor=actor,
            payload={
                "material_package_id": package.id,
                "category_key": payload.category_key,
                "job_ids": job_ids,
                "category_profile_hash": profile_hash,
                "creates_final_package_immediately": False,
            },
            event_key=f"evaluation-production-created:{hashlib.sha256(payload.idempotency_key.encode()).hexdigest()}",
        )
        db.commit()
    except IntegrityError:
        db.rollback()
        concurrent = db.scalar(
            select(EvaluationProductionRun).where(
                EvaluationProductionRun.idempotency_key == payload.idempotency_key
            )
        )
        if concurrent is None or concurrent.request_hash != request_hash:
            raise HTTPException(status_code=409, detail="生产请求并发冲突") from None
        return reconcile_production_run(db, concurrent, actor=actor), True
    db.refresh(run)
    return run, False


def build_evaluation_production_router(
    read_user_dependency: Callable[..., User],
    write_user_dependency: Callable[..., User],
    enqueue: EnqueueCallback,
) -> APIRouter:
    router = APIRouter(prefix="/api", tags=["evaluation-production-runs"])

    @router.post("/evaluation-production-runs")
    def create_run(
        payload: EvaluationProductionRunCreateRequest,
        user: User = Depends(write_user_dependency),
        db: Session = Depends(get_db),
    ) -> dict[str, Any]:
        run, duplicate = create_production_run(
            db,
            payload=payload,
            actor=user.username,
            enqueue=enqueue,
        )
        detail = production_run_payload(db, run)
        return {"duplicate": duplicate, "run": detail, **detail}

    @router.get("/evaluation-production-runs")
    def list_runs(
        status: Literal[
            "preparing", "queued", "evaluating", "first_review", "optimizing",
            "regressing", "awaiting_review", "approved", "rejected", "published",
            "blocked", "failed", "archived",
        ] | None = None,
        category_key: str | None = None,
        workflow_kind: Literal["incremental", "stock"] | None = None,
        limit: int = Query(default=200, ge=1, le=500),
        user: User = Depends(read_user_dependency),
        db: Session = Depends(get_db),
    ) -> dict[str, Any]:
        statement = select(EvaluationProductionRun).order_by(
            EvaluationProductionRun.created_at.desc(),
            EvaluationProductionRun.id.desc(),
        )
        if status is not None:
            statement = statement.where(EvaluationProductionRun.status == status)
        if category_key is not None:
            statement = statement.where(EvaluationProductionRun.category_key == category_key)
        if workflow_kind is not None:
            statement = statement.where(EvaluationProductionRun.workflow_kind == workflow_kind)
        runs = db.scalars(statement.limit(limit)).all()
        items = [
            production_run_payload(
                db,
                reconcile_production_run(db, run, actor=f"reconcile:{user.username}"),
            )
            for run in runs
        ]
        return {"items": items, "total": len(items)}

    @router.get("/evaluation-production-runs/{run_id}")
    def get_run(
        run_id: int,
        user: User = Depends(read_user_dependency),
        db: Session = Depends(get_db),
    ) -> dict[str, Any]:
        run = db.get(EvaluationProductionRun, run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="评测生产记录不存在")
        run = reconcile_production_run(db, run, actor=f"reconcile:{user.username}")
        return production_run_payload(db, run)

    @router.post("/evaluation-production-runs/{run_id}/reconcile")
    def reconcile_run(
        run_id: int,
        user: User = Depends(write_user_dependency),
        db: Session = Depends(get_db),
    ) -> dict[str, Any]:
        run = db.get(EvaluationProductionRun, run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="评测生产记录不存在")
        run = reconcile_production_run(db, run, actor=f"reconcile:{user.username}")
        return production_run_payload(db, run)

    return router
