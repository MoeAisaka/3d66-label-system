#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import secrets
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable


REPO_ROOT = Path(__file__).resolve().parents[2]
MOCK_SERVER = REPO_ROOT / "scripts/integration/mock_openai_server.py"
SEED_SCRIPT = REPO_ROOT / "scripts/integration/automation_e2e_seed.py"
FORBIDDEN_PORTS = set(range(18081, 18091))
NOW = datetime(2026, 8, 2, 12, 0, tzinfo=timezone.utc)

# Executing this file by path puts ``scripts/integration`` on sys.path, but the
# category-isolation fixture intentionally reuses helpers from the repository's
# ``scripts`` namespace.  Keep the CLI worker equivalent to importing the
# module from the repository root.
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

SCENARIOS = (
    "timeout",
    "missing_usage",
    "missing_optimizer_api_key",
    "zero_or_exhausted_budget",
    "duplicate_feedback_event",
    "cross_category_isolation",
    "concurrent_workers",
)
PARENT_DONE = {"succeeded", "failed", "cancelled", "awaiting_release_review"}


def record(
    scenario: str,
    *,
    expected: dict[str, Any],
    observed: dict[str, Any],
    passed: bool,
    run_id: int | None = None,
    event_id: str | None = None,
    category_key: str = "space_image",
    final_status: str,
    error_code: str | None = None,
    retry_count: int = 0,
    reserved: int = 0,
    released: int = 0,
    spent: int = 0,
    candidate: bool = False,
    parent_status: str | None = None,
) -> dict[str, Any]:
    return {
        "scenario": scenario,
        "expected": expected,
        "observed": observed,
        "pass": bool(passed),
        "run_id": run_id,
        "event_id": event_id,
        "category_key": category_key,
        "final_status": final_status,
        "error_code": error_code,
        "retry_count": retry_count,
        "budget_reserved_micros": reserved,
        "budget_released_micros": released,
        "budget_spent_micros": spent,
        "candidate_created": candidate,
        "parent_status": parent_status,
        "parent_terminal_or_review": parent_status in PARENT_DONE,
    }


def backend(data_dir: Path) -> SimpleNamespace:
    os.environ["DATA_DIR"] = str(data_dir.resolve())
    os.environ["API_KEY_MASTER_KEY_FILE"] = str(
        data_dir.resolve() / "secrets/master.key"
    )
    sys.path.insert(0, str(REPO_ROOT / "backend"))
    from sqlalchemy import func, select
    from app import models
    from app.database import SessionLocal
    from app.dimension_schema_registry import (
        ACTIVE_V13_VERSION,
        SPACE_SCHEMA_KEY,
    )
    from app.optimization_automation import (
        AutomationAdapterResult,
        category_bundle_contract_errors,
        configured_optimization_adapter,
        consume_optimization_queue_once,
    )
    from app.production_feedback import ingest_production_feedback
    from app.scoring import ENGINE_VERSION
    from app.strategy_bundle import (
        build_evaluation_strategy_snapshot,
        get_or_create_bundle,
    )
    return SimpleNamespace(
        m=models, Session=SessionLocal, select=select, func=func,
        AdapterResult=AutomationAdapterResult,
        configured_adapter=configured_optimization_adapter,
        consume=consume_optimization_queue_once, ingest=ingest_production_feedback,
        engine_version=ENGINE_VERSION,
        build_snapshot=build_evaluation_strategy_snapshot,
        bundle=get_or_create_bundle,
        category_bundle_contract_errors=category_bundle_contract_errors,
        dimension_schema_key=SPACE_SCHEMA_KEY,
        dimension_schema_version=ACTIVE_V13_VERSION,
    )


def set_mode(state_dir: Path, value: str) -> None:
    (state_dir / "mode.txt").write_text(value + "\n", encoding="utf-8")


def requests_log(state_dir: Path) -> list[dict[str, Any]]:
    path = state_dir / "requests.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def seeded(db: Any, b: SimpleNamespace) -> tuple[Any, Any, Any]:
    event = db.scalar(b.select(b.m.ProductionFeedbackEvent).where(
        b.m.ProductionFeedbackEvent.event_id == "automation-e2e-success-1"
    ))
    case = db.scalar(b.select(b.m.OptimizationCaseQueue).where(
        b.m.OptimizationCaseQueue.source_event_id == event.id
    ))
    policy = db.get(b.m.AutomationPolicy, 1)
    if event is None or case is None or policy is None:
        raise RuntimeError("fault_matrix_seed_incomplete")
    return event, case, policy


def budget(db: Any, b: SimpleNamespace) -> dict[str, int]:
    row = db.get(b.m.AutomationBudgetDay, NOW.date().isoformat())
    return (
        {"reserved": int(row.reserved_micros), "spent": int(row.spent_micros)}
        if row else {"reserved": 0, "spent": 0}
    )


def candidate_count(db: Any, b: SimpleNamespace, run_id: int | None) -> int:
    if run_id is None:
        return 0
    return int(db.scalar(
        b.select(b.func.count()).select_from(b.m.PromptVersion).where(
            b.m.PromptVersion.source_automation_run_id == run_id
        )
    ) or 0)


