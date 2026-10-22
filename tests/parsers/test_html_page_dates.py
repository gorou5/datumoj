from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from datumoj.parsers.html_page import HtmlPageParser
from datumoj.testing.samples import make_snapshot

FIXTURES = Path(__file__).parents[1] / "fixtures" / "gov_web" / "html"


def _parse_html(data: bytes, *, source_id: str = "jp-date", url: str = "https://example.go.jp/date"):
    snapshot = make_snapshot(source_id=source_id, url=url, data=data, content_type="text/html")
    return HtmlPageParser().parse(snapshot, data)


def _dated_page(date_block: str) -> bytes:
    return (
        "<html><head><title>日付確認</title></head><body><main><h1>日付確認</h1>"
        f"{date_block}<p>{'自治体向け支援制度の概要。' * 12}</p></main></body></html>"
    ).encode()


@pytest.mark.parametrize(
    ("filename", "expected"),
    [
        ("osaka-city/detail-1.html", date(2026, 8, 26)),
        ("osaka-city/detail-2.html", date(2026, 10, 16)),
    ],
)
def test_osaka_page_date_blocks_are_published_dates(filename, expected):
    data = (FIXTURES / filename).read_bytes()
    result = _parse_html(data, source_id="jp-osaka-city", url=f"https://www.city.osaka.lg.jp/{filename}")

    assert result.documents[0].published_at == expected


@pytest.mark.parametrize(
    "block",
    [
        '<div class="page_day01"><p>日付はありません</p></div>',
        '<div class="page_day01"><p>対象期間 2026年8月26日</p></div>',
        '<div class="other_day"><p>2026年8月26日</p></div>',
    ],
    ids=("not-a-date", "extra-text", "unlisted-class"),
)
def test_only_whole_dates_in_listed_page_blocks_are_candidates(block):
    result = _parse_html(_dated_page(block))

    assert result.documents[0].published_at is None


def test_page_block_and_labeled_update_dates_use_the_latest_candidate():
    data = _dated_page(
        '<div class="page_day01"><p>2025年4月1日</p></div><p>更新日：2025年5月1日</p>'
    )

    assert _parse_html(data).documents[0].published_at == date(2025, 5, 1)


@pytest.mark.parametrize(
    ("filename", "source_id", "url"),
    [
        ("cao/detail-1.html", "jp-cao", "https://www8.cao.go.jp/cstp/openinnovation/procurement/index.html"),
        ("soumu/detail-1.html", "jp-soumu", "https://www.soumu.go.jp/denshijiti/index_00001.html"),
    ],
    ids=("cao", "soumu"),
)
def test_pages_without_update_candidates_remain_undated(filename, source_id, url):
    data = (FIXTURES / filename).read_bytes()

    assert _parse_html(data, source_id=source_id, url=url).documents[0].published_at is None


def test_wareki_update_date_remains_supported():
    data = (FIXTURES / "synthetic/dated-wareki.html").read_bytes()

    assert _parse_html(data).documents[0].published_at == date(2025, 4, 1)


def test_gov_web_dispatcher_version_tracks_html_parser_version(monkeypatch):
    from datumoj import wiring

    original = HtmlPageParser.version
    before = wiring._GovWebContent.version
    changed = f"{original}-next"
    monkeypatch.setattr(HtmlPageParser, "version", changed)

    after = wiring._GovWebContent.version

    assert after != before
    assert after.endswith(f"html-{changed}")
