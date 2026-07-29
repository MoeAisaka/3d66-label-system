from __future__ import annotations

import io
import zipfile

from fastapi.testclient import TestClient

from app.historical_corrections import preview_historical_workbooks
from app.main import app, current_user
from app.models import User


def _xlsx(rows: list[list[object]]) -> bytes:
    def cell(ref: str, value: object) -> str:
        text = (
            str(value).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        )
        return f'<c r="{ref}" t="inlineStr"><is><t>{text}</t></is></c>'

    sheet_rows = []
    for row_number, values in enumerate(rows, start=1):
        cells = "".join(
            cell(f"{chr(64 + column)}{row_number}", value)
            for column, value in enumerate(values, start=1)
        )
        sheet_rows.append(f'<row r="{row_number}">{cells}</row>')
    entries = {
        "[Content_Types].xml": (
            '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/'
            'package/2006/content-types"><Override PartName="/xl/workbook.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.'
            'spreadsheetml.sheet.main+xml"/><Override '
            'PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.'
            'openxmlformats-officedocument.spreadsheetml.worksheet+xml"/></Types>'
        ),
        "xl/workbook.xml": (
            '<?xml version="1.0"?><workbook xmlns="http://schemas.openxmlformats.org/'
            'spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/'
            'officeDocument/2006/relationships"><sheets><sheet name="reviewed" '
            'sheetId="1" r:id="rId1"/></sheets></workbook>'
        ),
        "xl/_rels/workbook.xml.rels": (
            '<?xml version="1.0"?><Relationships xmlns="http://schemas.'
            'openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/'
            'relationships/worksheet" Target="worksheets/sheet1.xml"/></Relationships>'
        ),
        "xl/worksheets/sheet1.xml": (
            '<?xml version="1.0"?><worksheet xmlns="http://schemas.openxmlformats.org/'
            'spreadsheetml/2006/main"><sheetData>'
            + "".join(sheet_rows)
            + "</sheetData></worksheet>"
        ),
    }
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, value in entries.items():
            archive.writestr(name, value.encode())
    return buffer.getvalue()


def test_preview_maps_owner_confirmed_corrections_without_forming_gold() -> None:
    payload = _xlsx(
        [
            ["llid", "评测等级", "豆包等级", "hwreason"],
            ["a-1", "L2", "L4", "模型高估材质"],
            ["a-2", "L3", "L3", ""],
        ]
    )
    preview = preview_historical_workbooks(
        [("3D1.xlsx", payload)], blind_holdout_ratio=0
    )
    assert preview["writes_business_database"] is False
    assert preview["downloads_performed"] is False
    assert preview["model_runs_performed"] is False
    assert preview["forms_gold"] is False
    assert preview["summary"]["unique_item_count"] == 2
    by_key = {item["dedupe_key"]: item for item in preview["items"]}
    corrected = by_key["business:3d:a-1"]
    assert corrected["sample_role"] == "target_error"
    assert corrected["correction_candidate"]["human_level"] == "L2"
    assert corrected["correction_candidate"]["reason"] == "模型高估材质"
    assert corrected["provenance"]["owner_confirmed"] is True
    assert len(corrected["provenance"]["source_row_sha256"]) == 64
    assert by_key["business:3d:a-2"]["sample_role"] == "stable_control"


def test_3dreason_is_reason_only_and_never_invents_final_level() -> None:
    preview = preview_historical_workbooks(
        [
            (
                "3Dreason样本.xlsx",
                _xlsx(
                    [
                        ["llid", "豆包等级", "reason"],
                        ["reason-1", "L5", "光感判断错误"],
                    ]
                ),
            )
        ],
        blind_holdout_ratio=0,
    )
    item = preview["items"][0]
    assert item["sample_role"] == "reason_only"
    assert item["correction_candidate"]["human_level"] is None
    assert item["correction_candidate"]["model_level"] == "L5"
    assert item["forms_gold"] is False


def test_preview_deduplicates_stably_and_api_requires_authentication() -> None:
    payload = _xlsx(
        [["img_id", "评测等级"], ["same", "L3"], ["same", "L3"]]
    )
    first = preview_historical_workbooks(
        [("SU测评1.xlsx", payload)], blind_holdout_ratio=0.2
    )
    second = preview_historical_workbooks(
        [("SU测评1.xlsx", payload)], blind_holdout_ratio=0.2
    )
    assert first == second
    assert first["summary"]["unique_item_count"] == 1
    assert first["summary"]["duplicate_count"] == 1

    client = TestClient(app)
    unauthenticated = client.post(
        "/api/historical-corrections/preview",
        files={"files": ("SU测评1.xlsx", payload)},
    )
    assert unauthenticated.status_code == 401
    app.dependency_overrides[current_user] = lambda: User(
        id=1,
        username="owner",
        password_hash="unused",
        display_name="Owner",
    )
    try:
        authenticated = client.post(
            "/api/historical-corrections/preview",
            files={"files": ("SU测评1.xlsx", payload)},
        )
        assert authenticated.status_code == 200, authenticated.text
        assert authenticated.json()["summary"]["unique_item_count"] == 1
    finally:
        app.dependency_overrides.clear()