def ingest_case(
    db: Any, b: SimpleNamespace, *, event_id: str,
    category: str = "space_image", prompt: str = "automation-e2e-b-v1",
) -> tuple[Any, Any]:
    event, case, duplicate = b.ingest(
        db, event_id=event_id, schema_version="production-feedback-v1",
        event_type="human_correction_finalized",
        source_system="automation-fault-matrix", occurred_at=NOW,
        payload={
            "production_case_id": "case-" + event_id,
            "category_key": category, "prompt_version": prompt, "severity": "P2",
            "model_output": {"level": "L4"}, "human_truth": {"level": "L3"},
            "reason_codes": ["over_scored"], "production_applied": True,
        }, received_by="automation-fault-matrix",
    )
    if duplicate:
        raise RuntimeError("fault_matrix_unexpected_duplicate")
    return event, case


def scenario_timeout(data_dir: Path, _url: str, state_dir: Path) -> dict[str, Any]:
    b = backend(data_dir)
    set_mode(state_dir, "timeout")
    with b.Session() as db:
        event, case, _ = seeded(db, b)
        config = db.get(b.m.OptimizerConfig, 1)
        config.timeout_seconds = 1
        config.max_retries = 0
        db.commit()
        before = budget(db, b)
        result = b.consume(db, worker_id="fault-timeout", now=NOW)
        db.commit()
        run = db.get(b.m.AutomationOptimizationRun, result.get("run_id"))
        db.refresh(case)
        after = budget(db, b)
        calls = requests_log(state_dir)
        candidates = candidate_count(db, b, run.id if run else None)
        observed = {
            "worker_result": result, "run_status": run.status if run else None,
            "run_error": run.error_message if run else None,
            "attempt_count": case.attempt_count,
            "retry_scheduled": case.next_attempt_at is not None,
            "provider_calls": len(calls), "candidate_count": candidates,
            "budget_before": before, "budget_after": after,
        }
        expected = {
            "run_status": "failed", "error_code": "model_timeout",
            "attempt_count": 1, "retry_scheduled": True, "provider_calls": 1,
            "candidate_count": 0, "reserved_budget_charged": True,
        }
        passed = bool(
            run and run.status == "failed" and run.error_message == "model_timeout"
            and case.attempt_count == 1 and case.next_attempt_at is not None
            and len(calls) == 1 and candidates == 0 and after["reserved"] == 0
            and after["spent"] - before["spent"] == run.estimated_cost_micros
        )
        return record(
            "timeout", expected=expected, observed=observed, passed=passed,
            run_id=run.id if run else None, event_id=event.event_id,
            final_status=run.status if run else str(result.get("status")),
            error_code=run.error_message if run else None,
            retry_count=case.attempt_count,
            reserved=run.estimated_cost_micros if run else 0,
            released=run.estimated_cost_micros if run else 0,
            spent=after["spent"] - before["spent"], candidate=candidates > 0,
            parent_status=run.status if run else None,
        )


def scenario_missing_usage(data_dir: Path, _url: str, state_dir: Path) -> dict[str, Any]:
    b = backend(data_dir)
    set_mode(state_dir, "missing_usage")
    with b.Session() as db:
        event, case, _ = seeded(db, b)
        before = budget(db, b)
        missing = b.consume(db, worker_id="fault-missing-usage", now=NOW)
        db.commit()
        missing_run = db.get(b.m.AutomationOptimizationRun, missing.get("run_id"))
        missing_candidates = candidate_count(db, b, missing_run.id if missing_run else None)
        after_missing = budget(db, b)
        ingest_case(db, b, event_id="fault-valid-usage-control")
        db.commit()
        set_mode(state_dir, "ok")
        control = b.consume(db, worker_id="fault-valid-usage", now=NOW)
        db.commit()
        control_run = db.get(b.m.AutomationOptimizationRun, control.get("run_id"))
        control_candidates = candidate_count(db, b, control_run.id if control_run else None)
        calls = requests_log(state_dir)
        missing_calls = [item for item in calls if item.get("mode") == "missing_usage"]
        valid_calls = [item for item in calls if item.get("mode") == "ok"]
        db.refresh(case)
        observed = {
            "missing_usage": {
                "status": missing_run.status if missing_run else None,
                "error_code": missing_run.error_message if missing_run else None,
                "provider_response_status": missing_calls[0].get("response_status") if missing_calls else None,
                "provider_usage_present": missing_calls[0].get("usage_included") if missing_calls else None,
                "provider_calls": len(missing_calls), "candidate_count": missing_candidates,
                "charged_micros": missing_run.estimated_cost_micros if missing_run else 0,
                "actual_cost_micros": missing_run.actual_cost_micros if missing_run else None,
            },
            "valid_usage_control": {
                "status": control_run.status if control_run else None,
                "provider_calls": len(valid_calls),
                "tokens": [control_run.input_tokens, control_run.output_tokens, control_run.total_tokens] if control_run else None,
                "actual_cost_micros": control_run.actual_cost_micros if control_run else None,
                "candidate_count": control_candidates,
            },
            "budget_before": before,
            "budget_after_missing_usage": after_missing,
            "budget_after": budget(db, b),
        }
        expected = {
            "missing_usage_status": "failed", "missing_usage_error": "optimizer_usage_missing",
            "provider_succeeded_without_usage": True,
            "missing_usage_not_valid_charged_response": True,
            "missing_usage_reserved_limit_charged": True,
            "stops_before_synthesis": True,
            "valid_usage_control": {"status": "succeeded", "tokens": [200, 100, 300], "candidate_count": 1},
        }
        passed = bool(
            missing_run and missing_run.status == "failed"
            and missing_run.error_message == "optimizer_usage_missing"
            and missing_run.input_tokens is None and len(missing_calls) == 1
            and missing_calls[0].get("response_status") == 200
            and missing_calls[0].get("usage_included") is False
            and missing_candidates == 0 and control_run and control_run.status == "succeeded"
            and missing_run.actual_cost_micros == 0
            and after_missing["reserved"] == 0
            and after_missing["spent"] - before["spent"] == missing_run.estimated_cost_micros
            and [control_run.input_tokens, control_run.output_tokens, control_run.total_tokens] == [200, 100, 300]
            and len(valid_calls) == 2 and all(x.get("usage_included") is True for x in valid_calls)
            and control_candidates == 1 and budget(db, b)["reserved"] == 0
            and budget(db, b)["spent"] - after_missing["spent"] == control_run.actual_cost_micros
        )
        return record(
            "missing_usage", expected=expected, observed=observed, passed=passed,
            run_id=missing_run.id if missing_run else None, event_id=event.event_id,
            final_status=missing_run.status if missing_run else str(missing.get("status")),
            error_code=missing_run.error_message if missing_run else None,
            retry_count=case.attempt_count,
            reserved=missing_run.estimated_cost_micros if missing_run else 0,
            released=missing_run.estimated_cost_micros if missing_run else 0,
            spent=missing_run.estimated_cost_micros if missing_run else 0,
            candidate=missing_candidates > 0,
            parent_status=missing_run.status if missing_run else None,
        )


