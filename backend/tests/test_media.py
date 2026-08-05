from __future__ import annotations

import io

import pytest
from PIL import Image

from app.media import (
    MODEL_IMAGE_MAX_BYTES,
    prepare_model_image,
    prepare_pdf_model_input,
)


def _gif_bytes() -> bytes:
    frames = [
        Image.new("RGBA", (8, 6), (255, 0, 0, 255)),
        Image.new("RGBA", (8, 6), (0, 255, 0, 255)),
        Image.new("RGBA", (8, 6), (0, 0, 255, 255)),
    ]
    output = io.BytesIO()
    frames[0].save(
        output,
        format="GIF",
        save_all=True,
        append_images=frames[1:],
        duration=100,
        loop=0,
    )
    return output.getvalue()


def test_gif_model_preview_is_deterministic_and_keeps_source_animated(tmp_path) -> None:
    source = tmp_path / "animated.gif"
    source.write_bytes(_gif_bytes())
    cache_dir = tmp_path / "derived"

    preview, mime_type = prepare_model_image(
        source,
        mime_type="image/gif",
        content_sha256="a" * 64,
        cache_dir=cache_dir,
    )

    assert mime_type == "image/png"
    assert preview.exists()
    assert source.read_bytes().startswith(b"GIF")
    with Image.open(preview) as image:
        assert image.format == "PNG"
        assert image.size == (16, 12)

    cached_preview, cached_mime = prepare_model_image(
        source,
        mime_type="image/gif",
        content_sha256="a" * 64,
        cache_dir=cache_dir,
    )
    assert cached_preview == preview
    assert cached_mime == "image/png"


def test_oversized_static_image_uses_bounded_cached_preview(tmp_path) -> None:
    source = tmp_path / "oversized.png"
    Image.new("RGB", (4200, 32), (12, 34, 56)).save(source, format="PNG")
    original = source.read_bytes()
    cache_dir = tmp_path / "derived"

    preview, mime_type = prepare_model_image(
        source,
        mime_type="image/png",
        content_sha256="d" * 64,
        cache_dir=cache_dir,
    )

    assert preview != source
    assert mime_type == "image/jpeg"
    assert preview.stat().st_size <= MODEL_IMAGE_MAX_BYTES
    assert source.read_bytes() == original
    with Image.open(preview) as image:
        assert image.format == "JPEG"
        assert max(image.size) <= 4096

    cached, cached_mime = prepare_model_image(
        source,
        mime_type="image/png",
        content_sha256="d" * 64,
        cache_dir=cache_dir,
    )
    assert cached == preview
    assert cached_mime == "image/jpeg"


def test_small_static_image_keeps_original_payload(tmp_path) -> None:
    source = tmp_path / "small.png"
    Image.new("RGBA", (12, 10), (1, 2, 3, 4)).save(source, format="PNG")

    prepared, mime_type = prepare_model_image(
        source,
        mime_type="image/png",
        content_sha256="e" * 64,
        cache_dir=tmp_path / "derived",
    )

    assert prepared == source
    assert mime_type == "image/png"


def test_pdf_preprocess_extracts_text_renders_contact_sheet_and_caches(tmp_path) -> None:
    import fitz

    source = tmp_path / "方案.pdf"
    document = fitz.open()
    page = document.new_page(width=240, height=160)
    page.insert_text((24, 48), "Living room proposal", fontsize=16)
    page.insert_text((24, 82), "Material and lighting", fontsize=11)
    document.save(source)
    document.close()

    result = prepare_pdf_model_input(
        source,
        content_sha256="b" * 64,
        cache_dir=tmp_path / "derived",
        max_pages=4,
    )
    assert result.preview_mime_type == "image/png"
    assert result.preview_path.exists()
    assert result.context["schema_version"] == "pdf-preprocess-v2"
    assert result.context["page_count"] == 1
    assert "Living room proposal" in str(result.context["text"])
    assert result.context["multimodal_summary"]["status"] == "pending_model"

    cached = prepare_pdf_model_input(
        source,
        content_sha256="b" * 64,
        cache_dir=tmp_path / "derived",
    )
    assert cached.preview_path == result.preview_path
    assert cached.context == result.context

    different_contract = prepare_pdf_model_input(
        source,
        content_sha256="b" * 64,
        cache_dir=tmp_path / "derived",
        max_pages=1,
        max_text_chars=1_000,
    )
    assert different_contract.preview_path != result.preview_path
    assert different_contract.context["max_text_chars"] == 1_000


def test_pdf_preprocess_reports_invalid_input_without_partial_cache(tmp_path) -> None:
    source = tmp_path / "not-a-pdf.pdf"
    source.write_bytes(b"not a PDF")
    with pytest.raises(RuntimeError, match="PDF 前处理"):
        prepare_pdf_model_input(
            source,
            content_sha256="c" * 64,
            cache_dir=tmp_path / "derived",
        )


def test_pdf_preprocess_rejects_unbounded_contract_before_reading_file(
    tmp_path,
) -> None:
    with pytest.raises(RuntimeError, match="参数超出"):
        prepare_pdf_model_input(
            tmp_path / "missing.pdf",
            content_sha256="f" * 64,
            cache_dir=tmp_path / "derived",
            max_pages=21,
        )
