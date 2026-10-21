"""真实 SQLite + MCP 内存会话的线程回归测试；无网络、无外部语料。"""

from __future__ import annotations

import asyncio
import threading
import weakref
from contextlib import contextmanager

import pytest
from mcp import Client

from datumoj import mcp_server
from datumoj.analysis.tools import AnalysisTools
from datumoj.core.manifest import SqliteManifest
from datumoj.core.rawstore import FileRawStore
from datumoj.index.fts import SqliteKeywordIndex
from datumoj.store.chunks import SqliteChunkStore
from datumoj.testing.fakes import FakeCatalogStore, FakeObservationStore, FakeParser, FakeRegistry, FakeRetriever
from datumoj.testing.samples import T0, make_snapshot, make_spec

TEXT = "お知らせ\n\n人口統計の公開について。"
SNAPSHOT = make_snapshot("jp-open", "https://example.go.jp/notice.txt", TEXT.encode(), fetched_at=T0)
CITE = {"snapshot_sha256": SNAPSHOT.sha256, "locator": "section=2"}


def _data(result):
    assert not result.is_error, result.content
    return result.structured_content.get("result", result.structured_content)


@pytest.fixture
def corpus(tmp_path):
    events = []
    started, unblock = threading.Event(), threading.Event()
    state = {"block": False}

    @contextmanager
    def record(name):
        assert threading.current_thread() is state["worker"]
        events.append(("start", name))
        try:
            if state["block"]:
                state["block"] = False
                started.set()
                assert unblock.wait(5), "test did not release the worker"
            yield
        finally:
            events.append(("end", name))

    class IndexedRetriever(FakeRetriever):
        # 其余接线用 testing 假实现；实际 FTS 查询也必须在 SQLite 所属线程运行。
        def __init__(self, chunks, manifest, registry, keyword):
            super().__init__(chunks, manifest, registry)
            self.keyword = keyword

        def search(self, queries, k=10, source_ids=None, date_range=None):
            indexed = {
                item.chunk_id for query in queries
                for item in self.keyword.search(query, k=k, source_ids=source_ids).items
            }
            result = super().search(queries, k=k, source_ids=source_ids, date_range=date_range)
            assert {hit.chunk_id for hit in result.data} == indexed
            return result

    class TrackedTools(AnalysisTools):
        def search_docs(self, *args, **kwargs):
            with record("search_docs"):
                return super().search_docs(*args, **kwargs)

        def cite(self, *args, **kwargs):
            with record("cite"):
                return super().cite(*args, **kwargs)

    def factory():
        assert "worker" not in state, "factory must run once per session"
        state["worker"] = threading.current_thread()
        raw = FileRawStore(tmp_path)
        manifest = SqliteManifest(tmp_path / "manifest.db")
        chunks = SqliteChunkStore(tmp_path / "chunks.db")
        keyword = SqliteKeywordIndex(tmp_path / "keyword.db")
        assert raw.put(TEXT.encode()) == SNAPSHOT.sha256
        manifest.record_snapshot(SNAPSHOT, {})
        parsed = FakeParser().parse(SNAPSHOT, TEXT.encode(), {})
        chunks.put_document(parsed.documents[0], parsed.chunks)
        keyword.add(parsed.chunks)
        registry = FakeRegistry([make_spec("jp-open", kind="fake")])
        tools = TrackedTools(
            retriever=IndexedRetriever(chunks, manifest, registry, keyword),
            catalog=FakeCatalogStore(), observations=FakeObservationStore(),
            chunks=chunks, manifest=manifest, raw=raw, registry=registry,
            parsers={("fake", "text/plain"): FakeParser}, data_dir=tmp_path,
        )

        def close():
            assert threading.current_thread() is state["worker"]
            keyword.close()
            chunks.close()
            manifest.close()
            state["closed"] = True

        weakref.finalize(tools, close)
        return tools

    return factory, state, events, started, unblock


