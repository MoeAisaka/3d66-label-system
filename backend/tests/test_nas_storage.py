from __future__ import annotations

import hashlib
from types import SimpleNamespace

import pytest
from PIL import Image

from app.nas_storage import (
    NasStorageError,
    inspect_nas_file,
    normalize_nas_uri,
    resolve_asset_path,
    resolve_nas_uri,
)


def test_normalize_nas_uri_accepts_supported_unc_and_uri_forms() -> None:
    assert normalize_nas_uri(r"\\nas\maps\小聪\灵感图\a.jpg") == "nas://maps/小聪/灵感图/a.jpg"
    assert normalize_nas_uri("//192.168.1.51/maps/小聪/灵感图/a.jpg") == "nas://maps/小聪/灵感图/a.jpg"
    assert normalize_nas_uri("nas://maps/小聪/灵感图/a.jpg") == "nas://maps/小聪/灵感图/a.jpg"


@pytest.mark.parametrize(
    "value",
    [
        r"\\other-host\maps\a.jpg",
        r"\\nas\other-share\a.jpg",
        "nas://other-share/a.jpg",
        "nas://maps/../secret.jpg",
        "nas://maps/%2e%2e/secret.jpg",
        "/mnt/label-nas/maps/a.jpg",
    ],
)
def test_normalize_nas_uri_rejects_unsafe_locations(value: str) -> None:
    with pytest.raises(NasStorageError):
        normalize_nas_uri(value)


def test_resolve_nas_uri_stays_inside_root_and_rejects_symlink_escape(tmp_path) -> None:
    root = tmp_path / "maps"
    nested = root / "样本"
    nested.mkdir(parents=True)
    image = nested / "a.jpg"
    image.write_bytes(b"image")

    assert resolve_nas_uri("nas://maps/样本/a.jpg", root) == image.resolve()

    outside = tmp_path / "outside.jpg"
    outside.write_bytes(b"outside")
    link = nested / "escape.jpg"
    link.symlink_to(outside)
    with pytest.raises(NasStorageError, match="符号链接"):
        resolve_nas_uri("nas://maps/样本/escape.jpg", root)


def test_inspect_nas_file_returns_metadata_and_sha256(tmp_path) -> None:
    root = tmp_path / "maps"
    root.mkdir()
    image = root / "sample.png"
    Image.new("RGB", (12, 8), (1, 2, 3)).save(image, format="PNG")

    info = inspect_nas_file("nas://maps/sample.png", root)

    assert info.uri == "nas://maps/sample.png"
    assert info.mime_type == "image/png"
    assert (info.width, info.height) == (12, 8)
    assert info.sha256 == hashlib.sha256(image.read_bytes()).hexdigest()


def test_resolve_asset_path_uses_nas_source_when_present(tmp_path) -> None:
    root = tmp_path / "maps"
    root.mkdir()
    image = root / "sample.jpg"
    image.write_bytes(b"image")
    settings = SimpleNamespace(upload_dir=tmp_path / "local", nas_maps_root=root)
    asset = SimpleNamespace(
        storage_backend="nas_maps",
        source_uri="nas://maps/sample.jpg",
        stored_name="nas-placeholder.jpg",
    )

    assert resolve_asset_path(asset, settings) == image.resolve()


def test_sha256_file_cached_reads_once_and_reacts_to_content_change(
    tmp_path, monkeypatch
) -> None:
    """同一个 NAS 文件在一次任务里会被反复校验，摘要只应真正算一次。"""
    from app import nas_storage

    image = tmp_path / "cached.jpg"
    image.write_bytes(b"first")
    nas_storage._digest_cache.clear()

    calls: list[str] = []
    real = nas_storage._sha256_file

    def counting(path):
        calls.append(str(path))
        return real(path)

    monkeypatch.setattr(nas_storage, "_sha256_file", counting)

    first = nas_storage.sha256_file_cached(image)
    second = nas_storage.sha256_file_cached(image)
    assert first == second == hashlib.sha256(b"first").hexdigest()
    assert len(calls) == 1, "第二次调用应命中缓存，不再跨 SMB 全量重读"

    # 文件被替换后 mtime_ns/大小都会变，必须重算而不是返回过期摘要。
    image.write_bytes(b"second-and-longer")
    changed = nas_storage.sha256_file_cached(image)
    assert changed == hashlib.sha256(b"second-and-longer").hexdigest()
    assert len(calls) == 2


def test_resolve_asset_path_verifies_digest_without_rereading(tmp_path) -> None:
    """resolve_asset_path 反复调用（锚图按锚点逐个调）时不应重复算摘要。"""
    from app import nas_storage

    root = tmp_path / "maps"
    root.mkdir()
    image = root / "anchor.jpg"
    image.write_bytes(b"anchor-bytes")
    nas_storage._digest_cache.clear()

    calls: list[str] = []
    real = nas_storage._sha256_file

    def counting(path):
        calls.append(str(path))
        return real(path)

    original = nas_storage._sha256_file
    nas_storage._sha256_file = counting
    try:
        settings = SimpleNamespace(upload_dir=tmp_path / "local", nas_maps_root=root)
        asset = SimpleNamespace(
            storage_backend="nas_maps",
            source_uri="nas://maps/anchor.jpg",
            stored_name="nas-placeholder.jpg",
            sha256=hashlib.sha256(b"anchor-bytes").hexdigest(),
        )
        for _ in range(4):
            assert resolve_asset_path(asset, settings) == image.resolve()
    finally:
        nas_storage._sha256_file = original

    assert len(calls) == 1, f"4 次解析只应读一遍文件，实际读了 {len(calls)} 遍"
