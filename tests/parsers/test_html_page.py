from __future__ import annotations

import hashlib
from datetime import date
from pathlib import Path

import pytest

from datumoj.core.contracts import ParseError
from datumoj.parsers.html_page import HtmlPageParser
from datumoj.testing.contract_suites import ParserSuite
from datumoj.testing.samples import ParseSample, make_snapshot

FIXTURES = Path(__file__).parents[1] / "fixtures" / "gov_web" / "html"
PARENT_SHA = "a" * 64


def _sample(filename: str, source_id: str, url: str, meta=None) -> ParseSample:
    data = (FIXTURES / filename).read_bytes()
    return ParseSample(
        make_snapshot(source_id=source_id, url=url, data=data, content_type="text/html"),
        data,
        meta or {},
    )


def _site_samples() -> list[ParseSample]:
    return [
        _sample(
            "cao/detail-1.html",
            "jp-cao",
            "https://www8.cao.go.jp/cstp/openinnovation/procurement/index.html",
            {"parent_url": "https://www8.cao.go.jp/cstp/openinnovation/index.html", "parent_sha256": PARENT_SHA},
        ),
        _sample(
            "soumu/detail-1.html",
            "jp-soumu",
            "https://www.soumu.go.jp/denshijiti/index_00001.html",
        ),
        _sample(
            "osaka-city/detail-1.html",
            "jp-osaka-city",
            "https://www.city.osaka.lg.jp/keizaisenryaku/page/0000685899.html",
        ),
    ]


class TestHtmlPageParser(ParserSuite):
    @pytest.fixture
    def parser(self):
        return HtmlPageParser()

    @pytest.fixture
    def samples(self):
        return [
            *_site_samples(),
            _sample(
                "synthetic/dated-wareki.html",
                "jp-synthetic",
                "https://example.go.jp/support/startup.html",
                {"seed": "true"},
            ),
        ]

    @pytest.fixture
    def bad_samples(self):
        return [
            b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00",
            (FIXTURES / "spa/shell.html").read_bytes(),
        ]


@pytest.mark.parametrize(
    ("sample", "key_phrase", "navigation", "expected_path"),
    [
        (
            _site_samples()[0],
            "スタートアップからの公共調達等の推進",
            "ホーム",
            ("スタートアップによる公共調達の促進や研究開発の活用に係る取組",),
        ),
        (
            _site_samples()[1],
            "自治体DX",
            "総務省トップ",
            ("自治体DXの推進", "自治体におけるDX推進の意義"),
        ),
        (
            _site_samples()[2],
            "日本国内の法人又は個人",
            "本文へ",
            ("先駆的金融市場等形成支援事業補助金について", "補助金の概要", "2 補助事業者"),
        ),
    ],
    ids=("cao", "soumu-shift-jis", "osaka-city"),
)
def test_real_sites_extract_body_and_heading_path(sample, key_phrase, navigation, expected_path):
    result = HtmlPageParser().parse(sample.snapshot, sample.data, sample.meta)
    document = result.documents[0]
    text = "\n".join(chunk.text for chunk in result.chunks)

    assert document.kind == "html_page"
    assert document.content_type == "text/html"
    assert key_phrase in text
    assert navigation not in text
    assert result.chunks
    assert all(chunk.title_path for chunk in result.chunks)
    assert all(chunk.locator.startswith("section=") for chunk in result.chunks)
    assert expected_path in [tuple(chunk.title_path) for chunk in result.chunks]


def test_dates_section_chunks_and_snapshot_meta_are_preserved():
    sample = _sample(
        "synthetic/dated-wareki.html",
        "jp-synthetic",
        "https://EXAMPLE.go.jp:443/support/old/../%73tartup.html#top",
        {
            "parent_url": "https://example.go.jp/",
            "parent_sha256": PARENT_SHA,
            "seed": "seed",
        },
    )
    result = HtmlPageParser().parse(sample.snapshot, sample.data, sample.meta)
    document = result.documents[0]
    expected_url = "https://example.go.jp/support/startup.html"

    assert document.doc_key == f"jp-synthetic:page:{hashlib.sha1(expected_url.encode()).hexdigest()[:16]}"
    assert document.published_at == date(2025, 4, 1)
    assert all(chunk.published_at == date(2025, 4, 1) for chunk in result.chunks)
    assert any(chunk.title_path == [document.title, "申請の手続き"] for chunk in result.chunks)
    assert document.metadata == {
        "parent_url": "https://example.go.jp/",
        "parent_sha256": PARENT_SHA,
        "seed": "seed",
    }


