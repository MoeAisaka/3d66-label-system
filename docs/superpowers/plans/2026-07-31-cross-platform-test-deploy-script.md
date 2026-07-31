# Cross-Platform Test Deploy Script Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Provide Windows and macOS teammates with a safe local command that deploys only Codeup `main` to the existing Docker test environment on `192.168.1.35:8081`.

**Architecture:** A Python standard-library orchestrator validates the local repository, fetches Codeup `main`, creates a Git bundle, and transfers it with system `scp`/`ssh`. Thin Windows and macOS launchers call the same Python entry point. A server-side Bash script performs commit validation, guarded checkout, Compose rebuild, health verification, and rollback without touching persistent data.

**Tech Stack:** Python 3 standard library, Git, OpenSSH (`ssh`/`scp`), POSIX shell/Bash, Docker Compose

---

## File Structure

- Create `scripts/deploy-test.py`: cross-platform local orchestration and dry-run behavior.
- Create `scripts/deploy-test-server.sh`: guarded server update, health wait, and rollback.
- Create `部署测试环境.cmd`: Windows double-click launcher.
- Create `部署测试环境.command`: macOS double-click and terminal launcher.
- Modify `README.md`: teammate-facing deployment instructions and prerequisites.
- Local-only test `scripts/tests/test_deploy_test.py`: unit coverage for local validation helpers; run locally but exclude from commits per repository instructions.

### Task 1: Local Orchestrator Validation Core

**Files:**
- Create: `scripts/deploy-test.py`
- Local-only test: `scripts/tests/test_deploy_test.py`

- [ ] **Step 1: Write failing tests for command lookup, origin validation, and commit validation**

Create local-only tests that import `scripts/deploy-test.py` and assert:

```python
def test_validate_origin_accepts_exact_codeup_url():
    assert validate_origin(
        "https://codeup.aliyun.com/3d66/tepeng/3d66.label-system.git"
    ) is None


def test_validate_origin_rejects_other_repository():
    with pytest.raises(DeployError, match="Codeup"):
        validate_origin("https://github.com/example/other.git")


def test_validate_commit_rejects_non_sha():
    with pytest.raises(DeployError, match="commit"):
        validate_commit("main")
```

- [ ] **Step 2: Run tests and verify failure**

Run: `python -m pytest scripts/tests/test_deploy_test.py -q`

Expected: FAIL because `scripts/deploy-test.py` and its helpers do not exist.

- [ ] **Step 3: Implement constants and validation helpers**

Implement:

```python
CODEUP_URL = "https://codeup.aliyun.com/3d66/tepeng/3d66.label-system.git"
SERVER = "yuankangzhi@192.168.1.35"
REMOTE_PROJECT = "/opt/3d66-label-system"
HEALTH_URL = "http://192.168.1.35:8081/api/health"


class DeployError(RuntimeError):
    pass


def validate_origin(url: str) -> None:
    if url.rstrip("/") != CODEUP_URL.rstrip("/"):
        raise DeployError(f"origin must be the Codeup repository: {CODEUP_URL}")


def validate_commit(commit: str) -> None:
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise DeployError("resolved main commit is not a full Git commit SHA")
```

Add helpers that run subprocesses with argument arrays, preserve inherited stdin/stdout for authentication commands, and return captured output only for non-secret Git queries.

- [ ] **Step 4: Run local helper tests**

Run: `python -m pytest scripts/tests/test_deploy_test.py -q`

Expected: PASS.

- [ ] **Step 5: Commit implementation only**

Check: `git diff --cached --name-only`

Stage and commit only `scripts/deploy-test.py`; do not stage `scripts/tests/test_deploy_test.py`.

Commit: `chore: add deploy preflight checks`

### Task 2: Bundle Creation and Dry Run

**Files:**
- Modify: `scripts/deploy-test.py`
- Local-only test: `scripts/tests/test_deploy_test.py`

- [ ] **Step 1: Write failing tests for temporary artifact cleanup and dry-run summary**

Add tests using a temporary Git repository and monkeypatched subprocess runner. Assert that dry-run:

```python
result = prepare_release(repo_root, dry_run=True)
assert result.commit == "a" * 40
assert result.bundle_path.exists()
cleanup_release(result)
assert not result.temp_dir.exists()
```

- [ ] **Step 2: Run the focused tests and verify failure**

Run: `python -m pytest scripts/tests/test_deploy_test.py -q`

Expected: FAIL because release preparation is not implemented.

