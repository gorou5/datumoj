from datumoj.rikolto.parsers.html_page import HtmlPageParser
from datumoj.rikolto.testing.samples import make_snapshot


def test_removing_page_chrome_keeps_following_body_text():
    tail_text = "尾随正文应当保留。" * 18
    data = (
        "<html><head><title>尾随正文测试</title></head><body><main>"
        "<div id='page-header'>共通页眉</div>"
        f"{tail_text}"
        "</main></body></html>"
    ).encode()
    snapshot = make_snapshot(source_id="jp-tail", url="https://example.go.jp/tail", data=data)

    result = HtmlPageParser().parse(snapshot, data)
    text = "\n".join(chunk.text for chunk in result.chunks)

    assert HtmlPageParser.version == "3"
    assert "共通页眉" not in text
    assert tail_text in text
