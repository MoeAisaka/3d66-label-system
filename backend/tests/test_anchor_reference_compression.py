"""锚图压缩缓存：参照图发送压缩版，审计链仍对着原图。

锚图是每次调用B重复发送的参照标尺，占单样本图片 token 约八成（五锚实测
8174 → 3064，省 63%）。压缩只改变发送载荷，不改变合同与 sha 审计。
"""
from __future__ import annotations

import hashlib
import io

import pytest
from PIL import Image

from app.inspiration_anchor_mechanism import (
    InspirationAnchorContractError,
    anchor_mechanism_request,
)
from app.media import prepare_anchor_reference_image


def _write_image(path, *, size, color=(120, 40, 40), fmt="PNG"):
    image = Image.new("RGB", size, color)
    image.save(path, format=fmt)
    return path


def _sha(path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_large_image_is_bounded_to_max_edge_jpeg(tmp_path) -> None:
    src = _write_image(tmp_path / "big.png", size=(1600, 1000))
    out, mime = prepare_anchor_reference_image(
        src, content_sha256=_sha(src), cache_dir=tmp_path / "cache"
    )
    assert mime == "image/jpeg"
    with Image.open(out) as image:
        assert image.format == "JPEG"
        assert max(image.size) == 800
        # 比例保持
        assert image.size == (800, 500)
    assert out.stat().st_size < src.stat().st_size


def test_small_image_is_not_upscaled(tmp_path) -> None:
    src = _write_image(tmp_path / "small.png", size=(400, 300))
    out, _ = prepare_anchor_reference_image(
        src, content_sha256=_sha(src), cache_dir=tmp_path / "cache"
    )
    with Image.open(out) as image:
        assert image.size == (400, 300)


def test_cache_is_reused_by_source_sha(tmp_path) -> None:
    src = _write_image(tmp_path / "a.png", size=(1200, 900))
    cache = tmp_path / "cache"
    first, _ = prepare_anchor_reference_image(src, content_sha256=_sha(src), cache_dir=cache)
    stamp = first.stat().st_mtime_ns
    second, _ = prepare_anchor_reference_image(src, content_sha256=_sha(src), cache_dir=cache)
    assert second == first
    assert second.stat().st_mtime_ns == stamp, "缓存命中时不得重新生成"


def _contract_for(asset_id: int, sha: str) -> dict:
    return {
        "anchor_mechanism": {
            "spec_version": "anchor-mechanism-v1",
            "enabled": True,
            "max_anchor_images": 5,
            "anchors": [
                {"level": "L3", "asset_id": asset_id, "mime_type": "image/png", "sha256": sha}
            ],
        }
    }


class _Asset:
    def __init__(self, path):
        self.path = path


def test_request_sends_compressed_rendition_but_audits_original(tmp_path) -> None:
    src = _write_image(tmp_path / "anchor.png", size=(1600, 1200))
    target = _write_image(tmp_path / "target.png", size=(1000, 800))
    cache = tmp_path / "cache"
    payload = anchor_mechanism_request(
        _contract_for(7, _sha(src)),
        target,
        "image/png",
        assets_by_id={7: _Asset(src)},
        asset_path_resolver=lambda a: a.path,
        reference_image_builder=lambda path, sha: prepare_anchor_reference_image(
            path, content_sha256=sha, cache_dir=cache
        ),
    )
    assert payload is not None
    samples, count = payload
    assert count == 2
    label, sent_path, sent_mime = samples[0]
    assert "L3" in label
    assert sent_path != src, "发送的应是压缩版"
    assert sent_mime == "image/jpeg"
    with Image.open(sent_path) as image:
        assert max(image.size) == 800
    # 待评图保持原样
    assert samples[1][1] == target


def test_tampered_original_is_rejected_even_with_cache_present(tmp_path) -> None:
    """sha 审计必须对着原图：缓存存在也拦截被替换的原件。"""
    src = _write_image(tmp_path / "anchor.png", size=(1600, 1200))
    original_sha = _sha(src)
    cache = tmp_path / "cache"
    prepare_anchor_reference_image(src, content_sha256=original_sha, cache_dir=cache)
    # 篡改原图
    _write_image(src, size=(1600, 1200), color=(0, 200, 0))
    with pytest.raises(InspirationAnchorContractError) as excinfo:
        anchor_mechanism_request(
            _contract_for(7, original_sha),
            _write_image(tmp_path / "t.png", size=(500, 400)),
            "image/png",
            assets_by_id={7: _Asset(src)},
            asset_path_resolver=lambda a: a.path,
            reference_image_builder=lambda path, sha: prepare_anchor_reference_image(
                path, content_sha256=sha, cache_dir=cache
            ),
        )
    assert excinfo.value.code == "anchor_hash_mismatch"


def test_builder_failure_falls_back_to_original(tmp_path) -> None:
    """压缩失败绝不能把整轮评测打挂：回退发送原图。"""
    src = _write_image(tmp_path / "anchor.png", size=(900, 700))
    def broken(path, sha):
        raise RuntimeError("boom")
    payload = anchor_mechanism_request(
        _contract_for(7, _sha(src)),
        _write_image(tmp_path / "t.png", size=(500, 400)),
        "image/png",
        assets_by_id={7: _Asset(src)},
        asset_path_resolver=lambda a: a.path,
        reference_image_builder=broken,
    )
    samples, _ = payload
    assert samples[0][1] == src
    assert samples[0][2] == "image/png"


def test_without_builder_behavior_is_unchanged(tmp_path) -> None:
    src = _write_image(tmp_path / "anchor.png", size=(1600, 1200))
    payload = anchor_mechanism_request(
        _contract_for(7, _sha(src)),
        _write_image(tmp_path / "t.png", size=(500, 400)),
        "image/png",
        assets_by_id={7: _Asset(src)},
        asset_path_resolver=lambda a: a.path,
    )
    samples, _ = payload
    assert samples[0][1] == src
