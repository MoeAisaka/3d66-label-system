#!/usr/bin/env python3
"""Relink verified historical local assets to read-only NAS source files."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine
from sqlalchemy.orm import Session


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.audit import append_audit_event, canonical_json  # noqa: E402
from app.nas_history_migration import (  # noqa: E402
    NasHistoryMigrationError,
    apply_relink_plan,
    build_relink_plan,
    cleanup_verified_local_files,
    relink_plan_from_payload,
    relink_plan_payload,
    relink_verification_payload,
    verify_relink_plan,
)


DEFAULT_SCAN_ROOTS = (
    "采集任务交付文件/国圣坤/已处理样本3d&SU",
    "采集任务交付文件/林周金/模型迭代样本/灵感图-普通样本",
)


def _atomic_json(path: Path, payload: dict[str, Any]) -> str:
    rendered = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    path = path.expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            output.write(rendered)
            output.flush()
            os.fsync(output.fileno())
        os.chmod(temporary_name, 0o600)
        os.replace(temporary_name, path)
    finally:
        if os.path.exists(temporary_name):
            os.unlink(temporary_name)
    return hashlib.sha256(rendered.encode("utf-8")).hexdigest()


def _load_plan(path: Path):
    payload = json.loads(path.expanduser().resolve().read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise NasHistoryMigrationError("迁移计划必须是 JSON 对象")
    return relink_plan_from_payload(payload)


def _engine(database: Path):
    path = database.expanduser().resolve()
    if not path.is_file():
        raise NasHistoryMigrationError(f"数据库不存在：{path}")
    return create_engine(
        f"sqlite:///{path.as_posix()}",
        connect_args={"timeout": 30},
    )


def _common_paths(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--database", type=Path, default=Path("/data/database/app.db"))
    parser.add_argument("--upload-dir", type=Path, default=Path("/data/images"))
    parser.add_argument("--nas-root", type=Path, default=Path("/mnt/label-nas/maps"))


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    plan = subparsers.add_parser("plan")
    _common_paths(plan)
    plan.add_argument("--scan-root", action="append", dest="scan_roots")
    plan.add_argument("--output", type=Path, required=True)

    for name in ("apply", "verify", "cleanup"):
        command = subparsers.add_parser(name)
        _common_paths(command)
        command.add_argument("--plan", type=Path, required=True)
        if name in {"apply", "cleanup"}:
            command.add_argument("--confirm-plan-hash", required=True)
        if name in {"verify", "cleanup"}:
            command.add_argument("--output", type=Path, required=True)
        if name in {"apply", "cleanup"}:
            command.add_argument("--actor", default="nas-history-migration")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    engine = _engine(args.database)
    try:
        with Session(engine) as db:
            if args.command == "plan":
                plan = build_relink_plan(
                    db,
                    upload_dir=args.upload_dir,
                    nas_root=args.nas_root,
                    scan_roots=args.scan_roots or DEFAULT_SCAN_ROOTS,
                )
                output_sha256 = _atomic_json(args.output, relink_plan_payload(plan))
                print(canonical_json({
                    "command": "plan",
                    "plan_hash": plan.plan_hash,
                    "output_sha256": output_sha256,
                    "summary": plan.summary,
                }))
                return 0

            plan = _load_plan(args.plan)
            if args.command in {"apply", "cleanup"} and args.confirm_plan_hash != plan.plan_hash:
                raise NasHistoryMigrationError("确认哈希与迁移计划不一致")

            if args.command == "apply":
                with db.begin():
                    result = apply_relink_plan(
                        db,
                        plan=plan,
                        upload_dir=args.upload_dir,
                        nas_root=args.nas_root,
                        actor=args.actor,
                    )
                print(canonical_json({"command": "apply", "plan_hash": plan.plan_hash, **result}))
                return 0

            verification = verify_relink_plan(
                db,
                plan=plan,
                upload_dir=args.upload_dir,
                nas_root=args.nas_root,
            )
            verification_payload = relink_verification_payload(plan, verification)
            output_sha256 = _atomic_json(args.output, verification_payload)
            if args.command == "verify":
                print(canonical_json({
                    "command": "verify",
                    "plan_hash": plan.plan_hash,
                    "output_sha256": output_sha256,
                    "ok": verification.ok,
                    "cleanup_count": len(verification.cleanup_stored_names),
                    "failed_count": len(verification.issues),
                }))
                return 0 if verification.ok else 2

            append_audit_event(
                db,
                category="assets",
                action="nas_local_original_cleanup_planned",
                subject_type="nas_relink_plan",
                subject_id=plan.plan_hash,
                actor=args.actor,
                payload={
                    "plan_hash": plan.plan_hash,
                    "verification_output_sha256": output_sha256,
                    "cleanup_count": len(verification.cleanup_stored_names),
                    "cleanup_total_bytes": verification_payload["cleanup_total_bytes"],
                    "cleanup_manifest_sha256": verification_payload["cleanup_manifest_sha256"],
                    "preserved_count": len(plan.matches) - len(verification.cleanup_stored_names),
                },
                event_key=f"nas-relink:{plan.plan_hash}:cleanup-planned",
            )
            db.commit()
            result = cleanup_verified_local_files(
                db,
                plan=plan,
                verification=verification,
                upload_dir=args.upload_dir,
                nas_root=args.nas_root,
                confirmed_plan_hash=args.confirm_plan_hash,
            )
            append_audit_event(
                db,
                category="assets",
                action="nas_local_original_cleanup_completed",
                subject_type="nas_relink_plan",
                subject_id=plan.plan_hash,
                actor=args.actor,
                payload={"plan_hash": plan.plan_hash, **result},
                event_key=f"nas-relink:{plan.plan_hash}:cleanup-completed",
            )
            db.commit()
            print(canonical_json({"command": "cleanup", "plan_hash": plan.plan_hash, **result}))
            return 0
    finally:
        engine.dispose()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (NasHistoryMigrationError, OSError, ValueError) as exc:
        print(canonical_json({"status": "failed", "error": str(exc)}), file=sys.stderr)
        raise SystemExit(1) from None
