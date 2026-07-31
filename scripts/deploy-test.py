#!/usr/bin/env python3
"""Deploy Codeup main to the shared 3d66 label-system test server."""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Iterable, Sequence


CODEUP_URL = "https://codeup.aliyun.com/3d66/tepeng/3d66.label-system.git"
SERVER = "yuankangzhi@192.168.1.35"
REMOTE_PROJECT = "/opt/3d66-label-system"
HEALTH_URL = "http://192.168.1.35:8081/api/health"


class DeployError(RuntimeError):
    """A deployment precondition or command failed."""


class Release:
    def __init__(self, repo_root: Path, temp_dir: Path, bundle_path: Path, commit: str):
        self.repo_root = repo_root
        self.temp_dir = temp_dir
        self.bundle_path = bundle_path
        self.commit = commit


def validate_origin(url: str) -> None:
    if url.rstrip("/") != CODEUP_URL.rstrip("/"):
        raise DeployError(f"origin must be the Codeup repository: {CODEUP_URL}")


def validate_commit(commit: str) -> None:
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise DeployError("resolved main commit is not a full Git commit SHA")


def require_commands(commands: Iterable[str]) -> None:
    missing = [command for command in commands if shutil.which(command) is None]
    if missing:
        raise DeployError(f"required command not found: {', '.join(missing)}")


def run_capture(args: Sequence[str], cwd: Path) -> str:
    result = subprocess.run(
        list(args),
        cwd=cwd,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip()
        raise DeployError(f"command failed ({args[0]}): {detail}")
    return result.stdout.strip()


def run_interactive(args: Sequence[str], cwd: Path) -> None:
    result = subprocess.run(list(args), cwd=cwd, check=False)
    if result.returncode != 0:
        raise DeployError(f"command failed with exit code {result.returncode}: {args[0]}")


def prepare_release(start_dir: Path) -> Release:
    repo_root = Path(run_capture(["git", "rev-parse", "--show-toplevel"], start_dir))
    origin = run_capture(["git", "remote", "get-url", "origin"], repo_root)
    validate_origin(origin)
    run_capture(["git", "fetch", "origin", "main"], repo_root)
    commit = run_capture(["git", "rev-parse", "origin/main"], repo_root)
    validate_commit(commit)

    temp_dir = Path(tempfile.mkdtemp(prefix="3d66-label-deploy-"))
    bundle_path = temp_dir / "main.bundle"
    try:
        run_capture(
            ["git", "bundle", "create", str(bundle_path), "origin/main"], repo_root
        )
        run_capture(["git", "bundle", "verify", str(bundle_path)], repo_root)
    except Exception:
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise
    return Release(repo_root, temp_dir, bundle_path, commit)


def cleanup_release(release: Release) -> None:
    shutil.rmtree(release.temp_dir, ignore_errors=True)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Deploy Codeup main to the shared 3d66 label-system test server."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="validate and create the main bundle without connecting to the server",
    )
    parser.add_argument("--yes", action="store_true", help="skip DEPLOY confirmation")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    require_commands(("git", "ssh", "scp"))
    release = prepare_release(Path.cwd())
    try:
        print(f"Codeup: {CODEUP_URL}")
        print(f"Branch: main")
        print(f"Commit: {release.commit} ({release.commit[:8]})")
        if args.dry_run:
            print("Dry run passed. No server connection was made.")
            return 0
        raise DeployError("server transfer is not implemented yet")
    finally:
        cleanup_release(release)

    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except DeployError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
