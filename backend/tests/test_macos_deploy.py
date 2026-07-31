from __future__ import annotations

import json
import os
import sqlite3
import stat
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app import config, macos_deploy


FAKE_KEY = "FAKE_KEY_DO_NOT_USE_123"
FAKE_TOKEN = "f" * 64


def _make_database(
    path: Path,
    *,
    schema_version: int = macos_deploy.SUPPORTED_SCHEMA_VERSION,
    marker: str = "source",
    include_secrets: bool = True,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    try:
        connection.executescript(
            """
            CREATE TABLE schema_migrations (
                version INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
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
            (schema_version, f"migration-{schema_version}"),
        )
        connection.execute("INSERT INTO test_markers(value) VALUES (?)", (marker,))
        if include_secrets:
            connection.execute(
                "INSERT INTO session_tokens(token_hash) VALUES (?)",
                (FAKE_TOKEN,),
            )
            connection.execute(
                "INSERT INTO model_configs(encrypted_api_key) VALUES (?)",
                ("keychain:v1:model-config",),
            )
            connection.execute(
                "INSERT INTO optimizer_configs(encrypted_api_key) VALUES (?)",
                ("keychain:v1:optimizer-config",),
            )
        connection.commit()
    finally:
        connection.close()


def _make_data(
    root: Path,
    *,
    marker: str = "source",
    image_bytes: bytes = b"fake-image-source",
) -> Path:
    data_dir = root / "data"
    _make_database(data_dir / "database" / "app.db", marker=marker)
    (data_dir / "images" / "nested").mkdir(parents=True)
    (data_dir / "images" / "nested" / "sample.jpg").write_bytes(image_bytes)
    (data_dir / "logs").mkdir()
    (data_dir / "logs" / "service.log").write_text(
        f"log-{FAKE_KEY}-{FAKE_TOKEN}",
        encoding="utf-8",
    )
    (data_dir / ".env").write_text(f"API_KEY={FAKE_KEY}\n", encoding="utf-8")
    return data_dir


def _make_repo(root: Path, *, built: bool = True) -> Path:
    repo = root / "repo"
    (repo / "backend" / "app").mkdir(parents=True)
    (repo / "backend" / "requirements.txt").write_text("pytest\n", encoding="utf-8")
    (repo / "backend" / "app" / "launcher.py").write_text("", encoding="utf-8")
    (repo / "frontend").mkdir()
    (repo / "frontend" / "package-lock.json").write_text("{}\n", encoding="utf-8")
    if built:
        (repo / "frontend" / "dist").mkdir()
        (repo / "frontend" / "dist" / "index.html").write_text(
            "<!doctype html>",
            encoding="utf-8",
        )
    (repo / ".venv" / "bin").mkdir(parents=True)
    (repo / ".venv" / "bin" / "python").write_text("", encoding="utf-8")
    return repo


def _read_marker(database: Path) -> str:
    with sqlite3.connect(database) as connection:
        row = connection.execute("SELECT value FROM test_markers").fetchone()
    assert row is not None
    return str(row[0])


def _read_count(database: Path, table: str) -> int:
    with sqlite3.connect(database) as connection:
        row = connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
    assert row is not None
    return int(row[0])


def _read_credential_count(database: Path) -> int:
    with sqlite3.connect(database) as connection:
        model = connection.execute(
            "SELECT COUNT(*) FROM model_configs WHERE encrypted_api_key IS NOT NULL"
        ).fetchone()
        optimizer = connection.execute(
            "SELECT COUNT(*) FROM optimizer_configs WHERE encrypted_api_key IS NOT NULL"
        ).fetchone()
    assert model is not None and optimizer is not None
    return int(model[0]) + int(optimizer[0])


def _create_backup(tmp_path: Path, *, marker: str = "backup") -> tuple[Path, Path, Path]:
    repo = _make_repo(tmp_path / "repository")
    data_dir = _make_data(tmp_path / "source", marker=marker)
    backup_root = tmp_path / "backups"
    backup = macos_deploy.create_backup(
        data_dir,
        backup_root=backup_root,
        repo_root=repo,
        now=datetime(2026, 7, 28, 1, 2, 3, tzinfo=timezone.utc),
    )
    return repo, data_dir, backup


def _rewrite_manifest_for_file(backup: Path, relative: str) -> None:
    manifest_path = backup / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    file_path = backup / relative
    for entry in manifest["files"]:
        if entry["path"] == relative:
            entry["size"] = file_path.stat().st_size
            entry["sha256"] = macos_deploy._hash_file(file_path)
            break
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    manifest_path.chmod(0o600)


def test_default_macos_data_dir_is_application_support(tmp_path: Path) -> None:
    assert config._default_data_dir(platform_name="darwin", home=tmp_path) == (
        tmp_path / "Library" / "Application Support" / "3d66-label-system"
    )
    assert macos_deploy.default_macos_data_dir(tmp_path) == (
        tmp_path / "Library" / "Application Support" / "3d66-label-system"
    )


def test_windows_localappdata_behavior_is_unchanged(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    local_app_data = tmp_path / "Local App Data"
    monkeypatch.setenv("LOCALAPPDATA", str(local_app_data))
    assert config._default_data_dir(platform_name="win32") == (
        local_app_data / "3d66-label-system"
    )


def test_explicit_data_dir_still_has_priority(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    explicit = tmp_path / "explicit data"
    monkeypatch.setenv("DATA_DIR", str(explicit))
    config.get_settings.cache_clear()
    try:
        assert config.get_settings().data_dir == explicit.resolve()
    finally:
        config.get_settings.cache_clear()


def test_production_feedback_token_loads_from_data_directory_secret_file(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    data_dir = tmp_path / "runtime data"
    token_file = data_dir / "secrets" / "production-feedback.token"
    token_file.parent.mkdir(parents=True)
    token_file.write_text("test-feedback-token\n", encoding="utf-8")
    monkeypatch.setenv("DATA_DIR", str(data_dir))
    monkeypatch.delenv("PRODUCTION_FEEDBACK_TOKEN", raising=False)
    monkeypatch.delenv("PRODUCTION_FEEDBACK_TOKEN_FILE", raising=False)
    config.get_settings.cache_clear()
    try:
        assert (
            config.get_settings().production_feedback_token
            == "test-feedback-token"
        )
    finally:
        config.get_settings.cache_clear()


def test_production_feedback_environment_token_overrides_secret_file(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    data_dir = tmp_path / "runtime data"
    token_file = data_dir / "secrets" / "production-feedback.token"
    token_file.parent.mkdir(parents=True)
    token_file.write_text("file-token\n", encoding="utf-8")
    monkeypatch.setenv("DATA_DIR", str(data_dir))
    monkeypatch.setenv("PRODUCTION_FEEDBACK_TOKEN", "environment-token")
    config.get_settings.cache_clear()
    try:
        assert (
            config.get_settings().production_feedback_token
            == "environment-token"
        )
    finally:
        config.get_settings.cache_clear()


def test_doctor_accepts_complete_offline_install_without_reading_keychain(
    tmp_path: Path,
) -> None:
    repo = _make_repo(tmp_path)
    data_dir = _make_data(tmp_path / "runtime")
    report = macos_deploy.doctor(
        repo,
        data_dir=data_dir,
        platform_name="Darwin",
        python_version=(3, 11, 15),
        node_version="v26.0.0",
        npm_version="11.12.1",
    )
    assert report.database_exists is True
    assert report.schema_version == macos_deploy.SUPPORTED_SCHEMA_VERSION
    assert "未读取 Keychain 凭据" in report.checks


@pytest.mark.parametrize(
    ("overrides", "expected_code"),
    [
        ({"platform_name": "Linux"}, "PLATFORM_UNSUPPORTED"),
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
        "data_dir": tmp_path / "runtime-data",
        "platform_name": "Darwin",
        "python_version": (3, 11, 15),
        "node_version": "v22.0.0",
        "npm_version": "10.0.0",
    }
    kwargs.update(overrides)
    with pytest.raises(macos_deploy.DeployError) as error:
        macos_deploy.doctor(repo, **kwargs)
    assert error.value.code == expected_code


def test_doctor_rejects_data_dir_inside_repository(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    with pytest.raises(macos_deploy.DeployError) as error:
        macos_deploy.doctor(
            repo,
            data_dir=repo / "data",
            platform_name="Darwin",
            python_version=(3, 11, 0),
            node_version="v20.0.0",
            npm_version="10.0.0",
        )
    assert error.value.code == "PATH_INSIDE_REPOSITORY"


def test_backup_sanitizes_sessions_credentials_logs_and_environment(
    tmp_path: Path,
) -> None:
    _repo, _source, backup = _create_backup(tmp_path)
    database = backup / "database" / "app.db"
    assert _read_count(database, "session_tokens") == 0
    assert _read_credential_count(database) == 0
    assert not (backup / "logs").exists()
    assert not (backup / ".env").exists()
    all_bytes = b"".join(
        path.read_bytes() for path in backup.rglob("*") if path.is_file()
    )
    assert FAKE_KEY.encode() not in all_bytes
    assert FAKE_TOKEN.encode() not in all_bytes
    manifest = json.loads((backup / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["exclusions"]["session_tokens_cleared"] is True
    assert manifest["exclusions"]["model_credentials_cleared"] is True
    assert "Keychain" in manifest["restore_notice"]


def test_backup_manifest_hashes_validate_and_paths_are_versioned(tmp_path: Path) -> None:
    _repo, _source, backup = _create_backup(tmp_path)
    validation = macos_deploy.validate_backup(backup)
    assert backup.name == "3d66-backup-v1-20260728T010203Z"
    assert validation.schema_version == macos_deploy.SUPPORTED_SCHEMA_VERSION
    assert validation.file_count == 2
    paths = {entry["path"] for entry in validation.manifest["files"]}
    assert paths == {"database/app.db", "images/nested/sample.jpg"}


def test_backup_permissions_are_private(tmp_path: Path) -> None:
    _repo, _source, backup = _create_backup(tmp_path)
    assert stat.S_IMODE(backup.stat().st_mode) == 0o700
    for path in backup.rglob("*"):
        expected = 0o700 if path.is_dir() else 0o600
        assert stat.S_IMODE(path.stat().st_mode) == expected


def test_manifest_path_traversal_is_rejected(tmp_path: Path) -> None:
    _repo, _source, backup = _create_backup(tmp_path)
    manifest_path = backup / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["files"][0]["path"] = "../outside"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    manifest_path.chmod(0o600)
    with pytest.raises(macos_deploy.DeployError) as error:
        macos_deploy.validate_backup(backup)
    assert error.value.code == "PATH_TRAVERSAL_REJECTED"


def test_tampered_backup_is_rejected(tmp_path: Path) -> None:
    _repo, _source, backup = _create_backup(tmp_path)
    image = backup / "images" / "nested" / "sample.jpg"
    image.write_bytes(image.read_bytes() + b"tampered")
    image.chmod(0o600)
    with pytest.raises(macos_deploy.DeployError) as error:
        macos_deploy.validate_backup(backup)
    assert error.value.code == "BACKUP_TAMPERED"


def test_future_migration_is_rejected_even_with_updated_hash(
    tmp_path: Path,
) -> None:
    _repo, _source, backup = _create_backup(tmp_path)
    database = backup / "database" / "app.db"
    with sqlite3.connect(database) as connection:
        connection.execute(
            "INSERT INTO schema_migrations(version, name) VALUES (?, ?)",
            (macos_deploy.SUPPORTED_SCHEMA_VERSION + 1, "future"),
        )
        connection.commit()
    _rewrite_manifest_for_file(backup, "database/app.db")
    with pytest.raises(macos_deploy.DeployError) as error:
        macos_deploy.validate_backup(backup)
    assert error.value.code == "FUTURE_SCHEMA_REJECTED"


def test_restore_dry_run_validates_without_modifying_target(tmp_path: Path) -> None:
    repo, _source, backup = _create_backup(tmp_path)
    target = _make_data(tmp_path / "target", marker="current", image_bytes=b"current-image")
    before_database = (target / "database" / "app.db").read_bytes()
    before_image = (target / "images" / "nested" / "sample.jpg").read_bytes()

    validation = macos_deploy.restore_backup(
        backup,
        target,
        repo_root=repo,
        dry_run=True,
        service_check=lambda _host, _port: (_ for _ in ()).throw(
            AssertionError("dry-run must not check or change live service state")
        ),
    )

    assert validation.schema_version == macos_deploy.SUPPORTED_SCHEMA_VERSION
    assert (target / "database" / "app.db").read_bytes() == before_database
    assert (target / "images" / "nested" / "sample.jpg").read_bytes() == before_image


def test_successful_restore_atomically_replaces_database_and_images(
    tmp_path: Path,
) -> None:
    repo, _source, backup = _create_backup(tmp_path, marker="restored")
    target = _make_data(tmp_path / "target", marker="current", image_bytes=b"old-image")

    macos_deploy.restore_backup(
        backup,
        target,
        repo_root=repo,
        service_check=lambda _host, _port: False,
    )

    assert _read_marker(target / "database" / "app.db") == "restored"
    assert _read_count(target / "database" / "app.db", "session_tokens") == 0
    assert _read_credential_count(target / "database" / "app.db") == 0
    assert (target / "images" / "nested" / "sample.jpg").read_bytes() == b"fake-image-source"
    assert stat.S_IMODE((target / "database" / "app.db").stat().st_mode) == 0o600
    assert not list(target.parent.glob(".3d66-rollback-*"))
    assert not list(target.parent.glob(".3d66-restore-stage-*"))


def test_restore_failure_automatically_rolls_back(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    repo, _source, backup = _create_backup(tmp_path, marker="new")
    target = _make_data(tmp_path / "target", marker="old", image_bytes=b"old-image")
    original_apply = macos_deploy._apply_staged_restore

    def apply_then_fail(staging: Path, data_dir: Path) -> None:
        original_apply(staging, data_dir)
        raise RuntimeError("deterministic injected failure")

    monkeypatch.setattr(macos_deploy, "_apply_staged_restore", apply_then_fail)
    with pytest.raises(macos_deploy.DeployError) as error:
        macos_deploy.restore_backup(
            backup,
            target,
            repo_root=repo,
            service_check=lambda _host, _port: False,
        )

    assert error.value.code == "RESTORE_FAILED_ROLLED_BACK"
    assert _read_marker(target / "database" / "app.db") == "old"
    assert _read_count(target / "database" / "app.db", "session_tokens") == 1
    assert _read_credential_count(target / "database" / "app.db") == 2
    assert (target / "images" / "nested" / "sample.jpg").read_bytes() == b"old-image"
    assert not list(target.parent.glob(".3d66-rollback-*"))


def test_restore_refuses_when_service_port_is_in_use(tmp_path: Path) -> None:
    repo, _source, backup = _create_backup(tmp_path)
    target = _make_data(tmp_path / "target", marker="old")
    with pytest.raises(macos_deploy.DeployError) as error:
        macos_deploy.restore_backup(
            backup,
            target,
            repo_root=repo,
            service_check=lambda _host, _port: True,
        )
    assert error.value.code == "SERVICE_RUNNING"
    assert _read_marker(target / "database" / "app.db") == "old"


def test_validate_rejects_world_readable_backup_file(tmp_path: Path) -> None:
    _repo, _source, backup = _create_backup(tmp_path)
    image = backup / "images" / "nested" / "sample.jpg"
    image.chmod(0o644)
    with pytest.raises(macos_deploy.DeployError) as error:
        macos_deploy.validate_backup(backup)
    assert error.value.code == "PERMISSIONS_UNSAFE"
