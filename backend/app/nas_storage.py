from __future__ import annotations

import hashlib
import mimetypes
import unicodedata
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import unquote, urlsplit

from PIL import Image, UnidentifiedImageError


NAS_SCHEME = "nas"
NAS_SHARE = "maps"
NAS_HOSTS = frozenset({"nas", "192.168.1.51"})
NAS_MAX_FILE_BYTES = 25 * 1024 * 1024
NAS_IMAGE_MIME_TYPES = frozenset(
    {"image/jpeg", "image/png", "image/webp", "image/gif"}
)


class NasStorageError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class NasFileInfo:
    uri: str
    path: Path
    original_name: str
    mime_type: str
    size_bytes: int
    width: int | None
    height: int | None
    sha256: str


def _safe_relative_parts(value: str) -> tuple[str, ...]:
    decoded = unicodedata.normalize("NFC", unquote(value)).replace("\\", "/")
    if "\x00" in decoded:
        raise NasStorageError("NAS_PATH_INVALID", "NAS 路径包含非法字符")
    path = PurePosixPath(decoded)
    if path.is_absolute():
        raise NasStorageError("NAS_PATH_INVALID", "NAS 共享内路径必须是相对路径")
    parts = tuple(part for part in path.parts if part not in {"", "."})
    if not parts or any(part == ".." for part in parts):
        raise NasStorageError("NAS_PATH_INVALID", "NAS 路径不能为空或跨越共享根目录")
    if any("/" in part or "\\" in part for part in parts):
        raise NasStorageError("NAS_PATH_INVALID", "NAS 路径包含非法分隔符")
    return parts


def normalize_nas_uri(value: str) -> str:
    raw = unicodedata.normalize("NFC", str(value or "").strip())
    if not raw:
        raise NasStorageError("NAS_PATH_INVALID", "NAS 路径不能为空")

    if raw.lower().startswith(f"{NAS_SCHEME}://"):
        parsed = urlsplit(raw)
        if parsed.scheme.lower() != NAS_SCHEME or parsed.query or parsed.fragment:
            raise NasStorageError("NAS_PATH_INVALID", "NAS URI 格式无效")
        if parsed.netloc.lower() != NAS_SHARE:
            raise NasStorageError("NAS_SHARE_NOT_ALLOWED", "只允许 maps 共享")
        relative = parsed.path.lstrip("/")
    else:
        slash_path = raw.replace("\\", "/")
        if not slash_path.startswith("//"):
            raise NasStorageError(
                "NAS_PATH_INVALID", "NAS 路径必须是受支持的 UNC 或 nas:// URI"
            )
        parts = [part for part in slash_path[2:].split("/") if part]
        if len(parts) < 3:
            raise NasStorageError("NAS_PATH_INVALID", "NAS UNC 路径缺少文件位置")
        host, share = parts[0].lower(), parts[1].lower()
        if host not in NAS_HOSTS:
            raise NasStorageError("NAS_HOST_NOT_ALLOWED", "NAS 主机不在允许列表")
        if share != NAS_SHARE:
            raise NasStorageError("NAS_SHARE_NOT_ALLOWED", "只允许 maps 共享")
        relative = "/".join(parts[2:])

    safe_parts = _safe_relative_parts(relative)
    return f"{NAS_SCHEME}://{NAS_SHARE}/" + "/".join(safe_parts)


def nas_relative_path(uri: str) -> PurePosixPath:
    normalized = normalize_nas_uri(uri)
    parsed = urlsplit(normalized)
    return PurePosixPath(*_safe_relative_parts(parsed.path.lstrip("/")))


def resolve_nas_uri(uri: str, root: Path) -> Path:
    root_path = Path(root).expanduser()
    if not root_path.is_dir():
        raise NasStorageError("NAS_MOUNT_UNAVAILABLE", "NAS 只读挂载不可用")
    if root_path.is_symlink():
        raise NasStorageError("NAS_MOUNT_INVALID", "NAS 挂载根目录不能是符号链接")
    resolved_root = root_path.resolve(strict=True)
    relative = nas_relative_path(uri)
    candidate = root_path.joinpath(*relative.parts)

    current = root_path
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise NasStorageError("NAS_SYMLINK_REJECTED", "NAS 路径不能经过符号链接")

    resolved_candidate = candidate.resolve(strict=False)
    try:
        resolved_candidate.relative_to(resolved_root)
    except ValueError as exc:
        raise NasStorageError(
            "NAS_PATH_ESCAPE", "NAS 路径超出只读共享根目录"
        ) from exc
    return resolved_candidate


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


