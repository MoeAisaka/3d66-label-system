"""P0-E offline XLSX preflight.

This module deliberately stops at a deterministic, reviewable import plan.  It
does not write Asset, SampleSet, truth, or any other business database record.
"""
from __future__ import annotations

import hashlib
import io
import json
import re
import stat
import unicodedata
import zipfile
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath
from typing import Any
from xml.etree import ElementTree


PREFLIGHT_SCHEMA_VERSION = "p0e-xlsx-preflight-v1"
_REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_XML_HAZARDS = (b"<!DOCTYPE", b"<!ENTITY")
_CELL_REF_RE = re.compile(r"^([A-Z]+)([1-9][0-9]*)$")


@dataclass(frozen=True)
class ImportIssue:
    code: str
    message: str
    location: str | None = None
    retryable: bool = False

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


class ImportPreflightError(ValueError):
    """A safe, machine-readable preflight rejection."""

    def __init__(self, issue: ImportIssue):
        super().__init__(f"{issue.code}: {issue.message}")
        self.issue = issue

    def as_dict(self) -> dict[str, Any]:
        return {"error": self.issue.as_dict()}


@dataclass(frozen=True)
class XlsxLimits:
    max_file_bytes: int = 25 * 1024 * 1024
    max_entries: int = 512
    max_entry_uncompressed_bytes: int = 16 * 1024 * 1024
    max_total_uncompressed_bytes: int = 64 * 1024 * 1024
    max_compression_ratio: float = 100.0
    max_rows: int = 100_000
    max_columns: int = 512
    preview_rows: int = 200


@dataclass(frozen=True)
class ImportTarget:
    domain: str = "3D"
    target_kind: str = "candidate"
    gold_lock_state: str | None = None


def _reject(
    code: str,
    message: str,
    *,
    location: str | None = None,
    retryable: bool = False,
) -> None:
    raise ImportPreflightError(
        ImportIssue(
            code=code,
            message=message,
            location=location,
            retryable=retryable,
        )
    )


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _assert_target_is_preflightable(target: ImportTarget) -> None:
    if target.domain.casefold() != "3d":
        _reject("DOMAIN_NOT_ALLOWED", "P0-E 仅允许 3D 域候选数据预检。")
    if target.target_kind not in {"candidate", "gold"}:
        _reject("TARGET_KIND_INVALID", "导入目标类型不受支持。")
    if target.target_kind == "gold":
        if target.gold_lock_state is None:
            _reject(
                "GOLD_LOCK_STATE_REQUIRED",
                "Gold 目标缺少锁状态，已按 fail-closed 拒绝。",
            )
        if target.gold_lock_state != "unlocked":
            _reject(
                "GOLD_TARGET_LOCKED",
                "Gold 目标已锁定；预检不得覆盖或绕过锁。",
            )


def _validate_archive_member(info: zipfile.ZipInfo) -> None:
    name = info.filename
    pure = PurePosixPath(name)
    if (
        not name
        or "\\" in name
        or name.startswith("/")
        or pure.is_absolute()
        or ".." in pure.parts
    ):
        _reject(
            "XLSX_UNSAFE_ZIP_PATH",
            "XLSX ZIP 包含不安全路径。",
            location=name[:160],
        )
    unix_mode = info.external_attr >> 16
    if unix_mode and stat.S_ISLNK(unix_mode):
        _reject(
            "XLSX_ZIP_SYMLINK",
            "XLSX ZIP 不允许符号链接。",
            location=name[:160],
        )


