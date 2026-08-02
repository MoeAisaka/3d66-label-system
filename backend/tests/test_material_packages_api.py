from __future__ import annotations

import io
import json
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
from app.models import (
    Asset,
    AuditEvent,
    EvaluationJob,
    MaterialPackage,
    ModelConfig,
    PromptVersion,
    User,
)


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


def test_gif_upload_preserves_animated_asset_mime(tmp_path: Path) -> None:
    frames = [
        Image.new("RGBA", (8, 6), (255, 0, 0, 255)),
        Image.new("RGBA", (8, 6), (0, 255, 0, 255)),
    ]
    output = io.BytesIO()
    frames[0].save(
        output,
        format="GIF",
        save_all=True,
        append_images=[frames[1]],
        duration=100,
        loop=0,
    )
    with _api_context(tmp_path) as (client, _sessions):
        uploaded = client.post(
            "/api/assets/upload",
            files={"files": ("motion.gif", output.getvalue(), "image/gif")},
        )

    assert uploaded.status_code == 200, uploaded.text
    assert uploaded.json()["items"][0]["mime_type"] == "image/gif"


def test_category_contracts_keep_pdf_and_material_inputs_isolated(tmp_path: Path) -> None:
    with _api_context(tmp_path) as (client, _sessions):
        image_upload = client.post(
            "/api/assets/upload",
            data={"category_key": "material_image", "package_name": "材质图"},
            files={"files": ("fabric.png", _image_bytes((12, 34, 56), format_name="PNG"), "image/png")},
        )
        assert image_upload.status_code == 200, image_upload.text
        assert image_upload.json()["package"]["category_key"] == "material_image"

        wrong_pdf = client.post(
            "/api/assets/upload",
            data={"category_key": "pdf_text"},
            files={"files": ("looks-like.pdf", _image_bytes((1, 2, 3)), "image/jpeg")},
        )
        assert wrong_pdf.status_code == 400
        assert "PDF" in wrong_pdf.json()["detail"]

        categories = client.get("/api/evaluation-categories")
        assert categories.status_code == 200
        assert {item["category_key"] for item in categories.json()["items"]} == {
            "space_image", "pdf_text", "material_image"
        }

        pdf_profile = next(
            item for item in categories.json()["items"]
            if item["category_key"] == "pdf_text"
        )
        invalid_profile = {
            key: value
            for key, value in pdf_profile.items()
            if key not in {"id", "category_key", "created_by", "created_at", "updated_at"}
        }
        invalid_profile["allowed_mime_types"] = ["image/jpeg"]
        rejected = client.put(
            "/api/evaluation-categories/pdf_text",
            json=invalid_profile,
        )
        assert rejected.status_code == 422

        material_profile = next(
            item for item in categories.json()["items"]
            if item["category_key"] == "material_image"
        )
        retired_profile = {
            key: value
            for key, value in material_profile.items()
            if key not in {"id", "category_key", "created_by", "created_at", "updated_at"}
        }
        retired_profile["status"] = "retired"
        retired = client.put(
            "/api/evaluation-categories/material_image",
            json=retired_profile,
        )
        assert retired.status_code == 200
        blocked = client.post(
            "/api/assets/upload",
            data={"category_key": "material_image"},
            files={"files": ("blocked.png", _image_bytes((3, 2, 1), format_name="PNG"), "image/png")},
        )
        assert blocked.status_code == 409


def test_frontline_reviewer_cannot_select_category_baseline_bundle(tmp_path: Path) -> None:
    with _api_context(tmp_path) as (client, sessions):
        with sessions() as db:
            reviewer = User(
                username="frontline-reviewer",
                password_hash="unused",
                display_name="一线审核员",
                is_admin=False,
                role="reviewer",
            )
            db.add(reviewer)
            db.commit()
            db.refresh(reviewer)
        app.dependency_overrides[current_user] = lambda: reviewer
        profile = next(
            item
            for item in client.get("/api/evaluation-categories").json()["items"]
            if item["category_key"] == "space_image"
        )
        payload = {
            key: value
            for key, value in profile.items()
            if key
            not in {
                "id",
                "category_key",
                "pipeline_revision",
                "automation_revision",
                "created_by",
                "created_at",
                "updated_at",
            }
        }
        payload["automation_config"] = {
            **payload["automation_config"],
            "baseline_strategy_bundle_id": 1,
        }

        response = client.put(
            "/api/evaluation-categories/space_image", json=payload
        )

        assert response.status_code == 403
        assert "仅管理员" in response.json()["detail"]


