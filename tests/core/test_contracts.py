"""契约二：接口协议、插件自动发现、假实现与合格测试（DATUMOJ-IVY-002）。"""

import sys
import textwrap
import uuid
from datetime import date
from decimal import Decimal

import pytest
from pydantic import ValidationError

from datumoj.rikolto.core.contracts import (
    CrawlContext,
    FetchResult,
    FetchTask,
    ParseError,
    ParseRecord,
    ParseResult,
    ParseStatus,
    PluginConflict,
    PluginError,
    PluginWarning,
    SourceSpec,
    citation_for_chunk,
    citation_for_observation,
    discover_crawlers,
    discover_parsers,
    normalize_media_type,
    select_parser,
)
from datumoj.rikolto.core.models import FetchOutcome, GapState, LicenseInfo, Redistribution, TimeBasis, UsageScope
from datumoj.rikolto.testing.contract_suites import (
    CatalogStoreSuite,
    ChunkStoreSuite,
    CrawlerSuite,
    EmbedderSuite,
    HttpFetcherSuite,
    KeywordIndexSuite,
    ManifestSuite,
    ObservationReleaseSuite,
    ObservationStoreSuite,
    ParserSuite,
    RawStoreSuite,
    RegistrySuite,
    RetrieverSuite,
    VectorIndexSuite,
    _kw_corpus,
    _obs_table,
    retrieval_corpus,
)
from datumoj.rikolto.testing.fakes import (
    FakeCatalogStore,
    FakeChunkStore,
    FakeCrawler,
    FakeEmbedder,
    FakeHttpFetcher,
    FakeKeywordIndex,
    FakeManifest,
    FakeObservationStore,
    FakeParser,
    FakeRawStore,
    FakeRegistry,
    FakeResponse,
    FakeRetriever,
    FakeVectorIndex,
    UnregisteredUrl,
    drive_crawler,
)
from datumoj.rikolto.testing.samples import (
    T0,
    ParseSample,
    make_chunk,
    make_observation,
    make_snapshot,
    make_spec,
    sha,
)

BASE = "https://example.go.jp"
PAGE_A = f"{BASE}/a.txt"
PAGE_B = f"{BASE}/b.txt"
TEXT_A = f"第一条　この法律は、個人情報の保護を目的とする。\n\n第二条　定義。\n\nlink: {PAGE_B}\n".encode()
TEXT_B = f"お知らせ\n\nlink: {PAGE_A}\n".encode()  # 回链到 A：执行器必须去重


# ---------- 假实现跑合格测试 ----------


class TestFakeRegistry(RegistrySuite):
    @pytest.fixture
    def make_registry(self):
        return FakeRegistry


class TestFakeRawStore(RawStoreSuite):
    @pytest.fixture
    def store(self):
        return FakeRawStore()

    def corrupt(self, store, sha256):
        store.blobs[sha256] = b"tampered"


class TestFakeManifest(ManifestSuite):
    @pytest.fixture
    def manifest(self):
        return FakeManifest()


class TestFakeHttpFetcher(HttpFetcherSuite):
    @pytest.fixture
    def make_fetcher(self):
        return lambda routes, max_bytes=None: FakeHttpFetcher(routes, max_bytes=max_bytes)


class TestFakeCrawler(CrawlerSuite):
    @pytest.fixture
    def crawler(self):
        return FakeCrawler()

    @pytest.fixture
    def spec(self):
        return make_spec(kind="fake", scope={"urls": [PAGE_A]})

    @pytest.fixture
    def make_fetcher(self):
        return lambda: FakeHttpFetcher({PAGE_A: TEXT_A, PAGE_B: TEXT_B})


class TestFakeParser(ParserSuite):
    @pytest.fixture
    def parser(self):
        return FakeParser()

    @pytest.fixture
    def samples(self):
        return [
            ParseSample(make_snapshot(url=PAGE_A, data=TEXT_A), TEXT_A),
            ParseSample(make_snapshot(url=PAGE_B, data=TEXT_B), TEXT_B, {"doc_id": "notice-1"}),
        ]


