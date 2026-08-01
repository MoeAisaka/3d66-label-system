from __future__ import annotations

import os
import io
import json
import hashlib
import tempfile
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, UnidentifiedImageError


@dataclass(frozen=True)
class PdfPreprocessResult:
    preview_path: Path
    preview_mime_type: str
    context: dict[str, object]


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


def prepare_pdf_model_input(
    source_path: Path,
    *,
    content_sha256: str,
    cache_dir: Path,
    max_pages: int = 4,
    max_text_chars: int = 24_000,
) -> PdfPreprocessResult:
    """Extract PDF text/OCR and render a bounded page contact sheet.

    The original PDF remains immutable. Derived files are content-addressed so
    retries and repeated evaluations reuse exactly the same preprocessing.
    OCR is opportunistic: an unavailable local tesseract binary is recorded in
    the context instead of silently pretending OCR happened.
    """
    if not 1 <= max_pages <= 20 or not 1_000 <= max_text_chars <= 100_000:
        raise RuntimeError("PDF 前处理参数超出允许范围")
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_contract = {
        "schema_version": "pdf-preprocess-v2",
        "content_sha256": content_sha256,
        "max_pages": max_pages,
        "max_text_chars": max_text_chars,
    }
    cache_key = hashlib.sha256(
        json.dumps(
            cache_contract,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    preview_path = cache_dir / f"{cache_key}.png"
    context_path = cache_dir / f"{cache_key}.json"
    if preview_path.exists() and context_path.exists():
        try:
            context = json.loads(context_path.read_text(encoding="utf-8"))
            if isinstance(context, dict) and all(
                context.get(key) == value
                for key, value in cache_contract.items()
            ):
                return PdfPreprocessResult(preview_path, "image/png", context)
        except (OSError, json.JSONDecodeError):
            pass

    try:
        import fitz  # type: ignore[import-not-found]
        from pypdf import PdfReader  # type: ignore[import-not-found]
    except ImportError as exc:
        raise RuntimeError("PDF 前处理依赖未安装") from exc

    try:
        reader = PdfReader(str(source_path))
        page_count = len(reader.pages)
        extracted_pages: list[str] = []
        for page in reader.pages[:max_pages]:
            extracted_pages.append((page.extract_text() or "").strip())
        document_text = "\n\n".join(
            f"[第 {index + 1} 页]\n{text}" for index, text in enumerate(extracted_pages) if text
        )[:max_text_chars]

        document = fitz.open(str(source_path))
        rendered: list[Image.Image] = []
        ocr_pages: list[tuple[int, str]] = []
        ocr_attempts = 0
        ocr_failures = 0
        try:
            for index in range(min(max_pages, document.page_count)):
                page = document.load_page(index)
                pixmap = page.get_pixmap(matrix=fitz.Matrix(1.25, 1.25), alpha=False)
                frame = Image.open(io.BytesIO(pixmap.tobytes("png"))).convert("RGB")
                rendered.append(frame)
                if not extracted_pages[index].strip():
                    ocr_attempts += 1
                    try:
                        import pytesseract  # type: ignore[import-not-found]
                        ocr_pages.append(
                            (
                                index + 1,
                                pytesseract.image_to_string(
                                    frame,
                                    lang="chi_sim+eng",
                                ),
                            )
                        )
                    except Exception:
                        ocr_failures += 1
        finally:
            document.close()
    except Exception as exc:
        raise RuntimeError("PDF 前处理失败") from exc

    ocr_text = "\n\n".join(
        f"[第 {page_number} 页 OCR]\n{text.strip()}"
        for page_number, text in ocr_pages
        if text.strip()
    )[:max_text_chars]
    if ocr_text:
        document_text = "\n\n".join(
            item for item in (document_text, ocr_text) if item
        )[:max_text_chars]
    if ocr_attempts == 0:
        ocr_status = "not_needed"
    elif ocr_failures == ocr_attempts:
        ocr_status = "unavailable"
    elif ocr_failures:
        ocr_status = "partial"
    else:
        ocr_status = "completed"
    if not rendered:
        raise RuntimeError("PDF 没有可渲染页面")

    width = max(frame.width for frame in rendered)
    height = max(frame.height for frame in rendered)
    columns = 2 if len(rendered) > 1 else 1
    rows = (len(rendered) + columns - 1) // columns
    sheet = Image.new("RGB", (width * columns, height * rows), "white")
    for position, frame in enumerate(rendered):
        sheet.paste(frame, ((position % columns) * width, (position // columns) * height))
    temporary_path: str | None = None
    temporary_context_path: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=cache_dir, prefix=f".{content_sha256}.", suffix=".tmp", delete=False
        ) as temporary:
            temporary_path = temporary.name
        sheet.save(temporary_path, format="PNG", optimize=True)
        os.replace(temporary_path, preview_path)
        context = {
            **cache_contract,
            "page_count": page_count,
            "rendered_pages": len(rendered),
            "text_extraction": "pypdf",
            "ocr_status": ocr_status,
            "ocr_attempted_pages": ocr_attempts,
            "ocr_failed_pages": ocr_failures,
            "text_chars": len(document_text),
            "text": document_text,
            "multimodal_summary": {
                "status": "pending_model",
                "instruction": "结合 PDF 文本与页图接触表判断方案内容，不要把页眉页脚当成评测主体。",
            },
        }
        with tempfile.NamedTemporaryFile(
            dir=cache_dir,
            prefix=f".{cache_key}.",
            suffix=".json.tmp",
            delete=False,
            mode="w",
            encoding="utf-8",
        ) as temporary_context:
            temporary_context_path = temporary_context.name
            json.dump(context, temporary_context, ensure_ascii=False, sort_keys=True)
        os.replace(temporary_context_path, context_path)
    except OSError as exc:
        if temporary_path:
            try:
                Path(temporary_path).unlink()
            except OSError:
                pass
        if temporary_context_path:
            try:
                Path(temporary_context_path).unlink()
            except OSError:
                pass
        raise RuntimeError("PDF 前处理结果写入失败") from exc
    return PdfPreprocessResult(preview_path, "image/png", context)
