from __future__ import annotations

import io
import json
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Mapping, Sequence

import pytest
from PIL import Image

from app.p0e_candidate_package import build_candidate_package_preview
from app.p0e_image_freeze import (
    ControlledImageFetcher,
    FetchPolicy,
    FreezeMetadata,
    FrozenAsset,
    ImageFreezer,
    ImageImportError,
    build_manifest,
    write_manifest_atomic,
)
from app.p0e_safe_import import (
    ImportIssue,
    ImportPreflightError,
    ImportTarget,
    XlsxLimits,
    preflight_xlsx,
)


def _xml_cell(reference: str, value: object, *, formula: bool = False) -> str:
    if formula:
        return f'<c r="{reference}"><f>1+1</f><v>{value}</v></c>'
    if isinstance(value, (int, float)):
        return f'<c r="{reference}"><v>{value}</v></c>'
    escaped = (
        str(value)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )
    return (
        f'<c r="{reference}" t="inlineStr"><is><t>{escaped}</t></is></c>'
    )


def _column_letters(number: int) -> str:
    result = ""
    while number:
        number, remainder = divmod(number - 1, 26)
        result = chr(ord("A") + remainder) + result
    return result


def _worksheet_xml(
    rows: Sequence[Sequence[object]],
    *,
    formula_cell: tuple[int, int] | None = None,
) -> str:
    row_xml: list[str] = []
    for row_number, values in enumerate(rows, start=1):
        cells = []
        for column_number, value in enumerate(values, start=1):
            reference = f"{_column_letters(column_number)}{row_number}"
            cells.append(
                _xml_cell(
                    reference,
                    value,
                    formula=formula_cell == (row_number, column_number),
                )
            )
        row_xml.append(f'<row r="{row_number}">{"".join(cells)}</row>')
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/'
        'spreadsheetml/2006/main"><sheetData>'
        + "".join(row_xml)
        + "</sheetData></worksheet>"
    )


def _write_xlsx(
    path: Path,
    rows: Sequence[Sequence[object]],
    *,
    formula_cell: tuple[int, int] | None = None,
    extra_entries: Mapping[str, bytes] | None = None,
    macro_enabled: bool = False,
) -> None:
    content_type = (
        "application/vnd.ms-excel.sheet.macroEnabled.main+xml"
        if macro_enabled
        else "application/vnd.openxmlformats-officedocument."
        "spreadsheetml.sheet.main+xml"
    )
    entries: dict[str, bytes] = {
        "[Content_Types].xml": (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/'
            'package/2006/content-types">'
            f'<Override PartName="/xl/workbook.xml" ContentType="{content_type}"/>'
            '<Override PartName="/xl/worksheets/sheet1.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.'
            'spreadsheetml.worksheet+xml"/></Types>'
        ).encode(),
        "xl/workbook.xml": (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<workbook xmlns="http://schemas.openxmlformats.org/'
            'spreadsheetml/2006/main" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/'
            '2006/relationships"><sheets>'
            '<sheet name="RAW" sheetId="1" r:id="rId1"/>'
            "</sheets></workbook>"
        ).encode(),
        "xl/_rels/workbook.xml.rels": (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/'
            'package/2006/relationships">'
            '<Relationship Id="rId1" '
            'Type="http://schemas.openxmlformats.org/officeDocument/'
            '2006/relationships/worksheet" '
            'Target="worksheets/sheet1.xml"/></Relationships>'
        ).encode(),
        "xl/worksheets/sheet1.xml": _worksheet_xml(
            rows,
            formula_cell=formula_cell,
        ).encode(),
    }
    entries.update(extra_entries or {})
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, payload in entries.items():
            archive.writestr(name, payload)


def _error_code(exc: pytest.ExceptionInfo[Exception]) -> str:
    issue = getattr(exc.value, "issue")
    return str(issue.code)