def scenario_missing_key(data_dir: Path, _url: str, state_dir: Path) -> dict[str, Any]:
    b = backend(data_dir)
    set_mode(state_dir, "ok")
    with b.Session() as db:
        event, case, _ = seeded(db, b)
        for config in db.scalars(b.select(b.m.OptimizerConfig)).all():
            config.encrypted_api_key = ""
        for config in db.scalars(b.select(b.m.ModelConfig)).all():
            config.encrypted_api_key = ""
        db.commit()
        result = b.consume(db, worker_id="fault-missing-key", now=NOW)
        db.commit()
        db.refresh(case)
        runs = int(db.scalar(b.select(b.func.count()).select_from(
            b.m.AutomationOptimizationRun
        )) or 0)
        candidates = int(db.scalar(
            b.select(b.func.count()).select_from(b.m.PromptVersion).where(
                b.m.PromptVersion.source_automation_run_id.is_not(None)
            )
        ) or 0)
        calls = requests_log(state_dir)
        current_budget = budget(db, b)
        observed = {
            "worker_result": result, "case_status": case.status,
            "attempt_count": case.attempt_count, "run_count": runs,
            "provider_calls": len(calls), "candidate_count": candidates,
            "budget": current_budget,
        }
        expected = {
            "status": "executor_config_blocked", "error_code": "optimizer_config_incomplete",
            "case_status": "pending", "attempt_count": 0, "run_count": 0,
            "provider_calls": 0,
        }
        passed = bool(
            result.get("status") == "executor_config_blocked"
            and result.get("reason") == "optimizer_config_incomplete"
            and case.status == "pending" and case.attempt_count == 0
            and runs == 0 and candidates == 0 and not calls
            and current_budget == {"reserved": 0, "spent": 0}
        )
        return record(
            "missing_optimizer_api_key", expected=expected, observed=observed,
            passed=passed, event_id=event.event_id,
            final_status=str(result.get("status")), error_code=str(result.get("reason")),
            retry_count=case.attempt_count,
        )


def scenario_budget(data_dir: Path, _url: str, state_dir: Path) -> dict[str, Any]:
    b = backend(data_dir)
    set_mode(state_dir, "ok")
    with b.Session() as db:
        event, case, policy = seeded(db, b)
        policy.daily_budget_micros = 0
        db.commit()
        zero = b.consume(db, worker_id="fault-zero-budget", now=NOW)
        db.commit()
        estimate = int(zero.get("estimated_micros", 0))
        row = db.get(b.m.AutomationBudgetDay, NOW.date().isoformat())
        policy.daily_budget_micros = estimate
        row.spent_micros = estimate
        row.reserved_micros = 0
        db.commit()
        exhausted = b.consume(db, worker_id="fault-exhausted-budget", now=NOW)
        db.commit()
        db.refresh(case)
        runs = int(db.scalar(b.select(b.func.count()).select_from(
            b.m.AutomationOptimizationRun
        )) or 0)
        candidates = int(db.scalar(
            b.select(b.func.count()).select_from(b.m.PromptVersion).where(
                b.m.PromptVersion.source_automation_run_id.is_not(None)
            )
        ) or 0)
        calls = requests_log(state_dir)
        final_budget = budget(db, b)
        observed = {
            "zero_budget": zero, "exhausted_budget": exhausted,
            "case_status": case.status, "attempt_count": case.attempt_count,
            "run_count": runs, "provider_calls": len(calls),
            "candidate_count": candidates, "budget": final_budget,
        }
        expected = {
            "zero_status": "budget_blocked", "exhausted_status": "budget_blocked",
            "case_status": "pending", "attempt_count": 0, "run_count": 0,
            "provider_calls": 0, "reserved_micros": 0,
        }
        passed = bool(
            zero.get("status") == "budget_blocked" and zero.get("budget_micros") == 0
            and estimate > 0 and exhausted.get("status") == "budget_blocked"
            and exhausted.get("used_micros") == estimate
            and exhausted.get("budget_micros") == estimate
            and case.status == "pending" and case.attempt_count == 0
            and runs == 0 and candidates == 0 and not calls
            and final_budget == {"reserved": 0, "spent": estimate}
        )
        return record(
            "zero_or_exhausted_budget", expected=expected, observed=observed,
            passed=passed, event_id=event.event_id, final_status="budget_blocked",
            error_code="budget_blocked", retry_count=case.attempt_count,
        )


