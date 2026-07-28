"""Fail-closed image acquisition and deterministic local freezing for P0-E."""
from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import socket
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Protocol, Sequence
from urllib.parse import urljoin, urlsplit, urlunsplit

from PIL import Image, UnidentifiedImageError

from .p0e_safe_import import ImportIssue


MANIFEST_VERSION = "p0e-frozen-manifest-v1"
_ALLOWED_MIME = {"image/jpeg", "image/png", "image/webp"}
_REDIRECT_STATUSES = {301, 302, 303, 307, 308}
_EXTENSIONS = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
}


class ImageImportError(ValueError):
    """A safe, machine-readable image import failure."""

    def __init__(self, issue: ImportIssue):
        super().__init__(f"{issue.code}: {issue.message}")
        self.issue = issue

    def as_dict(self) -> dict[str, Any]:
        return {"error": self.issue.as_dict()}


def _fail(
    code: str,
    message: str,
    *,
    location: str | None = None,
    retryable: bool = False,
) -> None:
    raise ImageImportError(
        ImportIssue(
            code=code,
            message=message,
            location=location,
            retryable=retryable,
        )
    )


def safe_source_url(value: str) -> str:
    """Remove userinfo, query and fragment from URL-shaped provenance."""

    try:
        parts = urlsplit(value)
    except ValueError:
        return "[invalid-url]"
    host = parts.hostname
    if not host:
        return "[invalid-url]"
    try:
        normalized_host = host.encode("idna").decode("ascii").casefold()
    except UnicodeError:
        return "[invalid-url]"
    if ":" in normalized_host:
        normalized_host = f"[{normalized_host}]"
    try:
        parsed_port = parts.port
    except ValueError:
        return "[invalid-url]"
    port = f":{parsed_port}" if parsed_port else ""
    return urlunsplit(
        (
            parts.scheme.casefold(),
            f"{normalized_host}{port}",
            parts.path or "/",
            "",
            "",
        )
    )


def _normalize_allowed_host(host: str) -> str:
    candidate = host.strip().rstrip(".")
    if not candidate:
        _fail("HOST_ALLOWLIST_INVALID", "域名白名单包含空值。")
    try:
        return candidate.encode("idna").decode("ascii").casefold()
    except UnicodeError as exc:
        raise ImageImportError(
            ImportIssue(
                code="HOST_ALLOWLIST_INVALID",
                message="域名白名单包含无效域名。",
            )
        ) from exc


@dataclass(frozen=True)
class ValidatedUrl:
    request_url: str = field(repr=False)
    safe_url: str
    host: str
    port: int


def validate_source_url(value: str, *, allowed_hosts: frozenset[str]) -> ValidatedUrl:
    safe = safe_source_url(value)
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        _fail(
            "URL_CONTROL_CHARACTER",
            "图片 URL 包含不允许的控制字符。",
            location=safe,
        )
    try:
        parts = urlsplit(value)
    except ValueError as exc:
        raise ImageImportError(
            ImportIssue(
                code="URL_INVALID",
                message="图片 URL 无效。",
                location=safe,
            )
        ) from exc
    if parts.scheme.casefold() != "https":
        _fail("URL_HTTPS_REQUIRED", "图片 URL 必须使用 HTTPS。", location=safe)
    if parts.username is not None or parts.password is not None:
        _fail("URL_USERINFO_NOT_ALLOWED", "图片 URL 不允许 userinfo。", location=safe)
    host = parts.hostname
    if not host:
        _fail("URL_HOST_REQUIRED", "图片 URL 缺少主机名。", location=safe)
    try:
        normalized_host = host.rstrip(".").encode("idna").decode("ascii").casefold()
    except UnicodeError as exc:
        raise ImageImportError(
            ImportIssue(
                code="URL_HOST_INVALID",
                message="图片 URL 主机名无效。",
                location=safe,
            )
        ) from exc
    if normalized_host not in allowed_hosts:
        _fail(
            "URL_HOST_NOT_ALLOWED",
            "图片 URL 主机名不在显式白名单。",
            location=safe,
        )
    try:
        port = parts.port or 443
    except ValueError as exc:
        raise ImageImportError(
            ImportIssue(
                code="URL_PORT_INVALID",
                message="图片 URL 端口无效。",
                location=safe,
            )
        ) from exc
    if port != 443:
        _fail("URL_PORT_NOT_ALLOWED", "图片 URL 仅允许 443 端口。", location=safe)
    request_url = urlunsplit(
        (
            "https",
            parts.netloc,
            parts.path or "/",
            parts.query,
            "",
        )
    )
    return ValidatedUrl(
        request_url=request_url,
        safe_url=safe,
        host=normalized_host,
        port=port,
    )