# 同一个 NAS 文件在一次任务里会被反复校验：resolve_asset_path 每次调用都算一遍
# （锚图按锚点逐个调用），调用方拿到 path 后往往又自己算一遍。跨 SMB 全量重读
# 一张图的代价远高于 stat，因此用 (路径, mtime_ns, 大小) 做进程内缓存——
# 文件被替换时这三者必变，不会拿到过期摘要。
_DIGEST_CACHE_MAX_ENTRIES = 512
_digest_cache: dict[tuple[str, int, int], str] = {}


def sha256_file_cached(path: Path) -> str:
    try:
        stat_result = path.stat()
        key = (str(path), stat_result.st_mtime_ns, stat_result.st_size)
    except OSError:
        # stat 失败时不缓存，让下面的读取抛出真实错误。
        return _sha256_file(path)
    cached = _digest_cache.get(key)
    if cached is not None:
        return cached
    digest = _sha256_file(path)
    if len(_digest_cache) >= _DIGEST_CACHE_MAX_ENTRIES:
        # 简单的 FIFO 淘汰，避免长驻 worker 无界增长。
        for stale_key in list(_digest_cache)[: _DIGEST_CACHE_MAX_ENTRIES // 4]:
            _digest_cache.pop(stale_key, None)
    _digest_cache[key] = digest
    return digest


def inspect_nas_file(uri: str, root: Path) -> NasFileInfo:
    normalized = normalize_nas_uri(uri)
    path = resolve_nas_uri(normalized, root)
    if not path.is_file():
        raise NasStorageError("NAS_FILE_MISSING", "NAS 素材文件不存在")
    size_bytes = path.stat().st_size
    if size_bytes < 1 or size_bytes > NAS_MAX_FILE_BYTES:
        raise NasStorageError("NAS_FILE_SIZE_INVALID", "NAS 素材为空或超过 25MB")

    width: int | None = None
    height: int | None = None
    try:
        with Image.open(path) as image:
            image.verify()
        with Image.open(path) as image:
            width, height = image.size
            mime_type = Image.MIME.get(image.format or "", "")
    except (OSError, UnidentifiedImageError):
        if path.read_bytes()[:5] == b"%PDF-":
            mime_type = "application/pdf"
        else:
            raise NasStorageError(
                "NAS_FILE_TYPE_INVALID", "NAS 素材不是受支持的图片或 PDF"
            ) from None
    if mime_type not in NAS_IMAGE_MIME_TYPES | {"application/pdf"}:
        guessed = mimetypes.guess_type(path.name)[0] or mime_type
        raise NasStorageError(
            "NAS_FILE_TYPE_INVALID", f"NAS 素材类型不受支持：{guessed}"
        )
    return NasFileInfo(
        uri=normalized,
        path=path,
        original_name=path.name,
        mime_type=mime_type,
        size_bytes=size_bytes,
        width=width,
        height=height,
        sha256=_sha256_file(path),
    )


def resolve_asset_path(asset: Any, settings: Any) -> Path:
    backend = str(getattr(asset, "storage_backend", "local") or "local")
    if backend == "local":
        return Path(settings.upload_dir) / str(asset.stored_name)
    if backend != "nas_maps":
        raise NasStorageError("ASSET_STORAGE_UNSUPPORTED", "素材存储后端不受支持")
    source_uri = getattr(asset, "source_uri", None)
    if not isinstance(source_uri, str) or not source_uri:
        raise NasStorageError("NAS_SOURCE_URI_MISSING", "NAS 素材缺少来源 URI")
    root = getattr(settings, "nas_maps_root", None)
    if root is None:
        raise NasStorageError("NAS_MOUNT_UNAVAILABLE", "NAS 只读挂载未配置")
    path = resolve_nas_uri(source_uri, Path(root))
    if not path.is_file():
        raise NasStorageError("NAS_FILE_MISSING", "NAS 素材文件不存在")
    expected_sha256 = str(getattr(asset, "sha256", "") or "").lower()
    if expected_sha256 and sha256_file_cached(path) != expected_sha256:
        raise NasStorageError(
            "NAS_HASH_MISMATCH",
            "NAS 素材哈希与导入时记录不一致",
        )
    return path