def scenario_duplicate(data_dir: Path, _url: str, state_dir: Path) -> dict[str, Any]:
    b = backend(data_dir)
    set_mode(state_dir, "ok")
    with b.Session() as db:
        event, case, _ = seeded(db, b)
        same_event, same_case, duplicate = b.ingest(
            db, event_id=event.event_id, schema_version=event.schema_version,
            event_type=event.event_type, source_system=event.source_system,
            occurred_at=event.occurred_at, payload=json.loads(event.payload_json),
            received_by="automation-fault-matrix-replay",
        )
        db.commit()
        event_count = int(db.scalar(
            b.select(b.func.count()).select_from(b.m.ProductionFeedbackEvent).where(
                b.m.ProductionFeedbackEvent.event_id == event.event_id
            )
        ) or 0)
        case_count = int(db.scalar(
            b.select(b.func.count()).select_from(b.m.OptimizationCaseQueue).where(
                b.m.OptimizationCaseQueue.source_event_id == event.id
            )
        ) or 0)
        audit_count = int(db.scalar(
            b.select(b.func.count()).select_from(b.m.AuditEvent).where(
                b.m.AuditEvent.event_key == f"production-feedback:{event.event_id}"
            )
        ) or 0)
        observed = {
            "duplicate": duplicate, "original_event_db_id": event.id,
            "replayed_event_db_id": same_event.id, "original_case_id": case.id,
            "replayed_case_id": same_case.id, "event_count": event_count,
            "case_count": case_count, "audit_count": audit_count,
            "case_status": case.status,
        }
        expected = {
            "duplicate": True, "same_event": True, "same_case": True,
            "event_count": 1, "case_count": 1, "no_run_created": True,
        }
        passed = bool(
            duplicate and same_event.id == event.id and same_case.id == case.id
            and event_count == case_count == audit_count == 1
            and db.scalar(b.select(b.m.AutomationOptimizationRun.id)) is None
        )
        return record(
            "duplicate_feedback_event", expected=expected, observed=observed,
            passed=passed, event_id=event.event_id, final_status="duplicate_noop",
            retry_count=case.attempt_count,
        )


