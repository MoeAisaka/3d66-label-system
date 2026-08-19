from __future__ import annotations

import hashlib
import os
import unicodedata
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .audit import append_audit_event, canonical_json
from .field_demand_contracts import record_asset_version
from .models import Asset, AssetVersion
from .nas_storage import normalize_nas_uri, nas_relative_path, resolve_nas_uri


class NasHistoryMigrationError(RuntimeError):
    pass


@dataclass(frozen=True)
class RelinkMatch:
    asset_id: int
    original_name: str
    stored_name: str
    sha256: str
    size_bytes: int
    source_uri: str


@dataclass(frozen=True)
class RelinkIssue:
    asset_id: int
    stored_name: str
    reason: str
    detail: str


@dataclass(frozen=True)
class RelinkPlan:
    generated_at: str
    scan_roots: tuple[str, ...]
    matches: tuple[RelinkMatch, ...]
    issues: tuple[RelinkIssue, ...]
    summary: dict[str, int]
    plan_hash: str


@dataclass(frozen=True)
class RelinkVerification:
    plan_hash: str
    ok: bool
    cleanup_stored_names: tuple[str, ...]
    issues: tuple[RelinkIssue, ...]


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_local_path(upload_dir: Path, stored_name: str) -> Path:
    root = upload_dir.resolve(strict=True)
    candidate = upload_dir / stored_name
    if candidate.is_symlink():
        raise NasHistoryMigrationError("本地原图不能是符号链接")
    resolved = candidate.resolve(strict=False)
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise NasHistoryMigrationError("本地原图路径越过存储根目录") from exc
    return resolved


def _plan_hash_payload(
    *,
    generated_at: str,
    scan_roots: Sequence[str],
    matches: Sequence[RelinkMatch],
    issues: Sequence[RelinkIssue],
    summary: dict[str, int],
) -> str:
    payload = {
        "schema_version": "nas-history-relink-plan-v1",
        "generated_at": generated_at,
        "scan_roots": list(scan_roots),
        "matches": [asdict(item) for item in matches],
        "issues": [asdict(item) for item in issues],
        "summary": summary,
    }
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def _normalized_name(value: str) -> str:
    return unicodedata.normalize("NFC", value).replace("\\", "/").strip("/")


def _choose_candidate(original_name: str, candidates: Sequence[str]) -> str | None:
    if len(candidates) == 1:
        return candidates[0]
    original = _normalized_name(original_name)
    suffix_matches = [
        uri
        for uri in candidates
        if _normalized_name(str(nas_relative_path(uri))) == original
        or _normalized_name(str(nas_relative_path(uri))).endswith("/" + original)
    ]
    if len(suffix_matches) == 1:
        return suffix_matches[0]
    basename = Path(original).name
    basename_matches = [
        uri for uri in candidates if Path(str(nas_relative_path(uri))).name == basename
    ]
    return basename_matches[0] if len(basename_matches) == 1 else None


def _scan_nas_hashes(
    *,
    nas_root: Path,
    scan_roots: Sequence[str],
    expected_by_size: dict[int, set[str]],
) -> dict[str, list[str]]:
    hashes: dict[str, list[str]] = defaultdict(list)
    visited: set[Path] = set()
    for raw_root in scan_roots:
        root_uri = normalize_nas_uri(f"nas://maps/{raw_root.strip('/')}")
        root_path = resolve_nas_uri(root_uri, nas_root)
        if not root_path.is_dir():
            raise NasHistoryMigrationError(f"NAS 扫描目录不存在：{root_uri}")
        for current, directories, files in os.walk(root_path, followlinks=False):
            current_path = Path(current)
            directories[:] = [
                name for name in directories if not (current_path / name).is_symlink()
            ]
            for name in files:
                path = current_path / name
                if path.is_symlink() or path in visited:
                    continue
                visited.add(path)
                size_bytes = path.stat().st_size
                expected_hashes = expected_by_size.get(size_bytes)
                if not expected_hashes:
                    continue
                digest = _sha256_file(path)
                if digest not in expected_hashes:
                    continue
                relative = path.relative_to(nas_root).as_posix()
                hashes[digest].append(normalize_nas_uri(f"nas://maps/{relative}"))
    return hashes


