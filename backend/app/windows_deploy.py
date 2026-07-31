from __future__ import annotations

import argparse
import base64
import binascii
import hashlib
import json
import os
import platform
import shutil
import socket
import sqlite3
import stat
import subprocess
import sys
import tempfile
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Callable, Mapping, Sequence

from .migrations.runner import MIGRATIONS
from .security import (
    DPAPI_MACHINE_REFERENCE_PREFIX,
    DPAPI_REFERENCE_PREFIX,
    DPAPI_SCOPE_CURRENT_USER,
    DPAPI_SCOPE_LOCAL_MACHINE,
    SecretStorageError,
    probe_windows_dpapi,
)


BACKUP_SCHEMA = "3d66-label-system-windows-backup"
BACKUP_SCHEMA_VERSION = 1
SUPPORTED_SCHEMA_VERSION = max(migration.version for migration in MIGRATIONS)
PYTHON_MIN = (3, 11)
PYTHON_MAX_EXCLUSIVE = (3, 13)
NODE_MIN_MAJOR = 20
NODE_MAX_EXCLUSIVE = 27
NPM_MIN_MAJOR = 10
NPM_MAX_EXCLUSIVE = 12
MAX_MANIFEST_BYTES = 2 * 1024 * 1024
REPARSE_POINT_FLAG = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
CREDENTIAL_NOTICE = (
    "DPAPI、Keychain、API Key 与登录会话均未备份；恢复后必须由目标机当前用户重新填写凭据。"
)
_WINDOWS_RESERVED_NAMES = frozenset(
    {
        "CON",
        "PRN",
        "AUX",
        "NUL",
        *(f"COM{index}" for index in range(1, 10)),
        *(f"LPT{index}" for index in range(1, 10)),
    }
)


class DeployError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class DoctorReport:
    data_dir: Path
    database_exists: bool
    schema_version: int | None
    checks: tuple[str, ...]


@dataclass(frozen=True)
class BackupValidation:
    backup_dir: Path
    database_path: Path
    schema_version: int
    file_count: int
    manifest: dict[str, object]


def _absolute_path(path: Path, *, label: str) -> Path:
    expanded = path.expanduser()
    if not expanded.is_absolute():
        raise DeployError("PATH_NOT_ABSOLUTE", f"{label}必须是绝对路径：{path}")
    return Path(os.path.abspath(expanded))


def _is_reparse_point(path: Path) -> bool:
    try:
        metadata = os.lstat(path)
    except FileNotFoundError:
        return False
    return stat.S_ISLNK(metadata.st_mode) or bool(
        getattr(metadata, "st_file_attributes", 0) & REPARSE_POINT_FLAG
    )


def _reject_reparse_components(path: Path, label: str) -> None:
    absolute = _absolute_path(path, label=label)
    candidates = [absolute, *absolute.parents]
    for candidate in reversed(candidates):
        if _is_reparse_point(candidate):
            raise DeployError(
                "REPARSE_POINT_REJECTED",
                f"{label}路径不得包含符号链接、junction 或其他重解析点：{candidate}",
            )


def _reject_reparse_tree(root: Path, label: str) -> None:
    _reject_reparse_components(root, label)
    if not root.exists() or not root.is_dir():
        return
    pending = [root]
    while pending:
        current = pending.pop()
        try:
            entries = list(os.scandir(current))
        except OSError as exc:
            raise DeployError(
                "PATH_INSPECTION_FAILED",
                f"无法检查 {label}：{current}",
            ) from exc
        for entry in entries:
            path = Path(entry.path)
            if _is_reparse_point(path):
                raise DeployError(
                    "REPARSE_POINT_REJECTED",
                    f"{label}不得包含符号链接、junction 或其他重解析点：{path}",
                )
            if entry.is_dir(follow_symlinks=False):
                pending.append(path)


def _normalized_path(path: Path, *, label: str) -> Path:
    absolute = _absolute_path(path, label=label)
    _reject_reparse_components(absolute, label)
    return absolute.resolve(strict=False)


def _casefolded_parts(path: Path) -> tuple[str, ...]:
    return tuple(part.casefold() for part in path.parts)


def _is_same_or_descendant(path: Path, parent: Path) -> bool:
    child_parts = _casefolded_parts(path)
    parent_parts = _casefolded_parts(parent)
    return (
        len(child_parts) >= len(parent_parts)
        and child_parts[: len(parent_parts)] == parent_parts
    )


def _require_outside_repo(path: Path, repo_root: Path, *, label: str) -> None:
    candidate = _normalized_path(path, label=label)
    repository = _normalized_path(repo_root, label="代码仓库")
    if _is_same_or_descendant(candidate, repository):
        raise DeployError(
            "PATH_INSIDE_REPOSITORY",
            f"{label}不得位于代码仓库内：{path}",
        )


def _require_backup_outside_data(backup_root: Path, data_dir: Path) -> None:
    backup = _normalized_path(backup_root, label="备份目录")
    data = _normalized_path(data_dir, label="DATA_DIR")
    if _is_same_or_descendant(backup, data) or _is_same_or_descendant(data, backup):
        raise DeployError("BACKUP_DATA_OVERLAP", "备份目录与数据目录不得互相包含")