class TestFakeChunkStore(ChunkStoreSuite):
    @pytest.fixture
    def store(self):
        return FakeChunkStore()


class TestFakeKeywordIndex(KeywordIndexSuite):
    @pytest.fixture
    def index(self):
        return FakeKeywordIndex()


class TestFakeEmbedder(EmbedderSuite):
    @pytest.fixture
    def embedder(self):
        return FakeEmbedder()


class TestFakeVectorIndex(VectorIndexSuite):
    @pytest.fixture
    def make_index(self):
        storage: dict = {}
        return lambda embedder: FakeVectorIndex(embedder, storage)


class TestFakeRetriever(RetrieverSuite):
    @pytest.fixture
    def make_retriever(self):
        def build(corpus):
            manifest, chunks = FakeManifest(), FakeChunkStore()
            for snap in corpus.snapshots:
                manifest.record_snapshot(snap)
            for doc, doc_chunks in corpus.documents:
                chunks.put_document(doc, doc_chunks)
            return FakeRetriever(chunks, manifest, FakeRegistry(corpus.specs))

        return build


class TestFakeCatalogStore(CatalogStoreSuite):
    @pytest.fixture
    def store(self):
        return FakeCatalogStore()


class TestFakeObservationStore(ObservationStoreSuite):
    @pytest.fixture
    def store(self):
        return FakeObservationStore()

    def test_timeout(self, filled):
        from datumoj.rikolto.core.contracts import QueryTimeout

        endless = "WITH RECURSIVE n(i) AS (SELECT 1 UNION ALL SELECT i + 1 FROM n) SELECT count(*) FROM n"
        with pytest.raises(QueryTimeout):
            filled.query_readonly(endless, timeout_s=0.2)


# ---------- 合格测试要约束行为：故意写错的实现必须被抓出来 ----------


class TestSuitesCatchBrokenImplementations:
    def test_manifest_latest_by_insertion_order(self):
        class Broken(FakeManifest):
            def latest_snapshot(self, resource_url):
                snaps = [s for s in self._snaps if s.resource_url == resource_url]
                return snaps[-1] if snaps else None

        with pytest.raises(AssertionError):
            ManifestSuite().test_latest_by_fetched_at_not_insertion(Broken())

    def test_chunk_store_dropping_old_snapshots(self):
        class Broken(FakeChunkStore):
            def put_document(self, document, chunks):
                self._versions.pop(document.doc_key, None)
                super().put_document(document, chunks)

        with pytest.raises(AssertionError):
            ChunkStoreSuite().test_new_snapshot_replaces_but_keeps_old(Broken())

    def test_keyword_index_without_nfkc(self):
        class Broken(FakeKeywordIndex):
            def search(self, query, k=10, source_ids=None):
                result = super().search(query, k, source_ids)
                keep = [h for h in result.items if query in self._chunks[h.chunk_id].text]
                return result.model_copy(update={"items": keep})

        index = Broken()
        index.add(_kw_corpus())
        with pytest.raises(AssertionError):
            KeywordIndexSuite().test_nfkc_normalized(index)

    def test_observation_store_filling_zero(self):
        class Broken(FakeObservationStore):
            def write_table(self, source_id, table_key, observations):
                filled = [o.model_copy(update={"value": Decimal(0)}) if o.value is None else o for o in observations]
                super().write_table(source_id, table_key, filled)

        store = Broken()
        store.write_table("jp-stat", "t1", _obs_table())
        with pytest.raises(AssertionError):
            ObservationStoreSuite().test_missing_value_stays_null(store)

    def test_fetcher_leaking_key_in_error(self):
        class Broken(FakeHttpFetcher):
            def fetch(self, url, etag=None, last_modified=None):
                result = super().fetch(url, etag, last_modified)
                return result.model_copy(update={"error": f"failed: {url}"}) if result.error else result

        with pytest.raises(AssertionError):
            HttpFetcherSuite().test_keys_never_leak(lambda routes, max_bytes=None: Broken(routes), 404)

    def test_retriever_blurring_gap_states(self):
        class Broken(FakeRetriever):
            def _gap(self, source_ids):
                return GapState.zero_hits, "零命中"

        def build(corpus):
            manifest, chunks = FakeManifest(), FakeChunkStore()
            for snap in corpus.snapshots:
                manifest.record_snapshot(snap)
            for doc, doc_chunks in corpus.documents:
                chunks.put_document(doc, doc_chunks)
            return Broken(chunks, manifest, FakeRegistry(corpus.specs))

        with pytest.raises(AssertionError):
            RetrieverSuite().test_gap_states(build(retrieval_corpus()), "jp-fail", GapState.fetch_failed)