def create_material_golden(db: Any, b: SimpleNamespace, data_dir: Path) -> tuple[Any, Any]:
    import hashlib
    from PIL import Image
    from scripts.integration.automation_e2e_seed import (
        _aesthetic,
        _bind_profile_baseline_contract,
        _precheck,
    )

    model = db.get(b.m.ModelConfig, 1)
    optimizer = db.get(b.m.OptimizerConfig, 1)
    sampling = db.get(b.m.SamplingPolicy, 1)
    profile = db.scalar(b.select(b.m.EvaluationCategoryProfile).where(
        b.m.EvaluationCategoryProfile.category_key == "material_image"
    ))
    prompt_a = b.m.PromptVersion(
        stage="A", name="Fault matrix material A", version="fault-material-a-v1",
        system_prompt="E2E_STAGE_A：材质类目预检。",
        user_prompt="评测素材 {{image_metadata}}。",
        rubric_version="fault-material-rubric-v1", status="published",
        source="integration_test", created_by="automation-fault-matrix",
    )
    prompt_b = b.m.PromptVersion(
        stage="B", name="Fault matrix material B", version="fault-material-b-v1",
        system_prompt="E2E_STAGE_B：材质类目美感评测。",
        user_prompt="根据 {{precheck_json}} 评测，规则 {{rubric_version}}。",
        rubric_version="fault-material-rubric-v1", status="published",
        source="integration_test", created_by="automation-fault-matrix",
    )
    db.add_all([prompt_a, prompt_b])
    db.flush()
    material_optimizer = b.m.OptimizerConfig(
        name="Fault matrix material optimizer",
        provider=optimizer.provider,
        protocol=optimizer.protocol,
        capabilities_json=optimizer.capabilities_json,
        base_url=optimizer.base_url,
        api_path=optimizer.api_path,
        model_id="automation-e2e-optimizer-material",
        encrypted_api_key=optimizer.encrypted_api_key,
        temperature=optimizer.temperature,
        max_tokens=optimizer.max_tokens,
        timeout_seconds=optimizer.timeout_seconds,
        max_retries=optimizer.max_retries,
        structured_output=optimizer.structured_output,
        input_micros_per_million_tokens=10_000,
        output_micros_per_million_tokens=20_000,
        max_input_tokens=optimizer.max_input_tokens,
    )
    db.add(material_optimizer)
    db.flush()
    profile.prompt_a_id = prompt_a.id
    profile.prompt_b_id = prompt_b.id
    profile.model_config_id = model.id
    profile.optimizer_config_id = material_optimizer.id
    profile.rubric_version = prompt_b.rubric_version
    profile.automation_config_json = json.dumps({
        "enabled": True, "case_threshold": 2,
        "cooldown_seconds": 0, "max_candidates": 1,
    }, separators=(",", ":"))
    bundle = b.bundle(
        db, model, prompt_a, prompt_b, prompt_b.rubric_version,
        b.engine_version, None, sampling,
    )
    _bind_profile_baseline_contract(
        profile=profile,
        baseline_bundle=bundle,
        dimension_schema_key=b.dimension_schema_key,
        dimension_schema_version=b.dimension_schema_version,
    )
    db.flush()
    contract_errors = b.category_bundle_contract_errors(
        db,
        profile=profile,
        bundle=bundle,
        require_complete=True,
        require_prompt_b=True,
        enforce_baseline_id=True,
    )
    if contract_errors:
        raise RuntimeError(
            "fault_matrix_material_baseline_contract_mismatch:"
            + ",".join(contract_errors)
        )
    sample_set = b.m.SampleSet(
        name="Fault matrix material golden", description="Category isolation fixture",
        kind="golden", status="locked", category_key="material_image",
        created_by="automation-fault-matrix",
    )
    db.add(sample_set)
    db.flush()
    for index, role in enumerate(
        ("target_error", "stable_control", "blind_holdout"), start=1
    ):
        name = f"fault-material-{role}.jpg"
        path = data_dir / "images" / name
        Image.new("RGB", (64, 64), (35 * index, 130, 70)).save(path, format="JPEG")
        raw = path.read_bytes()
        asset = b.m.Asset(
            original_name=name, stored_name=name, mime_type="image/jpeg",
            size_bytes=len(raw), width=64, height=64,
            sha256=hashlib.sha256(raw).hexdigest(), category_key="material_image",
            status="evaluated",
        )
        db.add(asset)
        db.flush()
        job = b.m.EvaluationJob(
            asset_id=asset.id, category_key="material_image",
            prompt_a_id=prompt_a.id, prompt_b_id=prompt_b.id,
            strategy_bundle_id=bundle.id, status="completed", stage="done", progress=100,
        )
        db.add(job)
        db.flush()
        aesthetic = _aesthetic(color_grade=5 if role == "target_error" else 3)
        result = b.m.EvaluationResult(
            asset_id=asset.id, job_id=job.id, strategy_bundle_id=bundle.id,
            strategy_snapshot_json=b.build_snapshot(
                db=db, bundle=bundle, prompt_a=prompt_a, prompt_b=prompt_b,
                sampling_policy=sampling, aesthetic=aesthetic,
            ),
            preprocess_json='{"schema_version":"fault-matrix-v1"}',
            precheck_json=json.dumps(_precheck(), ensure_ascii=False),
            aesthetic_json=json.dumps(aesthetic, ensure_ascii=False),
            scoring_json=json.dumps({
                "formal": True, "level": "L4" if role == "target_error" else "L3",
                "caps": [],
            }),
            raw_response_a="{}", raw_response_b="{}",
            score=82.0 if role == "target_error" else 65.0,
            level="L4" if role == "target_error" else "L3",
            confidence=0.95, needs_review=False, review_stage="completed",
            review_revision=1, model_id=model.model_id,
            prompt_a_version=prompt_a.version, prompt_b_version=prompt_b.version,
            rubric_version=prompt_b.rubric_version, engine_version=b.engine_version,
        )
        db.add(result)
        db.flush()
        review = b.m.HumanReview(
            evaluation_id=result.id, reviewer_name="automation-fault-matrix",
            stage="initial", decision="corrected" if role == "target_error" else "approved",
            corrected_level="L3" if role == "target_error" else None,
            corrected_score=65.0 if role == "target_error" else None,
            note=role,
            corrections_json=json.dumps([{
                "target_type": "dimension", "field_key": "color_material",
                "model_value": 5, "human_value": 3,
                "reason_codes": ["overrated"], "note": "material target error",
            }]) if role == "target_error" else "[]",
        )
        db.add(review)
        db.flush()
        db.add(b.m.SampleSetItem(
            sample_set_id=sample_set.id, asset_id=asset.id,
            source_result_id=result.id, expected_level="L3",
            expected_category="住宅设计", truth_json='{"level":"L3"}',
            truth_revision=1, truth_updated_by="automation-fault-matrix",
            note=role, added_by="automation-fault-matrix",
        ))
    return profile, prompt_b