def test_long_section_is_split_at_paragraph_boundaries():
    sample = _sample(
        "synthetic/dated-wareki.html",
        "jp-synthetic",
        "https://example.go.jp/support/startup.html",
    )
    repeated_paragraph = "<p>創業者は事業計画を作成し、支援窓口で相談できます。</p>" * 30
    data = sample.data.replace(
        "<h2>申請の手続き</h2>".encode(),
        ("<h2>申請の手続き</h2>" + repeated_paragraph).encode(),
    )
    snapshot = make_snapshot(
        source_id=sample.snapshot.source_id,
        url=sample.snapshot.resource_url,
        data=data,
        content_type="text/html",
    )

    result = HtmlPageParser().parse(snapshot, data)
    dated_chunks = [chunk for chunk in result.chunks if chunk.title_path[-1] == "申請の手続き"]

    assert len(dated_chunks) >= 2
    assert all(chunk.title_path == [result.documents[0].title, "申請の手続き"] for chunk in dated_chunks)
    assert [chunk.locator.rsplit("=", 1)[-1] for chunk in dated_chunks] == [str(i) for i in range(1, len(dated_chunks) + 1)]


@pytest.mark.parametrize(
    ("label", "value", "expected"),
    [
        ("更新日", "2025年4月1日", date(2025, 4, 1)),
        ("更新日", "2025/4/1", date(2025, 4, 1)),
        ("最終更新日", "2025-04-01", date(2025, 4, 1)),
        ("掲載日", "2025年3月15日", None),
    ],
)
def test_western_date_formats_and_update_date_selection(label, value, expected):
    data = (
        "<html><head><title>日付確認</title></head><body><main><h1>日付確認</h1>"
        f"<p>{label}：{value}</p><p>{'自治体向け支援制度の概要。' * 12}</p></main></body></html>"
    ).encode()
    snapshot = make_snapshot(source_id="jp-date", url="https://example.go.jp/date", data=data)

    result = HtmlPageParser().parse(snapshot, data)

    assert result.documents[0].published_at == expected


def test_osaka_unlabeled_page_date_is_not_treated_as_published_date():
    sample = _site_samples()[2]
    result = HtmlPageParser().parse(sample.snapshot, sample.data, sample.meta)

    assert result.documents[0].published_at is None


def test_update_date_from_page_metadata():
    data = (
        '<html><head><title>日付確認</title><meta property="article:modified_time" '
        'content="2025-04-01T00:00:00+09:00"></head><body><main><h1>日付確認</h1>'
        f"<p>{'自治体向け支援制度の概要。' * 12}</p></main></body></html>"
    ).encode()
    snapshot = make_snapshot(source_id="jp-date", url="https://example.go.jp/date", data=data)

    result = HtmlPageParser().parse(snapshot, data)

    assert result.documents[0].published_at == date(2025, 4, 1)


def test_latest_update_date_wins_and_role_main_is_preferred():
    body = "自治体向け支援制度の案内です。" + "制度の詳細を説明します。" * 8
    data = (
        "<html><head><title>政策ページ</title></head><body>"
        f"<div role='region navigation'>{'共通ナビゲーション項目。' * 10}</div>"
        f"<div role='main'><h1>政策ページ</h1><p>更新日：2025/4/1</p>"
        f"<p>最終更新日：2025-05-01</p><p>{body}</p></div>"
        f"<article><p>{'別の長い記事本文。' * 40}</p></article></body></html>"
    ).encode()
    snapshot = make_snapshot(source_id="jp-main", url="https://example.go.jp/main", data=data)

    result = HtmlPageParser().parse(snapshot, data)
    text = "\n".join(chunk.text for chunk in result.chunks)

    assert result.documents[0].published_at == date(2025, 5, 1)
    assert "共通ナビゲーション項目" not in text
    assert "別の長い記事本文" not in text


def test_title_element_is_used_when_page_has_no_h1():
    data = (
        "<html><head><title>ページタイトル</title></head><body><main>"
        "<h2>制度の概要</h2><p>自治体向け支援制度の案内です。" + "制度の詳細を説明します。" * 8 + "</p>"
        "</main></body></html>"
    ).encode()
    snapshot = make_snapshot(source_id="jp-title", url="https://example.go.jp/title", data=data)

    result = HtmlPageParser().parse(snapshot, data)

    assert result.documents[0].title == "ページタイトル"
    assert result.chunks[0].title_path == ["制度の概要"]


def test_spa_shell_is_reported_as_empty_content():
    sample = _sample("spa/shell.html", "jp-jgrants", "https://www.jgrants-portal.go.jp/index.html")

    with pytest.raises(ParseError, match="empty_content"):
        HtmlPageParser().parse(sample.snapshot, sample.data, sample.meta)


def test_navigation_only_page_is_reported_as_empty_content():
    data = ("<html><body><nav>" + "補助金情報のページ一覧 " * 20 + "</nav></body></html>").encode()
    snapshot = make_snapshot(source_id="jp-empty", url="https://example.go.jp/empty", data=data)

    with pytest.raises(ParseError, match="empty_content"):
        HtmlPageParser().parse(snapshot, data)
