from __future__ import annotations

from datetime import date
from pathlib import Path

from datumoj.rikolto.parsers.pdf import PdfParser
from datumoj.rikolto.testing.samples import make_snapshot

PDF_FIXTURE = Path(__file__).parents[1] / "fixtures" / "gov_web" / "pdf" / "text.pdf"
PDF_URL = "https://www8.cao.go.jp/cstp/openinnovation/procurement/guideline.pdf"


def _parse(meta: dict[str, str] | None = None):
    data = PDF_FIXTURE.read_bytes()
    snapshot = make_snapshot(source_id="jp-cao", url=PDF_URL, data=data, content_type="application/pdf")
    return PdfParser().parse(snapshot, data, meta)


def test_pdf_dates_are_copied_to_every_chunk():
    result = _parse({"published_at": "2019-06-21", "effective_at": "2020-04-01"})
    document = result.documents[0]

    assert PdfParser.version == "2"
    assert document.published_at == date(2019, 6, 21)
    assert document.effective_at == date(2020, 4, 1)
    assert result.chunks
    assert all(chunk.published_at == document.published_at for chunk in result.chunks)
    assert all(chunk.effective_at == document.effective_at for chunk in result.chunks)


def test_pdf_chunks_remain_undated_without_date_meta():
    result = _parse()

    assert result.documents[0].published_at is None
    assert result.documents[0].effective_at is None
    assert result.chunks
    assert all(chunk.published_at is None for chunk in result.chunks)
    assert all(chunk.effective_at is None for chunk in result.chunks)
