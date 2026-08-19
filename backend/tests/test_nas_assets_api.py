from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Iterator

from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool
from sqlalchemy import create_engine

from app.database import Base, get_db
from app.main import app, current_user
from app.models import Asset, AssetVersion, User


@contextmanager
def _context(tmp_path: Path) -> Iterator[dict[str, object]]:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    root = tmp_path / "maps"
    source_dir = root / "采集任务交付文件" / "灵感图"
    source_dir.mkdir(parents=True)
    image_path = source_dir / "room.png"
    Image.new("RGB", (3, 2), (120, 80, 40)).save(image_path)
    local_dir = tmp_path / "local"
    local_dir.mkdir()
    with sessions() as db:
        user = User(
            username="nas-owner",
            password_hash="unused",
            display_name="NAS 管理员",
            is_admin=True,
            role="admin",
        )
        db.add(user)
        db.commit()

    def override_db() -> Iterator[Session]:
        with sessions() as db:
            yield db

    import app.main as main_module

    original_settings = main_module.settings
    main_module.settings = SimpleNamespace(
        upload_dir=local_dir,
        nas_maps_root=root,
    )
    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[current_user] = lambda: user
    try:
        yield {
            "client": TestClient(app),
            "sessions": sessions,
            "source_dir": source_dir,
            "image_path": image_path,
            "local_dir": local_dir,
            "main_module": main_module,
        }
    finally:
        main_module.settings = original_settings
        app.dependency_overrides.clear()
        engine.dispose()


def test_import_nas_creates_reference_without_copying_and_serves_file(tmp_path: Path) -> None:
    with _context(tmp_path) as fixture:
        client = fixture["client"]
        response = client.post(
            "/api/assets/import-nas",
            json={
                "source_uri": r"\\nas\maps\采集任务交付文件\灵感图",
                "package_name": "NAS 灵感图",
                "category_key": "inspiration_image",
            },
        )
        assert response.status_code == 200, response.text
        item = response.json()["items"][0]
        assert item["storage_backend"] == "nas_maps"
        assert item["source_uri"] == "nas://maps/采集任务交付文件/灵感图/room.png"
        assert not list(fixture["local_dir"].iterdir())

        with fixture["sessions"]() as db:
            asset = db.scalar(select(Asset).where(Asset.source_uri == item["source_uri"]))
            assert asset is not None
            version = db.scalar(select(AssetVersion).where(AssetVersion.asset_id == asset.id))
            assert version is not None
            assert version.storage_backend == "nas_maps"
            assert version.source_uri == asset.source_uri

        served = client.get(f"/api/assets/{item['id']}/file")
        assert served.status_code == 200
        assert served.headers["content-type"].startswith("image/png")
        assert served.content


def test_import_nas_is_idempotent_and_rejects_hash_drift(tmp_path: Path) -> None:
    with _context(tmp_path) as fixture:
        client = fixture["client"]
        payload = {
            "source_uri": "nas://maps/采集任务交付文件/灵感图/room.png",
            "category_key": "inspiration_image",
        }
        first = client.post("/api/assets/import-nas", json=payload)
        assert first.status_code == 200
        second = client.post("/api/assets/import-nas", json=payload)
        assert second.status_code == 200
        assert second.json()["items"][0]["duplicate"] is True

        fixture["image_path"].write_bytes(b"changed")
        drift = client.get(f"/api/assets/{first.json()['items'][0]['id']}/file")
        assert drift.status_code == 400
        assert "NAS_HASH_MISMATCH" in str(drift.json()["detail"])


def test_import_nas_fails_closed_when_mount_is_not_configured(tmp_path: Path) -> None:
    with _context(tmp_path) as fixture:
        fixture["main_module"].settings = SimpleNamespace(
            upload_dir=fixture["local_dir"],
            nas_maps_root=None,
        )
        response = fixture["client"].post(
            "/api/assets/import-nas",
            json={"source_uri": "nas://maps/room.png"},
        )
        assert response.status_code == 503
        assert response.json()["detail"]["code"] == "NAS_MOUNT_UNAVAILABLE"