class AddressResolver(Protocol):
    def resolve_all(self, host: str, port: int) -> Sequence[str]: ...


class SystemAddressResolver:
    def resolve_all(self, host: str, port: int) -> Sequence[str]:
        try:
            results = socket.getaddrinfo(
                host,
                port,
                family=socket.AF_UNSPEC,
                type=socket.SOCK_STREAM,
            )
        except socket.gaierror as exc:
            raise ImageImportError(
                ImportIssue(
                    code="DNS_RESOLUTION_FAILED",
                    message="图片域名解析失败。",
                    location=host,
                    retryable=True,
                )
            ) from exc
        return sorted({str(result[4][0]) for result in results})


def validate_public_addresses(addresses: Sequence[str], *, safe_url: str) -> list[str]:
    if not addresses:
        _fail(
            "DNS_NO_ADDRESS",
            "图片域名没有可用 A/AAAA 地址。",
            location=safe_url,
            retryable=True,
        )
    parsed: list[ipaddress.IPv4Address | ipaddress.IPv6Address] = []
    for raw_address in addresses:
        try:
            address = ipaddress.ip_address(raw_address)
        except ValueError as exc:
            raise ImageImportError(
                ImportIssue(
                    code="DNS_ADDRESS_INVALID",
                    message="DNS 返回了无效地址。",
                    location=safe_url,
                )
            ) from exc
        if (
            not address.is_global
            or address.is_loopback
            or address.is_private
            or address.is_link_local
            or address.is_multicast
            or address.is_reserved
            or address.is_unspecified
        ):
            _fail(
                "SSRF_NON_PUBLIC_ADDRESS",
                "图片域名解析到非公网地址，已拒绝。",
                location=safe_url,
            )
        parsed.append(address)
    return [
        str(address)
        for address in sorted(parsed, key=lambda item: (item.version, item.packed))
    ]


class PinnedHTTPResponse(Protocol):
    status_code: int
    headers: Mapping[str, str]

    def iter_bytes(self) -> Iterable[bytes]: ...

    def close(self) -> None: ...


class PinnedHTTPTransport(Protocol):
    supports_ip_pinning: bool

    def request(
        self,
        *,
        url: str,
        pinned_ip: str,
        server_hostname: str,
        port: int,
        connect_timeout_seconds: float,
        read_timeout_seconds: float,
    ) -> PinnedHTTPResponse: ...


class FailClosedPinnedTransport:
    """Default transport: explicit refusal until real IP pinning is supplied."""

    supports_ip_pinning = False

    def request(self, **_: Any) -> PinnedHTTPResponse:
        _fail(
            "DNS_PINNING_UNAVAILABLE",
            "当前 HTTP 传输无法把连接固定到已验证 IP，已按 fail-closed 拒绝。",
        )


@dataclass(frozen=True)
class FetchPolicy:
    max_redirects: int = 3
    max_bytes: int = 25 * 1024 * 1024
    connect_timeout_seconds: float = 5.0
    read_timeout_seconds: float = 15.0


@dataclass(frozen=True)
class FreezeMetadata:
    domain: str
    source_file: str
    source_row: int
    source_business_id: str | None
    source_url: str
    historical_grade: str | None
    historical_category: str | None
    truth_status: str
    sample_role: str


@dataclass(frozen=True)
class FrozenAsset:
    domain: str
    source_file: str
    source_row: int
    source_business_id: str | None
    source_url: str
    content_sha256: str
    actual_mime: str
    width: int
    height: int
    imported_at: str
    historical_grade: str | None
    historical_category: str | None
    truth_status: str
    sample_role: str
    asset_id: str
    stored_relative_path: str

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _safe_source_file(value: str) -> str:
    return value.replace("\\", "/").rsplit("/", 1)[-1][:255] or "[unknown]"


def _magic_mime(header: bytes) -> str | None:
    if header.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if header.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if (
        len(header) >= 12
        and header.startswith(b"RIFF")
        and header[8:12] == b"WEBP"
    ):
        return "image/webp"
    return None


def _actual_image(path: Path, *, max_pixels: int) -> tuple[str, int, int]:
    try:
        with Image.open(path) as image:
            image.verify()
        with Image.open(path) as image:
            width, height = image.size
            if width <= 0 or height <= 0 or width * height > max_pixels:
                _fail("IMAGE_DIMENSIONS_INVALID", "图片尺寸无效或像素数超过限制。")
            image.load()
            image_format = str(image.format or "").upper()
    except ImageImportError:
        raise
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise ImageImportError(
            ImportIssue(
                code="IMAGE_DECODE_FAILED",
                message="文件头似乎是图片，但图片解码失败。",
            )
        ) from exc
    mime = {
        "JPEG": "image/jpeg",
        "PNG": "image/png",
        "WEBP": "image/webp",
    }.get(image_format)
    if mime is None:
        _fail("IMAGE_FORMAT_NOT_ALLOWED", "图片解码格式不受支持。")
    return mime, width, height