def _env_file_value(repo_root: Path, key: str) -> str | None:
    env_file = repo_root / ".env"
    _reject_reparse_components(env_file, ".env")
    if not env_file.is_file():
        return None
    try:
        lines = env_file.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise DeployError("ENV_READ_FAILED", "无法安全读取 .env") from exc
    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        candidate_key, value = line.split("=", 1)
        if candidate_key.strip() != key:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        return value
    return None


def _safe_env_value(repo_root: Path, key: str, env: Mapping[str, str]) -> str | None:
    direct = env.get(key)
    if direct is not None and direct.strip():
        return direct.strip()
    return _env_file_value(repo_root, key)


def default_windows_data_dir(env: Mapping[str, str] | None = None) -> Path:
    effective_env = os.environ if env is None else env
    local_app_data = effective_env.get("LOCALAPPDATA", "").strip()
    if not local_app_data:
        raise DeployError(
            "LOCALAPPDATA_MISSING",
            "未检测到 LOCALAPPDATA，无法确定 Windows 默认 DATA_DIR",
        )
    return _absolute_path(Path(local_app_data) / "3d66-label-system", label="DATA_DIR")


def resolve_data_dir(
    repo_root: Path,
    *,
    explicit: Path | None = None,
    env: Mapping[str, str] | None = None,
) -> Path:
    effective_env = os.environ if env is None else env
    if explicit is not None:
        return _normalized_path(explicit, label="DATA_DIR")
    configured = _safe_env_value(repo_root, "DATA_DIR", effective_env)
    if configured:
        return _normalized_path(Path(configured), label="DATA_DIR")
    return _normalized_path(
        default_windows_data_dir(effective_env),
        label="DATA_DIR",
    )


def default_backup_root(env: Mapping[str, str] | None = None) -> Path:
    effective_env = os.environ if env is None else env
    user_profile = effective_env.get("USERPROFILE", "").strip()
    if not user_profile:
        raise DeployError(
            "USERPROFILE_MISSING",
            "未检测到 USERPROFILE；请使用 --backup-dir 指定仓库外备份目录",
        )
    return _absolute_path(
        Path(user_profile) / "Documents" / "3d66-label-system-backups",
        label="备份目录",
    )


def _parse_major(raw: str, label: str) -> int:
    cleaned = raw.strip().lstrip("v")
    try:
        return int(cleaned.split(".", 1)[0])
    except (TypeError, ValueError) as exc:
        raise DeployError("VERSION_UNREADABLE", f"无法解析 {label} 版本") from exc


def _command_version(command: str) -> str:
    try:
        result = subprocess.run(
            [command, "--version"],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
        )
    except (FileNotFoundError, subprocess.SubprocessError) as exc:
        raise DeployError("DEPENDENCY_MISSING", f"未检测到 {command}") from exc
    return (result.stdout or result.stderr).strip()


def _validate_runtime_versions(
    *,
    python_version: tuple[int, int, int],
    node_version: str,
    npm_version: str,
) -> None:
    if not (PYTHON_MIN <= python_version[:2] < PYTHON_MAX_EXCLUSIVE):
        raise DeployError("PYTHON_VERSION_UNSUPPORTED", "Python 必须为 3.11 或 3.12")
    node_major = _parse_major(node_version, "Node.js")
    if not (NODE_MIN_MAJOR <= node_major < NODE_MAX_EXCLUSIVE):
        raise DeployError("NODE_VERSION_UNSUPPORTED", "Node.js 必须为 20.x 至 26.x")
    npm_major = _parse_major(npm_version, "npm")
    if not (NPM_MIN_MAJOR <= npm_major < NPM_MAX_EXCLUSIVE):
        raise DeployError("NPM_VERSION_UNSUPPORTED", "npm 必须为 10.x 或 11.x")


def _existing_writable_ancestor(path: Path) -> Path:
    candidate = path
    while not candidate.exists() and candidate.parent != candidate:
        candidate = candidate.parent
    return candidate


def _sqlite_connect_readonly(path: Path) -> sqlite3.Connection:
    uri = path.resolve(strict=True).as_uri() + "?mode=ro"
    connection = sqlite3.connect(uri, uri=True, timeout=10)
    connection.execute("PRAGMA busy_timeout=10000")
    return connection


def _integrity_check(connection: sqlite3.Connection) -> None:
    if connection.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
        raise DeployError("SQLITE_INTEGRITY_FAILED", "SQLite integrity_check 未通过")


def _table_exists(connection: sqlite3.Connection, table: str) -> bool:
    return (
        connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (table,),
        ).fetchone()
        is not None
    )


def _database_schema_version(connection: sqlite3.Connection) -> int:
    if not _table_exists(connection, "schema_migrations"):
        return 0
    row = connection.execute("SELECT COALESCE(MAX(version), 0) FROM schema_migrations").fetchone()
    return int(row[0]) if row else 0