def test_xlsx_duplicate_headers_stable_internal_names_and_raw_retention(
    tmp_path: Path,
) -> None:
    headers = [f"field_{index}" for index in range(1, 20)]
    headers[2] = "status"
    headers[3] = "farmat"
    headers[4] = "format"
    headers[18] = "status"
    values = [f"value_{index}" for index in range(1, 20)]
    path = tmp_path / "source.xlsx"
    _write_xlsx(path, [headers, values])

    plan = preflight_xlsx(path)

    assert plan["columns"][2]["internal_name"] == "status__col_3"
    assert plan["columns"][18]["internal_name"] == "status__col_19"
    assert plan["raw_preview"][0]["raw_cells"] == values
    assert (
        plan["raw_preview"][0]["values_by_internal_name"]["status__col_19"]
        == "value_19"
    )
    assert plan["mode"] == "preflight_only"
    assert plan["writes_business_database"] is False


def test_xlsx_raw_data_beyond_last_named_header_is_not_dropped(
    tmp_path: Path,
) -> None:
    path = tmp_path / "sparse-header.xlsx"
    _write_xlsx(path, [["status"], ["ok", "RAW-extra"]])

    plan = preflight_xlsx(path)

    assert len(plan["columns"]) == 2
    assert plan["columns"][1]["internal_name"] == "unnamed_col_2"
    assert plan["raw_preview"][0]["raw_cells"] == ["ok", "RAW-extra"]


def test_farmat_to_format_is_preview_only_and_collision_is_explicit(
    tmp_path: Path,
) -> None:
    path = tmp_path / "mapping.xlsx"
    _write_xlsx(path, [["farmat", "format"], ["渲染图", "摄影图"]])

    plan = preflight_xlsx(path)

    candidates = {
        item["source_internal_name"]: item for item in plan["mapping_preview"]
    }
    assert candidates["farmat"]["target_field"] == "format"
    assert candidates["farmat"]["reason"] == "known_typo_alias"
    assert candidates["farmat"]["requires_confirmation"] is True
    assert candidates["farmat"]["applied"] is False
    assert plan["mapping_conflicts"] == [
        {
            "target_field": "format",
            "source_internal_names": ["farmat", "format"],
            "code": "MULTIPLE_COLUMNS_TARGET_SAME_FIELD",
        }
    ]
    assert plan["applied_mapping"] == {}


def test_xlsx_formula_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "formula.xlsx"
    _write_xlsx(path, [["status"], [2]], formula_cell=(2, 1))

    with pytest.raises(ImportPreflightError) as exc:
        preflight_xlsx(path)

    assert _error_code(exc) == "XLSX_FORMULA_NOT_ALLOWED"


@pytest.mark.parametrize(
    ("filename", "payload", "expected"),
    [
        ("source.xls", b"not-xlsx", "XLSX_EXTENSION_REQUIRED"),
        ("source.xlsx", b"not-a-zip", "XLSX_ZIP_INVALID"),
    ],
)
def test_xlsx_extension_and_invalid_zip_are_rejected(
    tmp_path: Path,
    filename: str,
    payload: bytes,
    expected: str,
) -> None:
    path = tmp_path / filename
    path.write_bytes(payload)
    with pytest.raises(ImportPreflightError) as exc:
        preflight_xlsx(path)
    assert _error_code(exc) == expected


def test_xlsx_unsafe_zip_member_and_active_content_are_rejected(
    tmp_path: Path,
) -> None:
    unsafe = tmp_path / "unsafe.xlsx"
    _write_xlsx(
        unsafe,
        [["status"], ["ok"]],
        extra_entries={"../escape.txt": b"bad"},
    )
    with pytest.raises(ImportPreflightError) as exc:
        preflight_xlsx(unsafe)
    assert _error_code(exc) == "XLSX_UNSAFE_ZIP_PATH"

    macro = tmp_path / "macro.xlsx"
    _write_xlsx(macro, [["status"], ["ok"]], macro_enabled=True)
    with pytest.raises(ImportPreflightError) as macro_exc:
        preflight_xlsx(macro)
    assert _error_code(macro_exc) == "XLSX_ACTIVE_CONTENT"


def test_xlsx_limits_and_idempotent_batch_key(tmp_path: Path) -> None:
    path = tmp_path / "idempotent.xlsx"
    _write_xlsx(path, [["status"], ["ok"]])

    first = preflight_xlsx(path)
    second = preflight_xlsx(path)

    assert first["batch_key"] == second["batch_key"]
    assert first["batch_key"].startswith("p0e:")
    with pytest.raises(ImportPreflightError) as exc:
        preflight_xlsx(path, limits=XlsxLimits(max_file_bytes=10))
    assert _error_code(exc) == "XLSX_FILE_TOO_LARGE"