class ImageFreezer:
    def __init__(
        self,
        root: str | Path,
        *,
        max_bytes: int = 25 * 1024 * 1024,
        max_pixels: int = 80_000_000,
        clock: Callable[[], datetime] | None = None,
    ):
        self.root = Path(root)
        self.max_bytes = max_bytes
        self.max_pixels = max_pixels
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    @property
    def staging_dir(self) -> Path:
        return self.root / ".staging"

    @property
    def assets_dir(self) -> Path:
        return self.root / "assets"

    def freeze_stream(
        self,
        chunks: Iterable[bytes],
        *,
        declared_content_type: str,
        metadata: FreezeMetadata,
    ) -> FrozenAsset:
        if metadata.domain.casefold() != "3d":
            _fail("DOMAIN_NOT_ALLOWED", "P0-E 图片冻结仅允许 3D 域。")
        if metadata.source_row < 1:
            _fail("SOURCE_ROW_INVALID", "来源行号必须为正整数。")
        declared = declared_content_type.split(";", 1)[0].strip().casefold()
        if declared not in _ALLOWED_MIME:
            _fail("CONTENT_TYPE_NOT_ALLOWED", "响应 Content-Type 不是允许的图片类型。")

        self.staging_dir.mkdir(parents=True, exist_ok=True)
        temp_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="wb",
                prefix=".p0e-image-",
                suffix=".part",
                dir=self.staging_dir,
                delete=False,
            ) as temp:
                temp_path = Path(temp.name)
                digest = hashlib.sha256()
                total = 0
                header = bytearray()
                for chunk in chunks:
                    if not isinstance(chunk, (bytes, bytearray, memoryview)):
                        _fail("DOWNLOAD_CHUNK_INVALID", "下载流返回了非字节数据。")
                    payload = bytes(chunk)
                    if not payload:
                        continue
                    total += len(payload)
                    if total > self.max_bytes:
                        _fail("IMAGE_TOO_LARGE", "图片下载大小超过限制。")
                    if len(header) < 32:
                        header.extend(payload[: 32 - len(header)])
                    digest.update(payload)
                    temp.write(payload)
                temp.flush()
                os.fsync(temp.fileno())
            if total == 0:
                _fail("IMAGE_EMPTY", "下载得到空文件。")
            magic_mime = _magic_mime(bytes(header))
            if magic_mime is None:
                _fail("IMAGE_MAGIC_INVALID", "文件头不是允许的图片格式。")
            actual_mime, width, height = _actual_image(
                temp_path,
                max_pixels=self.max_pixels,
            )
            if actual_mime != magic_mime or actual_mime != declared:
                _fail(
                    "IMAGE_MIME_MISMATCH",
                    "Content-Type、文件头与图片解码格式不一致。",
                )

            content_sha256 = digest.hexdigest()
            asset_id = f"asset_sha256_{content_sha256}"
            relative = (
                Path("assets")
                / content_sha256[:2]
                / f"{content_sha256}{_EXTENSIONS[actual_mime]}"
            )
            destination = self.root / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            if destination.exists():
                existing_digest = hashlib.sha256(destination.read_bytes()).hexdigest()
                if existing_digest != content_sha256:
                    _fail(
                        "FROZEN_STORAGE_COLLISION",
                        "冻结目录存在哈希名不匹配的文件。",
                    )
                temp_path.unlink()
                temp_path = None
            else:
                os.replace(temp_path, destination)
                temp_path = None
            imported_at = self.clock().astimezone(timezone.utc).isoformat(
                timespec="seconds"
            )
            return FrozenAsset(
                domain="3D",
                source_file=_safe_source_file(metadata.source_file),
                source_row=metadata.source_row,
                source_business_id=metadata.source_business_id,
                source_url=safe_source_url(metadata.source_url),
                content_sha256=content_sha256,
                actual_mime=actual_mime,
                width=width,
                height=height,
                imported_at=imported_at,
                historical_grade=metadata.historical_grade,
                historical_category=metadata.historical_category,
                truth_status=metadata.truth_status,
                sample_role=metadata.sample_role,
                asset_id=asset_id,
                stored_relative_path=relative.as_posix(),
            )
        except ImageImportError:
            raise
        except OSError as exc:
            raise ImageImportError(
                ImportIssue(
                    code="IMAGE_FREEZE_IO_FAILED",
                    message="图片临时写入或原子落盘失败。",
                    retryable=True,
                )
            ) from exc
        finally:
            if temp_path is not None:
                try:
                    temp_path.unlink(missing_ok=True)
                except OSError:
                    pass


