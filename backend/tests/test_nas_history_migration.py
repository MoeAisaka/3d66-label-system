from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.database import Base
from app.models import Asset, AssetVersion
from app.nas_history_migration import (
    NasHistoryMigrationError,
    apply_relink_plan,
    build_relink_plan,
    cleanup_verified_local_files,
    relink_plan_from_payload,
    relink_plan_payload,
    relink_verification_payload,
    verify_relink_plan,
)
import pytest


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _asset(local_dir: Path, *, name: str, content: bytes) -> Asset:
    stored_name = f"stored-{hashlib.sha256(name.encode()).hexdigest()[:12]}.jpg"
    path = local_dir / stored_name
    path.write_bytes(content)
    return Asset(
        original_name=name,
        stored_name=stored_name,
        storage_backend="local",
        source_uri=None,
        mime_type="image/jpeg",
        size_bytes=len(content),
        sha256=_sha256(path),
        category_key="inspiration_image",
    )


def test_build_relink_plan_matches_unique_hash_and_keeps_ambiguous_files(tmp_path: Path) -> None:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    local_dir = tmp_path / "local"
    nas_root = tmp_path / "maps"
    source_root = nas_root / "历史素材"
    local_dir.mkdir()
    source_root.mkdir(parents=True)

    unique = _asset(local_dir, name="unique.jpg", content=b"unique")
    ambiguous = _asset(local_dir, name="renamed.jpg", content=b"ambiguous")
    (source_root / "unique.jpg").write_bytes(b"unique")
    (source_root / "copy-a.jpg").write_bytes(b"ambiguous")
    (source_root / "copy-b.jpg").write_bytes(b"ambiguous")
    with Session(engine) as db:
        db.add_all([unique, ambiguous])
        db.commit()
        plan = build_relink_plan(
            db,
            upload_dir=local_dir,
            nas_root=nas_root,
            scan_roots=["历史素材"],
            generated_at=datetime(2026, 8, 19, tzinfo=timezone.utc),
        )

    assert len(plan.matches) == 1
    assert plan.matches[0].asset_id == unique.id
    assert plan.matches[0].source_uri == "nas://maps/历史素材/unique.jpg"
    assert plan.summary["nas_ambiguous"] == 1
    assert plan.plan_hash


def test_apply_and_verify_relink_plan_records_nas_source_version(tmp_path: Path) -> None:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    local_dir = tmp_path / "local"
    nas_root = tmp_path / "maps"
    source_root = nas_root / "历史素材"
    local_dir.mkdir()
    source_root.mkdir(parents=True)
    asset = _asset(local_dir, name="folder/item.jpg", content=b"same-image")
    (source_root / "folder").mkdir()
    (source_root / "folder" / "item.jpg").write_bytes(b"same-image")

    with Session(engine) as db:
        db.add(asset)
        db.commit()
        plan = build_relink_plan(
            db,
            upload_dir=local_dir,
            nas_root=nas_root,
            scan_roots=["历史素材"],
            generated_at=datetime(2026, 8, 19, tzinfo=timezone.utc),
        )
        result = apply_relink_plan(
            db,
            plan=plan,
            upload_dir=local_dir,
            nas_root=nas_root,
            actor="migration-test",
        )
        db.commit()

        db.refresh(asset)
        version = db.scalar(
            select(AssetVersion).where(AssetVersion.asset_id == asset.id)
        )
        verification = verify_relink_plan(
            db,
            plan=plan,
            upload_dir=local_dir,
            nas_root=nas_root,
        )

    assert result == {"relinked": 1, "already_relinked": 0}
    assert asset.storage_backend == "nas_maps"
    assert asset.source_uri == "nas://maps/历史素材/folder/item.jpg"
    assert version is not None
    assert version.storage_backend == "nas_maps"
    assert version.source_uri == asset.source_uri
    assert verification.ok is True
    assert verification.cleanup_stored_names == (asset.stored_name,)
    verification_payload = relink_verification_payload(plan, verification)
    assert verification_payload["cleanup_total_bytes"] == len(b"same-image")
    assert verification_payload["cleanup_manifest_sha256"]


def test_cleanup_deletes_only_files_that_still_pass_all_checks(tmp_path: Path) -> None:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    local_dir = tmp_path / "local"
    nas_root = tmp_path / "maps"
    source_root = nas_root / "历史素材"
    local_dir.mkdir()
    source_root.mkdir(parents=True)
    safe = _asset(local_dir, name="safe.jpg", content=b"safe")
    changed = _asset(local_dir, name="changed.jpg", content=b"changed")
    (source_root / "safe.jpg").write_bytes(b"safe")
    (source_root / "changed.jpg").write_bytes(b"changed")

    with Session(engine) as db:
        db.add_all([safe, changed])
        db.commit()
        plan = build_relink_plan(
            db,
            upload_dir=local_dir,
            nas_root=nas_root,
            scan_roots=["历史素材"],
            generated_at=datetime(2026, 8, 19, tzinfo=timezone.utc),
        )
        apply_relink_plan(
            db,
            plan=plan,
            upload_dir=local_dir,
            nas_root=nas_root,
            actor="migration-test",
        )
        db.commit()
        (source_root / "changed.jpg").write_bytes(b"nas-drift")

        verification = verify_relink_plan(
            db,
            plan=plan,
            upload_dir=local_dir,
            nas_root=nas_root,
        )
        result = cleanup_verified_local_files(
            db,
            plan=plan,
            verification=verification,
            upload_dir=local_dir,
            nas_root=nas_root,
            confirmed_plan_hash=plan.plan_hash,
        )

    assert verification.ok is False
    assert verification.cleanup_stored_names == (safe.stored_name,)
    assert result == {"deleted": 1, "preserved": 1}
    assert not (local_dir / safe.stored_name).exists()
    assert (local_dir / changed.stored_name).exists()


def test_relink_plan_payload_rejects_tampering(tmp_path: Path) -> None:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    local_dir = tmp_path / "local"
    nas_root = tmp_path / "maps"
    source_root = nas_root / "历史素材"
    local_dir.mkdir()
    source_root.mkdir(parents=True)
    asset = _asset(local_dir, name="safe.jpg", content=b"safe")
    (source_root / "safe.jpg").write_bytes(b"safe")
    with Session(engine) as db:
        db.add(asset)
        db.commit()
        plan = build_relink_plan(
            db,
            upload_dir=local_dir,
            nas_root=nas_root,
            scan_roots=["历史素材"],
        )

    payload = relink_plan_payload(plan)
    assert relink_plan_from_payload(payload) == plan
    payload["summary"]["matched"] = 999  # type: ignore[index]
    with pytest.raises(NasHistoryMigrationError, match="哈希"):
        relink_plan_from_payload(payload)