def test_gold_lock_is_fail_closed(tmp_path: Path) -> None:
    path = tmp_path / "gold.xlsx"
    _write_xlsx(path, [["status"], ["ok"]])

    with pytest.raises(ImportPreflightError) as missing:
        preflight_xlsx(path, target=ImportTarget(target_kind="gold"))
    assert _error_code(missing) == "GOLD_LOCK_STATE_REQUIRED"

    with pytest.raises(ImportPreflightError) as locked:
        preflight_xlsx(
            path,
            target=ImportTarget(target_kind="gold", gold_lock_state="locked"),
        )
    assert _error_code(locked) == "GOLD_TARGET_LOCKED"


def _image_bytes(image_format: str = "PNG") -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (3, 2), color=(20, 40, 60)).save(
        output,
        format=image_format,
    )
    return output.getvalue()


class FakeResolver:
    def __init__(self, answers: Sequence[Sequence[str]]):
        self.answers = list(answers)
        self.calls: list[tuple[str, int]] = []

    def resolve_all(self, host: str, port: int) -> Sequence[str]:
        self.calls.append((host, port))
        index = min(len(self.calls) - 1, len(self.answers) - 1)
        return self.answers[index]


class FakeResponse:
    def __init__(
        self,
        *,
        status_code: int = 200,
        headers: Mapping[str, str] | None = None,
        body: bytes | None = None,
        chunks: Iterable[bytes] | None = None,
    ):
        self.status_code = status_code
        self.headers = dict(headers or {})
        self.body = body or b""
        self.chunks = chunks
        self.closed = False

    def iter_bytes(self) -> Iterable[bytes]:
        return self.chunks if self.chunks is not None else [self.body]

    def close(self) -> None:
        self.closed = True


class FakePinnedTransport:
    supports_ip_pinning = True

    def __init__(self, responses: Sequence[FakeResponse]):
        self.responses = list(responses)
        self.calls: list[dict[str, object]] = []

    def request(self, **kwargs: object) -> FakeResponse:
        self.calls.append(kwargs)
        return self.responses[len(self.calls) - 1]


def _metadata(url: str = "https://images.example.test/a.png?token=secret") -> FreezeMetadata:
    return FreezeMetadata(
        domain="3D",
        source_file="3Dreason.xlsx",
        source_row=7,
        source_business_id="biz-7",
        source_url=url,
        historical_grade="L3",
        historical_category="住宅",
        truth_status="historical_unverified",
        sample_role="candidate",
    )


def _fetcher(
    responses: Sequence[FakeResponse],
    *,
    addresses: Sequence[Sequence[str]] | None = None,
    policy: FetchPolicy | None = None,
) -> tuple[ControlledImageFetcher, FakePinnedTransport, FakeResolver]:
    transport = FakePinnedTransport(responses)
    resolver = FakeResolver(addresses or [["93.184.216.34"]])
    fetcher = ControlledImageFetcher(
        allowed_hosts={"images.example.test", "cdn.example.test"},
        resolver=resolver,
        transport=transport,
        policy=policy,
    )
    return fetcher, transport, resolver


def test_url_fetch_defaults_to_deny_and_requires_ip_pinning(tmp_path: Path) -> None:
    freezer = ImageFreezer(tmp_path)
    url = "https://images.example.test/a.png?token=secret"
    with pytest.raises(ImageImportError) as no_allowlist:
        ControlledImageFetcher().fetch_and_freeze(
            url,
            freezer=freezer,
            metadata=_metadata(url),
        )
    assert _error_code(no_allowlist) == "URL_ALLOWLIST_REQUIRED"
    assert "token=" not in str(no_allowlist.value.issue.location)

    with pytest.raises(ImageImportError) as no_pinning:
        ControlledImageFetcher(
            allowed_hosts={"images.example.test"},
        ).fetch_and_freeze(url, freezer=freezer, metadata=_metadata(url))
    assert _error_code(no_pinning) == "DNS_PINNING_UNAVAILABLE"