class ControlledImageFetcher:
    def __init__(
        self,
        *,
        allowed_hosts: Iterable[str] = (),
        resolver: AddressResolver | None = None,
        transport: PinnedHTTPTransport | None = None,
        policy: FetchPolicy | None = None,
    ):
        self.allowed_hosts = frozenset(
            _normalize_allowed_host(host) for host in allowed_hosts
        )
        self.resolver = resolver or SystemAddressResolver()
        self.transport = transport or FailClosedPinnedTransport()
        self.policy = policy or FetchPolicy()

    def fetch_and_freeze(
        self,
        url: str,
        *,
        freezer: ImageFreezer,
        metadata: FreezeMetadata,
    ) -> FrozenAsset:
        if not self.allowed_hosts:
            _fail(
                "URL_ALLOWLIST_REQUIRED",
                "未配置显式图片域名白名单，默认拒绝任意 URL。",
                location=safe_source_url(url),
            )
        if not getattr(self.transport, "supports_ip_pinning", False):
            _fail(
                "DNS_PINNING_UNAVAILABLE",
                "当前 HTTP 传输无法把连接固定到已验证 IP，已按 fail-closed 拒绝。",
                location=safe_source_url(url),
            )

        current_url = url
        seen: set[str] = set()
        for hop in range(self.policy.max_redirects + 1):
            validated = validate_source_url(
                current_url,
                allowed_hosts=self.allowed_hosts,
            )
            loop_key = hashlib.sha256(
                validated.request_url.encode("utf-8")
            ).hexdigest()
            if loop_key in seen:
                _fail(
                    "REDIRECT_LOOP",
                    "图片下载发生重定向循环。",
                    location=validated.safe_url,
                )
            seen.add(loop_key)
            addresses = validate_public_addresses(
                self.resolver.resolve_all(validated.host, validated.port),
                safe_url=validated.safe_url,
            )
            try:
                response = self.transport.request(
                    url=validated.request_url,
                    pinned_ip=addresses[0],
                    server_hostname=validated.host,
                    port=validated.port,
                    connect_timeout_seconds=self.policy.connect_timeout_seconds,
                    read_timeout_seconds=self.policy.read_timeout_seconds,
                )
            except ImageImportError:
                raise
            except TimeoutError as exc:
                raise ImageImportError(
                    ImportIssue(
                        code="DOWNLOAD_TIMEOUT",
                        message="图片下载超时。",
                        location=validated.safe_url,
                        retryable=True,
                    )
                ) from exc
            except Exception as exc:
                raise ImageImportError(
                    ImportIssue(
                        code="DOWNLOAD_FAILED",
                        message="图片下载失败。",
                        location=validated.safe_url,
                        retryable=True,
                    )
                ) from exc

            try:
                if response.status_code in _REDIRECT_STATUSES:
                    location = next(
                        (
                            value
                            for key, value in response.headers.items()
                            if key.casefold() == "location"
                        ),
                        None,
                    )
                    if not location:
                        _fail(
                            "REDIRECT_LOCATION_REQUIRED",
                            "重定向响应缺少 Location。",
                            location=validated.safe_url,
                        )
                    if hop >= self.policy.max_redirects:
                        _fail(
                            "REDIRECT_LIMIT_EXCEEDED",
                            "图片重定向次数超过限制。",
                            location=validated.safe_url,
                        )
                    current_url = urljoin(validated.request_url, location)
                    continue
                if response.status_code != 200:
                    _fail(
                        "DOWNLOAD_HTTP_STATUS",
                        "图片下载返回非 200 状态。",
                        location=validated.safe_url,
                        retryable=response.status_code >= 500,
                    )
                content_type = next(
                    (
                        value
                        for key, value in response.headers.items()
                        if key.casefold() == "content-type"
                    ),
                    "",
                )
                normalized_content_type = content_type.split(";", 1)[0].strip().casefold()
                if normalized_content_type not in _ALLOWED_MIME:
                    _fail(
                        "CONTENT_TYPE_NOT_ALLOWED",
                        "响应 Content-Type 不是允许的图片类型。",
                        location=validated.safe_url,
                    )
                content_length = next(
                    (
                        value
                        for key, value in response.headers.items()
                        if key.casefold() == "content-length"
                    ),
                    None,
                )
                if content_length is not None:
                    try:
                        declared_length = int(content_length)
                    except ValueError as exc:
                        raise ImageImportError(
                            ImportIssue(
                                code="CONTENT_LENGTH_INVALID",
                                message="响应 Content-Length 无效。",
                                location=validated.safe_url,
                            )
                        ) from exc
                    if declared_length < 0:
                        _fail(
                            "CONTENT_LENGTH_INVALID",
                            "响应 Content-Length 无效。",
                            location=validated.safe_url,
                        )
                    if declared_length > self.policy.max_bytes:
                        _fail(
                            "CONTENT_LENGTH_TOO_LARGE",
                            "响应 Content-Length 超过限制。",
                            location=validated.safe_url,
                        )

                def bounded_chunks() -> Iterable[bytes]:
                    total = 0
                    for chunk in response.iter_bytes():
                        total += len(chunk)
                        if total > self.policy.max_bytes:
                            _fail(
                                "IMAGE_TOO_LARGE",
                                "图片下载大小超过限制。",
                                location=validated.safe_url,
                            )
                        yield chunk

                return freezer.freeze_stream(
                    bounded_chunks(),
                    declared_content_type=normalized_content_type,
                    metadata=metadata,
                )
            finally:
                response.close()
        raise AssertionError("unreachable")


