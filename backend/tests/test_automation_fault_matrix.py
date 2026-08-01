from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
RUNNER = REPO_ROOT / "scripts/integration/automation_fault_matrix.py"
MOCK_SERVER = REPO_ROOT / "scripts/integration/mock_openai_server.py"
SCENARIOS = {
    "timeout",
    "missing_usage",
    "missing_optimizer_api_key",
    "zero_or_exhausted_budget",
    "duplicate_feedback_event",
    "cross_category_isolation",
    "concurrent_workers",
}
REQUIRED_FIELDS = {
    "scenario",
    "expected",
    "observed",
    "pass",
    "run_id",
    "event_id",
    "category_key",
    "final_status",
    "error_code",
    "retry_count",
    "budget_reserved_micros",
    "budget_released_micros",
    "budget_spent_micros",
    "candidate_created",
    "parent_terminal_or_review",
}


def _load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run(*args: str) -> tuple[subprocess.CompletedProcess[str], list[dict]]:
    completed = subprocess.run(
        [sys.executable, str(RUNNER), *args],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    records = [
        json.loads(line)
        for line in completed.stdout.splitlines()
        if line.strip()
    ]
    return completed, records


def _skip_if_loopback_is_sandboxed(records: list[dict]) -> None:
    if records and all(
        item.get("observed")
        == {"runner_stage": "infrastructure", "exception_type": "PermissionError"}
        for item in records
    ):
        pytest.skip("execution sandbox forbids binding an isolated loopback port")


def test_mock_server_records_usage_contract_without_secrets(tmp_path: Path) -> None:
    mock = _load_module(MOCK_SERVER, "fault_matrix_mock_server")
    state = mock.State(tmp_path, timeout_seconds=1.25)
    (tmp_path / "mode.txt").write_text("missing_usage\n", encoding="utf-8")

    request_number = state.record(
        {
            "model": "deterministic-test-model",
            "messages": [
                {"role": "system", "content": "你是3D66提示词纠偏诊断专家。"}
            ],
        }
    )

    audit = json.loads((tmp_path / "requests.jsonl").read_text(encoding="utf-8"))
    assert request_number == 1
    assert audit == {
        "request": 1,
        "mode": "missing_usage",
        "model": "deterministic-test-model",
        "stage": "optimizer_diagnostic",
        "response_status": 200,
        "usage_included": False,
        "response_delayed": False,
    }
    assert state.timeout_seconds == 1.25


def test_runner_declares_exact_fault_matrix_and_stable_record_contract() -> None:
    runner = _load_module(RUNNER, "automation_fault_matrix_contract")
    assert set(runner.SCENARIOS) == SCENARIOS
    assert set(runner.RUNNERS) == SCENARIOS
    assert not (set(range(18081, 18091)) - runner.FORBIDDEN_PORTS)

    item = runner.failure_record("timeout", "test", "InjectedError")
    assert REQUIRED_FIELDS <= set(item)
    assert item["scenario"] == "timeout"
    assert item["pass"] is False
    assert item["parent_terminal_or_review"] is False

    processing = runner.record(
        "timeout",
        expected={},
        observed={},
        passed=False,
        final_status="running",
        parent_status="running",
    )
    awaiting_review = runner.record(
        "timeout",
        expected={},
        observed={},
        passed=True,
        final_status="awaiting_release_review",
        parent_status="awaiting_release_review",
    )
    assert processing["parent_terminal_or_review"] is False
    assert awaiting_review["parent_terminal_or_review"] is True


def test_failed_scenario_worker_emits_json_and_exits_nonzero(tmp_path: Path) -> None:
    missing_data = tmp_path / "missing-data"
    state_dir = tmp_path / "state"
    state_dir.mkdir()

    completed, records = _run(
        "--_scenario-worker",
        "duplicate_feedback_event",
        "--data-dir",
        str(missing_data),
        "--model-base-url",
        "http://127.0.0.1:1/v1",
        "--mock-state-dir",
        str(state_dir),
    )

    assert completed.returncode != 0
    assert len(records) == 1
    assert records[0]["scenario"] == "duplicate_feedback_event"
    assert records[0]["pass"] is False
    assert records[0]["final_status"] == "runner_failed"


def test_cross_category_policy_budget_regression_and_candidate_isolation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state_dir = tmp_path / "mock-state"
    state_dir.mkdir()
    seeded = subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "scripts/integration/automation_e2e_seed.py"),
            "--data-dir",
            str(tmp_path),
            "--model-base-url",
            "http://127.0.0.1:1/v1",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert seeded.returncode == 0, seeded.stderr

    runner = _load_module(RUNNER, "automation_fault_matrix_cross_category")
    runner.backend(tmp_path)
    from app import optimization_automation
    from app.optimizer import AutomationCandidateGeneration

    async def fake_generation(**_kwargs):
        return AutomationCandidateGeneration(
            candidates=[
                {
                    "system_prompt": "isolated candidate system",
                    "user_prompt": "isolated candidate user",
                    "change_note": "category-specific candidate",
                }
            ],
            input_tokens=200,
            output_tokens=100,
            total_tokens=300,
        )

    monkeypatch.setattr(
        optimization_automation,
        "generate_automation_candidates",
        fake_generation,
    )
    result = runner.scenario_cross_category(
        tmp_path,
        "http://127.0.0.1:1/v1",
        state_dir,
    )

    assert result["pass"] is True
    assert result["observed"]["case_counts"] == {
        "space_image": 1,
        "material_image": 2,
    }
    assert result["observed"]["profile_policy_unchanged"] is True
    assert result["observed"]["frozen_categories"] == {
        "space_image": "space_image",
        "material_image": "material_image",
    }
    assert result["observed"]["queued_case_categories"] == {
        "space_image": ["space_image"],
        "material_image": ["material_image", "material_image"],
    }
    assert result["observed"]["regression_categories"] == {
        "space_image": ["space_image"],
        "material_image": ["material_image"],
    }
    assert result["observed"]["candidate_counts"] == {
        "space_image": 1,
        "material_image": 1,
    }
    assert len(set(result["observed"]["optimizer_config_ids"].values())) == 2
    assert result["observed"]["actual_costs"] == result["observed"]["expected_costs"]
    assert len(set(result["observed"]["actual_costs"].values())) == 2
    assert result["observed"]["budget_after"]["reserved"] == 0