# ---------- 契约里的模型和纯函数 ----------


class TestSourceSpec:
    def test_valid_and_to_source(self):
        spec = make_spec("jp-egov-law", kind="egov_law", needs_key="env:EGOV_KEY")
        src = spec.to_source()
        assert (src.source_id, src.kind, src.license) == ("jp-egov-law", "egov_law", spec.license)
        assert spec.license.usage_scope is UsageScope.internal_only
        assert spec.license.redistribution is Redistribution.unverified

    @pytest.mark.parametrize("override", [
        {"id": "JP-Bad"},
        {"kind": "Egov Law"},
        {"scope": {}},
        {"allowed_hosts": []},
        {"needs_key": "plain-secret-value"},
        {"rate_per_sec": 0},
        {"max_files": 0},
        {"base_url": "https://example.go.jp/?appId=SECRET"},
        {"unknown_field": 1},
    ])
    def test_rejects(self, override):
        with pytest.raises(ValidationError):
            make_spec(**override)

    def test_redistributable_needs_verified_license(self):
        with pytest.raises(ValidationError):
            make_spec(license=LicenseInfo(usage_scope=UsageScope.redistributable))


class TestFetchModels:
    def test_ok_needs_content(self):
        with pytest.raises(ValidationError):
            FetchResult(url=PAGE_A, outcome=FetchOutcome.ok, fetched_at=T0, http_status=200)

    def test_failure_needs_error_and_no_content(self):
        with pytest.raises(ValidationError):
            FetchResult(url=PAGE_A, outcome=FetchOutcome.network_error, fetched_at=T0)
        with pytest.raises(ValidationError):
            FetchResult(url=PAGE_A, outcome=FetchOutcome.too_large, fetched_at=T0, error="big", content=b"x")
        with pytest.raises(ValidationError):
            FetchResult(url=PAGE_A, outcome=FetchOutcome.http_error, fetched_at=T0, error="HTTP")

    def test_url_must_be_redacted(self):
        with pytest.raises(ValidationError):
            FetchResult(url=f"{BASE}/x?appId=SECRET", outcome=FetchOutcome.ok, fetched_at=T0, content=b"")

    def test_to_snapshot(self):
        r = FetchResult(url=PAGE_A, outcome=FetchOutcome.ok, fetched_at=T0, http_status=200, content=b"abc",
                        content_type="text/plain", etag='"e"')
        snap = r.to_snapshot("s1", "jp-test", license="CC BY")
        assert (snap.sha256, snap.size, snap.etag, snap.license) == (sha(b"abc"), 3, '"e"', "CC BY")
        assert snap.resource_url == PAGE_A and snap.fetched_at == T0
        nm = FetchResult(url=PAGE_A, outcome=FetchOutcome.not_modified, fetched_at=T0, http_status=304)
        assert nm.to_snapshot("s2", "jp-test").sha256 is None

    def test_content_hidden_and_json_safe(self):
        body = "東京都".encode("cp932")
        r = FetchResult(url=PAGE_A, outcome=FetchOutcome.ok, fetched_at=T0, content=body)
        assert "content=" not in repr(r)
        assert FetchResult.model_validate_json(r.model_dump_json()) == r

    def test_fetch_task(self):
        task = FetchTask(url=f"{BASE}/api?appId=SECRET&x=1", meta={"role": "page", "n": 1})
        assert task.redacted_url == f"{BASE}/api?appId=REDACTED&x=1"
        with pytest.raises(ValidationError):
            FetchTask(url="file:///etc/passwd")


