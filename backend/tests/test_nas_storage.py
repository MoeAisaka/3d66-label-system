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
