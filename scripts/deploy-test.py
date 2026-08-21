#!/usr/bin/env python3
"""Deploy Codeup main to the shared 3d66 label-system test server."""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Iterable, Sequence


CODEUP_URL = "https://codeup.aliyun.com/3d66/tepeng/3d66.label-system.git"
CODEUP_SSH_URL = "git@codeup.aliyun.com:3d66/tepeng/3d66.label-system.git"
SERVER = "yuankangzhi@192.168.1.35"
REMOTE_PROJECT = "/opt/3d66-label-system"
REMOTE_DEPLOY_SCRIPT = "/usr/local/sbin/deploy-3d66-label-test"
HEALTH_URL = "http://192.168.1.35:8081/api/health"
DEFAULT_DEPLOY_KEY = Path.home() / ".ssh" / "3d66_label_test_ed25519"


class DeployError(RuntimeError):
    """A deployment precondition or command failed."""


class Release:
    def __init__(self, repo_root: Path, temp_dir: Path, bundle_path: Path, commit: str):
        self.repo_root = repo_root
        self.temp_dir = temp_dir
        self.bundle_path = bundle_path
        self.commit = commit


class RemoteCommands:
    def __init__(
        self,
        bundle_remote: str,
        upload_bundle: list[str],
        invoke: list[str],
        cleanup: list[str],
    ):
        self.bundle_remote = bundle_remote
        self.upload_bundle = upload_bundle
        self.invoke = invoke
        self.cleanup = cleanup


def validate_origin(url: str) -> None:
    normalized = url.rstrip("/")
    allowed = {CODEUP_URL.rstrip("/"), CODEUP_SSH_URL.rstrip("/")}
    if normalized not in allowed:
        raise DeployError(
            "origin must be the Codeup repository: "
            f"{CODEUP_URL} or {CODEUP_SSH_URL}"
        )


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


def build_remote_commands(
    bundle_path: Path, commit: str, process_id: int, key_path: Path
) -> RemoteCommands:
    validate_commit(commit)
    suffix = f"{commit[:8]}-{process_id}"
    bundle_remote = f"/tmp/3d66-label-main-{suffix}.bundle"
    ssh_options = [
        "-i",
        str(key_path),
        "-o",
        "IdentitiesOnly=yes",
        "-o",
        "BatchMode=yes",
    ]
    return RemoteCommands(
        bundle_remote=bundle_remote,
        upload_bundle=["scp", *ssh_options, str(bundle_path), f"{SERVER}:{bundle_remote}"],
        invoke=[
            "ssh",
            *ssh_options,
            SERVER,
            "sudo",
            "-n",
            REMOTE_DEPLOY_SCRIPT,
            bundle_remote,
            commit,
        ],
        cleanup=["ssh", *ssh_options, SERVER, "rm", "-f", "--", bundle_remote],
    )


def confirm_deploy(assume_yes: bool) -> None:
    if assume_yes:
        return
    answer = input("Type DEPLOY to publish this commit to the shared test server: ")
    if answer != "DEPLOY":
        raise DeployError("deployment cancelled")


def deploy_release(release: Release) -> None:
    key_path = Path(os.environ.get("THREED66_DEPLOY_KEY", DEFAULT_DEPLOY_KEY))
    if not key_path.is_file():
        raise DeployError(
            f"SSH deploy key not found: {key_path}. "
            "Configure the server key before publishing."
        )
    commands = build_remote_commands(
        release.bundle_path, release.commit, os.getpid(), key_path
    )
    try:
        print("Uploading release bundle...")
        run_interactive(commands.upload_bundle, release.repo_root)
        print("Starting guarded server deployment...")
        run_interactive(commands.invoke, release.repo_root)
    finally:
        subprocess.run(commands.cleanup, cwd=release.repo_root, check=False)


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
            print("发布检查通过，未连接测试服务器。")
            return 0
        confirm_deploy(args.yes)
        deploy_release(release)
        print(f"Published: {release.commit}")
        print("测试环境更新成功！")
        print(f"Test URL: http://192.168.1.35:8081")
        print(f"Health URL: {HEALTH_URL}")
        return 0
    finally:
        cleanup_release(release)

    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except DeployError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