@pytest.mark.parametrize(
    "address",
    [
        "127.0.0.1",
        "10.0.0.1",
        "169.254.1.1",
        "224.0.0.1",
        "0.0.0.0",
        "240.0.0.1",
        "::1",
        "fc00::1",
        "fe80::1",
        "ff02::1",
        "::",
        "2001:db8::1",
    ],
)
def test_ssrf_rejects_non_public_ipv4_and_ipv6(
    tmp_path: Path,
    address: str,
) -> None:
    response = FakeResponse(
        headers={"Content-Type": "image/png"},
        body=_image_bytes(),
    )
    fetcher, transport, _ = _fetcher([response], addresses=[[address]])

    with pytest.raises(ImageImportError) as exc:
        fetcher.fetch_and_freeze(
            "https://images.example.test/a.png",
            freezer=ImageFreezer(tmp_path),
            metadata=_metadata(),
        )

    assert _error_code(exc) == "SSRF_NON_PUBLIC_ADDRESS"
    assert transport.calls == []


def test_ssrf_rejects_mixed_public_and_private_dns_answer(
    tmp_path: Path,
) -> None:
    response = FakeResponse(
        headers={"Content-Type": "image/png"},
        body=_image_bytes(),
    )
    fetcher, transport, _ = _fetcher(
        [response],
        addresses=[["93.184.216.34", "127.0.0.1"]],
    )

    with pytest.raises(ImageImportError) as exc:
        fetcher.fetch_and_freeze(
            "https://images.example.test/a.png",
            freezer=ImageFreezer(tmp_path),
            metadata=_metadata(),
        )

    assert _error_code(exc) == "SSRF_NON_PUBLIC_ADDRESS"
    assert transport.calls == []


def test_url_rejects_userinfo_and_non_https_before_dns(tmp_path: Path) -> None:
    fetcher, transport, resolver = _fetcher([])
    for url, expected in (
        ("http://images.example.test/a.png", "URL_HTTPS_REQUIRED"),
        (
            "https://user:pass@images.example.test/a.png",
            "URL_USERINFO_NOT_ALLOWED",
        ),
    ):
        with pytest.raises(ImageImportError) as exc:
            fetcher.fetch_and_freeze(
                url,
                freezer=ImageFreezer(tmp_path),
                metadata=_metadata(url),
            )
        assert _error_code(exc) == expected
    assert resolver.calls == []
    assert transport.calls == []


def test_redirect_is_revalidated_and_each_connection_uses_pinned_ip(
    tmp_path: Path,
) -> None:
    image = _image_bytes()
    redirect = FakeResponse(
        status_code=302,
        headers={"Location": "https://cdn.example.test/final.png?sig=hidden"},
    )
    final = FakeResponse(
        headers={
            "Content-Type": "image/png",
            "Content-Length": str(len(image)),
        },
        body=image,
    )
    fetcher, transport, resolver = _fetcher(
        [redirect, final],
        addresses=[["93.184.216.34"], ["1.1.1.1"]],
    )

    frozen = fetcher.fetch_and_freeze(
        "https://images.example.test/start.png?token=hidden",
        freezer=ImageFreezer(
            tmp_path,
            clock=lambda: datetime(2026, 7, 28, tzinfo=timezone.utc),
        ),
        metadata=_metadata(),
    )

    assert [call["pinned_ip"] for call in transport.calls] == [
        "93.184.216.34",
        "1.1.1.1",
    ]
    assert [call["server_hostname"] for call in transport.calls] == [
        "images.example.test",
        "cdn.example.test",
    ]
    assert resolver.calls == [
        ("images.example.test", 443),
        ("cdn.example.test", 443),
    ]
    assert redirect.closed and final.closed
    assert "?" not in frozen.source_url


def test_redirect_to_non_allowlisted_host_is_blocked(tmp_path: Path) -> None:
    redirect = FakeResponse(
        status_code=302,
        headers={"Location": "https://evil.example.test/a.png?token=hidden"},
    )
    fetcher, transport, _ = _fetcher([redirect])

    with pytest.raises(ImageImportError) as exc:
        fetcher.fetch_and_freeze(
            "https://images.example.test/a.png",
            freezer=ImageFreezer(tmp_path),
            metadata=_metadata(),
        )

    assert _error_code(exc) == "URL_HOST_NOT_ALLOWED"
    assert len(transport.calls) == 1
    assert redirect.closed
    assert "token=" not in str(exc.value.issue.location)


