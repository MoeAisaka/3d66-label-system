from __future__ import annotations

import io

from PIL import Image

from app.media import prepare_model_image


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
