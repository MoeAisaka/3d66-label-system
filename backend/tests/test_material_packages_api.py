from __future__ import annotations

import io
import zipfile
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from typing import Iterator

from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app import main
from app.database import Base, get_db
from app.main import app, current_user
from app.migrations import run_migrations
from app.models import Asset, AuditEvent, MaterialPackage, User


def _image_bytes(color: tuple[int, int, int], *, format_name: str = "JPEG") -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (8, 6), color=color).save(output, format=format_name)
    return output.getvalue()


@contextmanager
def _api_context(tmp_path: Path) -> Iterator[tuple[TestClient, sessionmaker[Session]]]:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    with engine.begin() as connection:
        run_migrations(connection)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    with sessions() as db:
        user = User(
            username="material-owner",
            password_hash="unused",
            display_name="素材管理员",
            is_admin=True,
        )
        db.add(user)
        db.commit()

    upload_dir = tmp_path / "images"
    upload_dir.mkdir(parents=True)
    original_settings = main.settings
    main.settings = replace(
        original_settings,
        data_dir=tmp_path,
        database_path=tmp_path / "database" / "app.db",
        upload_dir=upload_dir,
        log_dir=tmp_path / "logs",
    )

    def test_db() -> Iterator[Session]:
        with sessions() as db:
            yield db

    app.dependency_overrides[get_db] = test_db
    app.dependency_overrides[current_user] = lambda: user
    client = TestClient(app)
    try:
        yield client, sessions
    finally:
        app.dependency_overrides.clear()
        main.settings = original_settings
        engine.dispose()


def test_empty_upload_reports_required_files_contract(tmp_path: Path) -> None:
    with _api_context(tmp_path) as (client, _sessions):
        response = client.post("/api/assets/upload")

    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"] == ["body", "files"]


def test_batch_upload_creates_one_package_and_manual_selection_creates_another(
    tmp_path: Path,
) -> None:
    first = _image_bytes((255, 0, 0))
    second = _image_bytes((0, 255, 0))
    with _api_context(tmp_path) as (client, _sessions):
        uploaded = client.post(
            "/api/assets/upload",
            data={"package_name": "文件夹 A"},
            files=[
                ("files", ("a.jpg", first, "image/jpeg")),
                ("files", ("nested/b.jpg", second, "image/jpeg")),
            ],
        )
        assert uploaded.status_code == 200, uploaded.text
        body = uploaded.json()
        assert body["package"]["name"] == "文件夹 A"
        assert body["package"]["item_count"] == 2
        asset_ids = [item["id"] for item in body["items"]]

        selected = client.post(
            "/api/material-packages",
            json={"name": "人工整理包", "asset_ids": asset_ids},
        )
        assert selected.status_code == 200, selected.text
        assert selected.json()["package_key"].startswith("selection:")

        packages = client.get("/api/material-packages")
        assert packages.status_code == 200
        assert [item["name"] for item in packages.json()["items"]] == [
            "人工整理包",
            "文件夹 A",
        ]


def test_zip_upload_aggregates_nested_images_and_ignores_metadata(
    tmp_path: Path,
) -> None:
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        bundle.writestr("room/a.jpg", _image_bytes((0, 0, 255)))
        bundle.writestr("room/deeper/b.png", _image_bytes((255, 255, 0), format_name="PNG"))
        bundle.writestr("__MACOSX/._a.jpg", b"metadata")
        bundle.writestr("notes.txt", b"not an image")
    archive.seek(0)

    with _api_context(tmp_path) as (client, _sessions):
        response = client.post(
            "/api/material-packages/import-archive",
            data={"package_name": "ZIP 素材包"},
            files={"archive": ("素材.zip", archive.getvalue(), "application/zip")},
        )

    assert response.status_code == 200, response.text
    assert response.json()["package"]["item_count"] == 2
    assert response.json()["package"]["ignored_count"] == 2