def _validate_supported_database(connection: sqlite3.Connection) -> int:
    _integrity_check(connection)
    version = _database_schema_version(connection)
    if version > SUPPORTED_SCHEMA_VERSION:
        raise DeployError(
            "FUTURE_SCHEMA_REJECTED",
            f"备份数据库迁移版本 {version} 高于当前代码支持版本 {SUPPORTED_SCHEMA_VERSION}",
        )
    return version


def _decode_dpapi_reference(reference: str) -> bytes:
    if reference.startswith("keychain:"):
        raise DeployError("CREDENTIAL_REFERENCE_UNSAFE", "Windows 数据库含 macOS Keychain 引用")
    if reference.startswith(DPAPI_REFERENCE_PREFIX):
        payload = reference.removeprefix(DPAPI_REFERENCE_PREFIX)
    elif reference.startswith(DPAPI_MACHINE_REFERENCE_PREFIX):
        payload = reference.removeprefix(DPAPI_MACHINE_REFERENCE_PREFIX)
    elif ":" not in reference:
        payload = reference
    else:
        raise DeployError("CREDENTIAL_REFERENCE_UNSAFE", "Windows 数据库含未知凭据引用")
    try:
        decoded = base64.b64decode(payload.encode("ascii"), validate=True)
    except (UnicodeEncodeError, binascii.Error, ValueError) as exc:
        raise DeployError("CREDENTIAL_REFERENCE_UNSAFE", "Windows 数据库含无效 DPAPI 密文") from exc
    if not decoded:
        raise DeployError("CREDENTIAL_REFERENCE_UNSAFE", "Windows 数据库含空 DPAPI 密文")
    return decoded


def _validate_windows_credential_references(connection: sqlite3.Connection) -> None:
    for table in ("model_configs", "optimizer_configs"):
        if not _table_exists(connection, table):
            continue
        rows = connection.execute(
            f"SELECT encrypted_api_key FROM {table} "
            "WHERE encrypted_api_key IS NOT NULL AND trim(encrypted_api_key) <> ''"
        ).fetchall()
        for row in rows:
            _decode_dpapi_reference(str(row[0]))


