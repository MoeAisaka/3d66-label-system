from __future__ import annotations

from PIL import Image

from app.media import (
    prepare_proposal_pdf_model_input,
    render_proposal_pdf_pages_high_fidelity,
)


def _proposal_pdf(path, page_count: int = 18) -> bytes:
    import fitz

    document = fitz.open()
    toc: list[list[object]] = []
    for index in range(page_count):
        page = document.new_page(width=1000, height=800)
        page.insert_text(
            (80, 100),
            f"Page {index + 1} proposal concept analysis render",
            fontsize=20,
        )
        if index in {0, 2, 4}:
            toc.append([1, f"Section {index + 1}", index + 1])
    document.set_toc(toc)
    document.save(path)
    document.close()
    return path.read_bytes()


def test_proposal_pdf_preprocess_renders_every_page_individually_and_batches(tmp_path) -> None:
    source = tmp_path / "proposal.pdf"
    original = _proposal_pdf(source)
    result = prepare_proposal_pdf_model_input(
        source,
        content_sha256="1" * 64,
        cache_dir=tmp_path / "derived",
        batch_size=16,
        call_a_max_side_px=1024,
        ocr_enabled=False,
    )

    assert result.page_count == 18
    assert len(result.pages) == 18
    assert [len(batch) for batch in result.page_batches()] == [16, 2]
    assert result.pages[0].page_number == 1
    assert "Page 1 proposal" in result.pages[0].text
    assert result.pages[0].text_source == "text_layer"
    assert len({page.call_a_image_path for page in result.pages}) == 18
    assert all(page.call_a_image_path.suffix == ".jpg" for page in result.pages)
    for page in result.pages:
        with Image.open(page.call_a_image_path) as image:
            assert max(image.size) <= 1024
    assert result.table_of_contents[0] == (1, "Section 1", 1)
    assert source.read_bytes() == original

    cached = prepare_proposal_pdf_model_input(
        source,
        content_sha256="1" * 64,
        cache_dir=tmp_path / "derived",
        batch_size=16,
        call_a_max_side_px=1024,
        ocr_enabled=False,
    )
    assert cached == result


def test_proposal_pdf_b_pages_are_high_fidelity_and_selected_only(tmp_path) -> None:
    source = tmp_path / "proposal.pdf"
    _proposal_pdf(source, page_count=6)
    prepared = prepare_proposal_pdf_model_input(
        source,
        content_sha256="2" * 64,
        cache_dir=tmp_path / "derived",
        ocr_enabled=False,
    )
    rendered = render_proposal_pdf_pages_high_fidelity(
        source,
        content_sha256="2" * 64,
        cache_dir=tmp_path / "derived",
        page_numbers=(1, 5),
    )
    assert tuple(rendered) == (1, 5)
    assert all(path.suffix == ".png" for path in rendered.values())
    with Image.open(prepared.pages[0].call_a_image_path) as low:
        with Image.open(rendered[1]) as high:
            assert max(high.size) > max(low.size)