- [ ] **Step 3: Implement repository preflight and bundle creation**

The implementation must:

```text
git rev-parse --show-toplevel
git remote get-url origin
git fetch origin main
git rev-parse origin/main
git bundle create <temp>/main.bundle origin/main
git bundle verify <temp>/main.bundle
```

Use `tempfile.TemporaryDirectory` or an owned `mkdtemp` path, and always clean it in `finally`. `--dry-run` stops before `scp` and prints the exact full and short commit IDs.

- [ ] **Step 4: Verify dry run against the real Codeup clone**

Run: `python scripts/deploy-test.py --dry-run`

Expected: exit 0, exact Codeup repository displayed, `origin/main` fetched, bundle verified, no SSH connection attempted.

- [ ] **Step 5: Commit implementation only**

Check: `git diff --cached --name-only`

Commit only `scripts/deploy-test.py` with message `chore: prepare Codeup main deployment bundle`.

### Task 3: Server-Side Guarded Deployment

**Files:**
- Create: `scripts/deploy-test-server.sh`
- Local-only test: `scripts/tests/test_deploy_test.py`

- [ ] **Step 1: Write failing static contract tests**

Add tests that inspect the shell script and require these literal safeguards:

```python
assert 'PROJECT_DIR="/opt/3d66-label-system"' in script
assert 'CONTAINER_NAME="3d66-label-system-test"' in script
assert 'HEALTH_URL="http://127.0.0.1:8081/api/health"' in script
assert "docker compose up -d --build" in script
assert "git clean" not in script
assert "/opt/3d66-label-system-data" not in destructive_commands(script)
```

- [ ] **Step 2: Run tests and shell parser**

Run: `python -m pytest scripts/tests/test_deploy_test.py -q`

Expected: FAIL because the script does not exist.

Run after creation starts: `bash -n scripts/deploy-test-server.sh`.

- [ ] **Step 3: Implement guarded deployment**

The script accepts exactly two arguments: bundle path and 40-character commit. Implement functions:

```bash
fail() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }
compose_up() { sudo docker compose up -d --build; }
wait_for_health() { ...; }
rollback() { git reset --hard "$previous_commit"; compose_up; }
```

Before code changes, reject a dirty tracked worktree with:

```bash
test -z "$(git status --porcelain --untracked-files=no)" || fail "server worktree has tracked changes"
```

Fetch only from the uploaded bundle, verify the requested object is a commit, record `previous_commit`, reset to the requested commit, rebuild, then poll `docker inspect` and `curl` for up to 180 seconds. A post-check failure invokes rollback and returns nonzero. Never run `git clean`, remove the data directory, or address the old `3d66-label-system` container.

- [ ] **Step 4: Verify parser and static safeguards**

Run:

```text
bash -n scripts/deploy-test-server.sh
python -m pytest scripts/tests/test_deploy_test.py -q
rg -n "git clean|rm -rf|9093|3d66-label-system-data" scripts/deploy-test-server.sh
```

Expected: parser and tests pass; search shows no destructive command or old-container action.

- [ ] **Step 5: Commit implementation only**

Check: `git diff --cached --name-only`

Commit only `scripts/deploy-test-server.sh` with message `chore: add guarded test server deployment`.

### Task 4: Upload and Remote Invocation

**Files:**
- Modify: `scripts/deploy-test.py`
- Local-only test: `scripts/tests/test_deploy_test.py`

- [ ] **Step 1: Write failing command-construction tests**

Test that generated commands use argument arrays and fixed destinations:

```python
assert scp_command[-1].startswith("yuankangzhi@192.168.1.35:/tmp/")
assert ssh_command[:2] == ["ssh", "yuankangzhi@192.168.1.35"]
assert commit in ssh_command[-1]
assert branch_name not in ssh_command[-1]
```

- [ ] **Step 2: Run tests and verify failure**

Run: `python -m pytest scripts/tests/test_deploy_test.py -q`

Expected: FAIL because transfer and invocation builders are missing.

- [ ] **Step 3: Implement confirmation, transfer, invocation, and cleanup**

Require exact interactive confirmation `DEPLOY` unless `--yes` is supplied. Upload bundle and server script using unique names derived from the commit and process ID. Invoke:

```text
ssh yuankangzhi@192.168.1.35 bash /tmp/<script> /tmp/<bundle> <commit>
```

Do not construct shell commands containing passwords. After success or failure, attempt best-effort deletion of only the two known remote temporary paths via a fixed `rm -f -- <path> <path>` command.