def test_real_sqlite_search_and_cite_restore_original(corpus):
    factory, state, events, _, _ = corpus
    server = mcp_server.create_server(factory)
    assert "worker" not in state  # 创建服务器不会在主线程调用工厂。

    async def run():
        async with Client(server) as client:
            found = _data(await client.call_tool("search_docs", {"queries": "人口統計"}))
            assert found["gap_state"] == "ok"
            hit = found["data"]["hits"][0]
            assert hit["citation"]["snapshot_sha256"] == SNAPSHOT.sha256
            cited = _data(await client.call_tool("cite", {
                "snapshot_sha256": hit["citation"]["snapshot_sha256"], "locator": hit["locator"],
            }))
            assert cited["data"]["status"] == "ok"
            assert cited["data"]["passages"][0]["text"]["content"] == TEXT.split("\n\n")[1]

    asyncio.run(run())
    assert state["worker"] is not threading.current_thread()
    assert not state["worker"].is_alive()
    assert state["closed"]
    assert events == [(phase, name) for name in ("search_docs", "cite") for phase in ("start", "end")]


def test_concurrent_calls_serialize_without_blocking_event_loop(corpus):
    factory, state, events, started, unblock = corpus
    state["block"] = True

    async def run():
        async with Client(mcp_server.create_server(factory)) as client:
            first = asyncio.create_task(client.call_tool("search_docs", {"queries": "人口統計"}))
            pending = []
            try:
                assert await asyncio.to_thread(started.wait, 5)
                pending = [asyncio.create_task(client.call_tool(name, args)) for name, args in [
                    ("cite", CITE), ("search_docs", {"queries": "人口統計"}), ("cite", CITE),
                ]]
                # 专用线程仍被阻塞时，事件循环继续运行；其余调用尚未进入工具层。
                await asyncio.sleep(0.05)
                assert events == [("start", "search_docs")]
                assert not any(task.done() for task in [first, *pending])
            finally:
                unblock.set()
            results = await asyncio.wait_for(asyncio.gather(first, *pending), timeout=10)
            assert all(_data(result)["gap_state"] == "ok" for result in results)

    asyncio.run(run())
    names = ("search_docs", "cite", "search_docs", "cite")
    assert events == [(phase, name) for name in names for phase in ("start", "end")]
    assert state["closed"]


@pytest.mark.parametrize(("name", "args"), [
    ("search_docs", {"queries": "人口統計", "k": 0}),
    ("cite", {"snapshot_sha256": "invalid", "locator": "section=2"}),
])
def test_worker_exceptions_return_errors_and_session_survives(corpus, name, args):
    factory, _, _, _, _ = corpus

    async def run():
        async with Client(mcp_server.create_server(factory)) as client:
            bad = await client.call_tool(name, args)
            assert bad.is_error
            assert "ValueError" in bad.content[0].text
            assert _data(await client.call_tool("cite", CITE))["data"]["status"] == "ok"

    asyncio.run(run())


def test_factory_exception_is_an_error_result():
    calls = []

    def factory():
        calls.append(threading.current_thread())
        raise RuntimeError("factory failed")

    async def run():
        async with Client(mcp_server.create_server(factory)) as client:
            for _ in range(2):
                result = await client.call_tool("version", {})
                assert result.is_error
                assert "RuntimeError: factory failed" in result.content[0].text

    asyncio.run(run())
    assert len(calls) == 2
    assert calls[0] is calls[1] and calls[0] is not threading.current_thread()
    assert not calls[0].is_alive()


def test_main_passes_factory_without_calling_it(monkeypatch):
    def factory():
        pytest.fail("main must not construct tools")

    class Server:
        def run(self, transport):
            assert transport == "stdio"

    def create_server(received):
        assert received is factory
        return Server()

    monkeypatch.setattr(mcp_server, "_factory_from_env", lambda: factory)
    monkeypatch.setattr(mcp_server, "create_server", create_server)
    mcp_server.main()