class TestParseModels:
    snap = make_snapshot(data=b"doc")

    def _doc_and_chunk(self, sha256=None):
        from datumoj.rikolto.testing.samples import make_document

        s = sha256 or self.snap.sha256
        return make_document("jp-test:d", s), make_chunk("jp-test:d", s, "art=1", "本文")

    def test_chunk_needs_its_document(self):
        _, chunk = self._doc_and_chunk()
        with pytest.raises(ValidationError):
            ParseResult(chunks=[chunk])

    def test_duplicate_ids(self):
        doc, chunk = self._doc_and_chunk()
        with pytest.raises(ValidationError):
            ParseResult(documents=[doc], chunks=[chunk, chunk])
        with pytest.raises(ValidationError):
            ParseResult(documents=[doc, doc])

    def test_check_against(self):
        doc, chunk = self._doc_and_chunk()
        ParseResult(documents=[doc], chunks=[chunk]).check_against(self.snap)
        other_doc, other_chunk = self._doc_and_chunk(sha(b"other"))
        with pytest.raises(ValueError):
            ParseResult(documents=[other_doc], chunks=[other_chunk]).check_against(self.snap)
        with pytest.raises(ValueError):
            ParseResult(documents=[doc]).check_against(make_snapshot("jp-else", data=b"doc"))
        obs = make_observation("o1", "1", source_id="jp-test", dataset_key="jp-test:t", snapshot_sha256=self.snap.sha256)
        ParseResult(observations=[obs]).check_against(self.snap)
        late = obs.model_copy(update={"fetched_at": T0.replace(year=2027)})
        with pytest.raises(ValueError):
            ParseResult(observations=[late]).check_against(self.snap)

    def test_parse_record_failed_needs_error(self):
        with pytest.raises(ValidationError):
            ParseRecord(sha256=sha(b"x"), parser="p", version="1", status=ParseStatus.failed)


class TestCitations:
    snap = make_snapshot(data=b"law")
    lic = LicenseInfo(name="政府標準利用規約（第2.0版）", attribution="出典：e-Gov法令検索")

    @pytest.mark.parametrize("dates,basis", [
        ({"effective_at": date(2026, 10, 20), "published_at": date(2026, 1, 1)}, TimeBasis.effective),
        ({"published_at": date(2026, 1, 1)}, TimeBasis.published),
        ({}, TimeBasis.no_data_date),
    ])
    def test_chunk_time_basis(self, dates, basis):
        chunk = make_chunk("jp-test:d", self.snap.sha256, "art=1", "本文", **dates)
        cit = citation_for_chunk(chunk, self.snap, self.lic)
        assert cit.time_basis is basis
        assert (cit.resource_url, cit.fetched_at, cit.locator) == (self.snap.resource_url, self.snap.fetched_at, "art=1")
        assert (cit.license, cit.attribution, cit.usage_scope) == (self.lic.name, self.lic.attribution,
                                                                   UsageScope.internal_only)

    def test_chunk_wrong_snapshot(self):
        chunk = make_chunk("jp-test:d", sha(b"other"), "art=1", "本文")
        with pytest.raises(ValueError):
            citation_for_chunk(chunk, self.snap)

    def test_observation_period(self):
        obs = make_observation("o1", None, note="秘匿", source_id="jp-test", dataset_key="jp-test:t",
                               snapshot_sha256=self.snap.sha256)
        cit = citation_for_observation(obs, self.snap)
        assert cit.time_basis is TimeBasis.period and cit.period.label == "2020年"
        assert cit.license is None and cit.usage_scope is None