def _inspect_archive(
    archive: zipfile.ZipFile,
    *,
    limits: XlsxLimits,
) -> dict[str, zipfile.ZipInfo]:
    infos = archive.infolist()
    if len(infos) > limits.max_entries:
        _reject("XLSX_TOO_MANY_ENTRIES", "XLSX ZIP 条目数超过限制。")

    by_name: dict[str, zipfile.ZipInfo] = {}
    total_size = 0
    for info in infos:
        _validate_archive_member(info)
        if info.filename in by_name:
            _reject(
                "XLSX_DUPLICATE_ZIP_ENTRY",
                "XLSX ZIP 包含重复条目。",
                location=info.filename[:160],
            )
        by_name[info.filename] = info
        if info.is_dir():
            continue
        if info.flag_bits & 0x1:
            _reject(
                "XLSX_ENCRYPTED_ENTRY",
                "XLSX ZIP 不允许加密条目。",
                location=info.filename[:160],
            )
        if info.file_size > limits.max_entry_uncompressed_bytes:
            _reject(
                "XLSX_ENTRY_TOO_LARGE",
                "XLSX ZIP 单条目解压后超过限制。",
                location=info.filename[:160],
            )
        total_size += info.file_size
        if total_size > limits.max_total_uncompressed_bytes:
            _reject(
                "XLSX_UNCOMPRESSED_TOO_LARGE",
                "XLSX ZIP 解压后总大小超过限制。",
            )
        if info.file_size:
            if info.compress_size <= 0:
                _reject(
                    "XLSX_SUSPICIOUS_COMPRESSION",
                    "XLSX ZIP 压缩信息异常。",
                    location=info.filename[:160],
                )
            ratio = info.file_size / info.compress_size
            if ratio > limits.max_compression_ratio:
                _reject(
                    "XLSX_SUSPICIOUS_COMPRESSION",
                    "XLSX ZIP 压缩比超过限制。",
                    location=info.filename[:160],
                )

    required = {"[Content_Types].xml", "xl/workbook.xml"}
    if not required.issubset(by_name):
        _reject("XLSX_STRUCTURE_INVALID", "文件缺少 XLSX 必需结构。")

    lowered = {name.casefold() for name in by_name}
    if any(
        name.endswith("vbaproject.bin")
        or name.startswith("xl/activex/")
        or name.startswith("xl/embeddings/")
        for name in lowered
    ):
        _reject("XLSX_ACTIVE_CONTENT", "XLSX 不允许宏、ActiveX 或嵌入对象。")
    if any(name.startswith("xl/externallinks/") for name in lowered):
        _reject("XLSX_EXTERNAL_LINK", "XLSX 不允许外部工作簿链接。")
    return by_name


def _read_entry(
    archive: zipfile.ZipFile,
    members: dict[str, zipfile.ZipInfo],
    name: str,
) -> bytes:
    info = members.get(name)
    if info is None:
        _reject(
            "XLSX_STRUCTURE_INVALID",
            "XLSX 引用了不存在的内部文件。",
            location=name[:160],
        )
    try:
        payload = archive.read(info)
    except (OSError, RuntimeError, zipfile.BadZipFile) as exc:
        raise ImportPreflightError(
            ImportIssue(
                code="XLSX_ZIP_READ_FAILED",
                message="XLSX ZIP 条目读取失败。",
                location=name[:160],
            )
        ) from exc
    upper = payload.upper()
    if any(marker in upper for marker in _XML_HAZARDS):
        _reject(
            "XLSX_UNSAFE_XML",
            "XLSX XML 包含不允许的文档类型或实体声明。",
            location=name[:160],
        )
    return payload


def _has_referenced_macro_content_type(
    payload: bytes,
    members: dict[str, zipfile.ZipInfo],
) -> bool:
    """Reject active content types only when they reference an actual part.

    Some WPS/Excel exports retain an unused ``Default Extension="bin"``
    declaration even though the archive contains no BIN member and the
    workbook itself is ordinary XLSX. Treating that orphan declaration as a
    macro makes safe, macro-free workbooks impossible to preview.
    """

    root = _parse_xml(payload, location="[Content_Types].xml")
    member_names = {name.casefold() for name in members}
    for child in root:
        content_type = str(child.attrib.get("ContentType") or "").casefold()
        if "macroenabled" not in content_type:
            continue
        if _local_name(child.tag) == "Override":
            part_name = str(child.attrib.get("PartName") or "").lstrip("/").casefold()
            if part_name in member_names:
                return True
        elif _local_name(child.tag) == "Default":
            extension = str(child.attrib.get("Extension") or "").lstrip(".").casefold()
            if extension and any(name.endswith(f".{extension}") for name in member_names):
                return True
    return False


def _parse_xml(payload: bytes, *, location: str) -> ElementTree.Element:
    try:
        return ElementTree.fromstring(payload)
    except ElementTree.ParseError as exc:
        raise ImportPreflightError(
            ImportIssue(
                code="XLSX_XML_INVALID",
                message="XLSX XML 结构无效。",
                location=location[:160],
            )
        ) from exc


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _normalize_part(base: str, target: str) -> str:
    if target.startswith("/"):
        pure = PurePosixPath(target.lstrip("/"))
    else:
        pure = PurePosixPath(base).parent / PurePosixPath(target)
    parts: list[str] = []
    for part in pure.parts:
        if part in {"", "."}:
            continue
        if part == "..":
            if not parts:
                _reject(
                    "XLSX_UNSAFE_RELATIONSHIP",
                    "XLSX 内部关系越出包根目录。",
                )
            parts.pop()
        else:
            parts.append(part)
    return "/".join(parts)