def scenario_cross_category(data_dir: Path, _url: str, state_dir: Path) -> dict[str, Any]:
    b = backend(data_dir)
    set_mode(state_dir, "ok")
    with b.Session() as db:
        event, _, policy = seeded(db, b)
        space_profile = db.scalar(b.select(b.m.EvaluationCategoryProfile).where(
            b.m.EvaluationCategoryProfile.category_key == "space_image"
        ))
        material_profile, material_prompt = create_material_golden(db, b, data_dir)
        ingest_case(db, b, event_id="fault-material-1", category="material_image", prompt=material_prompt.version)
        ingest_case(db, b, event_id="fault-material-2", category="material_image", prompt=material_prompt.version)
        policy_snapshots = {
            "space_image": space_profile.automation_config_json,
            "material_image": material_profile.automation_config_json,
        }
        global_policy_snapshot = {
            key: getattr(policy, key)
            for key in (
                "enabled", "dry_run", "revision", "case_threshold",
                "immediate_severities_json", "daily_budget_micros",
                "cooldown_seconds", "max_candidates", "lease_seconds",
                "max_attempts", "base_retry_seconds", "updated_by",
            )
        }
        optimizer_config_ids = {
            "space_image": space_profile.optimizer_config_id,
            "material_image": material_profile.optimizer_config_id,
        }
        db.commit()
        before = budget(db, b)
        space_result = b.consume(db, worker_id="fault-space", now=NOW)
        db.commit()
        material_result = b.consume(db, worker_id="fault-material", now=NOW)
        db.commit()
        runs = [db.get(b.m.AutomationOptimizationRun, item.get("run_id"))
                for item in (space_result, material_result)]
        runs = [run for run in runs if run]
        statuses = {run.category_key: run.status for run in runs}
        case_counts = {run.category_key: len(json.loads(run.case_ids_json)) for run in runs}
        candidates = {run.category_key: candidate_count(db, b, run.id) for run in runs}
        frozen_categories: dict[str, str | None] = {}
        queued_case_categories: dict[str, list[str | None]] = {}
        regressions: dict[str, list[str | None]] = {}
        candidate_details: dict[str, list[dict[str, Any]]] = {}
        for run in runs:
            payload = json.loads(run.result_json)
            frozen_categories[run.category_key] = json.loads(
                run.frozen_input_json
            ).get("category_key")
            queued_case_categories[run.category_key] = [
                db.get(b.m.OptimizationCaseQueue, case_id).category_key
                for case_id in json.loads(run.case_ids_json)
            ]
            regressions[run.category_key] = [
                db.get(b.m.PromptRegressionRun, regression_id).sample_set.category_key
                for regression_id in payload.get("regression_ids", [])
            ]
            candidate_details[run.category_key] = [
                {
                    "source_run_id": item.source_automation_run_id,
                    "source": item.source,
                    "status": item.status,
                    "rubric_version": item.rubric_version,
                }
                for item in db.scalars(
                    b.select(b.m.PromptVersion).where(
                        b.m.PromptVersion.source_automation_run_id == run.id
                    )
                ).all()
            ]
        after = budget(db, b)
        db.refresh(space_profile)
        db.refresh(material_profile)
        db.refresh(policy)
        policy_unchanged = (
            space_profile.automation_config_json == policy_snapshots["space_image"]
            and material_profile.automation_config_json == policy_snapshots["material_image"]
            and all(
                getattr(policy, key) == value
                for key, value in global_policy_snapshot.items()
            )
        )
        cohort_isolated = (
            frozen_categories == {
                "space_image": "space_image",
                "material_image": "material_image",
            }
            and all(
                values and set(values) == {key}
                for key, values in queued_case_categories.items()
            )
        )
        regression_isolated = all(
            values and set(values) == {key}
            for key, values in regressions.items()
        )
        rubric_versions = {
            "space_image": space_profile.rubric_version,
            "material_image": material_profile.rubric_version,
        }
        candidate_isolated = all(
            len(candidate_details[run.category_key]) == 1
            and candidate_details[run.category_key][0] == {
                "source_run_id": run.id,
                "source": "optimizer",
                "status": "draft",
                "rubric_version": rubric_versions[run.category_key],
            }
            for run in runs
        )
        costs = {run.category_key: run.actual_cost_micros for run in runs}
        expected_costs = {}
        for category_key, config_id in optimizer_config_ids.items():
            config = db.get(b.m.OptimizerConfig, config_id)
            expected_costs[category_key] = (
                200 * config.input_micros_per_million_tokens
                + 100 * config.output_micros_per_million_tokens
                + 999_999
            ) // 1_000_000
        budget_isolated = (
            after["reserved"] == 0
            and after["spent"] - before["spent"] == sum(costs.values())
            and costs == expected_costs
            and len(set(costs.values())) == 2
        )
        observed = {
            "run_ids": {run.category_key: run.id for run in runs},
            "run_statuses": statuses, "case_counts": case_counts,
            "profile_policy_unchanged": policy_unchanged,
            "optimizer_config_ids": optimizer_config_ids,
            "frozen_categories": frozen_categories,
            "queued_case_categories": queued_case_categories,
            "regression_categories": regressions,
            "candidate_details": candidate_details,
            "candidate_counts": candidates, "actual_costs": costs,
            "expected_costs": expected_costs,
            "budget_before": before, "budget_after": after,
            "no_residual_budget_reservation": budget_isolated,
        }
        expected = {
            "run_statuses": {"space_image": "succeeded", "material_image": "succeeded"},
            "case_counts": {"space_image": 1, "material_image": 2},
            "profile_policy_unchanged": True,
            "frozen_and_queued_cases_match_parent_category": True,
            "regression_category_matches_parent": True,
            "candidate_draft_source_run_and_rubric_match_parent": True,
            "candidate_counts": {"space_image": 1, "material_image": 1},
            "distinct_category_costs_match_category_pricing": True,
            "no_residual_budget_reservation": True,
        }
        passed = bool(
            len(runs) == 2 and statuses == expected["run_statuses"]
            and case_counts == expected["case_counts"] and policy_unchanged
            and len(set(optimizer_config_ids.values())) == 2
            and cohort_isolated and regression_isolated and candidate_isolated
            and candidates == expected["candidate_counts"] and budget_isolated
        )
        reserved = sum(run.estimated_cost_micros for run in runs)
        return record(
            "cross_category_isolation", expected=expected, observed=observed,
            passed=passed, run_id=runs[0].id if runs else None,
            event_id=event.event_id, category_key="space_image+material_image",
            final_status="succeeded" if passed else "isolation_failed",
            error_code=None if passed else "cross_category_state_leak",
            retry_count=max((case.attempt_count for case in db.scalars(
                b.select(b.m.OptimizationCaseQueue)).all()), default=0),
            reserved=reserved, released=reserved,
            spent=after["spent"] - before["spent"],
            candidate=any(candidates.values()),
            parent_status=runs[0].status if runs else None,
        )