def _record_sort_key(record: FrozenAsset) -> tuple[Any, ...]:
    return (
        record.domain.casefold(),
        record.source_file.casefold(),
        record.source_row,
        record.source_business_id or "",
        record.source_url,
        record.asset_id,
    )


def _deduplicate_records(records: Iterable[FrozenAsset]) -> list[dict[str, Any]]:
    grouped: dict[str, list[FrozenAsset]] = {}
    for record in records:
        grouped.setdefault(record.asset_id, []).append(record)
    result: list[dict[str, Any]] = []
    for asset_id, group in sorted(grouped.items()):
        ordered = sorted(group, key=_record_sort_key)
        primary = ordered[0].as_dict()
        primary["duplicate_sources"] = [
            {
                "source_file": item.source_file,
                "source_row": item.source_row,
                "source_business_id": item.source_business_id,
                "source_url": item.source_url,
            }
            for item in ordered[1:]
        ]
        result.append(primary)
    return sorted(
        result,
        key=lambda item: (
            str(item["domain"]).casefold(),
            str(item["source_file"]).casefold(),
            int(item["source_row"]),
            str(item["asset_id"]),
        ),
    )


def build_manifest(
    records: Iterable[FrozenAsset],
    *,
    expected_source_count: int,
    failures: Iterable[ImportIssue] = (),
) -> dict[str, Any]:
    record_list = list(records)
    failure_list = sorted(
        (failure.as_dict() for failure in failures),
        key=lambda item: (
            str(item["code"]),
            str(item.get("location") or ""),
            str(item["message"]),
        ),
    )
    missing_count = max(0, expected_source_count - len(record_list))
    if missing_count and not failure_list:
        failure_list.append(
            ImportIssue(
                code="SOURCE_COUNT_INCOMPLETE",
                message="成功冻结的来源数少于预期。",
                retryable=True,
            ).as_dict()
        )
    complete = not failure_list and len(record_list) == expected_source_count
    return {
        "manifest_version": MANIFEST_VERSION,
        "status": "complete" if complete else "incomplete",
        "complete": complete,
        "expected_source_count": expected_source_count,
        "frozen_source_count": len(record_list),
        "unique_asset_count": len({record.asset_id for record in record_list}),
        "assets": _deduplicate_records(record_list),
        "errors": failure_list,
    }


def write_manifest_atomic(path: str | Path, manifest: Mapping[str, Any]) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    payload = (
        json.dumps(
            manifest,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")
    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            prefix=f".{destination.name}.",
            suffix=".tmp",
            dir=destination.parent,
            delete=False,
        ) as temp:
            temp_path = Path(temp.name)
            temp.write(payload)
            temp.flush()
            os.fsync(temp.fileno())
        os.replace(temp_path, destination)
        temp_path = None
        try:
            directory_fd = os.open(destination.parent, os.O_RDONLY)
        except OSError:
            return
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except OSError as exc:
        raise ImageImportError(
            ImportIssue(
                code="MANIFEST_ATOMIC_WRITE_FAILED",
                message="manifest 原子写入失败；旧 manifest 未被标记为新完成状态。",
                retryable=True,
            )
        ) from exc
    finally:
        if temp_path is not None:
            try:
                temp_path.unlink(missing_ok=True)
            except OSError:
                pass
