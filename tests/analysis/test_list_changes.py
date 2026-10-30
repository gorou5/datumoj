"""list_changes and document-kind filtering, using contract fakes only."""

from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest

from datumoj.rikolto.analysis.tools import AnalysisTools
from datumoj.rikolto.core.models import (
    ChangeEvent,
    ChangeKind,
    DocKind,
    LicenseInfo,
    Redistribution,
    UsageScope,
)
from datumoj.rikolto.testing.fakes import (
    FakeCatalogStore,
    FakeChangeLog,
    FakeChunkStore,
    FakeManifest,
    FakeObservationStore,
    FakeRawStore,
    FakeRegistry,
    FakeRetriever,
)
from datumoj.rikolto.testing.samples import T0, make_chunk, make_document, make_snapshot, make_spec


@pytest.fixture
def env(tmp_path):
    raw, manifest, chunks = FakeRawStore(tmp_path), FakeManifest(), FakeChunkStore()
    catalog, observations, change_log = FakeCatalogStore(), FakeObservationStore(), FakeChangeLog()
    license_info = LicenseInfo(
        name="CC BY 4.0", url="https://example.go.jp/license", attribution="テスト省",
        usage_scope=UsageScope.redistributable, redistribution=Redistribution.allowed, outflow="raw_ok",
        verified_at=date(2026, 10, 20), notes="テスト用の検証根拠",
    )
    registry = FakeRegistry([make_spec("jp-test", kind="fake", license=license_info)])
    snapshots = {}
    for suffix, kind, title in (
        ("a", DocKind.bulletin, "告知文書"),
        ("b", None, "旧形式文書"),
        ("z", DocKind.pdf, "PDF 文書"),
    ):
        doc_key = f"jp-test:{suffix}"
        body = f"{title} needle".encode()
        snap = make_snapshot("jp-test", f"https://example.go.jp/{suffix}.txt", body, fetched_at=T0)
        snapshots[suffix] = snap
        raw.put(body)
        manifest.record_snapshot(snap, {})
        doc = make_document(doc_key, snap.sha256, title, kind=kind)
        chunk = make_chunk(
            doc_key, snap.sha256, "section=1", "needle", title_path=[title], published_at=date(2026, 10, 20),
        )
        chunks.put_document(doc, [chunk])
    tools = AnalysisTools(
        retriever=FakeRetriever(chunks, manifest, registry), catalog=catalog, observations=observations,
        chunks=chunks, manifest=manifest, raw=raw, registry=registry, change_log=change_log,
        parsers={}, data_dir=tmp_path,
    )
    return tools, change_log, chunks, snapshots


def test_list_changes_cites_new_snapshot_and_marks_source_text(env):
    tools, change_log, _, snapshots = env
    old = snapshots["a"]
    new = make_snapshot("jp-test", "https://example.go.jp/a.txt", b"new document", fetched_at=T0 + timedelta(hours=1))
    env[2].put_document(
        make_document("jp-test:a", new.sha256, "更新後タイトル", kind=DocKind.html_page),
        [make_chunk("jp-test:a", new.sha256, "section=1", "updated", published_at=date(2026, 10, 21))],
    )
    env[0].manifest.record_snapshot(new, {})
    change_log.append([
        ChangeEvent.make(
            source_id="jp-test", kind=ChangeKind.modified, doc_key="jp-test:a",
            resource_url=old.resource_url, old_sha256=old.sha256, new_sha256=new.sha256,
            detected_at=T0 + timedelta(hours=1), summary="网页内容发生更新",
        ),
        ChangeEvent.make(
            source_id="jp-test", kind=ChangeKind.removed, doc_key="jp-test:b",
            resource_url=snapshots["b"].resource_url, old_sha256=snapshots["b"].sha256,
            detected_at=T0 + timedelta(hours=2), summary="文档已移除",
        ),
    ])

    result = tools.list_changes(since=T0, until=T0 + timedelta(hours=2))
    assert result["gap_state"] == "ok"
    modified, removed = result["data"]
    assert modified["title"] == {"content": "更新後タイトル", "content_role": "untrusted_source_text"}
    assert modified["summary"] == {"content": "网页内容发生更新", "content_role": "untrusted_source_text"}
    assert modified["citation"]["snapshot_sha256"] == new.sha256
    assert modified["citation"]["time_basis"] == "published"
    assert removed["citation"]["snapshot_sha256"] == snapshots["b"].sha256
    assert removed["citation"]["locator"] == "section=1"
    assert result["freshness"]["as_of"] == (T0 + timedelta(hours=1)).isoformat()