def test_invalid_file_rolls_back_package_assets_and_written_files(
    tmp_path: Path,
) -> None:
    with _api_context(tmp_path) as (client, sessions):
        response = client.post(
            "/api/assets/upload",
            files=[
                ("files", ("valid.jpg", _image_bytes((1, 2, 3)), "image/jpeg")),
                ("files", ("invalid.jpg", b"not-an-image", "image/jpeg")),
            ],
        )
        assert response.status_code == 400
        with sessions() as db:
            assert db.scalars(select(Asset)).all() == []
            assert db.scalars(select(MaterialPackage)).all() == []
        assert list((tmp_path / "images").iterdir()) == []


def test_zip_path_traversal_is_rejected_without_persisting_package(
    tmp_path: Path,
) -> None:
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        bundle.writestr("../escape.jpg", _image_bytes((4, 5, 6)))
    archive.seek(0)

    with _api_context(tmp_path) as (client, sessions):
        response = client.post(
            "/api/material-packages/import-archive",
            files={"archive": ("unsafe.zip", archive.getvalue(), "application/zip")},
        )
        assert response.status_code == 400
        assert "不安全路径" in response.json()["detail"]
        with sessions() as db:
            assert db.scalars(select(MaterialPackage)).all() == []
        assert list((tmp_path / "images").iterdir()) == []


def test_soft_delete_hides_asset_but_retains_history_and_reupload_restores_it(
    tmp_path: Path,
) -> None:
    image = _image_bytes((64, 64, 64))
    with _api_context(tmp_path) as (client, sessions):
        uploaded = client.post(
            "/api/assets/upload",
            files={"files": ("delete-me.jpg", image, "image/jpeg")},
        ).json()
        asset_id = uploaded["items"][0]["id"]
        package_id = uploaded["package"]["id"]

        deleted = client.delete(f"/api/assets/{asset_id}")
        assert deleted.status_code == 200, deleted.text
        assert deleted.json()["history_retained"] is True
        assert client.get("/api/assets").json()["total"] == 0
        assert client.get(f"/api/assets/{asset_id}/file").status_code == 200

        package = next(
            item
            for item in client.get("/api/material-packages").json()["items"]
            if item["id"] == package_id
        )
        assert package["item_count"] == 1
        assert package["active_asset_count"] == 0
        assert package["removed_asset_count"] == 1

        restored = client.post(
            "/api/assets/upload",
            files={"files": ("restored.jpg", image, "image/jpeg")},
        )
        assert restored.status_code == 200, restored.text
        assert restored.json()["items"][0]["id"] == asset_id
        assert restored.json()["items"][0]["restored"] is True
        assert client.get("/api/assets").json()["total"] == 1

        with sessions() as db:
            actions = db.scalars(
                select(AuditEvent.action).where(
                    AuditEvent.subject_type == "asset",
                    AuditEvent.subject_id == str(asset_id),
                )
            ).all()
        assert actions == ["asset_deleted", "asset_restored_by_upload"]


def test_baseline_set_accepts_whole_package_provenance(tmp_path: Path) -> None:
    with _api_context(tmp_path) as (client, _sessions):
        uploaded = client.post(
            "/api/assets/upload",
            files=[
                ("files", ("one.jpg", _image_bytes((10, 20, 30)), "image/jpeg")),
                ("files", ("two.jpg", _image_bytes((30, 20, 10)), "image/jpeg")),
            ],
        ).json()
        package_id = uploaded["package"]["id"]
        response = client.post(
            "/api/baseline-sets",
            json={
                "name": "整包 L1 基准",
                "description": "",
                "default_expected_level": "L1",
                "source_package_id": package_id,
                "items": [],
            },
        )
        assert response.status_code == 200, response.text
        detail = client.get(f"/api/baseline-sets/{response.json()['id']}")
        assert detail.status_code == 200
        assert {
            item["source_package_id"] for item in detail.json()["items"]
        } == {package_id}
