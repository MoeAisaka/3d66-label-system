from __future__ import annotations

import csv
import io
import json
import re
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable, Literal
from xml.sax.saxutils import escape

from .models import PublishedLabel


ExportFormat = Literal["xlsx", "csv", "json"]
ExportScope = Literal["current", "history"]

EXPORT_COLUMNS = (
    "content_key",
    "category_key",
    "version",
    "status",
    "label_schema_version",
    "level",
    "score",
    "classification_json",
    "dimensions_json",
    "key_fields_json",
    "published_at",
    "superseded_at",
    "payload_hash",
    "label_json",
)

_CSV_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")
_INVALID_XML = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


@dataclass(frozen=True)
class ExportFile:
    content: bytes
    media_type: str
    extension: str


def _iso(value: datetime | None) -> str:
    if value is None:
        return ""
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _compact_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def export_row(label: PublishedLabel) -> dict[str, Any]:
    payload = json.loads(label.label_payload_json)
    return {
        "content_key": label.content_key,
        "category_key": label.category_key,
        "version": label.version,
        "status": label.status,
        "label_schema_version": label.label_schema_version,
        "level": payload.get("level", ""),
        "score": payload.get("score", ""),
        "classification_json": _compact_json(payload.get("classification", {})),
        "dimensions_json": _compact_json(payload.get("dimensions", {})),
        "key_fields_json": _compact_json(payload.get("key_fields", {})),
        "published_at": _iso(label.published_at),
        "superseded_at": _iso(label.superseded_at),
        "payload_hash": label.payload_hash,
        "label_json": _compact_json(payload),
    }


def spreadsheet_safe_text(value: Any) -> Any:
    if not isinstance(value, str) or not value.startswith(_CSV_FORMULA_PREFIXES):
        return value
    return "'" + value


def _build_csv(rows: list[dict[str, Any]]) -> bytes:
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=EXPORT_COLUMNS, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow(
            {key: spreadsheet_safe_text(row.get(key, "")) for key in EXPORT_COLUMNS}
        )
    return ("\ufeff" + stream.getvalue()).encode("utf-8")


def _build_json(rows: list[dict[str, Any]], *, scope: ExportScope) -> bytes:
    payload = {
        "schema_version": "published-label-export-v1",
        "scope": scope,
        "exported_at": _iso(datetime.now(timezone.utc)),
        "count": len(rows),
        "items": rows,
    }
    return json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")


def _xml_text(value: Any) -> str:
    cleaned = _INVALID_XML.sub("", str(value))
    return escape(cleaned)


def _column_name(index: int) -> str:
    result = ""
    while index:
        index, remainder = divmod(index - 1, 26)
        result = chr(65 + remainder) + result
    return result


def _cell_xml(reference: str, value: Any, *, header: bool = False) -> str:
    style = ' s="1"' if header else ""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return f'<c r="{reference}"{style}><v>{value}</v></c>'
    return (
        f'<c r="{reference}" t="inlineStr"{style}><is><t xml:space="preserve">'
        f"{_xml_text(value)}</t></is></c>"
    )


def _sheet_xml(headers: tuple[str, ...], rows: Iterable[Iterable[Any]]) -> str:
    rendered_rows = [headers, *rows]
    row_xml: list[str] = []
    for row_index, row in enumerate(rendered_rows, start=1):
        cells = "".join(
            _cell_xml(
                f"{_column_name(column_index)}{row_index}",
                value,
                header=row_index == 1,
            )
            for column_index, value in enumerate(row, start=1)
        )
        row_xml.append(f'<row r="{row_index}">{cells}</row>')
    last_column = _column_name(len(headers))
    last_row = max(1, len(rendered_rows))
    widths = [32, 18, 10, 14, 22, 12, 12, 42, 48, 42, 24, 24, 36, 64]
    cols = "".join(
        f'<col min="{index}" max="{index}" width="{width}" customWidth="1"/>'
        for index, width in enumerate(widths[: len(headers)], start=1)
    )
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        f'<dimension ref="A1:{last_column}{last_row}"/><sheetViews><sheetView workbookViewId="0">'
        '<pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/>'
        '</sheetView></sheetViews><sheetFormatPr defaultRowHeight="18"/>'
        f"<cols>{cols}</cols><sheetData>{''.join(row_xml)}</sheetData>"
        f'<autoFilter ref="A1:{last_column}{last_row}"/></worksheet>'
    )


def _build_xlsx(rows: list[dict[str, Any]], *, scope: ExportScope) -> bytes:
    data_rows = [[row.get(column, "") for column in EXPORT_COLUMNS] for row in rows]
    notes = [
        ("字段", "说明"),
        ("导出范围", "当前生效标签" if scope == "current" else "全部发布历史版本"),
        ("数据来源", "仅正式 PublishedLabel；不含候选标签、模型原始响应和密钥"),
        ("版本规则", "current 仅导出 status=published；history 同时包含 superseded"),
        ("JSON 字段", "classification、dimensions、key_fields 和完整 label 使用 JSON 文本保存"),
    ]
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as workbook:
        workbook.writestr(
            "[Content_Types].xml",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
            '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
            '<Override PartName="/xl/worksheets/sheet2.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
            '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
            '</Types>',
        )
        workbook.writestr(
            "_rels/.rels",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
            '</Relationships>',
        )
        workbook.writestr(
            "xl/workbook.xml",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
            '<sheets><sheet name="正式标签" sheetId="1" r:id="rId1"/>'
            '<sheet name="导出说明" sheetId="2" r:id="rId2"/></sheets></workbook>',
        )
        workbook.writestr(
            "xl/_rels/workbook.xml.rels",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>'
            '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet2.xml"/>'
            '<Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'
            '</Relationships>',
        )
        workbook.writestr(
            "xl/styles.xml",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            '<fonts count="2"><font><sz val="11"/><name val="Arial"/></font><font><b/><color rgb="FFFFFFFF"/><sz val="11"/><name val="Arial"/></font></fonts>'
            '<fills count="3"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill><fill><patternFill patternType="solid"><fgColor rgb="FF304238"/><bgColor indexed="64"/></patternFill></fill></fills>'
            '<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>'
            '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
            '<cellXfs count="2"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/><xf numFmtId="0" fontId="1" fillId="2" borderId="0" xfId="0" applyFont="1" applyFill="1"/></cellXfs>'
            '<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>'
            '</styleSheet>',
        )
        workbook.writestr("xl/worksheets/sheet1.xml", _sheet_xml(EXPORT_COLUMNS, data_rows))
        workbook.writestr("xl/worksheets/sheet2.xml", _sheet_xml(("字段", "说明"), notes[1:]))
    return output.getvalue()


def build_export(labels: Iterable[PublishedLabel], *, format: ExportFormat, scope: ExportScope) -> ExportFile:
    rows = [export_row(label) for label in labels]
    if format == "csv":
        return ExportFile(_build_csv(rows), "text/csv; charset=utf-8", "csv")
    if format == "json":
        return ExportFile(_build_json(rows, scope=scope), "application/json; charset=utf-8", "json")
    return ExportFile(
        _build_xlsx(rows, scope=scope),
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "xlsx",
    )
