"""MAPLE completion: shared production corpus → all gates and six stdio MCP tools.

The module-scoped MAPLE-149 fixture owns replay, parsing and indexing. Reuse it
without adding test projections, translations, fixtures or another slow build.
Evidence is temporary unless DATUMOJ_EVIDENCE_DIR is explicitly set.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from collections import Counter
from decimal import Decimal

from mcp import Client, StdioServerParameters

from datumoj.rikolto.core.models import Citation, SourceText
from datumoj.rikolto.core.registry import load_registry
from datumoj.rikolto.eval import load_questions
from tests.integration.test_maple_m2 import SOURCES as JAPANESE
from tests.integration.test_maple_m2 import _rows, _text
from tests.integration.test_tachibana_m3 import ROOT, _json
from tests.regressions import test_audit_149 as audit

# Both fixture names must be visible to pytest; the corpus is built only once.
previous_m3 = audit.previous_m3
maple_m3 = audit.maple_m3


def test_all_question_banks_pass(maple_m3, capsys):
    assert {path.stem for path in (ROOT / "eval/questions").glob("*.yaml")} == set(audit.BANKS)
    audit.test_all_banks_pass_together(maple_m3, capsys)
    scores = json.loads((maple_m3["evidence"] / "scores.json").read_bytes())
    metrics = next(iter(scores.values()))["metrics"]
    lines = ["# IVY / TACHIBANA / MAPLE：同一语料逐题库分数", "",
             "每格为分数 / 门槛；N/A 表示该题库没有适用题，不计作满分。", "",
             "| 题库 | 题数 | " + " | ".join(metrics) + " |",
             "| --- | ---: | " + " | ".join("---:" for _ in metrics) + " |"]
    for bank, score in scores.items():
        cells = ["N/A" if item["value"] is None else f"{item['value']:.4f} / {item['threshold']:.2f}"
                 for item in score["metrics"].values()]
        lines.append(f"| {bank} | {score['questions']} | " + " | ".join(cells) + " |")
    (maple_m3["evidence"] / "scores.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_six_tools_over_stdio(maple_m3):
    deps = maple_m3["deps"]
    # Start the real CLI/default wiring in a separate process. A transport guard
    # in that process makes accidental HTTP fail instead of reaching the network.
    bootstrap = (
        "import httpx; "
        "exec('def forbidden(*a, **kw):\\n raise RuntimeError(\"MAPLE MCP must stay offline\")'); "
        "httpx.HTTPTransport.handle_request=forbidden; "
        "httpx.AsyncHTTPTransport.handle_async_request=forbidden; "
        "from datumoj.rikolto.cli import app; app()"
    )
    params = StdioServerParameters(
        command=sys.executable, args=["-c", bootstrap, "--data-dir", str(deps.data_dir), "mcp"], cwd=ROOT,
        env={**os.environ, "PYTHONPATH": str(ROOT / "src"), "DATUMOJ_DATA_DIR": str(deps.data_dir),
             "DATUMOJ_RAW_BACKEND": "local", "DATUMOJ_VECTORS": "off",
             "DATUMOJ_TOOLS_FACTORY": "datumoj.rikolto.wiring:build_tools"},
    )
    trace = []

    async def run():
        async with Client(params, read_timeout_seconds=120) as client:
            async def call(tool, **arguments):
                result = await client.call_tool(tool, arguments)
                assert not result.is_error, result.content
                answer = result.structured_content.get("result", result.structured_content)
                trace.append({"tool": tool, "arguments": arguments, "answer": answer})
                return answer

            async def restore(value, source):
                citation = Citation.model_validate(value)
                assert citation.source_id == source
                deps.raw.verify(citation.snapshot_sha256)
                snapshot = deps.manifest.get_snapshot(citation.snapshot_id)
                assert snapshot is not None
                assert snapshot.sha256 == citation.snapshot_sha256
                assert snapshot.resource_url == citation.resource_url
                assert snapshot.fetched_at == citation.fetched_at
                answer = await call("cite", snapshot_sha256=citation.snapshot_sha256,
                                    locator=citation.locator, snapshot_id=citation.snapshot_id,
                                    resource_url=citation.resource_url)
                assert answer["data"]["status"] == "ok", answer
                passages = answer["data"]["passages"]
                assert passages
                assert all(SourceText.model_validate(p["text"]).content for p in passages)
                return passages

            questions = {q.id: q for q in load_questions(ROOT / "eval/questions")}
            selected = ("maple-ml-ja-en", "maple-ml-ja-fr", "maple-ml-zh-fr", "maple-ml-zh-en",
                        "maple-ml-en", "maple-west-fed-paperwork", "maple-ml-fr")
            assert Counter(questions[key].lang for key in selected) == {"ja": 2, "zh": 2, "en": 2, "fr": 1}
            for key in selected:
                question = questions[key]
                # No source filter: rank one must identify the source among the
                # whole mixed corpus. Translated queries are explicit bank data.
                answer = await call("search_docs", queries=question.queries, k=10)
                hits = answer["data"]["hits"]
                assert answer["gap_state"] == "ok" and hits, (key, answer)
                expected = question.expect.hits[0]
                assert hits[0]["citation"]["source_id"] == question.source_ids[0], (key, hits[0])
                hit = next(h for h in hits if h["doc_key"] == expected.doc_key
                           and h["locator"].startswith(expected.locator_prefix))
                await restore(hit["citation"], question.source_ids[0])

            for query, source, key in (
                ("nationalité statut immigré", "fr-data-gouv", "fr-data-gouv:6ad16a7506eea90157a47a35"),
                ("combined nomenclature", "eu-data-europa", "eu-data-europa:combined-nomenclature-2023"),
                ("概算値", "jp-estat-api", "jp-estat-api:0003443838"),
            ):
                answer = await call("search_catalog", query=query, sources=[source])
                item = next(item for item in answer["data"]["datasets"] if item["dataset_key"] == key)
                await restore(item["citation"], source)

            # Same year and GDP concept, original currencies and scales kept.
            # Numbers are obtained exclusively via SQL, with row identity intact.
            sql = (
                "SELECT source_id, table_key, obs_id, snapshot_sha256, release_id, "
                "value, unit, time_label, area_code FROM observations WHERE time_label='2024' AND "
                "((source_id='eu-eurostat' AND table_key='nama_10_gdp' AND area_code='DE') OR "
                "(source_id='intl-wdi' AND indicator_code='NY.GDP.MKTP.CD' AND area_code='JPN')) "
                "ORDER BY source_id"
            )
            answer = await call("query_sql", sql=sql)
            rows = _rows(answer)
            assert answer["evidence_state"] == "complete" and len(rows) == 2, answer
            expected = {"eu-eurostat": (Decimal("4386940.0"), "CP_MEUR", "DE"),
                        "intl-wdi": (Decimal("4190008188358.57"), "current US$", "JPN")}
            assert {row["source_id"] for row in rows} == set(expected)
            for row in rows:
                value, unit, area = expected[row["source_id"]]
                assert Decimal(row["value"]) == value
                assert (_text(row["unit"]), _text(row["time_label"]), _text(row["area_code"])) == (unit, "2024", area)
                item, = [p for p in answer["data"]["provenance"]
                         if all(p[key] == row[key] for key in ("source_id", "table_key", "obs_id"))]
                passages = await restore(item["citation"], row["source_id"])
                assert any(p["kind"] == "observation" and Decimal(p["value"]) == value for p in passages)

            sources = [*audit.LEGACY, *audit.WEST, *JAPANESE]
            for query in ("火星の恐竜の光合成と葉緑素", "Martian dinosaurs photosynthesis chlorophyll"):
                answer = await call("search_docs", queries=query, sources=sources, k=10)
                assert answer["gap_state"] == "zero_hits" and answer["data"]["hits"] == [], answer

            answer = await call("list_changes", since="2026-10-22", until="2026-10-22",
                                sources=["jp-gov-web"], kinds=["added", "modified", "removed"])
            assert answer["gap_state"] == "ok"
            assert Counter(row["kind"] for row in answer["data"]) == {"added": 1, "modified": 1, "removed": 1}
            for event in answer["data"]:
                assert SourceText.model_validate(event["summary"]).content
                await restore(event["citation"], event["source_id"])

            answer = await call("coverage")
            coverage = {row["source_id"]: row for row in answer["data"]["sources"]}
            assert set(coverage) == {spec.id for spec in load_registry(ROOT / "sources").all()}
            assert set(sources) <= set(coverage)
            for source in sources:
                assert coverage[source]["status"] == "ok" and coverage[source]["snapshots"] > 0
                assert coverage[source]["freshness"]["as_of"] is not None

    try:
        asyncio.run(run())
    finally:
        _json(maple_m3["evidence"] / "mcp-stdio.json", trace)
