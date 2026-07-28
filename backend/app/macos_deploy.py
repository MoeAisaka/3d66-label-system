from __future__ import annotations

import argparse
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


BACKUP_SCHEMA = "3d66-label-system-macos-backup"
BACKUP_SCHEMA_VERSION = 1
SUPPORTED_SCHEMA_VERSION = max(migration.version for migration in MIGRATIONS)
PYTHON_MIN = (3, 11)
PYTHON_MAX_EXCLUSIVE = (3, 13)
NODE_MIN_MAJOR = 20
NODE_MAX_EXCLUSIVE = 27
NPM_MIN_MAJOR = 10
NPM_MAX_EXCLUSIVE = 12
MAX_MANIFEST_BYTES = 2 * 1024 * 1024
KEYCHAIN_NOTICE = (
    "Keychain、DPAPI、API Key 与登录会话均未备份；恢复后必须在目标机重新填写凭据。"
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


def _resolved(path: Path) -> Path:
    return path.expanduser().resolve(strict=False)


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def _require_outside_repo(path: Path, repo_root: Path, *, label: str) -> None:
    if _is_relative_to(_resolved(path), _resolved(repo_root)):
        raise DeployError(
            "PATH_INSIDE_REPOSITORY",
            f"{label}不得位于代码仓库内：{path}",
        )


def _require_backup_outside_data(backup_root: Path, data_dir: Path) -> None:
    root = _resolved(backup_root)
    data = _resolved(data_dir)
    if _is_relative_to(root, data) or _is_relative_to(data, root):
        raise DeployError(
            "BACKUP_DATA_OVERLAP",
            "备份目录与数据目录不得互相包含",
        )


def _safe_env_value(repo_root: Path, key: str, env: Mapping[str, str]) -> str | None:
    direct = env.get(key)
    if direct is not None and direct.strip():
        return direct.strip()
    env_file = repo_root / ".env"
    if not env_file.is_file() or env_file.is_symlink():
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


def default_macos_data_dir(home: Path | None = None) -> Path:
    return (home or Path.home()) / "Library" / "Application Support" / "3d66-label-system"


def resolve_data_dir(
    repo_root: Path,
    *,
    explicit: Path | None = None,
    env: Mapping[str, str] | None = None,
    home: Path | None = None,
) -> Path:
    if explicit is not None:
        return _resolved(explicit)
    effective_env = os.environ if env is None else env
    configured = _safe_env_value(repo_root, "DATA_DIR", effective_env)
    if configured:
        return _resolved(Path(configured))
    return _resolved(default_macos_data_dir(home))


def default_backup_root(home: Path | None = None) -> Path:
    return (home or Path.home()) / "Documents" / "3d66-label-system-backups"


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
        raise DeployError(
            "PYTHON_VERSION_UNSUPPORTED",
            "Python 必须为 3.11 或 3.12",
        )
    node_major = _parse_major(node_version, "Node.js")
    if not (NODE_MIN_MAJOR <= node_major < NODE_MAX_EXCLUSIVE):
        raise DeployError(
            "NODE_VERSION_UNSUPPORTED",
            "Node.js 必须为 20.x 至 26.x",
        )
    npm_major = _parse_major(npm_version, "npm")
    if not (NPM_MIN_MAJOR <= npm_major < NPM_MAX_EXCLUSIVE):
        raise DeployError(
            "NPM_VERSION_UNSUPPORTED",
            "npm 必须为 10.x 或 11.x",
        )


def _reject_symlink(path: Path, label: str) -> None:
    if path.is_symlink():
        raise DeployError("SYMLINK_REJECTED", f"{label}不得是符号链接：{path}")


def _existing_writable_ancestor(path: Path) -> Path:
    candidate = path
    while not candidate.exists():
        if candidate.parent == candidate:
            break
        candidate = candidate.parent
    return candidate


def _sqlite_connect_readonly(path: Path) -> sqlite3.Connection:
    uri = path.resolve(strict=True).as_uri() + "?mode=ro"
    connection = sqlite3.connect(uri, uri=True, timeout=10)
    connection.execute("PRAGMA busy_timeout=10000")
    return connection


def _integrity_check(connection: sqlite3.Connection) -> None:
    rows = connection.execute("PRAGMA integrity_check").fetchall()
    if rows != [("ok",)]:
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


def _validate_macos_credential_references(connection: sqlite3.Connection) -> None:
    for table in ("model_configs", "optimizer_configs"):
        if not _table_exists(connection, table):
            continue
        row = connection.execute(
            f"""
            SELECT COUNT(*)
            FROM {table}
            WHERE encrypted_api_key IS NOT NULL
              AND trim(encrypted_api_key) <> ''
              AND encrypted_api_key NOT LIKE 'keychain:v1:%'
            """
        ).fetchone()
        if row and int(row[0]) > 0:
            raise DeployError(
                "CREDENTIAL_REFERENCE_UNSAFE",
                f"{table} 含非 macOS Keychain 版本化引用",
            )


def doctor(
    repo_root: Path,
    *,
    data_dir: Path | None = None,
    env: Mapping[str, str] | None = None,
    platform_name: str | None = None,
    python_version: tuple[int, int, int] | None = None,
    node_version: str | None = None,
    npm_version: str | None = None,
) -> DoctorReport:
    root = _resolved(repo_root)
    if (platform_name or platform.system()) != "Darwin":
        raise DeployError("PLATFORM_UNSUPPORTED", "该工具只允许在 macOS 上运行")
    for required in (
        root / "backend" / "requirements.txt",
        root / "frontend" / "package-lock.json",
        root / "backend" / "app" / "launcher.py",
    ):
        if not required.is_file():
            raise DeployError("REPOSITORY_INCOMPLETE", f"仓库缺少必要文件：{required.name}")
    venv_python = root / ".venv" / "bin" / "python"
    if not venv_python.is_file():
        raise DeployError("VENV_MISSING", "仓库内 .venv 不存在，请先运行 install.sh")
    frontend_index = root / "frontend" / "dist" / "index.html"
    if not frontend_index.is_file():
        raise DeployError("FRONTEND_BUILD_MISSING", "前端构建不存在，请先运行 install.sh")
    _validate_runtime_versions(
        python_version=python_version or tuple(sys.version_info[:3]),
        node_version=node_version or _command_version("node"),
        npm_version=npm_version or _command_version("npm"),
    )
    effective_data = resolve_data_dir(root, explicit=data_dir, env=env)
    _require_outside_repo(effective_data, root, label="DATA_DIR")
    ancestor = _existing_writable_ancestor(effective_data)
    if not ancestor.is_dir() or not os.access(ancestor, os.W_OK | os.X_OK):
        raise DeployError("DATA_DIR_NOT_WRITABLE", "DATA_DIR 的现有父目录不可写")
    if effective_data.exists():
        _reject_symlink(effective_data, "DATA_DIR")
        if not effective_data.is_dir():
            raise DeployError("DATA_DIR_INVALID", "DATA_DIR 不是目录")
    database = effective_data / "database" / "app.db"
    schema_version: int | None = None
    if database.exists():
        _reject_symlink(database, "数据库")
        if not database.is_file():
            raise DeployError("DATABASE_INVALID", "数据库路径不是普通文件")
        with _sqlite_connect_readonly(database) as connection:
            schema_version = _validate_supported_database(connection)
            _validate_macos_credential_references(connection)
    checks = (
        "macOS 平台",
        "Python 3.11/3.12",
        "Node.js 20-26 与 npm 10/11",
        "仓库内 .venv",
        "前端生产构建",
        "仓库外 DATA_DIR",
        "SQLite 完整性与迁移版本" if database.exists() else "首次启动数据目录",
        "未读取 Keychain 凭据",
    )
    return DoctorReport(effective_data, database.exists(), schema_version, checks)


def _chmod_directory(path: Path) -> None:
    path.chmod(0o700)


def _chmod_file(path: Path) -> None:
    path.chmod(0o600)


def _ensure_private_tree(root: Path) -> None:
    _chmod_directory(root)
    for current, directories, files in os.walk(root):
        current_path = Path(current)
        _chmod_directory(current_path)
        for directory in directories:
            _chmod_directory(current_path / directory)
        for filename in files:
            _chmod_file(current_path / filename)


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _copy_regular_file(source: Path, destination: Path) -> None:
    _reject_symlink(source, "源文件")
    source_stat_before = source.stat()
    if not stat.S_ISREG(source_stat_before.st_mode):
        raise DeployError("NON_REGULAR_FILE", f"只允许备份普通文件：{source}")
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    shutil.copyfile(source, destination)
    _chmod_file(destination)
    source_stat_after = source.stat()
    if (
        source_stat_before.st_ino,
        source_stat_before.st_size,
        source_stat_before.st_mtime_ns,
    ) != (
        source_stat_after.st_ino,
        source_stat_after.st_size,
        source_stat_after.st_mtime_ns,
    ):
        raise DeployError("SOURCE_CHANGED_DURING_BACKUP", f"备份期间文件发生变化：{source}")


def _copy_images(source: Path, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True, mode=0o700)
    _chmod_directory(destination)
    if not source.exists():
        return
    _reject_symlink(source, "images")
    if not source.is_dir():
        raise DeployError("IMAGES_INVALID", "images 不是目录")
    for current, directories, files in os.walk(source, followlinks=False):
        current_path = Path(current)
        relative = current_path.relative_to(source)
        target_dir = destination / relative
        target_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        _chmod_directory(target_dir)
        for directory in directories:
            _reject_symlink(current_path / directory, "图片目录")
        for filename in files:
            _copy_regular_file(current_path / filename, target_dir / filename)


def _sqlite_backup(source: Path, destination: Path) -> None:
    _reject_symlink(source, "数据库")
    if not source.is_file():
        raise DeployError("DATABASE_MISSING", f"数据库不存在：{source}")
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with _sqlite_connect_readonly(source) as source_connection:
        destination_connection = sqlite3.connect(destination)
        try:
            source_connection.backup(destination_connection)
            destination_connection.commit()
        finally:
            destination_connection.close()
    _chmod_file(destination)


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
            f"""
            SELECT COUNT(*) FROM {table}
            WHERE encrypted_api_key IS NOT NULL AND trim(encrypted_api_key) <> ''
            """
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
            for directory in directories:
                _reject_symlink(current_path / directory, "备份目录")
            for filename in filenames:
                path = current_path / filename
                _reject_symlink(path, "备份文件")
                relative = path.relative_to(backup_dir).as_posix()
                files.append(
                    {
                        "path": relative,
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
    now: datetime | None = None,
) -> Path:
    _reject_symlink(data_dir.expanduser(), "DATA_DIR")
    if backup_root is not None:
        _reject_symlink(backup_root.expanduser(), "备份目录")
    source = _resolved(data_dir)
    root = _resolved(backup_root or default_backup_root())
    if repo_root is not None:
        _require_outside_repo(source, repo_root, label="DATA_DIR")
        _require_outside_repo(root, repo_root, label="备份目录")
    _require_backup_outside_data(root, source)
    _reject_symlink(source, "DATA_DIR")
    if not source.is_dir():
        raise DeployError("DATA_DIR_MISSING", "DATA_DIR 不存在")
    database = source / "database" / "app.db"
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    _chmod_directory(root)
    created_at, compact_timestamp = _timestamp(now)
    destination = _unique_backup_destination(root, compact_timestamp)
    temporary = Path(tempfile.mkdtemp(prefix=".3d66-backup-tmp-", dir=root))
    _chmod_directory(temporary)
    try:
        copied_database = temporary / "database" / "app.db"
        _sqlite_backup(database, copied_database)
        schema_version = _sanitize_backup_database(copied_database)
        _copy_images(source / "images", temporary / "images")
        _ensure_private_tree(temporary)
        files = _manifest_files(temporary)
        manifest: dict[str, object] = {
            "schema": BACKUP_SCHEMA,
            "schema_version": BACKUP_SCHEMA_VERSION,
            "created_at": created_at,
            "source": {
                "application": "3d66-label-system",
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
            "restore_notice": KEYCHAIN_NOTICE,
        }
        manifest_path = temporary / "manifest.json"
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        _chmod_file(manifest_path)
        _ensure_private_tree(temporary)
        os.replace(temporary, destination)
        _ensure_private_tree(destination)
        return destination
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise


def _validated_relative_path(raw: object) -> PurePosixPath:
    if not isinstance(raw, str) or not raw:
        raise DeployError("MANIFEST_PATH_INVALID", "manifest 文件路径无效")
    path = PurePosixPath(raw)
    if (
        path.is_absolute()
        or path.as_posix() != raw
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise DeployError("PATH_TRAVERSAL_REJECTED", f"拒绝不安全相对路径：{raw}")
    if raw != "database/app.db" and not raw.startswith("images/"):
        raise DeployError("MANIFEST_PATH_FORBIDDEN", f"备份含未授权路径：{raw}")
    if raw.startswith("images/") and len(path.parts) < 2:
        raise DeployError("MANIFEST_PATH_INVALID", f"图片路径无效：{raw}")
    return path


def _assert_private_mode(path: Path, *, directory: bool) -> None:
    mode = stat.S_IMODE(path.stat().st_mode)
    forbidden = mode & 0o077
    owner_required = stat.S_IRUSR | (stat.S_IXUSR if directory else 0)
    if forbidden or (mode & owner_required) != owner_required:
        raise DeployError("PERMISSIONS_UNSAFE", f"备份权限不安全：{path.name}")


def _enumerate_backup_files(root: Path) -> set[str]:
    found: set[str] = set()
    for base_name in ("database", "images"):
        base = root / base_name
        if not base.exists():
            continue
        _reject_symlink(base, "备份目录")
        if not base.is_dir():
            raise DeployError("BACKUP_LAYOUT_INVALID", f"{base_name} 不是目录")
        for current, directories, filenames in os.walk(base, followlinks=False):
            current_path = Path(current)
            _assert_private_mode(current_path, directory=True)
            for directory in directories:
                _reject_symlink(current_path / directory, "备份目录")
            for filename in filenames:
                path = current_path / filename
                _reject_symlink(path, "备份文件")
                if not path.is_file():
                    raise DeployError("NON_REGULAR_FILE", f"备份含非普通文件：{path}")
                found.add(path.relative_to(root).as_posix())
    return found


def validate_backup(
    backup_dir: Path,
    *,
    supported_schema_version: int = SUPPORTED_SCHEMA_VERSION,
) -> BackupValidation:
    _reject_symlink(backup_dir.expanduser(), "备份目录")
    root = _resolved(backup_dir)
    _reject_symlink(root, "备份目录")
    if not root.is_dir():
        raise DeployError("BACKUP_MISSING", "备份目录不存在")
    _assert_private_mode(root, directory=True)
    manifest_path = root / "manifest.json"
    _reject_symlink(manifest_path, "manifest")
    if not manifest_path.is_file():
        raise DeployError("MANIFEST_MISSING", "备份缺少 manifest.json")
    _assert_private_mode(manifest_path, directory=False)
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
    for entry in entries:
        if not isinstance(entry, dict):
            raise DeployError("MANIFEST_FILES_INVALID", "manifest 文件条目无效")
        relative = _validated_relative_path(entry.get("path"))
        raw_path = relative.as_posix()
        if raw_path in declared:
            raise DeployError("MANIFEST_DUPLICATE_PATH", f"manifest 路径重复：{raw_path}")
        declared.add(raw_path)
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
        resolved_file = file_path.resolve(strict=False)
        if not _is_relative_to(resolved_file, root):
            raise DeployError("PATH_TRAVERSAL_REJECTED", f"路径逃逸备份目录：{raw_path}")
        _reject_symlink(file_path, "备份文件")
        if not file_path.is_file():
            raise DeployError("BACKUP_FILE_MISSING", f"备份文件缺失：{raw_path}")
        _assert_private_mode(file_path, directory=False)
        if file_path.stat().st_size != size or _hash_file(file_path) != digest:
            raise DeployError("BACKUP_TAMPERED", f"备份文件校验失败：{raw_path}")
    if "database/app.db" not in declared:
        raise DeployError("DATABASE_MISSING", "manifest 未包含 database/app.db")
    actual = _enumerate_backup_files(root)
    if actual != declared:
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
    if not isinstance(source, dict) or source.get("database_schema_version") != schema_version:
        raise DeployError("MANIFEST_SCHEMA_MISMATCH", "manifest 与数据库迁移版本不一致")
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
    parent = data_dir.parent
    parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    snapshot = Path(tempfile.mkdtemp(prefix=".3d66-rollback-", dir=parent))
    _chmod_directory(snapshot)
    database = data_dir / "database" / "app.db"
    images = data_dir / "images"
    had_database = database.exists()
    had_images = images.exists()
    try:
        if had_database:
            _copy_database_exact(database, snapshot / "database" / "app.db")
        if had_images:
            _copy_images(images, snapshot / "images")
        _ensure_private_tree(snapshot)
        return snapshot, had_database, had_images
    except Exception:
        shutil.rmtree(snapshot, ignore_errors=True)
        raise


def _replace_images(staged: Path, target: Path) -> None:
    displaced = target.parent / f".images-displaced-{uuid.uuid4().hex}"
    had_target = target.exists()
    if had_target:
        _reject_symlink(target, "目标 images")
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
    database_target = data_dir / "database" / "app.db"
    database_target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    _chmod_directory(data_dir)
    _chmod_directory(database_target.parent)
    if database_target.exists():
        _reject_symlink(database_target, "目标数据库")
    os.replace(staging / "database" / "app.db", database_target)
    _chmod_file(database_target)
    _replace_images(staging / "images", data_dir / "images")
    _ensure_private_tree(data_dir / "images")
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
    rollback_stage = Path(
        tempfile.mkdtemp(prefix=".3d66-rollback-apply-", dir=data_dir.parent)
    )
    _chmod_directory(rollback_stage)
    try:
        database_target = data_dir / "database" / "app.db"
        database_target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if had_database:
            _copy_database_exact(
                snapshot / "database" / "app.db",
                rollback_stage / "database" / "app.db",
            )
            os.replace(rollback_stage / "database" / "app.db", database_target)
            _chmod_file(database_target)
        elif database_target.exists():
            database_target.unlink()
        images_target = data_dir / "images"
        if had_images:
            _copy_images(snapshot / "images", rollback_stage / "images")
            _replace_images(rollback_stage / "images", images_target)
        elif images_target.exists():
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
    root = _resolved(repo_root)
    _reject_symlink(data_dir.expanduser(), "DATA_DIR")
    target = _resolved(data_dir)
    _require_outside_repo(target, root, label="DATA_DIR")
    validation = validate_backup(backup_dir)
    _require_outside_repo(validation.backup_dir, root, label="备份目录")
    _require_backup_outside_data(validation.backup_dir, target)
    if dry_run:
        return validation
    effective_env = os.environ if env is None else env
    host, port = _app_bind(root, effective_env)
    if service_check(host, port):
        raise DeployError(
            "SERVICE_RUNNING",
            f"端口 {host}:{port} 正在使用；请先停止服务再恢复",
        )
    if target.exists():
        _reject_symlink(target, "DATA_DIR")
        if not target.is_dir():
            raise DeployError("DATA_DIR_INVALID", "DATA_DIR 不是目录")
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    staging = Path(tempfile.mkdtemp(prefix=".3d66-restore-stage-", dir=target.parent))
    _chmod_directory(staging)
    rollback: Path | None = None
    preserve_rollback = False
    try:
        _copy_regular_file(
            validation.database_path,
            staging / "database" / "app.db",
        )
        _copy_images(validation.backup_dir / "images", staging / "images")
        _copy_regular_file(
            validation.backup_dir / "manifest.json",
            staging / "manifest.json",
        )
        _ensure_private_tree(staging)
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
            raise DeployError(
                "RESTORE_FAILED_ROLLED_BACK",
                "恢复失败，已自动恢复原数据",
            ) from restore_exc
        shutil.rmtree(rollback)
        rollback = None
        return validation
    finally:
        shutil.rmtree(staging, ignore_errors=True)
        if rollback is not None and rollback.exists() and not preserve_rollback:
            shutil.rmtree(rollback, ignore_errors=True)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="3d66 标签系统 macOS 部署维护工具")
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
        if platform.system() != "Darwin":
            raise DeployError("PLATFORM_UNSUPPORTED", "该工具只允许在 macOS 上运行")
        repo_root = _resolved(args.repo_root)
        data_dir = resolve_data_dir(repo_root, explicit=args.data_dir)
        if args.command == "doctor":
            report = doctor(repo_root, data_dir=data_dir)
            print("诊断通过")
            for check in report.checks:
                print(f"[OK] {check}")
            print(f"DATA_DIR={report.data_dir}")
            print(KEYCHAIN_NOTICE)
            return 0
        if args.command == "backup":
            destination = create_backup(
                data_dir,
                backup_root=args.backup_dir,
                repo_root=repo_root,
            )
            print(f"备份完成：{destination}")
            print(KEYCHAIN_NOTICE)
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
        print(KEYCHAIN_NOTICE)
        return 0
    except DeployError as exc:
        print(f"错误 [{exc.code}]：{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