def _worksheet_parts(
    archive: zipfile.ZipFile,
    members: dict[str, zipfile.ZipInfo],
) -> list[tuple[str, str]]:
    workbook_payload = _read_entry(archive, members, "xl/workbook.xml")
    workbook = _parse_xml(workbook_payload, location="xl/workbook.xml")
    rel_name = "xl/_rels/workbook.xml.rels"
    rel_payload = _read_entry(archive, members, rel_name)
    relationships = _parse_xml(rel_payload, location=rel_name)
    rel_targets: dict[str, str] = {}
    for rel in relationships:
        if _local_name(rel.tag) != "Relationship":
            continue
        rel_id = rel.attrib.get("Id")
        target = rel.attrib.get("Target")
        target_mode = rel.attrib.get("TargetMode", "Internal")
        if target_mode.casefold() == "external":
            _reject(
                "XLSX_EXTERNAL_RELATIONSHIP",
                "XLSX 不允许外部关系。",
                location=rel_id,
            )
        if rel_id and target:
            rel_targets[rel_id] = _normalize_part("xl/workbook.xml", target)

    sheets: list[tuple[str, str]] = []
    for element in workbook.iter():
        if _local_name(element.tag) != "sheet":
            continue
        name = str(element.attrib.get("name") or "").strip() or "Sheet"
        rel_id = element.attrib.get(f"{{{_REL_NS}}}id")
        if not rel_id or rel_id not in rel_targets:
            _reject(
                "XLSX_STRUCTURE_INVALID",
                "工作表关系缺失。",
                location=name[:160],
            )
        part = rel_targets[rel_id]
        if not part.startswith("xl/worksheets/") or part not in members:
            _reject(
                "XLSX_UNSAFE_RELATIONSHIP",
                "工作表关系指向不允许的位置。",
                location=name[:160],
            )
        sheets.append((name, part))
    if not sheets:
        _reject("XLSX_NO_WORKSHEET", "XLSX 中没有可读取的工作表。")
    return sheets


def _shared_strings(
    archive: zipfile.ZipFile,
    members: dict[str, zipfile.ZipInfo],
) -> list[str]:
    name = "xl/sharedStrings.xml"
    if name not in members:
        return []
    root = _parse_xml(_read_entry(archive, members, name), location=name)
    result: list[str] = []
    for item in root:
        if _local_name(item.tag) != "si":
            continue
        result.append(
            "".join(
                node.text or ""
                for node in item.iter()
                if _local_name(node.tag) == "t"
            )
        )
    return result


def _column_index(reference: str) -> int:
    match = _CELL_REF_RE.fullmatch(reference)
    if not match:
        _reject(
            "XLSX_CELL_REFERENCE_INVALID",
            "工作表单元格引用无效。",
            location=reference[:80],
        )
    value = 0
    for character in match.group(1):
        value = value * 26 + ord(character) - ord("A") + 1
    return value


def _cell_value(cell: ElementTree.Element, shared: list[str]) -> Any:
    cell_type = cell.attrib.get("t")
    inline_text = "".join(
        node.text or ""
        for node in cell.iter()
        if _local_name(node.tag) == "t"
    )
    value_node = next(
        (node for node in cell if _local_name(node.tag) == "v"),
        None,
    )
    raw_value = value_node.text if value_node is not None else None
    if cell_type == "inlineStr":
        return inline_text
    if cell_type == "s":
        try:
            index = int(raw_value or "")
            return shared[index]
        except (ValueError, IndexError) as exc:
            raise ImportPreflightError(
                ImportIssue(
                    code="XLSX_SHARED_STRING_INVALID",
                    message="共享字符串索引无效。",
                    location=cell.attrib.get("r"),
                )
            ) from exc
    if cell_type == "b":
        return raw_value == "1"
    if cell_type in {"str", "e"}:
        return raw_value or ""
    if raw_value is None:
        return ""
    try:
        if re.fullmatch(r"-?[0-9]+", raw_value):
            return int(raw_value)
        if re.fullmatch(
            r"-?(?:[0-9]+\.[0-9]*|[0-9]*\.[0-9]+)(?:[Ee][+-]?[0-9]+)?",
            raw_value,
        ):
            return float(raw_value)
    except ValueError:
        pass
    return raw_value


