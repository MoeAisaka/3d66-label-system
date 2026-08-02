from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

import pytest

from app import security, windows_deploy


FAKE_DPAPI_REFERENCE = "dpapi:v1:ZmFrZS1kcGFwaS1jaXBoZXJ0ZXh0"
FAKE_DPAPI_MACHINE_REFERENCE = (
    "dpapi-machine:v1:ZmFrZS1tYWNoaW5lLWRwYXBpLWNpcGhlcnRleHQ="
)
FAKE_TOKEN = "f" * 64


@contextmanager
def _sqlite_connection(path: Path) -> Iterator[sqlite3.Connection]:
    connection = sqlite3.connect(path)
    try:
        with connection:
            yield connection
    finally:
        connection.close()


def _fake_dpapi_probe() -> str:
    return security.DPAPI_SCOPE_LOCAL_MACHINE


def _make_database(
    path: Path,
    *,
    marker: str = "source",
    include_secrets: bool = True,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with _sqlite_connection(path) as connection:
        connection.executescript(
            """
            CREATE TABLE schema_migrations (
                version INTEGER PRIMARY KEY,
                name TEXT NOT NULL
            );
            CREATE TABLE session_tokens (
                id INTEGER PRIMARY KEY,
                token_hash TEXT NOT NULL
            );
            CREATE TABLE model_configs (
                id INTEGER PRIMARY KEY,
                encrypted_api_key TEXT
            );
            CREATE TABLE optimizer_configs (
                id INTEGER PRIMARY KEY,
                encrypted_api_key TEXT
            );
            CREATE TABLE test_markers (
                id INTEGER PRIMARY KEY,
                value TEXT NOT NULL
            );
            """
        )
        connection.execute(
            "INSERT INTO schema_migrations(version, name) VALUES (?, ?)",
            (windows_deploy.SUPPORTED_SCHEMA_VERSION, "current"),
        )
        connection.execute("INSERT INTO test_markers(value) VALUES (?)", (marker,))
        if include_secrets:
            connection.execute(
                "INSERT INTO session_tokens(token_hash) VALUES (?)",
                (FAKE_TOKEN,),
            )
            connection.execute(
                "INSERT INTO model_configs(encrypted_api_key) VALUES (?)",
                (FAKE_DPAPI_REFERENCE,),
            )
            connection.execute(
                "INSERT INTO optimizer_configs(encrypted_api_key) VALUES (?)",
                ("ZmFrZS1sZWdhY3ktZHBhcGk=",),
            )


def _make_repo(root: Path, *, built: bool = True) -> Path:
    repo = root / "repo 中文"
    (repo / "backend" / "app").mkdir(parents=True)
    (repo / "backend" / "requirements.txt").write_text("pytest\n", encoding="utf-8")
    (repo / "backend" / "app" / "launcher.py").write_text("", encoding="utf-8")
    (repo / "frontend").mkdir()
    (repo / "frontend" / "package-lock.json").write_text("{}\n", encoding="utf-8")
    (repo / "scripts" / "windows").mkdir(parents=True)
    (repo / "scripts" / "windows" / "start.ps1").write_text("", encoding="utf-8")
    (repo / ".venv" / "Scripts").mkdir(parents=True)
    (repo / ".venv" / "Scripts" / "python.exe").write_bytes(b"fake")
    if built:
        (repo / "frontend" / "dist").mkdir()
        (repo / "frontend" / "dist" / "index.html").write_text(
            "<!doctype html>",
            encoding="utf-8",
        )
    return repo


def _make_data(root: Path, *, marker: str = "source", image: bytes = b"image") -> Path:
    data_dir = root / "数据 目录"
    _make_database(data_dir / "database" / "app.db", marker=marker)
    (data_dir / "images" / "嵌套 目录").mkdir(parents=True)
    (data_dir / "images" / "嵌套 目录" / "样例.jpg").write_bytes(image)
    (data_dir / "logs").mkdir()
    (data_dir / "logs" / "service.log").write_text(FAKE_TOKEN, encoding="utf-8")
    (data_dir / ".env").write_text("API_KEY=FAKE_DO_NOT_USE\n", encoding="utf-8")
    return data_dir


def _create_backup(tmp_path: Path, *, marker: str = "backup") -> tuple[Path, Path, Path]:
    repo = _make_repo(tmp_path / "repository")
    data_dir = _make_data(tmp_path / "source", marker=marker)
    backup = windows_deploy.create_backup(
        data_dir,
        backup_root=tmp_path / "备份 根目录",
        repo_root=repo,
        now=datetime(2026, 7, 30, 1, 2, 3, tzinfo=timezone.utc),
    )
    return repo, data_dir, backup


def _read_marker(database: Path) -> str:
    with _sqlite_connection(database) as connection:
        row = connection.execute("SELECT value FROM test_markers").fetchone()
    assert row is not None
    return str(row[0])


def _read_count(database: Path, table: str, where: str = "") -> int:
    with _sqlite_connection(database) as connection:
        row = connection.execute(f"SELECT COUNT(*) FROM {table} {where}").fetchone()
    assert row is not None
    return int(row[0])


def test_readonly_connection_context_closes_handle(tmp_path: Path) -> None:
    database = tmp_path / "app.db"
    _make_database(database)

    with windows_deploy._sqlite_connect_readonly(database) as connection:
        assert connection.execute("SELECT 1").fetchone() == (1,)

    with pytest.raises(sqlite3.ProgrammingError, match="closed database"):
        connection.execute("SELECT 1")


def test_database_replace_displaces_existing_file_before_installing_staged(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    target = tmp_path / "database" / "app.db"
    staged = tmp_path / "stage" / "app.db"
    target.parent.mkdir(parents=True)
    staged.parent.mkdir(parents=True)
    target.write_bytes(b"old")
    staged.write_bytes(b"new")
    real_replace = windows_deploy.os.replace
    calls: list[tuple[Path, Path]] = []

    def record_replace(source, destination) -> None:
        calls.append((Path(source), Path(destination)))
        real_replace(source, destination)

    monkeypatch.setattr(windows_deploy.os, "replace", record_replace)

    windows_deploy._replace_database(staged, target)

    assert target.read_bytes() == b"new"
    assert calls[0][0] == target
    assert calls[0][1].name.startswith(".app-db-displaced-")
    assert calls[1] == (staged, target)
    assert not list(target.parent.glob(".app-db-displaced-*"))


def test_database_replace_restores_displaced_file_when_install_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    target = tmp_path / "database" / "app.db"
    staged = tmp_path / "stage" / "app.db"
    target.parent.mkdir(parents=True)
    staged.parent.mkdir(parents=True)
    target.write_bytes(b"old")
    staged.write_bytes(b"new")
    real_replace = windows_deploy.os.replace
    call_count = 0

    def fail_install(source, destination) -> None:
        nonlocal call_count
        call_count += 1
        if call_count == 2:
            raise PermissionError("injected staged install failure")
        real_replace(source, destination)

    monkeypatch.setattr(windows_deploy.os, "replace", fail_install)

    with pytest.raises(PermissionError, match="injected"):
        windows_deploy._replace_database(staged, target)

    assert target.read_bytes() == b"old"
    assert staged.read_bytes() == b"new"
    assert not list(target.parent.glob(".app-db-displaced-*"))


def test_windows_data_dir_priority_is_explicit_then_environment_then_dotenv_then_localappdata(
    tmp_path: Path,
) -> None:
    repo = _make_repo(tmp_path)
    dotenv_data = tmp_path / "dotenv data"
    (repo / ".env").write_text(f'DATA_DIR="{dotenv_data}"\n', encoding="utf-8")
    process_data = tmp_path / "process data"
    explicit_data = tmp_path / "explicit data"
    env = {
        "DATA_DIR": str(process_data),
        "LOCALAPPDATA": str(tmp_path / "Local App Data"),
    }

    assert windows_deploy.resolve_data_dir(repo, explicit=explicit_data, env=env) == explicit_data
    assert windows_deploy.resolve_data_dir(repo, env=env) == process_data
    assert windows_deploy.resolve_data_dir(
        repo,
        env={"LOCALAPPDATA": str(tmp_path / "Local App Data")},
    ) == dotenv_data
    (repo / ".env").unlink()
    assert windows_deploy.resolve_data_dir(repo, env=env | {"DATA_DIR": ""}) == (
        tmp_path / "Local App Data" / "3d66-label-system"
    )


def test_relative_data_dir_and_missing_localappdata_fail_closed(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    with pytest.raises(windows_deploy.DeployError) as relative_error:
        windows_deploy.resolve_data_dir(repo, explicit=Path("relative-data"), env={})
    assert relative_error.value.code == "PATH_NOT_ABSOLUTE"

    with pytest.raises(windows_deploy.DeployError) as default_error:
        windows_deploy.resolve_data_dir(repo, env={})
    assert default_error.value.code == "LOCALAPPDATA_MISSING"


def test_case_insensitive_repository_containment_is_rejected(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    differently_cased = Path(str(repo / "data").upper())
    with pytest.raises(windows_deploy.DeployError) as error:
        windows_deploy._require_outside_repo(differently_cased, repo, label="DATA_DIR")
    assert error.value.code == "PATH_INSIDE_REPOSITORY"


def test_doctor_checks_complete_install_without_creating_data(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    data_dir = tmp_path / "尚未创建 数据"

    report = windows_deploy.doctor(
        repo,
        data_dir=data_dir,
        platform_name="Windows",
        python_version=(3, 12, 4),
        node_version="v26.1.0",
        npm_version="11.2.0",
        dpapi_probe=_fake_dpapi_probe,
    )

    assert report.data_dir == data_dir
    assert report.database_exists is False
    assert not data_dir.exists()
    assert "Windows DPAPI local-machine 内存回环" in report.checks


def test_doctor_rejects_reparse_point_inside_runtime_tree(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    outside = tmp_path / "outside.py"
    outside.write_text("# outside\n", encoding="utf-8")
    (repo / ".venv" / "linked.py").symlink_to(outside)

    with pytest.raises(windows_deploy.DeployError) as error:
        windows_deploy.doctor(
            repo,
            data_dir=tmp_path / "runtime data",
            platform_name="Windows",
            python_version=(3, 12, 4),
            node_version="v20.0.0",
            npm_version="10.0.0",
        )

    assert error.value.code == "REPARSE_POINT_REJECTED"


@pytest.mark.parametrize(
    ("overrides", "expected_code"),
    [
        ({"platform_name": "Darwin"}, "PLATFORM_UNSUPPORTED"),
        ({"python_version": (3, 10, 9)}, "PYTHON_VERSION_UNSUPPORTED"),
        ({"node_version": "v18.20.0"}, "NODE_VERSION_UNSUPPORTED"),
        ({"npm_version": "9.9.0"}, "NPM_VERSION_UNSUPPORTED"),
    ],
)
def test_doctor_runtime_gates_fail_closed(
    tmp_path: Path,
    overrides: dict[str, object],
    expected_code: str,
) -> None:
    repo = _make_repo(tmp_path)
    kwargs: dict[str, object] = {
        "data_dir": tmp_path / "runtime data",
        "platform_name": "Windows",
        "python_version": (3, 11, 9),
        "node_version": "v20.0.0",
        "npm_version": "10.0.0",
    }
    kwargs.update(overrides)
    with pytest.raises(windows_deploy.DeployError) as error:
        windows_deploy.doctor(repo, **kwargs)
    assert error.value.code == expected_code


def test_doctor_validates_dpapi_references_and_runs_dpapi_probe(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    data_dir = _make_data(tmp_path / "runtime")
    probe_calls: list[bool] = []

    def probe() -> str:
        probe_calls.append(True)
        return security.DPAPI_SCOPE_CURRENT_USER

    report = windows_deploy.doctor(
        repo,
        data_dir=data_dir,
        platform_name="Windows",
        python_version=(3, 11, 9),
        node_version="v20.0.0",
        npm_version="10.0.0",
        dpapi_probe=probe,
    )

    assert report.database_exists is True
    assert report.schema_version == windows_deploy.SUPPORTED_SCHEMA_VERSION
    assert probe_calls == [True]
    assert "Windows DPAPI current-user 内存回环" in report.checks

    with _sqlite_connection(data_dir / "database" / "app.db") as connection:
        connection.execute(
            "UPDATE model_configs SET encrypted_api_key='keychain:v1:model-config'"
        )
    with pytest.raises(windows_deploy.DeployError) as error:
        windows_deploy.doctor(
            repo,
            data_dir=data_dir,
            platform_name="Windows",
            python_version=(3, 11, 9),
            node_version="v20.0.0",
            npm_version="10.0.0",
            dpapi_probe=probe,
        )
    assert error.value.code == "CREDENTIAL_REFERENCE_UNSAFE"


def test_doctor_accepts_machine_scope_reference_and_rejects_failed_probe(
    tmp_path: Path,
) -> None:
    repo = _make_repo(tmp_path)
    data_dir = _make_data(tmp_path / "runtime")
    with _sqlite_connection(data_dir / "database" / "app.db") as connection:
        connection.execute(
            "UPDATE model_configs SET encrypted_api_key=?",
            (FAKE_DPAPI_MACHINE_REFERENCE,),
        )

    def failed_probe() -> str:
        raise security.SecretStorageError(
            "safe DPAPI failure",
            reason="DPAPI_PROTECT_FAILED",
            system_error=2148073483,
        )

    with pytest.raises(windows_deploy.DeployError) as error:
        windows_deploy.doctor(
            repo,
            data_dir=data_dir,
            platform_name="Windows",
            python_version=(3, 11, 9),
            node_version="v20.0.0",
            npm_version="10.0.0",
            dpapi_probe=failed_probe,
        )

    assert error.value.code == "DPAPI_ROUND_TRIP_FAILED"
    assert "DPAPI_PROTECT_FAILED" in str(error.value)
    assert "2148073483" in str(error.value)


def test_doctor_cli_prints_checklist_summary_and_zero_exit(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    repo = _make_repo(tmp_path)
    data_dir = tmp_path / "runtime data"
    monkeypatch.setattr(windows_deploy.platform, "system", lambda: "Windows")
    monkeypatch.setattr(windows_deploy.sys, "version_info", (3, 12, 4))
    monkeypatch.setattr(
        windows_deploy,
        "_command_version",
        lambda command: "v20.1.0" if command == "node.exe" else "10.2.0",
    )
    monkeypatch.setattr(
        windows_deploy,
        "probe_windows_dpapi",
        _fake_dpapi_probe,
    )

    exit_code = windows_deploy.main(
        ["doctor", "--repo-root", str(repo), "--data-dir", str(data_dir)]
    )

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "[OK] Windows 平台" in captured.out
    assert f"[OK] DATA_DIR={data_dir}" in captured.out
    assert "诊断汇总：全部门禁通过" in captured.out
    assert captured.err == ""


def test_doctor_cli_prints_stable_failure_and_nonzero_exit(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    repo = _make_repo(tmp_path)
    monkeypatch.setattr(windows_deploy.platform, "system", lambda: "Windows")
    monkeypatch.setattr(windows_deploy.sys, "version_info", (3, 12, 4))
    monkeypatch.setattr(
        windows_deploy,
        "_command_version",
        lambda command: "v20.1.0" if command == "node.exe" else "10.2.0",
    )

    exit_code = windows_deploy.main(
        ["doctor", "--repo-root", str(repo), "--data-dir", str(repo / "data")]
    )

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "[FAIL] PATH_INSIDE_REPOSITORY" in captured.err
    assert "诊断汇总：门禁未通过" in captured.err


def test_backup_uses_sqlite_api_and_sanitizes_sensitive_data(tmp_path: Path) -> None:
    _repo, _source, backup = _create_backup(tmp_path)
    database = backup / "database" / "app.db"
    manifest = json.loads((backup / "manifest.json").read_text(encoding="utf-8"))

    assert backup.name == "3d66-backup-v1-20260730T010203Z"
    assert manifest["schema"] == windows_deploy.BACKUP_SCHEMA
    assert manifest["source"]["platform"] == "windows"
    assert _read_count(database, "session_tokens") == 0
    assert _read_count(
        database,
        "model_configs",
        "WHERE encrypted_api_key IS NOT NULL",
    ) == 0
    assert _read_count(
        database,
        "optimizer_configs",
        "WHERE encrypted_api_key IS NOT NULL",
    ) == 0
    assert not (backup / "logs").exists()
    assert not (backup / ".env").exists()
    all_bytes = b"".join(path.read_bytes() for path in backup.rglob("*") if path.is_file())
    assert FAKE_TOKEN.encode() not in all_bytes
    validation = windows_deploy.validate_backup(backup)
    assert validation.file_count == 2


def test_active_wal_database_backup_contains_latest_committed_state(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path / "repository")
    data_dir = _make_data(tmp_path / "source", marker="before")
    source_database = data_dir / "database" / "app.db"
    connection = sqlite3.connect(source_database)
    try:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("UPDATE test_markers SET value='committed-in-wal'")
        connection.commit()
        backup = windows_deploy.create_backup(
            data_dir,
            backup_root=tmp_path / "backups",
            repo_root=repo,
        )
    finally:
        connection.close()

    assert _read_marker(backup / "database" / "app.db") == "committed-in-wal"


def test_backup_rejects_symlinked_image_and_backup_root(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path / "repository")
    data_dir = _make_data(tmp_path / "source")
    outside = tmp_path / "outside.jpg"
    outside.write_bytes(b"outside")
    (data_dir / "images" / "escape.jpg").symlink_to(outside)

    with pytest.raises(windows_deploy.DeployError) as image_error:
        windows_deploy.create_backup(
            data_dir,
            backup_root=tmp_path / "backups",
            repo_root=repo,
        )
    assert image_error.value.code == "REPARSE_POINT_REJECTED"

    (data_dir / "images" / "escape.jpg").unlink()
    real_root = tmp_path / "real backups"
    real_root.mkdir()
    linked_root = tmp_path / "linked backups"
    linked_root.symlink_to(real_root, target_is_directory=True)
    with pytest.raises(windows_deploy.DeployError) as root_error:
        windows_deploy.create_backup(
            data_dir,
            backup_root=linked_root,
            repo_root=repo,
        )
    assert root_error.value.code == "REPARSE_POINT_REJECTED"


def test_reparse_attribute_is_rejected_even_without_symlink(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    target = tmp_path / "junction-like" / "data"
    junction = target.parent
    junction.mkdir()
    original = windows_deploy._is_reparse_point
    monkeypatch.setattr(
        windows_deploy,
        "_is_reparse_point",
        lambda path: path == junction or original(path),
    )
    with pytest.raises(windows_deploy.DeployError) as error:
        windows_deploy._normalized_path(target, label="DATA_DIR")
    assert error.value.code == "REPARSE_POINT_REJECTED"


@pytest.mark.parametrize(
    "unsafe_path",
    [
        "../outside",
        "images/evil.jpg:stream",
        "images/CON.txt",
        "images/CON .txt",
        "images/trailing. ",
    ],
)
def test_manifest_rejects_windows_path_escape_and_special_names(
    unsafe_path: str,
) -> None:
    with pytest.raises(windows_deploy.DeployError):
        windows_deploy._validated_relative_path(unsafe_path)


def test_tampered_backup_is_rejected(tmp_path: Path) -> None:
    _repo, _source, backup = _create_backup(tmp_path)
    image = backup / "images" / "嵌套 目录" / "样例.jpg"
    image.write_bytes(image.read_bytes() + b"tampered")
    with pytest.raises(windows_deploy.DeployError) as error:
        windows_deploy.validate_backup(backup)
    assert error.value.code == "BACKUP_TAMPERED"


def test_backup_root_rejects_undeclared_entry(tmp_path: Path) -> None:
    _repo, _source, backup = _create_backup(tmp_path)
    (backup / ".env").write_text("FAKE=value\n", encoding="utf-8")
    with pytest.raises(windows_deploy.DeployError) as error:
        windows_deploy.validate_backup(backup)
    assert error.value.code == "BACKUP_LAYOUT_INVALID"


def test_restore_dry_run_does_not_create_or_modify_target(tmp_path: Path) -> None:
    repo, _source, backup = _create_backup(tmp_path)
    target = tmp_path / "target" / "尚未创建"

    validation = windows_deploy.restore_backup(
        backup,
        target,
        repo_root=repo,
        dry_run=True,
        service_check=lambda _host, _port: (_ for _ in ()).throw(
            AssertionError("dry-run must not inspect service state")
        ),
    )

    assert validation.schema_version == windows_deploy.SUPPORTED_SCHEMA_VERSION
    assert not target.exists()


def test_restore_replaces_database_and_images_without_restoring_credentials(
    tmp_path: Path,
) -> None:
    repo, _source, backup = _create_backup(tmp_path, marker="restored")
    target = _make_data(tmp_path / "target", marker="current", image=b"old")

    windows_deploy.restore_backup(
        backup,
        target,
        repo_root=repo,
        service_check=lambda _host, _port: False,
    )

    database = target / "database" / "app.db"
    assert _read_marker(database) == "restored"
    assert _read_count(database, "session_tokens") == 0
    assert _read_count(database, "model_configs", "WHERE encrypted_api_key IS NOT NULL") == 0
    assert (target / "images" / "嵌套 目录" / "样例.jpg").read_bytes() == b"image"
    assert not list(target.parent.glob(".3d66-rollback-*"))
    assert not list(target.parent.glob(".3d66-restore-stage-*"))


def test_restore_failure_rolls_back_original_data(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    repo, _source, backup = _create_backup(tmp_path, marker="new")
    target = _make_data(tmp_path / "target", marker="old", image=b"old-image")
    original_apply = windows_deploy._apply_staged_restore

    def apply_then_fail(staging: Path, data_dir: Path) -> None:
        original_apply(staging, data_dir)
        raise RuntimeError("injected restore failure")

    monkeypatch.setattr(windows_deploy, "_apply_staged_restore", apply_then_fail)
    with pytest.raises(windows_deploy.DeployError) as error:
        windows_deploy.restore_backup(
            backup,
            target,
            repo_root=repo,
            service_check=lambda _host, _port: False,
        )

    assert error.value.code == "RESTORE_FAILED_ROLLED_BACK"
    database = target / "database" / "app.db"
    assert _read_marker(database) == "old"
    assert _read_count(database, "session_tokens") == 1
    assert (target / "images" / "嵌套 目录" / "样例.jpg").read_bytes() == b"old-image"
    assert not list(target.parent.glob(".3d66-rollback-*"))


def test_restore_refuses_when_service_port_is_in_use(tmp_path: Path) -> None:
    repo, _source, backup = _create_backup(tmp_path)
    target = _make_data(tmp_path / "target", marker="old")
    with pytest.raises(windows_deploy.DeployError) as error:
        windows_deploy.restore_backup(
            backup,
            target,
            repo_root=repo,
            service_check=lambda _host, _port: True,
        )
    assert error.value.code == "SERVICE_RUNNING"
    assert _read_marker(target / "database" / "app.db") == "old"


def test_windows_scripts_hold_strict_utf8_and_native_exit_contracts() -> None:
    repository = Path(__file__).resolve().parents[2]
    scripts = repository / "scripts" / "windows"
    banned = (
        "invoke-webrequest",
        "start-service",
        "new-service",
        "set-itemproperty",
        "netsh",
        "schtasks",
        "-verb runas",
        "start-job",
    )
    for name in ("install.ps1", "doctor.ps1", "start.ps1", "backup.ps1", "restore.ps1"):
        content = (scripts / name).read_text(encoding="utf-8")
        lowered = content.lower()
        assert "$ErrorActionPreference = 'Stop'" in content
        assert "Set-StrictMode -Version Latest" in content
        assert "[Console]::OutputEncoding" in content
        assert "$OutputEncoding" in content
        assert "Assert-NoReparsePoint" in content
        assert "[IO.FileAttributes]::ReparsePoint" in content
        assert "\n+function" not in content
        assert "-X', 'utf8'" in content or "-X utf8" in content
        assert "$LASTEXITCODE" in content
        assert "exit " in content
        assert all(forbidden not in lowered for forbidden in banned)

    install = (scripts / "install.ps1").read_text(encoding="utf-8")
    assert "$npmCommand.Source ci" in install
    assert "$npmCommand.Source run build" in install
    assert "Assert-NoReparseTree -Root $venvRoot" in install
    assert "Assert-NoReparseTree -Root (Join-Path $frontendRoot 'node_modules')" in install

    # 下面三组钉的是 2026-08-01 修复的 PS 5.1 运行期缺陷。三条都逃过了 5.1 与 7.x
    # 的纯语法解析，只能用字面契约 + 实机冒烟双重防守。

    # 缺陷 1：Get-Command 在 PATH 多命中时返回数组，.Source 成为 Object[] 后绑
    # [string] 失败。必须先包数组、判 .Count、再索引。特别不能写成
    # @(Get-Command ...)[0]：StrictMode Latest 下空命中取 [0] 会抛
    # IndexOutOfRangeException，反而掉包原有的友好报错。
    assert "$commandMatches.Count -eq 0" in install
    assert "$nodeMatches.Count -gt 0" in install
    assert "$npmMatches.Count -gt 0" in install
    assert "-ErrorAction SilentlyContinue)[0]" not in install

    # 缺陷 2：5.1 传参给原生命令时会剥掉字面双引号，使用 join 拼接的探测代码
    # 会被改形而触发 SyntaxError。只检查真实的探测代码行（不能拿全文做
    # 子串判定，否则会误伤解释该缺陷的中文注释）。
    probe_lines = [
        line
        for line in install.splitlines()
        if "import sys;" in line and "raise SystemExit" in line
    ]
    assert len(probe_lines) == 2
    for probe in probe_lines:
        assert "sys.version.split()[0]" in probe
        assert '"' not in probe
        assert "join" not in probe

    # 缺陷 3：$ErrorActionPreference='Stop' 下，`2>&1` 会把原生 stderr 升级为终止
    # 性错误，读到 $LASTEXITCODE 之前就抛出。捕获时必须丢弃 stderr，并在调用
    # 期间临时 Continue、finally 还原，一律以退出码判定成败。
    # 同样只看代码行：下面两个注释行为解释本缺陷而引用了 2>&1 字面量。
    capture_lines = [
        line
        for line in install.splitlines()
        if "& $FilePath @Arguments" in line and not line.lstrip().startswith("#")
    ]
    assert len(capture_lines) == 1
    assert "2>$null" in capture_lines[0]
    assert "2>&1" not in capture_lines[0]
    assert "$ErrorActionPreference = 'Continue'" in install
    assert "$ErrorActionPreference = $previousPreference" in install
    start = (scripts / "start.ps1").read_text(encoding="utf-8")
    assert start.index("doctor.ps1") < start.index("app.launcher")
    assert "$env:APP_HOST = '127.0.0.1'" in start
    assert "[string]$DpapiScope = 'LocalMachine'" in start
    assert "$env:API_KEY_DPAPI_SCOPE = 'local-machine'" in start
    # PS 5.1 下调用 PowerShell 脚本必须用哈希表 splat。数组 splat 会按位置传参、
    # 不解析其中的参数名，'-DpapiScope' 会被绑给 doctor.ps1 的第一个位置参数
    # $DataDir，导致两个启动入口在 5.1 上完全无法启动服务（已于 2026-08-01 修复）。
    # 本断言原先钉的是带 bug 的数组写法，现改为钉正确写法并反向禁止回归。
    assert "$doctorParameters = @{ DpapiScope = $DpapiScope }" in start
    assert "$doctorParameters['DataDir'] = $DataDir" in start
    assert "@doctorParameters" in start
    assert "@('-DpapiScope', $DpapiScope)" not in start
    assert "@doctorArguments" not in start
    doctor = (scripts / "doctor.ps1").read_text(encoding="utf-8")
    assert "[string]$DpapiScope = 'LocalMachine'" in doctor
    assert "$env:API_KEY_DPAPI_SCOPE = 'local-machine'" in doctor


def test_cmd_compatibility_launchers_only_forward_arguments_and_exit_code() -> None:
    repository = Path(__file__).resolve().parents[2]
    expected = {
        "start-3d66.cmd": "scripts\\windows\\start.ps1",
        "启动3d66标签系统.cmd": "scripts\\windows\\start.ps1",
        "首次安装.cmd": "scripts\\windows\\install.ps1",
    }
    for filename, target in expected.items():
        content = (repository / filename).read_text(encoding="utf-8")
        assert "chcp 65001" in content
        assert target in content
        assert "%*" in content
        assert "exit /b %EXIT_CODE%" in content
        assert "app.launcher" not in content
        assert "pip install" not in content
        assert "npm install" not in content