def test_admin_can_create_modular_category_and_freeze_v2_job_contract(tmp_path: Path) -> None:
    with _api_context(tmp_path) as (client, sessions):
        catalog = client.get("/api/evaluation-categories/modules")
        assert catalog.status_code == 200
        assert {item["module"] for item in catalog.json()["processors"]} >= {
            "image.prepare", "document.pdf_extract", "context.material_focus"
        }
        pipeline = {
            "schema_version": "category-pipeline-v1",
            "input_kind": "image",
            "allowed_suffixes": [".jpg", ".png"],
            "processors": [
                {"module": "image.prepare", "enabled": True, "config": {}},
                {"module": "context.material_focus", "enabled": True, "config": {"enabled": True}},
            ],
            "prompt_mode": "single",
            "prompt_context": {"instruction": "重点检查室外植物与铺装关系。"},
            "dimensions": {"enabled": True, "mode": "selected", "enabled_keys": ["composition_viewpoint"]},
            "model_nodes": {"evaluation_main": True, "pdf_summary": False},
        }
        created = client.post(
            "/api/evaluation-categories",
            json={
                "category_key": "landscape_image",
                "display_name": "景观效果图",
                "description": "景观类图片队列",
                "status": "draft",
                "allowed_mime_types": ["image/jpeg", "image/png"],
                "preprocess_config": {},
                "pipeline_config": pipeline,
                "prompt_a_id": None,
                "prompt_b_id": None,
                "model_config_id": None,
                "rubric_version": "landscape-v1",
            },
        )
        assert created.status_code == 201, created.text
        assert created.json()["category_key"] == "landscape_image"
        assert created.json()["pipeline_revision"] == 1

        invalid = dict(pipeline)
        invalid["processors"] = [{"module": "python.user_plugin", "enabled": True, "config": {}}]
        rejected = client.put(
            "/api/evaluation-categories/landscape_image",
            json={**created.json(), "pipeline_config": invalid},
        )
        assert rejected.status_code == 422
        assert "未知处理模块" in rejected.json()["detail"]

        with sessions() as db:
            prompt = PromptVersion(
                stage="A", name="景观单提示词", version="landscape-a1",
                system_prompt="return a complete structured evaluation result",
                user_prompt="evaluate {{image_metadata}} with all required fields",
                rubric_version="landscape-v1", status="published",
            )
            inactive_model = ModelConfig(name="已停用模型", active=False)
            db.add_all([prompt, inactive_model])
            db.commit()
            prompt_id = prompt.id
            inactive_model_id = inactive_model.id
        inactive_model_rejected = client.put(
            "/api/evaluation-categories/landscape_image",
            json={**created.json(), "model_config_id": inactive_model_id},
        )
        assert inactive_model_rejected.status_code == 422
        assert "未启用" in inactive_model_rejected.json()["detail"]
        activated = client.put(
            "/api/evaluation-categories/landscape_image",
            json={
                **created.json(),
                "status": "active",
                "pipeline_config": pipeline,
                "prompt_a_id": prompt_id,
                "prompt_b_id": None,
            },
        )
        assert activated.status_code == 200, activated.text
        uploaded = client.post(
            "/api/assets/upload",
            data={"category_key": "landscape_image"},
            files={"files": ("garden.jpg", _image_bytes((20, 80, 30)), "image/jpeg")},
        )
        assert uploaded.status_code == 200, uploaded.text
        asset_id = uploaded.json()["items"][0]["id"]
        queued = client.post(
            "/api/jobs/enqueue",
            json={"asset_ids": [asset_id], "category_key": "landscape_image"},
        )
        assert queued.status_code == 200, queued.text
        with sessions() as db:
            job = db.get(EvaluationJob, queued.json()["job_ids"][0])
            frozen = json.loads(job.category_profile_snapshot_json)
            assert frozen["schema_version"] == "evaluation-category-profile-v2"
            assert frozen["category_key"] == "landscape_image"
            assert frozen["pipeline_config"]["prompt_context"]["instruction"].startswith("重点检查")


