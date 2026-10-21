from pathlib import Path

import pytest

from datumoj.core.contracts import ParseError
from datumoj.parsers.xml_law import LawXMLParser
from datumoj.testing.samples import make_snapshot

FIXTURES = Path(__file__).parents[1] / "fixtures" / "egov_law"


@pytest.mark.parametrize(
    ("law_id", "expected_prefixes"),
    [
        (
            "132AC1000000040",
            ["民法第七百九条ノ規定ハ失火ノ場合ニハ之ヲ適用セス"],
        ),
        (
            "135AC1000000050",
            [
                "年齢ハ出生ノ日ヨリ之ヲ起算ス",
                "民法第百四十三条ノ規定ハ年齢ノ計算ニ之ヲ準用ス",
                "明治六年第三十六号布告ハ之ヲ廃止ス",
            ],
        ),
        (
            "120IO0000000001",
            [
                "明治十七年三月十四日（西暦千八百八十四年）ノ海底電信線保護万国連合条約ニ調印セシ各政府ヨリ出セル下名ノ全権委員ハ",
                "明治十七年三月十四日（西暦千八百八十四年）ノ条約第二条中ニ記入セル「故意」ト云ヘル文字ノ意義ニ疑惑ヲ生シタルニ依リ",
                "又該条約第四条ハ海底電信線ノ所有者其海底電信線ヲ布設シ又ハ修繕スルノ際",
                "明治十九年十二月一日（西暦千八百八十六年）各国全権委員巴里ニ於テ調印ス",
            ],
        ),
    ],
)
def test_paragraph_only_real_laws_are_split_into_paragraph_chunks(law_id, expected_prefixes):
    data = (FIXTURES / f"law_data.{law_id}.asof2026-10-21.xml-in-json.json").read_bytes()
    snapshot = make_snapshot(
        source_id="jp-egov-law",
        url=f"https://laws.e-gov.go.jp/api/2/law_data/{law_id}",
        data=data,
        content_type="application/json",
    )

    result = LawXMLParser().parse(
        snapshot,
        data,
        {"role": "law_data", "law_id": law_id, "asof": "2026-10-21"},
    )

    assert len(result.chunks) == len(expected_prefixes)
    assert [chunk.locator for chunk in result.chunks] == [
        f"para={index}" for index in range(1, len(expected_prefixes) + 1)
    ]
    assert all(
        chunk.text.startswith(prefix)
        for chunk, prefix in zip(result.chunks, expected_prefixes)
    )
    assert [chunk.chunk_id for chunk in result.chunks] == [
        f"jp-egov-law:{law_id}#para={index}" for index in range(1, len(expected_prefixes) + 1)
    ]
    if len(expected_prefixes) == 1:
        assert result.chunks[0].title_path == [result.documents[0].title]
    else:
        assert [chunk.title_path[-1] for chunk in result.chunks] == [
            f"第{index}項" for index in range(1, len(expected_prefixes) + 1)
        ]


def test_main_provision_can_mix_articles_and_paragraphs():
    data = """<?xml version='1.0' encoding='UTF-8'?>
    <Law>
      <LawNum>令和元年法律第一号</LawNum>
      <LawBody>
        <LawTitle>混合法</LawTitle>
        <MainProvision>
          <Paragraph>
            <ParagraphSentence><Sentence>第一項本文。</Sentence></ParagraphSentence>
          </Paragraph>
          <Article Num="1">
            <ArticleTitle>第一条</ArticleTitle>
            <Paragraph Num="1">
              <ParagraphSentence><Sentence>第一条本文。</Sentence></ParagraphSentence>
            </Paragraph>
          </Article>
          <Paragraph Num="3">
            <ParagraphSentence><Sentence>第三項本文。</Sentence></ParagraphSentence>
          </Paragraph>
        </MainProvision>
      </LawBody>
    </Law>""".encode()
    snapshot = make_snapshot(
        source_id="jp-egov-law",
        url="https://laws.e-gov.go.jp/api/2/law_data/synthetic",
        data=data,
        content_type="application/xml",
    )

    result = LawXMLParser().parse(
        snapshot,
        data,
        {"role": "law_data", "law_id": "synthetic"},
    )

    assert [chunk.locator for chunk in result.chunks] == ["para=1", "art=1", "para=3"]
    assert result.chunks[0].text == "第一項本文。"
    assert result.chunks[1].text.startswith("第一条")
    assert result.chunks[2].text == "第三項本文。"
    assert result.chunks[0].title_path == ["混合法", "第1項"]
    assert result.chunks[2].title_path == ["混合法", "第3項"]


def test_law_without_indexable_main_or_supplement_body_raises_parse_error():
    data = """<Law>
      <LawNum>令和元年法律第一号</LawNum>
      <LawBody>
        <LawTitle>空法</LawTitle>
        <MainProvision />
        <SupplProvision><SupplProvisionLabel>附則</SupplProvisionLabel></SupplProvision>
      </LawBody>
    </Law>""".encode()
    snapshot = make_snapshot(
        source_id="jp-egov-law",
        url="https://laws.e-gov.go.jp/api/2/law_data/empty",
        data=data,
        content_type="application/xml",
    )

    with pytest.raises(ParseError, match="没有可索引条文"):
        LawXMLParser().parse(snapshot, data, {"role": "law_data", "law_id": "empty"})