- [ ] **Step 4: Verify local tests and real dry run**

Run:

```text
python -m pytest scripts/tests/test_deploy_test.py -q
python scripts/deploy-test.py --dry-run
```

Expected: tests pass; dry-run performs no network transfer to the server.

- [ ] **Step 5: Commit implementation only**

Check: `git diff --cached --name-only`

Commit only `scripts/deploy-test.py` with message `chore: deploy bundles through SSH`.

### Task 5: Windows and macOS Launchers

**Files:**
- Create: `部署测试环境.cmd`
- Create: `部署测试环境.command`

- [ ] **Step 1: Add launcher contract tests locally**

Extend local-only tests to assert the Windows launcher uses `%~dp0` and tries `py -3` before `python`, while the macOS launcher uses its own directory and `python3`:

```python
assert "%~dp0" in windows_launcher
assert "py -3" in windows_launcher
assert "python3" in mac_launcher
assert 'dirname "$0"' in mac_launcher
```

- [ ] **Step 2: Run tests and verify failure**

Run: `python -m pytest scripts/tests/test_deploy_test.py -q`

Expected: FAIL because launchers do not exist.

- [ ] **Step 3: Implement both thin launchers**

Windows launcher behavior:

```bat
@echo off
setlocal
cd /d "%~dp0"
where py >nul 2>nul && py -3 scripts\deploy-test.py %* && goto :done
python scripts\deploy-test.py %*
:done
set "EXIT_CODE=%ERRORLEVEL%"
pause
exit /b %EXIT_CODE%
```

macOS launcher behavior:

```sh
#!/bin/sh
set -eu
REPO_ROOT=$(CDPATH= cd -- "$(dirname "$0")" && pwd)
cd "$REPO_ROOT"
exec python3 scripts/deploy-test.py "$@"
```

Set executable mode with `git update-index --chmod=+x 部署测试环境.command`.

- [ ] **Step 4: Verify launchers**

Run:

```text
cmd /c "部署测试环境.cmd --dry-run"
sh -n 部署测试环境.command
sh 部署测试环境.command --dry-run
git ls-files --stage 部署测试环境.command
```

Expected: both dry runs succeed; macOS launcher mode is `100755`.

- [ ] **Step 5: Commit launchers only**

Check: `git diff --cached --name-only` and exclude local tests.

Commit: `chore: add cross-platform deploy launchers`.

### Task 6: Documentation and End-to-End Validation

**Files:**
- Modify: `README.md`
- Modify: `PROJECT_STATUS.md`

- [ ] **Step 1: Document prerequisites and usage**

Add a concise section covering:

```text
Windows: double-click 部署测试环境.cmd
macOS: double-click 部署测试环境.command or run python3 scripts/deploy-test.py
Prerequisites: Git, Python 3, OpenSSH ssh/scp, Codeup access, test-server SSH/sudo access
Source: fixed Codeup main
Target: http://192.168.1.35:8081
Data: /opt/3d66-label-system-data is preserved
```

Update `PROJECT_STATUS.md` with the script commit, validation evidence, and the remaining limitation that passwords are interactive until SSH keys and restricted passwordless sudo are approved.

- [ ] **Step 2: Run all non-deploy verification**

Run:

```text
python -m py_compile scripts/deploy-test.py
python -m pytest scripts/tests/test_deploy_test.py -q
bash -n scripts/deploy-test-server.sh
sh -n 部署测试环境.command
python scripts/deploy-test.py --dry-run
git diff --check
```

Expected: all commands exit 0.

- [ ] **Step 3: Review staged files before documentation commit**

Run: `git diff --cached --name-only`

Expected: only `README.md` and `PROJECT_STATUS.md`; no test files.

- [ ] **Step 4: Commit documentation**

Commit: `docs: document cross-platform test deployment`.

- [ ] **Step 5: Perform one controlled real deployment**

Run: `python scripts/deploy-test.py --yes`

Expected:

- Codeup `origin/main` commit is displayed.
- Server HEAD equals that commit.
- `docker inspect` reports `running healthy` for `3d66-label-system-test`.
- `curl http://192.168.1.35:8081/api/health` returns `status=ok`.
- Mount remains `/opt/3d66-label-system-data:/data`.

- [ ] **Step 6: Final repository checks and push**

Run:

```text
git status --short --branch
git diff --check
git log --oneline -8
git diff origin/main...HEAD --name-only
```

Confirm no local-only test file is committed. Push the task branch to Codeup, then fast-forward `main` only after final review.