def test_dns_change_to_private_on_redirect_fails_closed(tmp_path: Path) -> None:
    redirect = FakeResponse(status_code=302, headers={"Location": "/next.png"})
    fetcher, transport, resolver = _fetcher(
        [redirect],
        addresses=[["93.184.216.34"], ["127.0.0.1"]],
    )

    with pytest.raises(ImageImportError) as exc:
        fetcher.fetch_and_freeze(
            "https://images.example.test/start.png",
            freezer=ImageFreezer(tmp_path),
            metadata=_metadata(),
        )

    assert _error_code(exc) == "SSRF_NON_PUBLIC_ADDRESS"
    assert len(resolver.calls) == 2
    assert len(transport.calls) == 1


def test_length_and_content_type_limits_are_enforced(tmp_path: Path) -> None:
    too_large = FakeResponse(
        headers={"Content-Type": "image/png", "Content-Length": "101"},
        body=b"",
    )
    fetcher, _, _ = _fetcher(
        [too_large],
        policy=FetchPolicy(max_bytes=100),
    )
    with pytest.raises(ImageImportError) as length_error:
        fetcher.fetch_and_freeze(
            "https://images.example.test/a.png",
            freezer=ImageFreezer(tmp_path),
            metadata=_metadata(),
        )
    assert _error_code(length_error) == "CONTENT_LENGTH_TOO_LARGE"

    wrong_type = FakeResponse(
        headers={"Content-Type": "text/html"},
        body=b"<html></html>",
    )
    fetcher, _, _ = _fetcher([wrong_type])
    with pytest.raises(ImageImportError) as type_error:
        fetcher.fetch_and_freeze(
            "https://images.example.test/a.png",
            freezer=ImageFreezer(tmp_path),
            metadata=_metadata(),
        )
    assert _error_code(type_error) == "CONTENT_TYPE_NOT_ALLOWED"


def test_transport_timeout_is_machine_readable_and_retryable(
    tmp_path: Path,
) -> None:
    class TimeoutTransport:
        supports_ip_pinning = True

        def request(self, **_: object) -> FakeResponse:
            raise TimeoutError("simulated timeout")

    fetcher = ControlledImageFetcher(
        allowed_hosts={"images.example.test"},
        resolver=FakeResolver([["93.184.216.34"]]),
        transport=TimeoutTransport(),
    )
    with pytest.raises(ImageImportError) as exc:
        fetcher.fetch_and_freeze(
            "https://images.example.test/a.png?token=hidden",
            freezer=ImageFreezer(tmp_path),
            metadata=_metadata(),
        )

    assert _error_code(exc) == "DOWNLOAD_TIMEOUT"
    assert exc.value.issue.retryable is True
    assert "token=" not in str(exc.value.issue.location)


def test_mime_spoof_and_broken_image_are_rejected(tmp_path: Path) -> None:
    png = _image_bytes("PNG")
    spoof = FakeResponse(
        headers={"Content-Type": "image/jpeg"},
        body=png,
    )
    fetcher, _, _ = _fetcher([spoof])
    with pytest.raises(ImageImportError) as mismatch:
        fetcher.fetch_and_freeze(
            "https://images.example.test/a.jpg",
            freezer=ImageFreezer(tmp_path),
            metadata=_metadata(),
        )
    assert _error_code(mismatch) == "IMAGE_MIME_MISMATCH"

    broken = FakeResponse(
        headers={"Content-Type": "image/png"},
        body=b"\x89PNG\r\n\x1a\nbroken",
    )
    fetcher, _, _ = _fetcher([broken])
    with pytest.raises(ImageImportError) as decode:
        fetcher.fetch_and_freeze(
            "https://images.example.test/a.png",
            freezer=ImageFreezer(tmp_path),
            metadata=_metadata(),
        )
    assert _error_code(decode) == "IMAGE_DECODE_FAILED"


