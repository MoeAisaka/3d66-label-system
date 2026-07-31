#!/usr/bin/env python3
"""Deploy Codeup main to the shared 3d66 label-system test server."""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Iterable, Sequence


CODEUP_URL = "https://codeup.aliyun.com/3d66/tepeng/3d66.label-system.git"
SERVER = "yuankangzhi@192.168.1.35"
REMOTE_PROJECT = "/opt/3d66-label-system"
HEALTH_URL = "http://192.168.1.35:8081/api/health"


class DeployError(RuntimeError):
    """A deployment precondition or command failed."""


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


def main() -> int:
    print("Deployment implementation is not complete yet.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except DeployError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