def doctor(
    repo_root: Path,
    *,
    data_dir: Path | None = None,
    env: Mapping[str, str] | None = None,
    platform_name: str | None = None,
    python_version: tuple[int, int, int] | None = None,
    node_version: str | None = None,
    npm_version: str | None = None,
    dpapi_probe: Callable[[], str] | None = None,
) -> DoctorReport:
    if (platform_name or platform.system()).casefold() != "windows":
        raise DeployError("PLATFORM_UNSUPPORTED", "该工具只允许在 Windows 上运行")
    root = _normalized_path(repo_root, label="代码仓库")
    required_files = (
        root / "backend" / "requirements.txt",
        root / "frontend" / "package-lock.json",
        root / "backend" / "app" / "launcher.py",
        root / "scripts" / "windows" / "start.ps1",
    )
    for required in required_files:
        _reject_reparse_components(required, "仓库必要文件")
        if not required.is_file():
            raise DeployError("REPOSITORY_INCOMPLETE", f"仓库缺少必要文件：{required}")
    venv_python = root / ".venv" / "Scripts" / "python.exe"
    _reject_reparse_tree(root / ".venv", "仓库内 .venv")
    _reject_reparse_components(venv_python, "仓库内 Python")
    if not venv_python.is_file():
        raise DeployError("VENV_MISSING", "仓库内 .venv 不存在，请先运行 install.ps1")
    frontend_index = root / "frontend" / "dist" / "index.html"
    _reject_reparse_tree(root / "frontend" / "dist", "前端构建")
    _reject_reparse_components(frontend_index, "前端构建")
    if not frontend_index.is_file():
        raise DeployError("FRONTEND_BUILD_MISSING", "前端生产构建不存在，请先运行 install.ps1")
    _validate_runtime_versions(
        python_version=python_version or tuple(sys.version_info[:3]),
        node_version=node_version or _command_version("node.exe"),
        npm_version=npm_version or _command_version("npm.cmd"),
    )
    effective_env = os.environ if env is None else env
    effective_data = resolve_data_dir(root, explicit=data_dir, env=effective_env)
    _require_outside_repo(effective_data, root, label="DATA_DIR")
    ancestor = _existing_writable_ancestor(effective_data)
    _reject_reparse_components(ancestor, "DATA_DIR 现有父目录")
    if not ancestor.is_dir() or not os.access(ancestor, os.W_OK):
        raise DeployError("DATA_DIR_NOT_WRITABLE", "DATA_DIR 的现有父目录不可写")
    if effective_data.exists() and not effective_data.is_dir():
        raise DeployError("DATA_DIR_INVALID", "DATA_DIR 不是目录")
    database = effective_data / "database" / "app.db"
    schema_version: int | None = None
    if database.exists():
        _reject_reparse_components(database, "数据库")
        if not database.is_file():
            raise DeployError("DATABASE_INVALID", "数据库路径不是普通文件")
        with _sqlite_connect_readonly(database) as connection:
            schema_version = _validate_supported_database(connection)
            _validate_windows_credential_references(connection)
    try:
        dpapi_scope = (dpapi_probe or probe_windows_dpapi)()
    except SecretStorageError as exc:
        system_error = (
            f"，系统错误 {exc.system_error}"
            if exc.system_error is not None
            else ""
        )
        raise DeployError(
            "DPAPI_ROUND_TRIP_FAILED",
            f"Windows DPAPI 回环失败（{exc.reason}{system_error}）",
        ) from exc
    if dpapi_scope not in {
        DPAPI_SCOPE_CURRENT_USER,
        DPAPI_SCOPE_LOCAL_MACHINE,
    }:
        raise DeployError(
            "DPAPI_SCOPE_INVALID",
            "Windows DPAPI 回环返回了未知存储范围",
        )
    checks = (
        "Windows 平台",
        "Python 3.11/3.12",
        "Node.js 20-26 与 npm 10/11",
        "仓库必要文件、仓库内 .venv 与前端生产构建",
        "仓库外 DATA_DIR 与 Windows 优先级",
        "路径不含符号链接、junction 或其他重解析点",
        "SQLite 完整性、迁移版本与 DPAPI 引用" if database.exists() else "首次启动数据目录（未创建）",
        f"Windows DPAPI {dpapi_scope} 内存回环",
    )
    return DoctorReport(effective_data, database.exists(), schema_version, checks)


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _copy_regular_file(source: Path, destination: Path) -> None:
    _reject_reparse_components(source, "源文件")
    source_before = os.stat(source, follow_symlinks=False)
    if not stat.S_ISREG(source_before.st_mode):
        raise DeployError("NON_REGULAR_FILE", f"只允许备份普通文件：{source}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, destination, follow_symlinks=False)
    source_after = os.stat(source, follow_symlinks=False)
    if (
        source_before.st_dev,
        source_before.st_ino,
        source_before.st_size,
        source_before.st_mtime_ns,
    ) != (
        source_after.st_dev,
        source_after.st_ino,
        source_after.st_size,
        source_after.st_mtime_ns,
    ):
        raise DeployError("SOURCE_CHANGED_DURING_BACKUP", f"备份期间文件发生变化：{source}")


def _copy_images(source: Path, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    _reject_reparse_components(source, "images")
    if not source.exists():
        return
    if not source.is_dir():
        raise DeployError("IMAGES_INVALID", "images 不是目录")
    for current, directories, files in os.walk(source, followlinks=False):
        current_path = Path(current)
        _reject_reparse_components(current_path, "图片目录")
        relative = current_path.relative_to(source)
        target_dir = destination / relative
        target_dir.mkdir(parents=True, exist_ok=True)
        for directory in directories:
            _reject_reparse_components(current_path / directory, "图片目录")
        for filename in files:
            _copy_regular_file(current_path / filename, target_dir / filename)


def _sqlite_backup(source: Path, destination: Path) -> None:
    _reject_reparse_components(source, "数据库")
    if not source.is_file():
        raise DeployError("DATABASE_MISSING", f"数据库不存在：{source}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with _sqlite_connect_readonly(source) as source_connection:
        destination_connection = sqlite3.connect(destination)
        try:
            source_connection.backup(destination_connection)
            destination_connection.commit()
        finally:
            destination_connection.close()


def _sanitize_backup_database(database: Path) -> int:
    connection = sqlite3.connect(database)
    try:
        _integrity_check(connection)
        with connection:
            if _table_exists(connection, "session_tokens"):
                connection.execute("DELETE FROM session_tokens")
            for table in ("model_configs", "optimizer_configs"):
                if _table_exists(connection, table):
                    connection.execute(f"UPDATE {table} SET encrypted_api_key = NULL")
        connection.execute("VACUUM")
        version = _validate_supported_database(connection)
        _validate_sanitized_database(connection)
        return version
    finally:
        connection.close()


def _validate_sanitized_database(connection: sqlite3.Connection) -> None:
    if _table_exists(connection, "session_tokens"):
        row = connection.execute("SELECT COUNT(*) FROM session_tokens").fetchone()
        if row and int(row[0]) != 0:
            raise DeployError("SESSION_TOKEN_PRESENT", "备份数据库仍包含登录会话")
    for table in ("model_configs", "optimizer_configs"):
        if not _table_exists(connection, table):
            continue
        row = connection.execute(
            f"SELECT COUNT(*) FROM {table} "
            "WHERE encrypted_api_key IS NOT NULL AND trim(encrypted_api_key) <> ''"
        ).fetchone()
        if row and int(row[0]) != 0:
            raise DeployError("CREDENTIAL_PRESENT", "备份数据库仍包含凭据引用")


def _git_commit(repo_root: Path | None) -> str | None:
    if repo_root is None:
        return None
    try:
        result = subprocess.run(
            ["git", "-C", str(repo_root), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
        )
    except (FileNotFoundError, subprocess.SubprocessError):
        return None
    commit = result.stdout.strip().lower()
    if len(commit) == 40 and all(character in "0123456789abcdef" for character in commit):
        return commit
    return None


def _manifest_files(backup_dir: Path) -> list[dict[str, object]]:
    files: list[dict[str, object]] = []
    for base in (backup_dir / "database", backup_dir / "images"):
        if not base.exists():
            continue
        for current, directories, filenames in os.walk(base, followlinks=False):
            current_path = Path(current)
            _reject_reparse_components(current_path, "备份目录")
            for directory in directories:
                _reject_reparse_components(current_path / directory, "备份目录")
            for filename in filenames:
                path = current_path / filename
                _reject_reparse_components(path, "备份文件")
                files.append(
                    {
                        "path": path.relative_to(backup_dir).as_posix(),
                        "sha256": _hash_file(path),
                        "size": path.stat().st_size,
                    }
                )
    return sorted(files, key=lambda item: str(item["path"]))


def _timestamp(now: datetime | None = None) -> tuple[str, str]:
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    return current.isoformat(timespec="seconds").replace("+00:00", "Z"), current.strftime(
        "%Y%m%dT%H%M%SZ"
    )


def _unique_backup_destination(root: Path, compact_timestamp: str) -> Path:
    base = root / f"3d66-backup-v{BACKUP_SCHEMA_VERSION}-{compact_timestamp}"
    if not base.exists():
        return base
    for index in range(1, 1000):
        candidate = root / f"{base.name}-{index:03d}"
        if not candidate.exists():
            return candidate
    raise DeployError("BACKUP_NAME_EXHAUSTED", "无法生成唯一备份目录名")


def create_backup(
    data_dir: Path,
    *,
    backup_root: Path | None = None,
    repo_root: Path | None = None,
    env: Mapping[str, str] | None = None,
    now: datetime | None = None,
) -> Path:
    source = _normalized_path(data_dir, label="DATA_DIR")
    root = _normalized_path(
        backup_root or default_backup_root(env),
        label="备份目录",
    )
    if repo_root is not None:
        _require_outside_repo(source, repo_root, label="DATA_DIR")
        _require_outside_repo(root, repo_root, label="备份目录")
    _require_backup_outside_data(root, source)
    if not source.is_dir():
        raise DeployError("DATA_DIR_MISSING", "DATA_DIR 不存在")
    database = source / "database" / "app.db"
    _reject_reparse_components(_existing_writable_ancestor(root), "备份目录现有父目录")
    root.mkdir(parents=True, exist_ok=True)
    _reject_reparse_components(root, "备份目录")
    created_at, compact_timestamp = _timestamp(now)
    destination = _unique_backup_destination(root, compact_timestamp)
    temporary = Path(tempfile.mkdtemp(prefix=".3d66-backup-tmp-", dir=root))
    try:
        copied_database = temporary / "database" / "app.db"
        _sqlite_backup(database, copied_database)
        schema_version = _sanitize_backup_database(copied_database)
        _copy_images(source / "images", temporary / "images")
        files = _manifest_files(temporary)
        manifest: dict[str, object] = {
            "schema": BACKUP_SCHEMA,
            "schema_version": BACKUP_SCHEMA_VERSION,
            "created_at": created_at,
            "source": {
                "application": "3d66-label-system",
                "platform": "windows",
                "database_schema_version": schema_version,
                "git_commit": _git_commit(repo_root),
            },
            "files": files,
            "exclusions": {
                "logs_excluded": True,
                "environment_files_excluded": True,
                "session_tokens_cleared": True,
                "model_credentials_cleared": True,
                "keychain_content_excluded": True,
                "dpapi_content_excluded": True,
            },
            "restore_notice": CREDENTIAL_NOTICE,
        }
        (temporary / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, destination)
        return destination
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise


def _validated_relative_path(raw: object) -> PurePosixPath:
    if not isinstance(raw, str) or not raw or "\\" in raw:
        raise DeployError("MANIFEST_PATH_INVALID", "manifest 文件路径无效")
    path = PurePosixPath(raw)
    if path.is_absolute() or path.as_posix() != raw or any(
        part in {"", ".", ".."} for part in path.parts
    ):
        raise DeployError("PATH_TRAVERSAL_REJECTED", f"拒绝不安全相对路径：{raw}")
    for part in path.parts:
        if (
            any(ord(character) < 32 or character in '<>:"|?*' for character in part)
            or part.rstrip(" .") != part
            or part.split(".", 1)[0].rstrip(" ").upper() in _WINDOWS_RESERVED_NAMES
        ):
            raise DeployError("MANIFEST_PATH_INVALID", f"拒绝不安全 Windows 文件名：{raw}")
    if raw != "database/app.db" and not raw.startswith("images/"):
        raise DeployError("MANIFEST_PATH_FORBIDDEN", f"备份含未授权路径：{raw}")
    if raw.startswith("images/") and len(path.parts) < 2:
        raise DeployError("MANIFEST_PATH_INVALID", f"图片路径无效：{raw}")
    return path


def _enumerate_backup_files(root: Path) -> set[str]:
    found: set[str] = set()
    for base_name in ("database", "images"):
        base = root / base_name
        if not base.exists():
            continue
        _reject_reparse_components(base, "备份目录")
        if not base.is_dir():
            raise DeployError("BACKUP_LAYOUT_INVALID", f"{base_name} 不是目录")
        for current, directories, filenames in os.walk(base, followlinks=False):
            current_path = Path(current)
            _reject_reparse_components(current_path, "备份目录")
            for directory in directories:
                _reject_reparse_components(current_path / directory, "备份目录")
            for filename in filenames:
                path = current_path / filename
                _reject_reparse_components(path, "备份文件")
                if not stat.S_ISREG(os.stat(path, follow_symlinks=False).st_mode):
                    raise DeployError("NON_REGULAR_FILE", f"备份含非普通文件：{path}")
                found.add(path.relative_to(root).as_posix())
    return found


def _validate_backup_root_layout(root: Path) -> None:
    allowed = {"manifest.json", "database", "images"}
    for child in root.iterdir():
        _reject_reparse_components(child, "备份根目录入口")
        if child.name not in allowed:
            raise DeployError(
                "BACKUP_LAYOUT_INVALID",
                f"备份根目录含未授权入口：{child.name}",
            )


def validate_backup(
    backup_dir: Path,
    *,
    supported_schema_version: int = SUPPORTED_SCHEMA_VERSION,
) -> BackupValidation:
    root = _normalized_path(backup_dir, label="备份目录")
    if not root.is_dir():
        raise DeployError("BACKUP_MISSING", "备份目录不存在")
    _validate_backup_root_layout(root)
    manifest_path = root / "manifest.json"
    _reject_reparse_components(manifest_path, "manifest")
    if not manifest_path.is_file():
        raise DeployError("MANIFEST_MISSING", "备份缺少 manifest.json")
    if manifest_path.stat().st_size > MAX_MANIFEST_BYTES:
        raise DeployError("MANIFEST_TOO_LARGE", "manifest 超过大小上限")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DeployError("MANIFEST_INVALID", "manifest 不是有效 UTF-8 JSON") from exc
    if not isinstance(manifest, dict):
        raise DeployError("MANIFEST_INVALID", "manifest 顶层必须是对象")
    if (
        manifest.get("schema") != BACKUP_SCHEMA
        or manifest.get("schema_version") != BACKUP_SCHEMA_VERSION
    ):
        raise DeployError("MANIFEST_SCHEMA_UNSUPPORTED", "manifest schema/version 不受支持")
    exclusions = manifest.get("exclusions")
    required_exclusions = {
        "logs_excluded",
        "environment_files_excluded",
        "session_tokens_cleared",
        "model_credentials_cleared",
        "keychain_content_excluded",
        "dpapi_content_excluded",
    }
    if not isinstance(exclusions, dict) or any(
        exclusions.get(key) is not True for key in required_exclusions
    ):
        raise DeployError("MANIFEST_EXCLUSIONS_INVALID", "manifest 未证明敏感数据已排除")
    entries = manifest.get("files")
    if not isinstance(entries, list):
        raise DeployError("MANIFEST_FILES_INVALID", "manifest files 必须是数组")
    declared: set[str] = set()
    declared_casefolded: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            raise DeployError("MANIFEST_FILES_INVALID", "manifest 文件条目无效")
        relative = _validated_relative_path(entry.get("path"))
        raw_path = relative.as_posix()
        folded = raw_path.casefold()
        if folded in declared_casefolded:
            raise DeployError("MANIFEST_DUPLICATE_PATH", f"manifest 路径重复：{raw_path}")
        declared.add(raw_path)
        declared_casefolded.add(folded)
        digest = entry.get("sha256")
        size = entry.get("size")
        if (
            not isinstance(digest, str)
            or len(digest) != 64
            or any(character not in "0123456789abcdef" for character in digest)
            or not isinstance(size, int)
            or isinstance(size, bool)
            or size < 0
        ):
            raise DeployError("MANIFEST_FILE_METADATA_INVALID", f"文件元数据无效：{raw_path}")
        file_path = root.joinpath(*relative.parts)
        _reject_reparse_components(file_path, "备份文件")
        resolved_file = file_path.resolve(strict=False)
        if not _is_same_or_descendant(resolved_file, root):
            raise DeployError("PATH_TRAVERSAL_REJECTED", f"路径逃逸备份目录：{raw_path}")
        if not file_path.is_file():
            raise DeployError("BACKUP_FILE_MISSING", f"备份文件缺失：{raw_path}")
        if file_path.stat().st_size != size or _hash_file(file_path) != digest:
            raise DeployError("BACKUP_TAMPERED", f"备份文件校验失败：{raw_path}")
    if "database/app.db" not in declared:
        raise DeployError("DATABASE_MISSING", "manifest 未包含 database/app.db")
    if _enumerate_backup_files(root) != declared:
        raise DeployError("BACKUP_UNDECLARED_FILE", "备份目录与 manifest 文件清单不一致")
    database = root / "database" / "app.db"
    with _sqlite_connect_readonly(database) as connection:
        _integrity_check(connection)
        schema_version = _database_schema_version(connection)
        if schema_version > supported_schema_version:
            raise DeployError(
                "FUTURE_SCHEMA_REJECTED",
                f"备份数据库迁移版本 {schema_version} 高于当前代码支持版本 {supported_schema_version}",
            )
        _validate_sanitized_database(connection)
    source = manifest.get("source")
    if (
        not isinstance(source, dict)
        or source.get("platform") != "windows"
        or source.get("database_schema_version") != schema_version
    ):
        raise DeployError("MANIFEST_SCHEMA_MISMATCH", "manifest 来源与数据库迁移版本不一致")
    return BackupValidation(root, database, schema_version, len(declared), manifest)


def _service_may_be_running(host: str, port: int) -> bool:
    bind_host = host.strip() or "127.0.0.1"
    if bind_host in {"0.0.0.0", "localhost"}:
        bind_host = "127.0.0.1"
    family = socket.AF_INET6 if ":" in bind_host else socket.AF_INET
    try:
        with socket.socket(family, socket.SOCK_STREAM) as probe:
            probe.bind((bind_host, port))
    except OSError:
        return True
    return False


def _app_bind(repo_root: Path, env: Mapping[str, str]) -> tuple[str, int]:
    host = _safe_env_value(repo_root, "APP_HOST", env) or "127.0.0.1"
    raw_port = _safe_env_value(repo_root, "APP_PORT", env) or "8080"
    try:
        port = int(raw_port)
    except ValueError as exc:
        raise DeployError("APP_PORT_INVALID", "APP_PORT 必须是整数") from exc
    if not 1 <= port <= 65535:
        raise DeployError("APP_PORT_INVALID", "APP_PORT 超出有效范围")
    return host, port


def _copy_database_exact(source: Path, destination: Path) -> None:
    _sqlite_backup(source, destination)
    with _sqlite_connect_readonly(destination) as connection:
        _integrity_check(connection)


def _create_rollback_snapshot(data_dir: Path) -> tuple[Path, bool, bool]:
    snapshot = Path(tempfile.mkdtemp(prefix=".3d66-rollback-", dir=data_dir.parent))
    database = data_dir / "database" / "app.db"
    images = data_dir / "images"
    had_database = database.exists()
    had_images = images.exists()
    try:
        if had_database:
            _copy_database_exact(database, snapshot / "database" / "app.db")
        if had_images:
            _copy_images(images, snapshot / "images")
        return snapshot, had_database, had_images
    except Exception:
        shutil.rmtree(snapshot, ignore_errors=True)
        raise


def _remove_sqlite_sidecars(database: Path) -> None:
    for suffix in ("-wal", "-shm", "-journal"):
        sidecar = Path(f"{database}{suffix}")
        _reject_reparse_components(sidecar, "SQLite sidecar")
        if sidecar.exists():
            if not sidecar.is_file():
                raise DeployError("DATABASE_INVALID", f"SQLite sidecar 不是普通文件：{sidecar}")
            sidecar.unlink()


def _replace_images(staged: Path, target: Path) -> None:
    displaced = target.parent / f".images-displaced-{uuid.uuid4().hex}"
    had_target = target.exists()
    if had_target:
        _reject_reparse_components(target, "目标 images")
        os.replace(target, displaced)
    try:
        os.replace(staged, target)
    except Exception:
        if had_target and displaced.exists():
            os.replace(displaced, target)
        raise
    if displaced.exists():
        shutil.rmtree(displaced)


def _apply_staged_restore(staging: Path, data_dir: Path) -> None:
    _reject_reparse_components(data_dir, "DATA_DIR")
    database_target = data_dir / "database" / "app.db"
    database_target.parent.mkdir(parents=True, exist_ok=True)
    _reject_reparse_components(database_target.parent, "数据库目录")
    if database_target.exists():
        _reject_reparse_components(database_target, "目标数据库")
    _remove_sqlite_sidecars(database_target)
    os.replace(staging / "database" / "app.db", database_target)
    _replace_images(staging / "images", data_dir / "images")
    with _sqlite_connect_readonly(database_target) as connection:
        _validate_supported_database(connection)
        _validate_sanitized_database(connection)


def _restore_rollback(
    snapshot: Path,
    data_dir: Path,
    *,
    had_database: bool,
    had_images: bool,
) -> None:
    rollback_stage = Path(tempfile.mkdtemp(prefix=".3d66-rollback-apply-", dir=data_dir.parent))
    try:
        database_target = data_dir / "database" / "app.db"
        database_target.parent.mkdir(parents=True, exist_ok=True)
        _remove_sqlite_sidecars(database_target)
        if had_database:
            _copy_database_exact(
                snapshot / "database" / "app.db",
                rollback_stage / "database" / "app.db",
            )
            os.replace(rollback_stage / "database" / "app.db", database_target)
        elif database_target.exists():
            _reject_reparse_components(database_target, "目标数据库")
            database_target.unlink()
        images_target = data_dir / "images"
        if had_images:
            _copy_images(snapshot / "images", rollback_stage / "images")
            _replace_images(rollback_stage / "images", images_target)
        elif images_target.exists():
            _reject_reparse_components(images_target, "目标 images")
            shutil.rmtree(images_target)
    finally:
        shutil.rmtree(rollback_stage, ignore_errors=True)


def restore_backup(
    backup_dir: Path,
    data_dir: Path,
    *,
    repo_root: Path,
    dry_run: bool = False,
    env: Mapping[str, str] | None = None,
    service_check: Callable[[str, int], bool] = _service_may_be_running,
) -> BackupValidation:
    root = _normalized_path(repo_root, label="代码仓库")
    target = _normalized_path(data_dir, label="DATA_DIR")
    backup = _normalized_path(backup_dir, label="备份目录")
    _require_outside_repo(target, root, label="DATA_DIR")
    _require_outside_repo(backup, root, label="备份目录")
    _require_backup_outside_data(backup, target)
    validation = validate_backup(backup)
    if dry_run:
        return validation
    effective_env = os.environ if env is None else env
    host, port = _app_bind(root, effective_env)
    if service_check(host, port):
        raise DeployError("SERVICE_RUNNING", f"端口 {host}:{port} 正在使用；请先停止服务再恢复")
    if target.exists() and not target.is_dir():
        raise DeployError("DATA_DIR_INVALID", "DATA_DIR 不是目录")
    _reject_reparse_components(_existing_writable_ancestor(target.parent), "DATA_DIR 现有父目录")
    target.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".3d66-restore-stage-", dir=target.parent))
    rollback: Path | None = None
    preserve_rollback = False
    try:
        _copy_regular_file(validation.database_path, staging / "database" / "app.db")
        _copy_images(validation.backup_dir / "images", staging / "images")
        _copy_regular_file(validation.backup_dir / "manifest.json", staging / "manifest.json")
        staged_validation = validate_backup(staging)
        if staged_validation.manifest != validation.manifest:
            raise DeployError(
                "BACKUP_CHANGED_DURING_RESTORE",
                "备份在恢复校验与 staging 之间发生变化",
            )
        rollback, had_database, had_images = _create_rollback_snapshot(target)
        try:
            _apply_staged_restore(staging, target)
        except Exception as restore_exc:
            try:
                _restore_rollback(
                    rollback,
                    target,
                    had_database=had_database,
                    had_images=had_images,
                )
            except Exception as rollback_exc:
                preserve_rollback = True
                raise DeployError(
                    "RESTORE_AND_ROLLBACK_FAILED",
                    f"恢复失败且自动回滚失败；受控快照保留在 {rollback}",
                ) from rollback_exc
            raise DeployError("RESTORE_FAILED_ROLLED_BACK", "恢复失败，已自动恢复原数据") from restore_exc
        shutil.rmtree(rollback)
        rollback = None
        return validation
    finally:
        shutil.rmtree(staging, ignore_errors=True)
        if rollback is not None and rollback.exists() and not preserve_rollback:
            shutil.rmtree(rollback, ignore_errors=True)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="3d66 标签系统 Windows 部署维护工具")
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command in ("doctor", "backup"):
        subparser = subparsers.add_parser(command)
        subparser.add_argument("--repo-root", type=Path, required=True)
        subparser.add_argument("--data-dir", type=Path)
        if command == "backup":
            subparser.add_argument("--backup-dir", type=Path)
    restore = subparsers.add_parser("restore")
    restore.add_argument("--repo-root", type=Path, required=True)
    restore.add_argument("--data-dir", type=Path)
    restore.add_argument("--backup", type=Path, required=True)
    restore.add_argument("--dry-run", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if platform.system().casefold() != "windows":
            raise DeployError("PLATFORM_UNSUPPORTED", "该工具只允许在 Windows 上运行")
        repo_root = _normalized_path(args.repo_root, label="代码仓库")
        data_dir = resolve_data_dir(repo_root, explicit=args.data_dir)
        if args.command == "doctor":
            report = doctor(repo_root, data_dir=data_dir)
            for check in report.checks:
                print(f"[OK] {check}")
            print(f"[OK] DATA_DIR={report.data_dir}")
            print("诊断汇总：全部门禁通过")
            print(CREDENTIAL_NOTICE)
            return 0
        if args.command == "backup":
            destination = create_backup(
                data_dir,
                backup_root=args.backup_dir,
                repo_root=repo_root,
            )
            print(f"备份完成：{destination}")
            print(CREDENTIAL_NOTICE)
            return 0
        validation = restore_backup(
            args.backup,
            data_dir,
            repo_root=repo_root,
            dry_run=args.dry_run,
        )
        if args.dry_run:
            print(
                f"恢复校验通过（未修改数据）：schema={validation.schema_version}, "
                f"files={validation.file_count}"
            )
        else:
            print("恢复完成")
        print(CREDENTIAL_NOTICE)
        return 0
    except DeployError as exc:
        if getattr(args, "command", None) == "doctor":
            print(f"[FAIL] {exc.code}：{exc}", file=sys.stderr)
            print("诊断汇总：门禁未通过", file=sys.stderr)
        else:
            print(f"错误 [{exc.code}]：{exc}", file=sys.stderr)
        return 1
    except (OSError, sqlite3.Error, subprocess.SubprocessError) as exc:
        print(f"错误 [OPERATING_SYSTEM_FAILURE]：{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