def _read_sheet_rows(
    payload: bytes,
    *,
    shared: list[str],
    limits: XlsxLimits,
    location: str,
) -> list[tuple[int, dict[int, Any]]]:
    root = _parse_xml(payload, location=location)
    rows: list[tuple[int, dict[int, Any]]] = []
    actual_count = 0
    for row in root.iter():
        if _local_name(row.tag) != "row":
            continue
        actual_count += 1
        if actual_count > limits.max_rows:
            _reject("XLSX_TOO_MANY_ROWS", "工作表行数超过限制。")
        try:
            row_number = int(row.attrib.get("r") or actual_count)
        except ValueError as exc:
            raise ImportPreflightError(
                ImportIssue(
                    code="XLSX_ROW_REFERENCE_INVALID",
                    message="工作表行号无效。",
                    location=location,
                )
            ) from exc
        if row_number > limits.max_rows:
            _reject(
                "XLSX_ROW_REFERENCE_INVALID",
                "工作表行号超过限制。",
                location=location,
            )
        values: dict[int, Any] = {}
        for cell in row:
            if _local_name(cell.tag) != "c":
                continue
            if any(_local_name(node.tag) == "f" for node in cell):
                _reject(
                    "XLSX_FORMULA_NOT_ALLOWED",
                    "XLSX 预检不接受公式单元格。",
                    location=cell.attrib.get("r"),
                )
            reference = cell.attrib.get("r")
            if not reference:
                _reject(
                    "XLSX_CELL_REFERENCE_INVALID",
                    "工作表单元格缺少引用。",
                    location=location,
                )
            column = _column_index(reference)
            if column > limits.max_columns:
                _reject("XLSX_TOO_MANY_COLUMNS", "工作表列数超过限制。")
            values[column] = _cell_value(cell, shared)
        rows.append((row_number, values))
    return rows


def _base_internal_name(raw_header: Any, column_number: int) -> str:
    normalized = unicodedata.normalize("NFKC", str(raw_header or "")).strip()
    normalized = normalized.casefold()
    normalized = re.sub(r"[^\w]+", "_", normalized, flags=re.UNICODE)
    normalized = normalized.strip("_")
    return normalized or f"unnamed_col_{column_number}"


def _column_plan(raw_headers: list[Any]) -> list[dict[str, Any]]:
    bases = [
        _base_internal_name(raw_header, index)
        for index, raw_header in enumerate(raw_headers, start=1)
    ]
    counts = {base: bases.count(base) for base in set(bases)}
    columns: list[dict[str, Any]] = []
    for index, (raw_header, base) in enumerate(
        zip(raw_headers, bases, strict=True),
        start=1,
    ):
        internal_name = (
            f"{base}__col_{index}" if counts[base] > 1 else base
        )
        columns.append(
            {
                "column_number": index,
                "raw_header": raw_header,
                "normalized_header": base,
                "internal_name": internal_name,
                "duplicate_header": counts[base] > 1,
            }
        )
    return columns


_TARGET_FIELDS = {
    "category": "category",
    "classification": "category",
    "format": "format",
    "grade": "grade",
    "historical_category": "historical_category",
    "historical_grade": "historical_grade",
    "risk": "risk",
    "source_business_id": "source_business_id",
    "source_url": "source_url",
    "status": "status",
}
_FIELD_ALIASES = {"farmat": "format"}


