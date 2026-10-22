import pytest

from doc_sorter import LimitError, ModelError, UnsupportedFileError
from doc_sorter.config import resolve
from doc_sorter.read import read_text


@pytest.fixture
def settings(server):
    return resolve(base_url=server.base_url, model="text", vision_model="vision", max_retries=0)


def test_plain_text_and_markdown(docs, settings, server):
    assert read_text(docs("a.txt", "Hello"), settings) == ("Hello", "text")
    assert read_text(docs("b.md", "# Title"), settings) == ("# Title", "text")
    assert server.requests == []


def test_docx(docs, settings):
    text, source = read_text(docs("c.docx", "Rental contract Vonovia"), settings)
    assert "Rental contract Vonovia" in text and source == "text"


def test_pdf_with_text_layer_needs_no_model(docs, settings, server):
    text, source = read_text(docs("d.pdf", "Invoice Telekom 2026-03-15 amount 39.99 EUR"), settings)
    assert "Telekom" in text and source == "text"
    assert server.requests == []


def test_scanned_pdf_goes_to_the_vision_model(docs, settings, server):
    server.vision_text = "Scanned invoice from Telekom"
    text, source = read_text(docs("scan.pdf", ["", ""]), settings)
    assert (text, source) == ("<!-- page 1 -->\n\nScanned invoice from Telekom\n\n<!-- page 2 -->\n\nScanned invoice from Telekom",
                              "vision")
    assert {r["body"]["model"] for r in server.requests} == {"vision"}


def test_long_scan_reads_only_the_first_pages(docs, server):
    settings = resolve(base_url=server.base_url, model="m", scan_pages=2)
    server.vision_text = "page"
    read_text(docs("long.pdf", [""] * 6), settings)
    assert len(server.requests) == 2


def test_image(docs, settings, server):
    server.vision_text = "Receipt"
    assert read_text(docs("photo.jpg"), settings) == ("Receipt", "vision")


def test_text_is_cut(docs, server):
    settings = resolve(base_url=server.base_url, model="m", max_chars=5)
    assert read_text(docs("a.txt", "0123456789"), settings)[0] == "01234"


def test_vision_failure_mentions_vision_model(docs, settings, server):
    server.fail = True
    with pytest.raises(ModelError) as exc:
        read_text(docs("photo.png"), settings)
    assert "--vision-model" in str(exc.value)


def test_unsupported_and_too_large(docs, server):
    settings = resolve(base_url=server.base_url, model="m", max_file_mb=0.001)
    with pytest.raises(UnsupportedFileError):
        read_text(docs("song.mp3", "x"), settings)
    with pytest.raises(LimitError):
        read_text(docs("big.txt", "x" * 5000), settings)


def test_broken_pdf(docs, settings):
    path = docs("broken.txt", "not a pdf")
    pdf = path.with_suffix(".pdf")
    path.rename(pdf)
    with pytest.raises(UnsupportedFileError):
        read_text(pdf, settings)
