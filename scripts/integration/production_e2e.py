#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import http.cookiejar
import json
import os
import secrets
import signal
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
MOCK_SERVER = REPO_ROOT / "scripts/integration/mock_openai_server.py"
SEED_SCRIPT = REPO_ROOT / "scripts/integration/automation_e2e_seed.py"
TERMINAL_RUN_STATUSES = {
    "awaiting_review",
    "approved",
    "rejected",
    "published",
    "blocked",
    "failed",
    "archived",
}


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class Api:
    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip("/")
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar())
        )

    def request(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        body = (
            json.dumps(payload, ensure_ascii=False).encode("utf-8")
            if payload is not None
            else None
        )
        request = urllib.request.Request(
            f"{self.base_url}{path}",
            data=body,
            method=method,
            headers={"Content-Type": "application/json"},
        )
        try:
            with self.opener.open(request, timeout=10) as response:
                value = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"http_{exc.code}:{method}:{path}:{detail}") from exc
        if not isinstance(value, dict):
            raise RuntimeError(f"unexpected_response:{method}:{path}")
        return value

    def get(self, path: str) -> dict[str, Any]:
        return self.request("GET", path)

    def post(self, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        return self.request("POST", path, payload)


def wait_for_health(url: str, process: subprocess.Popen[str], timeout: float = 30) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"process_exited:{process.returncode}")
        try:
            with urllib.request.urlopen(url, timeout=0.5) as response:
                if response.status == 200:
                    return
        except OSError:
            time.sleep(0.1)
    raise RuntimeError(f"service_not_ready:{url}")