def _mapping_preview(
    columns: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    candidates: list[dict[str, Any]] = []
    by_target: dict[str, list[str]] = {}
    for column in columns:
        normalized = str(column["normalized_header"])
        target = _TARGET_FIELDS.get(normalized)
        reason = "exact_header"
        confidence = 1.0
        if target is None:
            target = _FIELD_ALIASES.get(normalized)
            reason = "known_typo_alias"
            confidence = 0.8
        if target is None:
            continue
        internal = str(column["internal_name"])
        candidates.append(
            {
                "source_internal_name": internal,
                "raw_header": column["raw_header"],
                "target_field": target,
                "reason": reason,
                "confidence": confidence,
                "requires_confirmation": True,
                "applied": False,
            }
        )
        by_target.setdefault(target, []).append(internal)
    conflicts = [
        {
            "target_field": target,
            "source_internal_names": sorted(source_names),
            "code": "MULTIPLE_COLUMNS_TARGET_SAME_FIELD",
        }
        for target, source_names in sorted(by_target.items())
        if len(source_names) > 1
    ]
    return candidates, conflicts


def preflight_xlsx(
    path: str | Path,
    *,
    target: ImportTarget | None = None,
    limits: XlsxLimits | None = None,
) -> dict[str, Any]:
    """Return a deterministic, non-persistent import plan for one XLSX."""

    source = Path(path)
    target = target or ImportTarget()
    limits = limits or XlsxLimits()
    _assert_target_is_preflightable(target)
    if source.suffix.casefold() != ".xlsx":
        _reject("XLSX_EXTENSION_REQUIRED", "仅允许 .xlsx 文件。")
    try:
        size = source.stat().st_size
    except OSError as exc:
        raise ImportPreflightError(
            ImportIssue(
                code="XLSX_FILE_UNREADABLE",
                message="XLSX 文件不可读取。",
            )
        ) from exc
    if size <= 0:
        _reject("XLSX_EMPTY_FILE", "XLSX 文件为空。")
    if size > limits.max_file_bytes:
        _reject("XLSX_FILE_TOO_LARGE", "XLSX 文件超过大小限制。")

    try:
        raw_file = source.read_bytes()
    except OSError as exc:
        raise ImportPreflightError(
            ImportIssue(
                code="XLSX_FILE_UNREADABLE",
                message="XLSX 文件不可读取。",
            )
        ) from exc
    if len(raw_file) > limits.max_file_bytes:
        _reject("XLSX_FILE_TOO_LARGE", "XLSX 文件超过大小限制。")
    content_sha256 = hashlib.sha256(raw_file).hexdigest()
    try:
        archive = zipfile.ZipFile(io.BytesIO(raw_file))
    except (OSError, zipfile.BadZipFile) as exc:
        raise ImportPreflightError(
            ImportIssue(
                code="XLSX_ZIP_INVALID",
                message="文件不是有效的 XLSX ZIP 包。",
            )
        ) from exc

    with archive:
        members = _inspect_archive(archive, limits=limits)
        content_types = _read_entry(
            archive,
            members,
            "[Content_Types].xml",
        )
        if _has_referenced_macro_content_type(content_types, members):
            _reject("XLSX_ACTIVE_CONTENT", "XLSX 不允许宏启用内容。")
        worksheets = _worksheet_parts(archive, members)
        shared = _shared_strings(archive, members)
        all_rows: list[tuple[int, dict[int, Any]]] | None = None
        selected_sheet: tuple[str, str] | None = None
        for sheet in worksheets:
            payload = _read_entry(archive, members, sheet[1])
            rows = _read_sheet_rows(
                payload,
                shared=shared,
                limits=limits,
                location=sheet[1],
            )
            if all_rows is None:
                all_rows = rows
                selected_sheet = sheet

        assert all_rows is not None and selected_sheet is not None
        if not all_rows:
            _reject("XLSX_HEADER_REQUIRED", "工作表没有表头。")
        header_row_number, header_values = all_rows[0]
        if not header_values:
            _reject("XLSX_HEADER_REQUIRED", "工作表首行没有表头。")
        last_column = max(
            (
                max(row_values, default=0)
                for _, row_values in all_rows
            ),
            default=0,
        )
        raw_headers = [
            header_values.get(column_number, "")
            for column_number in range(1, last_column + 1)
        ]
        columns = _column_plan(raw_headers)
        mappings, conflicts = _mapping_preview(columns)
        preview_rows: list[dict[str, Any]] = []
        for source_row, row_values in all_rows[1 : limits.preview_rows + 1]:
            raw_cells = [
                row_values.get(column_number, "")
                for column_number in range(1, last_column + 1)
            ]
            preview_rows.append(
                {
                    "source_row": source_row,
                    "raw_cells": raw_cells,
                    "values_by_internal_name": {
                        column["internal_name"]: raw_cells[index]
                        for index, column in enumerate(columns)
                    },
                }
            )

    idempotency_material = {
        "schema_version": PREFLIGHT_SCHEMA_VERSION,
        "content_sha256": content_sha256,
        "domain": target.domain.casefold(),
        "target_kind": target.target_kind,
        "sheet_name": selected_sheet[0],
        "columns": columns,
    }
    batch_key = "p0e:" + hashlib.sha256(
        _canonical_json(idempotency_material).encode("utf-8")
    ).hexdigest()
    return {
        "schema_version": PREFLIGHT_SCHEMA_VERSION,
        "mode": "preflight_only",
        "writes_business_database": False,
        "source_file": source.name,
        "content_sha256": content_sha256,
        "batch_key": batch_key,
        "domain": target.domain,
        "target_kind": target.target_kind,
        "sheet": {
            "name": selected_sheet[0],
            "header_row": header_row_number,
            "data_row_count": max(0, len(all_rows) - 1),
            "preview_row_count": len(preview_rows),
        },
        "columns": columns,
        "mapping_preview": mappings,
        "mapping_conflicts": conflicts,
        "applied_mapping": {},
        "raw_preview": preview_rows,
        "requires_human_confirmation": bool(mappings),
    }