def test_material_category_requires_and_freezes_its_own_prompt_contract(
    tmp_path: Path,
) -> None:
    with _api_context(tmp_path) as (client, sessions):
        uploaded = client.post(
            "/api/assets/upload",
            data={"category_key": "material_image"},
            files={
                "files": (
                    "fabric.png",
                    _image_bytes((12, 34, 56), format_name="PNG"),
                    "image/png",
                )
            },
        )
        asset_id = uploaded.json()["items"][0]["id"]
        missing_prompt = client.post(
            "/api/jobs/enqueue",
            json={"asset_ids": [asset_id], "category_key": "material_image"},
        )
        assert missing_prompt.status_code == 409
        assert "专属提示词" in missing_prompt.json()["detail"]

        with sessions() as db:
            material_prompt = PromptVersion(
                stage="A",
                name="材质图单提示词",
                version="material-single-v1",
                system_prompt="return the complete material evaluation json",
                user_prompt="evaluate material {{image_metadata}}",
                rubric_version="material-rubric-v1",
                status="published",
            )
            wrong_prompt = PromptVersion(
                stage="A",
                name="其他规则",
                version="other-single-v1",
                system_prompt="return a different complete evaluation json",
                user_prompt="evaluate other {{image_metadata}}",
                rubric_version="other-rubric-v1",
                status="published",
            )
            db.add_all([material_prompt, wrong_prompt])
            db.commit()

        profile = next(
            item
            for item in client.get("/api/evaluation-categories").json()["items"]
            if item["category_key"] == "material_image"
        )
        update_payload = {
            key: value
            for key, value in profile.items()
            if key not in {"id", "category_key", "created_by", "created_at", "updated_at"}
        }
        update_payload.update(
            {
                "prompt_a_id": material_prompt.id,
                "prompt_b_id": None,
                "rubric_version": "material-rubric-v1",
            }
        )
        saved = client.put(
            "/api/evaluation-categories/material_image",
            json=update_payload,
        )
        assert saved.status_code == 200, saved.text

        mismatch = client.post(
            "/api/jobs/enqueue",
            json={
                "asset_ids": [asset_id],
                "category_key": "material_image",
                "prompt_id": wrong_prompt.id,
            },
        )
        assert mismatch.status_code == 409
        assert "rubric" in mismatch.json()["detail"]

        queued = client.post(
            "/api/jobs/enqueue",
            json={"asset_ids": [asset_id], "category_key": "material_image"},
        )
        assert queued.status_code == 200, queued.text
        with sessions() as db:
            job = db.get(EvaluationJob, queued.json()["job_ids"][0])
            frozen = json.loads(job.category_profile_snapshot_json)
            assert frozen["category_key"] == "material_image"
            assert frozen["prompt_a_id"] == material_prompt.id
            assert frozen["prompt_b_id"] is None
            assert frozen["rubric_version"] == "material-rubric-v1"


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


def test_asset_category_can_be_changed_and_bulk_or_package_deleted(tmp_path: Path) -> None:
    with _api_context(tmp_path) as (client, _sessions):
        uploaded = client.post(
            "/api/assets/upload",
            data={"category_key": "material_image", "package_name": "材质包"},
            files=[
                ("files", ("one.png", _image_bytes((1, 2, 3), format_name="PNG"), "image/png")),
                ("files", ("two.png", _image_bytes((3, 2, 1), format_name="PNG"), "image/png")),
            ],
        )
        assert uploaded.status_code == 200, uploaded.text
        body = uploaded.json()
        ids = [item["id"] for item in body["items"]]
        assert {item["category_key"] for item in body["items"]} == {"material_image"}
        changed = client.patch(
            f"/api/assets/{ids[0]}/category",
            json={"category_key": "space_image"},
        )
        assert changed.status_code == 200, changed.text
        assert changed.json()["category_key"] == "space_image"
        bulk = client.post("/api/assets/bulk-delete", json={"asset_ids": [ids[0]]})
        assert bulk.status_code == 200, bulk.text
        assert bulk.json()["deleted"] == 1
        package_delete = client.delete(f"/api/material-packages/{body['package']['id']}")
        assert package_delete.status_code == 200, package_delete.text
        assert package_delete.json()["deleted"] == 1
        assert client.get("/api/material-packages").json()["items"] == []
        assert client.get("/api/assets").json()["total"] == 0


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