def test_interrupted_download_removes_temporary_file(tmp_path: Path) -> None:
    def interrupted() -> Iterable[bytes]:
        yield _image_bytes()[:12]
        raise OSError("connection interrupted")

    response = FakeResponse(
        headers={"Content-Type": "image/png"},
        chunks=interrupted(),
    )
    fetcher, _, _ = _fetcher([response])
    freezer = ImageFreezer(tmp_path)

    with pytest.raises(ImageImportError) as exc:
        fetcher.fetch_and_freeze(
            "https://images.example.test/a.png",
            freezer=freezer,
            metadata=_metadata(),
        )

    assert _error_code(exc) == "IMAGE_FREEZE_IO_FAILED"
    assert list(freezer.staging_dir.glob("*")) == []
    assert response.closed


def test_stream_without_content_length_is_still_bounded(tmp_path: Path) -> None:
    response = FakeResponse(
        headers={"Content-Type": "image/png"},
        body=_image_bytes(),
    )
    fetcher, _, _ = _fetcher(
        [response],
        policy=FetchPolicy(max_bytes=10),
    )
    with pytest.raises(ImageImportError) as exc:
        fetcher.fetch_and_freeze(
            "https://images.example.test/a.png",
            freezer=ImageFreezer(tmp_path, max_bytes=1000),
            metadata=_metadata(),
        )
    assert _error_code(exc) == "IMAGE_TOO_LARGE"
    assert list((tmp_path / ".staging").glob("*")) == []


def test_sha256_deduplicates_bytes_and_asset_id_is_stable(tmp_path: Path) -> None:
    image = _image_bytes()
    clock = lambda: datetime(2026, 7, 28, tzinfo=timezone.utc)
    freezer = ImageFreezer(tmp_path, clock=clock)
    first = freezer.freeze_stream(
        [image],
        declared_content_type="image/png",
        metadata=_metadata("https://images.example.test/one.png"),
    )
    second_metadata = FreezeMetadata(
        **{
            **_metadata("https://images.example.test/two.png").__dict__,
            "source_row": 8,
        }
    )
    second = freezer.freeze_stream(
        [image],
        declared_content_type="image/png",
        metadata=second_metadata,
    )

    assert first.content_sha256 == second.content_sha256
    assert first.asset_id == second.asset_id
    assert first.stored_relative_path == second.stored_relative_path
    assert len(list((tmp_path / "assets").rglob("*.png"))) == 1


def _frozen(
    suffix: str,
    *,
    row: int,
    imported_at: str = "2026-07-28T00:00:00+00:00",
) -> FrozenAsset:
    digest = (suffix * 64)[:64]
    return FrozenAsset(
        domain="3D",
        source_file="source.xlsx",
        source_row=row,
        source_business_id=f"biz-{row}",
        source_url=f"https://images.example.test/{row}.png",
        content_sha256=digest,
        actual_mime="image/png",
        width=3,
        height=2,
        imported_at=imported_at,
        historical_grade="L3",
        historical_category="住宅",
        truth_status="historical_unverified",
        sample_role="candidate",
        asset_id=f"asset_sha256_{digest}",
        stored_relative_path=f"assets/{digest[:2]}/{digest}.png",
    )