def build_relink_plan(
    db: Session,
    *,
    upload_dir: Path,
    nas_root: Path,
    scan_roots: Sequence[str],
    generated_at: datetime | None = None,
) -> RelinkPlan:
    if not scan_roots:
        raise NasHistoryMigrationError("至少需要一个 NAS 扫描目录")
    assets = db.scalars(
        select(Asset)
        .where(func.coalesce(Asset.storage_backend, "local") == "local")
        .order_by(Asset.id)
    ).all()
    issues: list[RelinkIssue] = []
    eligible: list[Asset] = []
    expected_by_size: dict[int, set[str]] = defaultdict(set)
    for asset in assets:
        try:
            local_path = _safe_local_path(upload_dir, asset.stored_name)
        except NasHistoryMigrationError as exc:
            issues.append(RelinkIssue(asset.id, asset.stored_name, "local_path_invalid", str(exc)))
            continue
        if not local_path.is_file():
            issues.append(RelinkIssue(asset.id, asset.stored_name, "local_missing", "本地原图不存在"))
            continue
        if local_path.stat().st_size != asset.size_bytes or _sha256_file(local_path) != asset.sha256:
            issues.append(RelinkIssue(asset.id, asset.stored_name, "local_hash_mismatch", "本地原图与数据库哈希不一致"))
            continue
        eligible.append(asset)
        expected_by_size[int(asset.size_bytes)].add(asset.sha256)

    nas_hashes = _scan_nas_hashes(
        nas_root=nas_root,
        scan_roots=scan_roots,
        expected_by_size=expected_by_size,
    )
    matches: list[RelinkMatch] = []
    for asset in eligible:
        candidates = sorted(set(nas_hashes.get(asset.sha256, [])))
        if not candidates:
            issues.append(RelinkIssue(asset.id, asset.stored_name, "nas_missing", "NAS 中没有相同哈希文件"))
            continue
        source_uri = _choose_candidate(asset.original_name, candidates)
        if source_uri is None:
            issues.append(RelinkIssue(asset.id, asset.stored_name, "nas_ambiguous", f"NAS 中存在 {len(candidates)} 个相同哈希文件"))
            continue
        occupied = db.scalar(
            select(Asset.id).where(Asset.source_uri == source_uri, Asset.id != asset.id)
        )
        if occupied is not None:
            issues.append(RelinkIssue(asset.id, asset.stored_name, "nas_source_in_use", f"NAS 来源已由素材 #{occupied} 使用"))
            continue
        matches.append(
            RelinkMatch(
                asset_id=asset.id,
                original_name=asset.original_name,
                stored_name=asset.stored_name,
                sha256=asset.sha256,
                size_bytes=asset.size_bytes,
                source_uri=source_uri,
            )
        )

    counts = Counter(issue.reason for issue in issues)
    summary = {
        "local_assets": len(assets),
        "matched": len(matches),
        **{reason: counts.get(reason, 0) for reason in sorted(counts)},
    }
    generated = (generated_at or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat()
    normalized_roots = tuple(_normalized_name(value) for value in scan_roots)
    plan_hash = _plan_hash_payload(
        generated_at=generated,
        scan_roots=normalized_roots,
        matches=matches,
        issues=issues,
        summary=summary,
    )
    return RelinkPlan(
        generated_at=generated,
        scan_roots=normalized_roots,
        matches=tuple(matches),
        issues=tuple(issues),
        summary=summary,
        plan_hash=plan_hash,
    )


def _validate_match_files(
    match: RelinkMatch,
    *,
    upload_dir: Path,
    nas_root: Path,
) -> str | None:
    try:
        local_path = _safe_local_path(upload_dir, match.stored_name)
        nas_path = resolve_nas_uri(match.source_uri, nas_root)
    except (NasHistoryMigrationError, ValueError) as exc:
        return str(exc)
    if not local_path.is_file():
        return "本地原图不存在"
    if local_path.stat().st_size != match.size_bytes or _sha256_file(local_path) != match.sha256:
        return "本地原图与计划哈希不一致"
    if not nas_path.is_file():
        return "NAS 原图不存在"
    if nas_path.stat().st_size != match.size_bytes or _sha256_file(nas_path) != match.sha256:
        return "NAS 原图与计划哈希不一致"
    return None


def apply_relink_plan(
    db: Session,
    *,
    plan: RelinkPlan,
    upload_dir: Path,
    nas_root: Path,
    actor: str,
) -> dict[str, int]:
    relinked = 0
    already_relinked = 0
    occurred_at = datetime.fromisoformat(plan.generated_at)
    for match in plan.matches:
        asset = db.get(Asset, match.asset_id)
        if asset is None:
            raise NasHistoryMigrationError(f"素材 #{match.asset_id} 不存在")
        if (
            asset.storage_backend == "nas_maps"
            and asset.source_uri == match.source_uri
            and asset.sha256 == match.sha256
        ):
            already_relinked += 1
            continue
        if asset.storage_backend != "local" or asset.source_uri is not None:
            raise NasHistoryMigrationError(f"素材 #{match.asset_id} 当前存储状态已变化")
        if (
            asset.stored_name != match.stored_name
            or asset.original_name != match.original_name
            or asset.sha256 != match.sha256
            or asset.size_bytes != match.size_bytes
        ):
            raise NasHistoryMigrationError(f"素材 #{match.asset_id} 与迁移计划不一致")
        file_error = _validate_match_files(match, upload_dir=upload_dir, nas_root=nas_root)
        if file_error:
            raise NasHistoryMigrationError(f"素材 #{match.asset_id}：{file_error}")
        occupied = db.scalar(
            select(Asset.id).where(
                Asset.source_uri == match.source_uri,
                Asset.id != match.asset_id,
            )
        )
        if occupied is not None:
            raise NasHistoryMigrationError(
                f"素材 #{match.asset_id} 的 NAS 来源已由素材 #{occupied} 使用"
            )
        asset.storage_backend = "nas_maps"
        asset.source_uri = match.source_uri
        db.flush()
        record_asset_version(
            db,
            source_system="nas_maps",
            source_content_id=match.source_uri,
            source_version=f"sha256:{match.sha256}",
            asset=asset,
            occurred_at=occurred_at,
        )
        append_audit_event(
            db,
            category="assets",
            action="asset_storage_relinked_to_nas",
            subject_type="asset",
            subject_id=asset.id,
            actor=actor,
            payload={
                "plan_hash": plan.plan_hash,
                "storage_backend": "nas_maps",
                "source_uri": match.source_uri,
                "sha256": match.sha256,
            },
            event_key=f"asset:{asset.id}:nas-relink:{plan.plan_hash}",
        )
        relinked += 1
    return {"relinked": relinked, "already_relinked": already_relinked}


def verify_relink_plan(
    db: Session,
    *,
    plan: RelinkPlan,
    upload_dir: Path,
    nas_root: Path,
) -> RelinkVerification:
    cleanup: list[str] = []
    issues: list[RelinkIssue] = []
    for match in plan.matches:
        asset = db.get(Asset, match.asset_id)
        detail: str | None = None
        if asset is None:
            detail = "素材记录不存在"
        elif asset.storage_backend != "nas_maps" or asset.source_uri != match.source_uri:
            detail = "素材尚未切换为计划中的 NAS 引用"
        elif asset.sha256 != match.sha256:
            detail = "素材数据库哈希与计划不一致"
        else:
            file_error = _validate_match_files(
                match,
                upload_dir=upload_dir,
                nas_root=nas_root,
            )
            if file_error:
                detail = file_error
            else:
                version = db.scalar(
                    select(AssetVersion.id).where(
                        AssetVersion.asset_id == match.asset_id,
                        AssetVersion.storage_backend == "nas_maps",
                        AssetVersion.source_uri == match.source_uri,
                        AssetVersion.asset_sha256 == match.sha256,
                    )
                )
                if version is None:
                    detail = "缺少 NAS 来源资产版本记录"
        if detail:
            issues.append(RelinkIssue(match.asset_id, match.stored_name, "verification_failed", detail))
        else:
            cleanup.append(match.stored_name)
    return RelinkVerification(
        plan_hash=plan.plan_hash,
        ok=not issues,
        cleanup_stored_names=tuple(cleanup),
        issues=tuple(issues),
    )


def cleanup_verified_local_files(
    db: Session,
    *,
    plan: RelinkPlan,
    verification: RelinkVerification,
    upload_dir: Path,
    nas_root: Path,
    confirmed_plan_hash: str,
) -> dict[str, int]:
    if confirmed_plan_hash != plan.plan_hash or verification.plan_hash != plan.plan_hash:
        raise NasHistoryMigrationError("清理确认哈希与迁移计划不一致")
    fresh = verify_relink_plan(
        db,
        plan=plan,
        upload_dir=upload_dir,
        nas_root=nas_root,
    )
    allowed = set(fresh.cleanup_stored_names) & set(verification.cleanup_stored_names)
    deleted = 0
    for match in plan.matches:
        if match.stored_name not in allowed:
            continue
        path = _safe_local_path(upload_dir, match.stored_name)
        if path.is_file():
            path.unlink()
            deleted += 1
    return {"deleted": deleted, "preserved": len(plan.matches) - deleted}


def relink_plan_payload(plan: RelinkPlan) -> dict[str, object]:
    return {
        "schema_version": "nas-history-relink-plan-v1",
        "generated_at": plan.generated_at,
        "scan_roots": list(plan.scan_roots),
        "matches": [asdict(item) for item in plan.matches],
        "issues": [asdict(item) for item in plan.issues],
        "summary": plan.summary,
        "plan_hash": plan.plan_hash,
    }


def relink_plan_from_payload(payload: dict[str, object]) -> RelinkPlan:
    if payload.get("schema_version") != "nas-history-relink-plan-v1":
        raise NasHistoryMigrationError("迁移计划版本不受支持")
    generated_at = str(payload["generated_at"])
    scan_roots = tuple(str(value) for value in payload["scan_roots"])  # type: ignore[arg-type]
    matches = tuple(RelinkMatch(**item) for item in payload["matches"])  # type: ignore[arg-type]
    issues = tuple(RelinkIssue(**item) for item in payload["issues"])  # type: ignore[arg-type]
    summary = {str(key): int(value) for key, value in dict(payload["summary"]).items()}  # type: ignore[arg-type]
    expected_hash = _plan_hash_payload(
        generated_at=generated_at,
        scan_roots=scan_roots,
        matches=matches,
        issues=issues,
        summary=summary,
    )
    if payload.get("plan_hash") != expected_hash:
        raise NasHistoryMigrationError("迁移计划内容哈希校验失败")
    return RelinkPlan(
        generated_at=generated_at,
        scan_roots=scan_roots,
        matches=matches,
        issues=issues,
        summary=summary,
        plan_hash=expected_hash,
    )


def relink_verification_payload(
    plan: RelinkPlan,
    verification: RelinkVerification,
) -> dict[str, Any]:
    eligible_names = set(verification.cleanup_stored_names)
    cleanup_manifest = [
        asdict(match) for match in plan.matches if match.stored_name in eligible_names
    ]
    cleanup_manifest_sha256 = hashlib.sha256(
        canonical_json(cleanup_manifest).encode("utf-8")
    ).hexdigest()
    return {
        "schema_version": "nas-history-relink-verification-v1",
        "plan_hash": verification.plan_hash,
        "ok": verification.ok,
        "cleanup_stored_names": list(verification.cleanup_stored_names),
        "cleanup_count": len(cleanup_manifest),
        "cleanup_total_bytes": sum(int(item["size_bytes"]) for item in cleanup_manifest),
        "cleanup_manifest_sha256": cleanup_manifest_sha256,
        "issues": [asdict(item) for item in verification.issues],
    }
