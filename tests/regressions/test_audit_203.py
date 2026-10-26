from datetime import date
from pathlib import Path

from datumoj.rikolto.parsers.pdf import PdfParser
from datumoj.rikolto.testing.samples import make_snapshot

SYNTHETIC_PDF = (
    Path(__file__).parents[1]
    / "fixtures"
    / "gov_web"
    / "html"
    / "synthetic"
    / "site"
    / "policy"
    / "startup"
    / "guide.pdf"
)


def test_audit_203_pdf_published_date_is_present_on_every_chunk():
    data = SYNTHETIC_PDF.read_bytes()
    snapshot = make_snapshot(
        source_id="jp-synthetic",
        url="https://example.go.jp/policy/guide.pdf",
        data=data,
        content_type="application/pdf",
    )

    result = PdfParser().parse(snapshot, data, {"published_at": "2025-04-01"})

    assert result.documents[0].published_at == date(2025, 4, 1)
    assert result.chunks
    assert all(chunk.published_at == date(2025, 4, 1) for chunk in result.chunks)