def test_list_changes_filters_and_zero_hits(env):
    tools, change_log, _, snapshots = env
    change_log.append([
        ChangeEvent.make(
            source_id="jp-test", kind=ChangeKind.added, doc_key="jp-test:z",
            resource_url=snapshots["z"].resource_url, new_sha256=snapshots["z"].sha256,
            detected_at=T0, summary="PDF が追加されました",
        ),
        ChangeEvent.make(
            source_id="jp-test", kind=ChangeKind.fetch_failed, resource_url="https://example.go.jp/missing.txt",
            detected_at=T0 + timedelta(days=20), summary="取得に失敗しました",
        ),
    ])
    selected = tools.list_changes(since=T0, sources="jp-test", kinds=["added"], limit=1)
    assert [event["kind"] for event in selected["data"]] == ["added"]
    assert selected["data"][0]["citation"]["snapshot_sha256"] == snapshots["z"].sha256
    empty = tools.list_changes(since=T0 + timedelta(days=1), kinds="removed")
    assert empty["data"] == [] and empty["gap_state"] == "zero_hits"


def test_list_changes_defaults_to_the_last_seven_days(env, monkeypatch):
    tools, change_log, _, snapshots = env

    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return T0 + timedelta(days=1)

    monkeypatch.setattr("datumoj.rikolto.analysis.tools.datetime", FrozenDateTime)
    change_log.append([
        ChangeEvent.make(
            source_id="jp-test", kind=ChangeKind.added, doc_key="jp-test:a",
            resource_url=snapshots["a"].resource_url, new_sha256=snapshots["a"].sha256,
            detected_at=T0 - timedelta(days=8), summary="較旧事件",
        ),
        ChangeEvent.make(
            source_id="jp-test", kind=ChangeKind.added, doc_key="jp-test:z",
            resource_url=snapshots["z"].resource_url, new_sha256=snapshots["z"].sha256,
            detected_at=T0, summary="最近の事件",
        ),
    ])
    result = tools.list_changes()
    assert [event["summary"]["content"] for event in result["data"]] == ["最近の事件"]


def test_list_changes_missing_log_is_not_reported_as_zero_hits(env, tmp_path):
    tools, _, _, _ = env
    disconnected = AnalysisTools(
        retriever=tools.retriever, catalog=tools.catalog, observations=tools.observations, chunks=tools.chunks,
        manifest=tools.manifest, raw=tools.raw, registry=tools.registry, parsers={}, data_dir=tmp_path,
    )
    result = disconnected.list_changes()
    assert result["data"] == [] and result["gap_state"] == "not_ingested"


def test_search_docs_filters_document_kind_and_includes_legacy_when_unfiltered(env):
    tools, _, _, _ = env
    all_hits = tools.search_docs("needle", k=3)["data"]["hits"]
    assert {hit["doc_key"] for hit in all_hits} == {"jp-test:a", "jp-test:b", "jp-test:z"}
    pdf_hits = tools.search_docs("needle", k=1, kinds=["pdf"])["data"]["hits"]
    assert [hit["doc_key"] for hit in pdf_hits] == ["jp-test:z"]
    assert tools.search_docs("needle", kinds=["law"])["gap_state"] == "zero_hits"


@pytest.mark.parametrize(
    "kwargs",
    [
        {"limit": -1}, {"limit": 1001}, {"limit": True}, {"since": "not-a-date"},
        {"since": "2026-10-22T00:00:00"}, {"since": "2026-10-22", "until": "2026-10-21"},
        {"sources": []}, {"kinds": []}, {"kinds": ["unknown"]},
    ],
)
def test_list_changes_bad_input(env, kwargs):
    with pytest.raises(ValueError):
        env[0].list_changes(**kwargs)


@pytest.mark.parametrize("kwargs", [{"kinds": []}, {"kinds": ["unknown"]}, {"kinds": [1]}])
def test_search_docs_bad_kinds(env, kwargs):
    with pytest.raises(ValueError):
        env[0].search_docs("needle", **kwargs)