# ---------- 假执行器 ----------


class TestDriveCrawler:
    spec = make_spec(kind="fake", scope={"urls": [PAGE_A]})

    def test_follows_links_once_and_records_meta(self):
        manifest, raw = FakeManifest(), FakeRawStore()
        steps = drive_crawler(FakeCrawler(), self.spec, FakeHttpFetcher({PAGE_A: TEXT_A, PAGE_B: TEXT_B}),
                              manifest=manifest, raw=raw, now=T0)
        assert [s.task.url for s in steps] == [PAGE_A, PAGE_B]
        assert raw.get(sha(TEXT_A)) == TEXT_A
        assert manifest.snapshot_meta(steps[1].snapshot.snapshot_id) == {"role": "link"}

    def test_second_run_uses_conditional_request(self):
        manifest = FakeManifest()
        routes = {PAGE_A: FakeResponse(200, b"same", {"ETag": '"v1"'})}
        drive_crawler(FakeCrawler(), self.spec, FakeHttpFetcher(routes), manifest=manifest, now=T0)
        steps = drive_crawler(FakeCrawler(), self.spec, FakeHttpFetcher(routes), manifest=manifest, now=T0)
        assert steps[0].result.outcome is FetchOutcome.not_modified
        assert len(manifest.snapshots_for(self.spec.id)) == 2

    def test_failures_are_recorded(self):
        manifest = FakeManifest()
        steps = drive_crawler(FakeCrawler(), self.spec, FakeHttpFetcher({PAGE_A: FakeResponse(404)}), manifest=manifest)
        assert steps[0].snapshot.outcome is FetchOutcome.http_error
        assert manifest.failure_summary(self.spec.id).counts == {FetchOutcome.http_error: 1}

    def test_stops_at_max_files(self):
        spec = self.spec.model_copy(update={"max_files": 1})
        steps = drive_crawler(FakeCrawler(), spec, FakeHttpFetcher({PAGE_A: TEXT_A, PAGE_B: TEXT_B}))
        assert len(steps) == 1

    def test_classify_override(self):
        class Shell(FakeCrawler):
            def classify(self, task, result):
                return FetchOutcome.spa_shell

        steps = drive_crawler(Shell(), self.spec, FakeHttpFetcher({PAGE_A: b'<div id="root"></div>'}))
        assert steps[0].snapshot.outcome is FetchOutcome.spa_shell
        assert steps[0].snapshot.sha256 is not None  # 外壳本身留作证据

    def test_unregistered_url_is_a_test_bug(self):
        with pytest.raises(UnregisteredUrl):
            drive_crawler(FakeCrawler(), self.spec, FakeHttpFetcher({}))

    def test_runaway_pagination_detected(self):
        class Endless(FakeCrawler):
            def after_fetch(self, task, result, ctx):
                n = int(task.meta.get("n", 0)) + 1
                return [FetchTask(url=f"{BASE}/p?n={n}", meta={"n": n})]

        class AnyPage(FakeHttpFetcher):
            def fetch(self, url, etag=None, last_modified=None):
                self.routes.setdefault(url, FakeResponse(body=b"page"))
                return super().fetch(url, etag, last_modified)

        spec = self.spec.model_copy(update={"max_files": 10_000})
        with pytest.raises(AssertionError):
            drive_crawler(Endless(), spec, AnyPage({}), max_steps=50)

    def test_context(self):
        ctx = CrawlContext(spec=self.spec, manifest=FakeManifest(), raw=FakeRawStore(), now=T0, limit=5)
        assert [t.url for t in FakeCrawler().plan(ctx)] == [PAGE_A]

    def test_fake_parser_rejects_non_text(self):
        snap = make_snapshot(data=b"\xff\xfe")
        with pytest.raises(ParseError):
            FakeParser().parse(snap, b"\xff\xfe")


# ---------- 插件选择 ----------