def test_manifest_is_deterministic_atomic_and_never_fakes_completion(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = _frozen("a", row=2)
    second = _frozen("b", row=1)
    manifest_a = build_manifest(
        [first, second],
        expected_source_count=2,
    )
    manifest_b = build_manifest(
        [second, first],
        expected_source_count=2,
    )
    assert manifest_a == manifest_b
    assert manifest_a["complete"] is True
    assert [item["source_row"] for item in manifest_a["assets"]] == [1, 2]

    path = tmp_path / "manifest.json"
    write_manifest_atomic(path, manifest_a)
    original = path.read_bytes()
    write_manifest_atomic(path, manifest_b)
    assert path.read_bytes() == original

    incomplete = build_manifest([first], expected_source_count=2)
    assert incomplete["complete"] is False
    assert incomplete["status"] == "incomplete"
    assert incomplete["errors"][0]["code"] == "SOURCE_COUNT_INCOMPLETE"

    import app.p0e_image_freeze as module

    def failed_replace(_source: object, _destination: object) -> None:
        raise OSError("simulated")

    monkeypatch.setattr(module.os, "replace", failed_replace)
    with pytest.raises(ImageImportError) as exc:
        write_manifest_atomic(path, incomplete)
    assert _error_code(exc) == "MANIFEST_ATOMIC_WRITE_FAILED"
    assert path.read_bytes() == original
    assert list(tmp_path.glob(".manifest.json.*.tmp")) == []


def _candidate(
    index: int,
    *,
    source_file: str = "clean.xlsx",
    url: str | None = None,
    grade: str | None = None,
    category: str | None = None,
    business_id: str | None = None,
    conflict: bool = False,
) -> dict[str, object]:
    return {
        "domain": "3D",
        "source_file": source_file,
        "source_row": index + 2,
        "source_business_id": business_id or f"biz-{index}",
        "source_url": url or f"https://images.example.test/{index}.png?token=hidden",
        "historical_grade": grade if grade is not None else f"L{index % 5 + 1}",
        "historical_category": (
            category if category is not None else f"category-{index % 4}"
        ),
        "truth_status": "historical_unverified",
        "sample_role": "candidate",
        "risk": ("high", "medium", "low")[index % 3],
        "conflict": conflict,
    }


def test_candidate_preview_fixed_seed_is_reproducible_and_stratified() -> None:
    rows = [_candidate(index) for index in range(60)]

    first = build_candidate_package_preview(rows, target_size=40, seed="fixed")
    second = build_candidate_package_preview(
        reversed(rows),
        target_size=40,
        seed="fixed",
    )

    assert first == second
    assert first["selected_count"] == 40
    assert first["complete_for_requested_preview"] is True
    assert first["forms_gold"] is False
    assert first["downloads_performed"] is False
    assert first["model_runs_performed"] is False
    assert len(
        {
            (
                item["stratum"]["category"],
                item["stratum"]["grade"],
                item["stratum"]["risk"],
            )
            for item in first["selected"]
        }
    ) > 12
    assert all("?" not in str(item["source_url"]) for item in first["selected"])


def test_candidate_preview_excludes_697_3dreason_missing_truth() -> None:
    rows = [
        _candidate(
            index,
            source_file="3Dreason.xlsx",
            grade="",
            category="",
        )
        for index in range(697)
    ]
    rows.extend(_candidate(1000 + index) for index in range(40))

    preview = build_candidate_package_preview(rows, target_size=40, seed="fixed")

    assert preview["selected_count"] == 40
    assert (
        preview["exclusion_counts"][
            "3dreason_missing_human_grade_or_category"
        ]
        == 697
    )


def test_candidate_preview_excludes_duplicate_urls_and_conflicts() -> None:
    rows = [_candidate(index) for index in range(40)]
    rows.extend(
        [
            _candidate(
                100,
                url="https://images.example.test/duplicate.png?a=1",
                grade="L2",
                category="住宅",
            ),
            _candidate(
                101,
                url="https://images.example.test/duplicate.png?a=2",
                grade="L2",
                category="住宅",
            ),
            _candidate(102, business_id="same", grade="L1"),
            _candidate(103, business_id="same", grade="L5"),
            _candidate(104, conflict=True),
        ]
    )

    preview = build_candidate_package_preview(rows, target_size=40, seed="fixed")

    assert preview["exclusion_counts"]["duplicate_url"] == 2
    assert preview["exclusion_counts"]["conflicting_truth"] == 3
    reasons = {
        item["preview_id"]: set(item["reasons"]) for item in preview["excluded"]
    }
    assert reasons["biz-100"] == {"duplicate_url"}
    assert "conflicting_truth" in reasons["same"]
    assert preview["forms_gold"] is False


def test_candidate_preview_underfilled_is_explicitly_incomplete() -> None:
    preview = build_candidate_package_preview(
        [_candidate(index) for index in range(12)],
        target_size=30,
        seed="fixed",
    )

    assert preview["selected_count"] == 12
    assert preview["status"] == "preview_incomplete"
    assert preview["complete_for_requested_preview"] is False
    assert preview["forms_gold"] is False


def test_errors_are_machine_readable() -> None:
    error = ImageImportError(
        ImportIssue(
            code="SAFE_CODE",
            message="安全说明",
            location="https://images.example.test/a.png",
            retryable=False,
        )
    )
    assert json.loads(json.dumps(error.as_dict(), ensure_ascii=False)) == {
        "error": {
            "code": "SAFE_CODE",
            "message": "安全说明",
            "location": "https://images.example.test/a.png",
            "retryable": False,
        }
    }