def terminate(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return
    process.send_signal(signal.SIGINT)
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


def database_rows(data_dir: Path, statement: str, parameters: tuple[Any, ...] = ()) -> list[tuple[Any, ...]]:
    database = data_dir / "database" / "app.db"
    with sqlite3.connect(database) as connection:
        return list(connection.execute(statement, parameters))


def scalar(data_dir: Path, statement: str, parameters: tuple[Any, ...] = ()) -> Any:
    rows = database_rows(data_dir, statement, parameters)
    return rows[0][0] if rows else None


def create_material_package(api: Api, data_dir: Path) -> tuple[int, int]:
    asset_id = int(
        scalar(
            data_dir,
            "SELECT id FROM assets WHERE category_key = ? ORDER BY id LIMIT 1",
            ("space_image",),
        )
    )
    package = api.post(
        "/api/material-packages",
        {
            "name": "生产主链路真实验收包",
            "asset_ids": [asset_id],
            "category_key": "space_image",
        },
    )
    return int(package["id"]), asset_id


def wait_for_production_stage(
    api: Api,
    run_id: int,
    expected: set[str],
    *,
    timeout: float = 90,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    last: dict[str, Any] = {}
    while time.monotonic() < deadline:
        last = api.post(f"/api/evaluation-production-runs/{run_id}/reconcile", {})
        if last.get("status") in expected:
            return last
        time.sleep(0.5)
    raise RuntimeError(
        "production_run_timeout:" + json.dumps(last, ensure_ascii=False, default=str)
    )


def first_production_evaluation_id(data_dir: Path, run: dict[str, Any]) -> int:
    job_ids = [int(value) for value in run.get("job_ids") or []]
    if not job_ids:
        raise RuntimeError("production_job_identity_missing")
    placeholders = ",".join("?" for _ in job_ids)
    evaluation_id = scalar(
        data_dir,
        f"SELECT id FROM evaluation_results WHERE job_id IN ({placeholders}) "
        "ORDER BY id LIMIT 1",
        tuple(job_ids),
    )
    if evaluation_id is None:
        raise RuntimeError("production_evaluation_missing")
    return int(evaluation_id)


def submit_correction(api: Api, evaluation_id: int) -> None:
    panel = api.post(
        f"/api/evaluations/{evaluation_id}/review-panel/open",
        {"required_reviewers": 1},
    )
    api.post(
        f"/api/evaluations/{evaluation_id}/review-panel/votes",
        {
            "reviewer_name": "ignored-client-value",
            "decision": "corrected",
            "note": "真实 E2E：普通材质表现不应高估",
            "corrections": [
                {
                    "target_type": "dimension",
                    "field_key": "color_material",
                    "model_value": 5,
                    "human_value": 3,
                    "reason_codes": ["overrated"],
                    "note": "收紧普通材质等级锚点",
                }
            ],
            "expected_panel_revision": int(panel["revision"]),
        },
    )


def run_once(api: Api, data_dir: Path, iteration: int) -> dict[str, Any]:
    package_id, asset_id = create_material_package(api, data_dir)
    created = api.post(
        "/api/evaluation-production-runs",
        {
            "material_package_id": package_id,
            "category_key": "space_image",
            "idempotency_key": f"production-e2e-{iteration}-{secrets.token_hex(8)}",
        },
    )
    run = created.get("run") or created
    run_id = int(run["id"])
    run = wait_for_production_stage(api, run_id, {"first_review", "failed"})
    if run["status"] != "first_review":
        raise RuntimeError(f"evaluation_failed:{run}")
    evaluation_id = first_production_evaluation_id(data_dir, run)
    submit_correction(api, evaluation_id)
    run = wait_for_production_stage(api, run_id, TERMINAL_RUN_STATUSES)
    if run["status"] != "awaiting_review":
        raise RuntimeError(f"production_not_awaiting_review:{run}")
    package_id = int(run["evaluation_package_id"])
    package = api.get(f"/api/evaluation-packages/{package_id}")
    return {
        "run_id": run_id,
        "asset_id": asset_id,
        "evaluation_id": evaluation_id,
        "package_id": package_id,
        "automation_run_id": run["automation_run_id"],
        "regression_run_id": run["regression_run_id"],
        "run_status": run["status"],
        "package_status": package["status"],
        "regression_status": (run.get("regression") or {}).get("status"),
        "regression_recommendation": (run.get("regression") or {}).get("recommendation"),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--iterations", type=int, default=3)
    parser.add_argument("--keep-data", action="store_true")
    args = parser.parse_args()
    if args.iterations < 1:
        parser.error("--iterations must be positive")
    password = os.getenv("LABEL_LAB_E2E_PASSWORD")
    if not password:
        raise SystemExit("LABEL_LAB_E2E_PASSWORD is required")

    temporary: tempfile.TemporaryDirectory[str] | None = None
    if args.keep_data:
        root = Path(tempfile.mkdtemp(prefix="label-lab-production-e2e-"))
    else:
        temporary = tempfile.TemporaryDirectory(prefix="label-lab-production-e2e-")
        root = Path(temporary.name)
    data_dir = root / "data"
    state_dir = root / "mock-state"
    data_dir.mkdir()
    state_dir.mkdir()
    mock_port = free_port()
    app_port = free_port()
    model_url = f"http://127.0.0.1:{mock_port}/v1"
    mock_log = (root / "mock.log").open("w", encoding="utf-8")
    app_log = (root / "app.log").open("w", encoding="utf-8")
    mock = subprocess.Popen(
        [
            sys.executable,
            str(MOCK_SERVER),
            "--host",
            "127.0.0.1",
            "--port",
            str(mock_port),
            "--state-dir",
            str(state_dir),
        ],
        cwd=REPO_ROOT,
        stdout=mock_log,
        stderr=subprocess.STDOUT,
        text=True,
    )
    app: subprocess.Popen[str] | None = None
    try:
        wait_for_health(f"http://127.0.0.1:{mock_port}/health", mock, timeout=10)
        seeded = subprocess.run(
            [
                sys.executable,
                str(SEED_SCRIPT),
                "--data-dir",
                str(data_dir),
                "--model-base-url",
                model_url,
            ],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        if seeded.returncode:
            raise RuntimeError(f"seed_failed:{seeded.stderr}")
        seed = json.loads(seeded.stdout)
        env = os.environ.copy()
        env.update(
            {
                "DATA_DIR": str(data_dir),
                "DATABASE_URL": f"sqlite:///{(data_dir / 'database/app.db').as_posix()}",
                "API_KEY_MASTER_KEY_FILE": str(data_dir / "secrets/master.key"),
                "APP_HOST": "127.0.0.1",
                "APP_PORT": str(app_port),
                "BROWSER": "true",
                "PYTHONPATH": str(REPO_ROOT / "backend"),
            }
        )
        app = subprocess.Popen(
            [sys.executable, "-X", "utf8", "-m", "app.launcher"],
            cwd=REPO_ROOT / "backend",
            env=env,
            stdout=app_log,
            stderr=subprocess.STDOUT,
            text=True,
        )
        wait_for_health(f"http://127.0.0.1:{app_port}/api/health", app)
        api = Api(f"http://127.0.0.1:{app_port}")
        api.post("/api/auth/login", {"username": "sol", "password": password})
        baseline_before = int(
            scalar(
                data_dir,
                "SELECT json_extract(automation_config_json, '$.baseline_strategy_bundle_id') "
                "FROM evaluation_category_profiles WHERE category_key = ?",
                ("space_image",),
            )
        )
        runs = [run_once(api, data_dir, index) for index in range(1, args.iterations + 1)]

        rejected = runs[0]
        api.post(
            f"/api/evaluation-packages/{rejected['package_id']}/reject",
            {"note": "真实 E2E 拒绝分支，要求继续优化"},
        )
        baseline_after_reject = int(
            scalar(
                data_dir,
                "SELECT json_extract(automation_config_json, '$.baseline_strategy_bundle_id') "
                "FROM evaluation_category_profiles WHERE category_key = ?",
                ("space_image",),
            )
        )
        winner = runs[-1]
        if winner["package_id"] == rejected["package_id"]:
            retry_package = api.post(
                f"/api/evaluation-packages/from-automation/{winner['automation_run_id']}",
                {
                    "package_key": f"production-e2e-retry-{secrets.token_hex(8)}",
                    "change_summary": "二审拒绝后保留原证据并创建新评测包重试",
                },
            )
            winner = {**winner, "package_id": int(retry_package["id"])}
        api.post(
            f"/api/evaluation-packages/{winner['package_id']}/approve",
            {"note": "真实 E2E 二审批准，尚不发布"},
        )
        baseline_after_approve = int(
            scalar(
                data_dir,
                "SELECT json_extract(automation_config_json, '$.baseline_strategy_bundle_id') "
                "FROM evaluation_category_profiles WHERE category_key = ?",
                ("space_image",),
            )
        )
        published = api.post(
            f"/api/evaluation-packages/{winner['package_id']}/publish",
            {"note": "真实 E2E 显式发布"},
        )
        baseline_after_publish = int(
            scalar(
                data_dir,
                "SELECT json_extract(automation_config_json, '$.baseline_strategy_bundle_id') "
                "FROM evaluation_category_profiles WHERE category_key = ?",
                ("space_image",),
            )
        )
        requests = [
            json.loads(line)
            for line in (state_dir / "requests.jsonl").read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        schema_versions = [
            row[0]
            for row in database_rows(
                data_dir,
                "SELECT json_extract(strategy_snapshot_json, '$.resolved_dimension_schema_version') "
                "FROM evaluation_results ORDER BY id",
            )
        ]
        evidence = {
            "pass": (
                all(item["run_status"] == "awaiting_review" for item in runs)
                and baseline_after_reject == baseline_before
                and baseline_after_approve == baseline_before
                and baseline_after_publish != baseline_before
                and published["status"] == "published"
                and set(schema_versions) == {"1.3.0"}
            ),
            "iterations": args.iterations,
            "runs": runs,
            "baseline_bundle_ids": {
                "before": baseline_before,
                "after_reject": baseline_after_reject,
                "after_approve": baseline_after_approve,
                "after_publish": baseline_after_publish,
            },
            "published_package_id": winner["package_id"],
            "provider_request_count": len(requests),
            "provider_stage_counts": {
                stage: sum(item.get("stage") == stage for item in requests)
                for stage in sorted({str(item.get("stage")) for item in requests})
            },
            "dimension_schema_versions": schema_versions,
            "seed_baseline_contract_errors": seed.get("baseline_contract_errors"),
            "data_fingerprint": hashlib.sha256(
                (data_dir / "database/app.db").read_bytes()
            ).hexdigest(),
        }
        print(json.dumps(evidence, ensure_ascii=False, sort_keys=True))
        if not evidence["pass"]:
            raise SystemExit(1)
        if args.keep_data:
            print(json.dumps({"kept_data_root": str(root)}, ensure_ascii=False))
    finally:
        if app is not None:
            terminate(app)
        terminate(mock)
        app_log.close()
        mock_log.close()
        if temporary is not None:
            temporary.cleanup()


if __name__ == "__main__":
    main()