def test_normalize_media_type():
    assert normalize_media_type("Application/JSON; charset=UTF-8") == "application/json"
    assert normalize_media_type(" ") is None and normalize_media_type(None) is None


def test_select_parser_exact_then_wildcard():
    exact, wild = type("Exact", (), {}), type("Wild", (), {})
    parsers = {("ckan", "application/json"): exact, ("ckan", "*"): wild}
    assert select_parser(parsers, "ckan", "application/json; charset=utf-8") is exact
    assert select_parser(parsers, "ckan", "text/csv") is wild
    assert select_parser(parsers, "ckan", None) is wild
    assert select_parser(parsers, "egov_law", "application/json") is None


# ---------- 插件自动发现（用 tmp 包模拟） ----------


@pytest.fixture
def make_pkg(tmp_path, monkeypatch):
    """在 tmp 里建一个插件包，返回包名；模块源码里的 {pkg} 换成包名。测试结束后从 sys.modules 清掉。"""
    created = []
    monkeypatch.syspath_prepend(str(tmp_path))

    def make(modules: dict[str, str]) -> str:
        name = f"plugpkg_{uuid.uuid4().hex[:8]}"
        root = tmp_path / name
        root.mkdir()
        (root / "__init__.py").write_text("")
        for mod, src in modules.items():
            (root / f"{mod}.py").write_text(textwrap.dedent(src).replace("{pkg}", name), encoding="utf-8")
        created.append(name)
        return name

    yield make
    for mod in [m for m in sys.modules if m.split(".")[0] in created]:
        del sys.modules[mod]


CRAWLER_ALPHA = """
    from datumoj.rikolto.core.contracts import Crawler

    class Base(Crawler):
        '''没有声明 kind 的基类，不算插件。'''

    class Alpha(Base):
        kind = "alpha"

        def plan(self, ctx):
            return []

    class Tweaked(Alpha):
        '''没有重新声明 kind，不算插件（否则会和 Alpha 撞键）。'''
"""
CRAWLER_BETA = """
    from datumoj.rikolto.core.contracts import Crawler
    from {pkg}.alpha import Alpha  # 导入进来的类不能重复计

    class Beta(Crawler):
        kind = "beta"

        def plan(self, ctx):
            return []

    class Gamma(Alpha):
        kind = "gamma"

    class NotAPlugin:
        kind = "not_a_crawler"
"""
CRAWLER_ALPHA_AGAIN = """
    from datumoj.rikolto.core.contracts import Crawler

    class AlphaAgain(Crawler):
        kind = "alpha"
"""
PARSER_JSON = """
    from datumoj.rikolto.core.contracts import Parser, ParseResult

    class LawJson(Parser):
        name = "law-json"
        version = "1"
        source_kind = "egov_law"
        content_type = "application/json"

        def parse(self, snapshot, data, meta=None):
            return ParseResult()
"""
PARSER_ANY = """
    from datumoj.rikolto.core.contracts import Parser, ParseResult

    class AnyTable(Parser):
        name = "any-table"
        version = "2"
        source_kind = "estat_files"
        content_type = "*"

        def parse(self, snapshot, data, meta=None):
            return ParseResult()
"""