def test_fault_matrix_cli_covers_all_failures_and_is_machine_readable() -> None:
    completed, records = _run()
    _skip_if_loopback_is_sandboxed(records)

    assert completed.returncode == 0, (completed.stdout, completed.stderr)
    assert len(records) == len(SCENARIOS)
    assert {item["scenario"] for item in records} == SCENARIOS
    assert all(REQUIRED_FIELDS <= set(item) for item in records)
    assert all(item["pass"] is True for item in records)

    by_name = {item["scenario"]: item for item in records}
    assert by_name["timeout"]["error_code"] == "model_timeout"
    assert by_name["timeout"]["retry_count"] == 1
    assert by_name["timeout"]["candidate_created"] is False
    assert by_name["timeout"]["budget_reserved_micros"] > 0
    assert (
        by_name["timeout"]["budget_reserved_micros"]
        == by_name["timeout"]["budget_released_micros"]
        == by_name["timeout"]["budget_spent_micros"]
    )

    missing = by_name["missing_usage"]["observed"]
    assert missing["missing_usage"]["provider_response_status"] == 200
    assert missing["missing_usage"]["provider_usage_present"] is False
    assert missing["missing_usage"]["error_code"] == "optimizer_usage_missing"
    assert missing["missing_usage"]["provider_calls"] == 1
    assert missing["missing_usage"]["actual_cost_micros"] == 0
    assert missing["budget_after_missing_usage"]["reserved"] == 0
    assert (
        missing["budget_after_missing_usage"]["spent"]
        - missing["budget_before"]["spent"]
        == missing["missing_usage"]["charged_micros"]
    )
    assert missing["valid_usage_control"]["tokens"] == [200, 100, 300]
    assert missing["valid_usage_control"]["candidate_count"] == 1

    no_key = by_name["missing_optimizer_api_key"]
    assert no_key["final_status"] == "executor_config_blocked"
    assert no_key["observed"]["provider_calls"] == 0
    assert no_key["observed"]["attempt_count"] == 0
    assert no_key["observed"]["candidate_count"] == 0
    assert no_key["observed"]["budget"] == {"reserved": 0, "spent": 0}

    blocked = by_name["zero_or_exhausted_budget"]["observed"]
    assert blocked["zero_budget"]["status"] == "budget_blocked"
    assert blocked["exhausted_budget"]["status"] == "budget_blocked"
    assert blocked["provider_calls"] == 0
    assert blocked["candidate_count"] == 0

    duplicate = by_name["duplicate_feedback_event"]["observed"]
    assert duplicate["duplicate"] is True
    assert duplicate["event_count"] == duplicate["case_count"] == 1
    assert duplicate["audit_count"] == 1

    isolated = by_name["cross_category_isolation"]["observed"]
    assert isolated["case_counts"] == {"space_image": 1, "material_image": 2}
    assert isolated["profile_policy_unchanged"] is True
    assert isolated["frozen_categories"] == {
        "space_image": "space_image",
        "material_image": "material_image",
    }
    assert isolated["queued_case_categories"] == {
        "space_image": ["space_image"],
        "material_image": ["material_image", "material_image"],
    }
    assert isolated["regression_categories"] == {
        "space_image": ["space_image"],
        "material_image": ["material_image"],
    }
    assert isolated["candidate_counts"] == {
        "space_image": 1,
        "material_image": 1,
    }
    assert len(set(isolated["optimizer_config_ids"].values())) == 2
    assert isolated["actual_costs"] == isolated["expected_costs"]
    assert len(set(isolated["actual_costs"].values())) == 2
    assert isolated["budget_after"]["reserved"] == 0

    race = by_name["concurrent_workers"]["observed"]
    assert race["optimizer_calls"] == 1
    assert race["run_count"] == 1
    assert race["completed_mapping_count"] == 1
    assert race["attempt_count"] == 1
    assert race["candidate_count"] == 1
    assert race["completion_audit_count"] == 1
    assert race["run_case_ids"] == [race["case_id"]]

    parent_expected = {
        "timeout": True,
        "missing_usage": True,
        "missing_optimizer_api_key": False,
        "zero_or_exhausted_budget": False,
        "duplicate_feedback_event": False,
        "cross_category_isolation": True,
        "concurrent_workers": True,
    }
    assert {
        name: item["parent_terminal_or_review"]
        for name, item in by_name.items()
    } == parent_expected


def test_duplicate_event_scenario_is_repeatable_across_fresh_databases() -> None:
    first_process, first = _run("--scenario", "duplicate_feedback_event")
    _skip_if_loopback_is_sandboxed(first)
    second_process, second = _run("--scenario", "duplicate_feedback_event")

    assert first_process.returncode == second_process.returncode == 0
    assert len(first) == len(second) == 1
    assert first == second