def scenario_concurrent(data_dir: Path, _url: str, state_dir: Path) -> dict[str, Any]:
    b = backend(data_dir)
    set_mode(state_dir, "ok")
    started = threading.Event()
    release = threading.Event()
    lock = threading.Lock()
    calls = 0
    results: dict[str, dict[str, Any]] = {}
    errors: list[str] = []

    with b.Session() as db:
        delegate = b.configured_adapter(db, category_key="space_image")
    if delegate is None:
        raise RuntimeError("fault_matrix_concurrency_adapter_missing")

    class BlockingAdapter:
        @property
        def base_prompt(self) -> Any:
            return delegate.base_prompt

        def bind_base_prompt(self, db: Any, *, version: str) -> None:
            delegate.bind_base_prompt(db, version=version)

        def prepare_regression_binding(
            self, db: Any, *, base_prompt: Any, category_key: str
        ) -> dict[str, Any]:
            return delegate.prepare_regression_binding(
                db, base_prompt=base_prompt, category_key=category_key
            )

        def estimate_cost_micros(self, *, frozen_input: dict[str, Any]) -> int:
            del frozen_input
            return 1000

        def optimize(self, *, frozen_input: dict[str, Any], max_candidates: int) -> Any:
            nonlocal calls
            del frozen_input, max_candidates
            with lock:
                calls += 1
            started.set()
            if not release.wait(timeout=10):
                raise RuntimeError("fault_matrix_concurrency_release_timeout")
            return b.AdapterResult(
                candidates=[{
                    "system_prompt": "concurrency candidate system",
                    "user_prompt": "concurrency candidate user",
                    "change_note": "single execution proof",
                }],
                regression={}, actual_cost_micros=500,
                input_tokens=10, output_tokens=5, total_tokens=15,
            )

        def materialize(
            self, db: Any, *, run: Any, result: Any, worker_id: str
        ) -> dict[str, list[int]]:
            return delegate.materialize(
                db, run=run, result=result, worker_id=worker_id
            )

    adapter = BlockingAdapter()

    def worker_one() -> None:
        try:
            with b.Session() as db:
                results["worker_1"] = b.consume(
                    db, worker_id="fault-concurrent-1", adapter=adapter, now=NOW
                )
                db.commit()
        except BaseException as exc:  # recorded for deterministic failure output
            errors.append(type(exc).__name__)

    thread = threading.Thread(target=worker_one, name="fault-matrix-worker-1")
    thread.start()
    if not started.wait(timeout=10):
        release.set()
        thread.join(timeout=10)
        raise RuntimeError("fault_matrix_first_worker_did_not_claim")
    try:
        with b.Session() as db:
            results["worker_2"] = b.consume(
                db, worker_id="fault-concurrent-2", adapter=adapter, now=NOW
            )
            db.commit()
    finally:
        release.set()
        thread.join(timeout=10)
    if thread.is_alive():
        raise RuntimeError("fault_matrix_worker_thread_did_not_stop")

    with b.Session() as db:
        event, case, _ = seeded(db, b)
        runs = db.scalars(b.select(b.m.AutomationOptimizationRun)).all()
        run = runs[0] if len(runs) == 1 else None
        completed = int(db.scalar(
            b.select(b.func.count()).select_from(b.m.OptimizationCaseQueue).where(
                b.m.OptimizationCaseQueue.id == case.id,
                b.m.OptimizationCaseQueue.status == "completed",
                b.m.OptimizationCaseQueue.automation_run_id.is_not(None),
            )
        ) or 0)
        current_budget = budget(db, b)
        candidates = candidate_count(db, b, run.id if run else None)
        completion_audits = 0
        if run is not None:
            completion_audits = int(db.scalar(
                b.select(b.func.count()).select_from(b.m.AuditEvent).where(
                    b.m.AuditEvent.category == "automation",
                    b.m.AuditEvent.action == "succeeded",
                    b.m.AuditEvent.subject_type == "automation_optimization_run",
                    b.m.AuditEvent.subject_id == str(run.id),
                )
            ) or 0)
        run_case_ids = json.loads(run.case_ids_json) if run else []
        statuses = sorted(str(item.get("status")) for item in results.values())
        observed = {
            "worker_results": results, "worker_errors": errors,
            "optimizer_calls": calls, "run_count": len(runs),
            "case_status": case.status, "attempt_count": case.attempt_count,
            "completed_mapping_count": completed, "budget": current_budget,
            "candidate_count": candidates,
            "completion_audit_count": completion_audits,
            "run_case_ids": run_case_ids,
            "case_id": case.id,
            "case_automation_run_id": case.automation_run_id,
        }
        expected = {
            "worker_statuses": ["idle", "succeeded"], "optimizer_calls": 1,
            "run_count": 1, "case_completed_once": True, "attempt_count": 1,
            "candidate_count": 1, "completion_audit_count": 1,
            "residual_reserved_budget": 0,
        }
        passed = bool(
            not errors and statuses == expected["worker_statuses"] and calls == 1
            and len(runs) == 1 and run and run.status == "succeeded"
            and case.status == "completed" and case.attempt_count == 1
            and case.automation_run_id == run.id and run_case_ids == [case.id]
            and completed == 1 and candidates == 1 and completion_audits == 1
            and current_budget == {"reserved": 0, "spent": 500}
        )
        return record(
            "concurrent_workers", expected=expected, observed=observed, passed=passed,
            run_id=run.id if run else None, event_id=event.event_id,
            final_status=run.status if run else "concurrency_failed",
            error_code=None if passed else "duplicate_completion_detected",
            retry_count=case.attempt_count,
            reserved=run.estimated_cost_micros if run else 0,
            released=run.estimated_cost_micros if run else 0,
            spent=current_budget["spent"], candidate=candidates > 0,
            parent_status=run.status if run else None,
        )


RUNNERS: dict[str, Callable[[Path, str, Path], dict[str, Any]]] = {
    "timeout": scenario_timeout,
    "missing_usage": scenario_missing_usage,
    "missing_optimizer_api_key": scenario_missing_key,
    "zero_or_exhausted_budget": scenario_budget,
    "duplicate_feedback_event": scenario_duplicate,
    "cross_category_isolation": scenario_cross_category,
    "concurrent_workers": scenario_concurrent,
}