class TestDiscoverCrawlers:
    def test_collects_declared_subclasses(self, make_pkg):
        pkg = make_pkg({"alpha": CRAWLER_ALPHA, "beta": CRAWLER_BETA, "_util": "HELPER = 1\n"})
        found = discover_crawlers(pkg)
        assert sorted(found) == ["alpha", "beta", "gamma"]
        assert found["alpha"].__module__ == f"{pkg}.alpha" and found["gamma"].__qualname__ == "Gamma"
        assert list(found["beta"]().plan(None)) == []

    def test_duplicate_kind(self, make_pkg):
        pkg = make_pkg({"alpha": CRAWLER_ALPHA, "alpha2": CRAWLER_ALPHA_AGAIN})
        with pytest.raises(PluginConflict) as exc:
            discover_crawlers(pkg)
        assert "alpha" in str(exc.value) and "Alpha" in str(exc.value) and "AlphaAgain" in str(exc.value)

    def test_broken_module_reported_clearly(self, make_pkg):
        pkg = make_pkg({
            "alpha": CRAWLER_ALPHA,
            "broken": "import does_not_exist_anywhere\n",
            "syntax": "def oops(:\n",
        })
        with pytest.raises(PluginError) as exc:
            discover_crawlers(pkg)
        msg = str(exc.value)
        assert f"{pkg}.broken" in msg and "does_not_exist_anywhere" in msg
        assert f"{pkg}.syntax" in msg and "SyntaxError" in msg

    def test_skip_broken_keeps_the_rest(self, make_pkg):
        pkg = make_pkg({"alpha": CRAWLER_ALPHA, "broken": "raise RuntimeError('boom')\n"})
        with pytest.warns(PluginWarning, match=r"broken.*boom"):
            found = discover_crawlers(pkg, skip_broken=True)
        assert list(found) == ["alpha"]

    def test_skip_broken_does_not_hide_conflicts(self, make_pkg):
        pkg = make_pkg({"alpha": CRAWLER_ALPHA, "alpha2": CRAWLER_ALPHA_AGAIN})
        with pytest.raises(PluginConflict):
            discover_crawlers(pkg, skip_broken=True)

    @pytest.mark.parametrize("kind", ['""', '"Bad Kind"', "123"])
    def test_invalid_kind(self, make_pkg, kind):
        pkg = make_pkg({"bad": f"""
            from datumoj.rikolto.core.contracts import Crawler

            class Bad(Crawler):
                kind = {kind}
        """})
        with pytest.raises(PluginError, match="Bad"):
            discover_crawlers(pkg)

    def test_missing_package(self):
        with pytest.raises(PluginError, match="no_such_plugin_pkg"):
            discover_crawlers("no_such_plugin_pkg")

    def test_project_packages_scan_cleanly(self):
        assert isinstance(discover_crawlers(), dict)
        assert isinstance(discover_parsers(), dict)


class TestDiscoverParsers:
    def test_collects_by_kind_and_content_type(self, make_pkg):
        pkg = make_pkg({"law": PARSER_JSON, "table": PARSER_ANY})
        found = discover_parsers(pkg)
        assert sorted(found) == [("egov_law", "application/json"), ("estat_files", "*")]
        assert select_parser(found, "estat_files", "text/csv; charset=utf-8").name == "any-table"
        assert select_parser(found, "egov_law", "Application/JSON").name == "law-json"
        assert select_parser(found, "egov_law", "text/xml") is None

    def test_duplicate_key(self, make_pkg):
        pkg = make_pkg({"law": PARSER_JSON, "law2": PARSER_JSON.replace("LawJson", "LawJsonToo")})
        with pytest.raises(PluginConflict, match="LawJsonToo"):
            discover_parsers(pkg)

    @pytest.mark.parametrize("old,new", [
        ('version = "1"', ""),
        ('name = "law-json"', 'name = " "'),
        ('content_type = "application/json"', 'content_type = "application/json; charset=utf-8"'),
        ('content_type = "application/json"', 'content_type = "Application/JSON"'),
        ('source_kind = "egov_law"', 'source_kind = "e-Gov"'),
    ])
    def test_invalid_declarations(self, make_pkg, old, new):
        pkg = make_pkg({"law": PARSER_JSON.replace(old, new)})
        with pytest.raises(PluginError, match="LawJson"):
            discover_parsers(pkg)


class TestFakeObservationReleases(ObservationReleaseSuite):
    @pytest.fixture
    def store(self):
        return FakeObservationStore()

    @pytest.fixture
    def publish(self):
        return lambda store, manifest: store.publish_generation(manifest)

    @pytest.fixture
    def discard(self):
        return lambda store, generation: store.discard_generation(generation)
