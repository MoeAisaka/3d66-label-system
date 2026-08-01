from __future__ import annotations

import os
import tempfile
from pathlib import Path

from PIL import Image, UnidentifiedImageError


def prepare_model_image(
    source_path: Path,
    *,
    mime_type: str,
    content_sha256: str,
    cache_dir: Path,
) -> tuple[Path, str]:
    """Return a provider-safe image payload, preserving animated source files.

    Most vision endpoints accept still image media types but do not consistently
    decode GIF. The original GIF remains the asset and is served to the UI; the
    worker sends a deterministic contact sheet of up to four timeline frames.
    """
    if mime_type != "image/gif":
        return source_path, mime_type
    cache_dir.mkdir(parents=True, exist_ok=True)
    preview_path = cache_dir / f"{content_sha256}.png"
    if preview_path.exists():
        return preview_path, "image/png"

    try:
        with Image.open(source_path) as animation:
            frame_count = max(1, int(getattr(animation, "n_frames", 1)))
            indices = sorted(
                {
                    0,
                    frame_count // 3,
                    (frame_count * 2) // 3,
                    frame_count - 1,
                }
            )
            frames: list[Image.Image] = []
            for index in indices:
                animation.seek(index)
                frame = animation.convert("RGBA")
                background = Image.new("RGBA", frame.size, (255, 255, 255, 255))
                background.alpha_composite(frame)
                frames.append(background.convert("RGB"))
    except (OSError, UnidentifiedImageError) as exc:
        raise RuntimeError("动图素材无法生成评测预览") from exc

    width = max(frame.width for frame in frames)
    height = max(frame.height for frame in frames)
    columns = 2 if len(frames) > 1 else 1
    rows = (len(frames) + columns - 1) // columns
    sheet = Image.new("RGB", (width * columns, height * rows), (255, 255, 255))
    for position, frame in enumerate(frames):
        left = (position % columns) * width
        top = (position // columns) * height
        sheet.paste(frame, (left, top))

    temporary_path: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=cache_dir,
            prefix=f".{content_sha256}.",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary_path = temporary.name
        sheet.save(temporary_path, format="PNG", optimize=True)
        os.replace(temporary_path, preview_path)
    except OSError as exc:
        if temporary_path:
            try:
                Path(temporary_path).unlink()
            except OSError:
                pass
        raise RuntimeError("动图评测预览写入失败") from exc
    return preview_path, "image/png"