def free_port() -> int:
    while True:
        port = 30000 + secrets.randbelow(20000)
        if port in FORBIDDEN_PORTS:
            continue
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            try:
                sock.bind(("127.0.0.1", port))
            except PermissionError:
                raise
            except OSError:
                continue
            return port


def wait_for_mock(port: int, process: subprocess.Popen[str]) -> None:
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError("fault_matrix_mock_server_exited")
        try:
            with urllib.request.urlopen(
                f"http://127.0.0.1:{port}/health", timeout=0.25
            ) as response:
                if response.status == 200:
                    return
        except OSError:
            time.sleep(0.05)
    raise RuntimeError("fault_matrix_mock_server_not_ready")


def last_json(output: str) -> dict[str, Any]:
    for line in reversed(output.splitlines()):
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    raise ValueError("fault_matrix_child_did_not_emit_json")


def failure_record(scenario: str, stage: str, error: str) -> dict[str, Any]:
    return record(
        scenario, expected={"scenario_completed": True},
        observed={"runner_stage": stage, "exception_type": error}, passed=False,
        category_key="space_image+material_image"
        if scenario == "cross_category_isolation" else "space_image",
        final_status="runner_failed", error_code="fault_matrix_runner_failed",
    )


def run_parent(scenarios: list[str], timeout_seconds: float) -> int:
    all_passed = True
    with tempfile.TemporaryDirectory(prefix="automation-fault-matrix-") as root_text:
        root = Path(root_text)
        state_dir = root / "mock-state"
        state_dir.mkdir()
        port = free_port()
        model_url = f"http://127.0.0.1:{port}/v1"
        mock = subprocess.Popen(
            [sys.executable, str(MOCK_SERVER), "--host", "127.0.0.1",
             "--port", str(port), "--state-dir", str(state_dir),
             "--timeout-seconds", str(timeout_seconds)],
            cwd=REPO_ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True,
        )
        try:
            wait_for_mock(port, mock)
            for scenario in scenarios:
                data_dir = root / scenario
                data_dir.mkdir()
                (state_dir / "requests.jsonl").write_text("", encoding="utf-8")
                set_mode(state_dir, "ok")
                try:
                    seed = subprocess.run(
                        [sys.executable, str(SEED_SCRIPT), "--data-dir", str(data_dir),
                         "--model-base-url", model_url],
                        cwd=REPO_ROOT, capture_output=True, text=True,
                        timeout=30, check=False,
                    )
                    if seed.returncode:
                        item = failure_record(scenario, "seed", f"exit_{seed.returncode}")
                    else:
                        child = subprocess.run(
                            [sys.executable, str(Path(__file__).resolve()),
                             "--_scenario-worker", scenario, "--data-dir", str(data_dir),
                             "--model-base-url", model_url,
                             "--mock-state-dir", str(state_dir)],
                            cwd=REPO_ROOT, capture_output=True, text=True,
                            timeout=45, check=False,
                        )
                        try:
                            item = last_json(child.stdout)
                        except ValueError:
                            item = failure_record(
                                scenario, "scenario", f"exit_{child.returncode}"
                            )
                        if child.returncode != 0 and item.get("pass") is True:
                            item = failure_record(
                                scenario, "scenario", f"exit_{child.returncode}"
                            )
                except subprocess.TimeoutExpired:
                    item = failure_record(scenario, "subprocess", "TimeoutExpired")
                except Exception as exc:  # safety net keeps stdout machine-readable
                    item = failure_record(scenario, "parent", type(exc).__name__)
                print(json.dumps(item, ensure_ascii=False, sort_keys=True), flush=True)
                all_passed = all_passed and item.get("pass") is True
        finally:
            mock.terminate()
            try:
                mock.wait(timeout=5)
            except subprocess.TimeoutExpired:
                mock.kill()
                mock.wait(timeout=5)
            if mock.stderr:
                mock.stderr.close()
    return 0 if all_passed else 1


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run the auditable optimization automation fault matrix."
    )
    parser.add_argument("--scenario", action="append", choices=SCENARIOS)
    parser.add_argument("--timeout-seconds", type=float, default=2.0)
    parser.add_argument("--_scenario-worker", choices=SCENARIOS, help=argparse.SUPPRESS)
    parser.add_argument("--data-dir", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--model-base-url", help=argparse.SUPPRESS)
    parser.add_argument("--mock-state-dir", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args._scenario_worker:
        if args.data_dir is None or not args.model_base_url or args.mock_state_dir is None:
            parser.error("internal scenario arguments are incomplete")
        try:
            item = RUNNERS[args._scenario_worker](
                args.data_dir.resolve(), args.model_base_url,
                args.mock_state_dir.resolve(),
            )
        except Exception as exc:
            item = failure_record(args._scenario_worker, "scenario", type(exc).__name__)
        print(json.dumps(item, ensure_ascii=False, sort_keys=True), flush=True)
        return 0 if item["pass"] else 1
    if args.timeout_seconds <= 1.0:
        parser.error("--timeout-seconds must exceed the one-second client timeout")
    selected = args.scenario or list(SCENARIOS)
    try:
        return run_parent(selected, args.timeout_seconds)
    except Exception as exc:
        for scenario in selected:
            item = failure_record(scenario, "infrastructure", type(exc).__name__)
            print(json.dumps(item, ensure_ascii=False, sort_keys=True), flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
