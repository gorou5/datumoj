from __future__ import annotations

import base64
import json
from datetime import date, timedelta
from pathlib import Path

import pytest

from datumoj.rikolto.core.contracts import CrawlContext
from datumoj.rikolto.core.models import FetchOutcome
from datumoj.rikolto.crawlers.egov_law import EgovLawCrawler, extract_law_xml, pending_amendments
from datumoj.rikolto.testing.contract_suites import CrawlerSuite
from datumoj.rikolto.testing.fakes import FakeHttpFetcher, FakeManifest, FakeRawStore, FakeResponse, drive_crawler
from datumoj.rikolto.testing.samples import T0, make_spec

H = timedelta(hours=1)

FIXTURES = Path(__file__).parents[2] / "tests" / "fixtures" / "egov_law"
API = "https://laws.e-gov.go.jp/api/2"
ASOF = "2026-10-20"  # T0 的 JST 日期，与样本的 asof 一致
IDS = ["321CONSTITUTION", "322AC0000000049", "428AC1000000103"]


def _fixture(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def _spec(page_size: int = 10, **kw):
    return make_spec(
        source_id="jp-egov-law", kind="egov_law", base_url=API, allowed_hosts=["laws.e-gov.go.jp"],
        scope={"page_size": page_size}, **kw,
    )


def _list_page(ids: list[str], revision: str = "r1") -> bytes:
    def entry(law_id: str) -> dict:
        info = {"law_revision_id": f"{law_id}_{revision}", "updated": "2026-01-01T00:00:00+09:00"}
        return {"law_info": {"law_id": law_id}, "revision_info": info, "current_revision_info": info}

    return json.dumps({"total_count": len(ids), "count": len(ids), "laws": [entry(i) for i in ids]}).encode()


def _list_url(offset: int, size: int = 10) -> str:
    return f"{API}/laws?limit={size}&offset={offset}"


def _data_url(law_id: str) -> str:
    return f"{API}/law_data/{law_id}?asof={ASOF}&response_format=json&law_full_text_format=xml"


def _routes(ids: list[str] = IDS, size: int = 10, revision: str = "r1") -> dict[str, bytes]:
    routes = {_list_url(0, size): _list_page(ids, revision), _list_url(len(ids), size): _fixture("laws_list.limit10.offset9570.empty.json")}
    for law_id in ids:
        routes[f"{API}/law_revisions/{law_id}"] = _fixture(f"law_revisions.{law_id}.json")
        routes[_data_url(law_id)] = _fixture(f"law_data.{law_id}.asof{ASOF}.xml-in-json.json")
    return routes


def _fetcher(routes) -> FakeHttpFetcher:
    return FakeHttpFetcher(routes, now=lambda: T0)


class TestEgovCrawlerContract(CrawlerSuite):
    @pytest.fixture
    def crawler(self):
        return EgovLawCrawler()

    @pytest.fixture
    def spec(self):
        return _spec()

    @pytest.fixture
    def make_fetcher(self):
        return lambda: _fetcher(_routes())


# ---------- extract_law_xml ----------


def test_extract_law_xml_returns_decoded_xml():
    xml = extract_law_xml(_fixture("law_data.321CONSTITUTION.asof2026-10-20.xml-in-json.json"))
    assert xml.lstrip().startswith(b"<")
    assert "日本国憲法".encode() in xml


@pytest.mark.parametrize(
    "raw",
    [
        b"",
        b"not json",
        b"[1, 2]",
        b'{"law_info": {}}',
        b'{"law_full_text": ""}',
        b'{"law_full_text": 5}',
        json.dumps({"law_full_text": base64.b64encode(b"plain text").decode()}).encode(),
        _fixture("law_data.synthetic.bad-base64.json"),
        _fixture("law_data.404.999AC0000000999.json"),
    ],
)
def test_extract_law_xml_rejects_broken_input(raw):
    with pytest.raises(ValueError):
        extract_law_xml(raw)


def test_extract_law_xml_not_found_message_names_code():
    with pytest.raises(ValueError, match="404004"):
        extract_law_xml(_fixture("law_data.404.999AC0000000999.json"))


# ---------- pending_amendments ----------


def _revisions(law_id: str) -> list[dict]:
    return json.loads(_fixture(f"law_revisions.{law_id}.json"))["revisions"]


def test_pending_amendments_lists_promulgated_but_not_enforced():
    pending = pending_amendments(_revisions("322AC0000000049"), date(2026, 10, 20))
    assert [(p["promulgate_date"], p["enforcement_date"]) for p in pending] == [
        ("2026-07-17", "2027-04-01"),
        ("2026-06-24", "2028-12-23"),
    ]
    assert pending[1]["amendment_law_num"] == "令和八年法律第四十六号"
    assert pending[1]["enforcement_comment"].startswith("民法等の一部を改正する法律")


def test_pending_amendments_depends_on_asof():
    revisions = _revisions("322AC0000000049")
    assert [p["enforcement_date"] for p in pending_amendments(revisions, date(2027, 4, 1))] == ["2028-12-23"]
    # 2026-07-01：2027-04-01 施行的那条还没公布（2026-07-17），不算“已公布”
    assert [p["enforcement_date"] for p in pending_amendments(revisions, date(2026, 7, 1))] == ["2028-12-23"]
    assert pending_amendments(revisions, date(2029, 1, 1)) == []


@pytest.mark.parametrize(
    ("law_id", "count"),
    [("321CONSTITUTION", 0), ("428AC1000000103", 0), ("421AC0000000066", 3), ("425AC0000000027", 6), ("415AC0000000057", 6)],
)
def test_pending_amendments_counts_match_fixture_readme(law_id, count):
    # 421 / 425 没有 CurrentEnforced 条目，只能用施行日和 asof 比较
    assert len(pending_amendments(_revisions(law_id), date(2026, 10, 20))) == count


def test_pending_amendments_undecided_enforcement_date():
    revisions = [
        {"amendment_law_num": "令和九年法律第一号", "amendment_promulgate_date": "2026-09-01",
         "amendment_enforcement_date": None, "amendment_enforcement_comment": "政令で定める日",
         "current_revision_status": "UnEnforced"},
        {"amendment_law_num": "古い", "amendment_promulgate_date": "2020-01-01",
         "amendment_enforcement_date": None, "current_revision_status": "PreviousEnforced"},
    ]
    assert pending_amendments(revisions, date(2026, 10, 20)) == [{
        "amendment_law_num": "令和九年法律第一号", "promulgate_date": "2026-09-01", "enforcement_date": None,
        "enforcement_comment": "政令で定める日", "amendment_law_title": None, "law_revision_id": None,
    }]


# ---------- 采集 ----------


def _drive(routes, *, manifest=None, raw=None, limit=None, now=T0, size=10, fetcher=None):
    fetcher = fetcher or _fetcher(routes)
    steps = drive_crawler(
        EgovLawCrawler(), _spec(size), fetcher, manifest=manifest, raw=raw, limit=limit, now=now,
    )
    return steps, fetcher


def test_plan_pages_list_then_fetches_each_law_current_xml():
    steps, fetcher = _drive(_routes(size=3), size=3)
    assert fetcher.requests[0] == _list_url(0, 3)
    assert _list_url(3, 3) in fetcher.requests  # 满页之后继续翻，遇到空页才停
    data_steps = [s for s in steps if s.task.meta["role"] == "law_data"]
    assert [s.task.meta["law_id"] for s in data_steps] == IDS
    assert all(s.snapshot.outcome is FetchOutcome.ok for s in steps)
    assert extract_law_xml(data_steps[0].result.content).lstrip().startswith(b"<")


def test_last_partial_page_stops_paging():
    routes = _routes(IDS[:2], size=5)
    routes.pop(_list_url(2, 5))  # 不登记下一页：翻页就会抛 UnregisteredUrl
    steps, fetcher = _drive(routes, size=5)
    assert _list_url(2, 5) not in fetcher.requests
    assert len([s for s in steps if s.task.meta["role"] == "law_data"]) == 2


def test_next_offset_key_is_not_needed():
    page = json.loads(_fixture("laws_list.limit10.offset9560.last.json"))
    assert "next_offset" not in page and page["count"] == 7
    ids = [e["law_info"]["law_id"] for e in page["laws"]]
    missing = FakeResponse(status=404, body=_fixture("law_revisions.404.428AC0000000103.json"))
    routes = {_list_url(0): _fixture("laws_list.limit10.offset9560.last.json")}
    routes.update({f"{API}/law_revisions/{i}": missing for i in ids})
    steps, fetcher = _drive(routes)  # 7 条 < 10：结束翻页，没有 next_offset 也不报错
    assert fetcher.requests == [_list_url(0)] + [f"{API}/law_revisions/{i}" for i in ids]
    assert all(s.snapshot.outcome is FetchOutcome.http_error for s in steps[1:])


def test_meta_has_asof_and_pending_amendments():
    steps, _ = _drive(_routes())
    meta = next(s.task.meta for s in steps if s.task.meta.get("law_id") == "322AC0000000049" and s.task.meta["role"] == "law_data")
    assert meta["role"] == "law_data" and meta["asof"] == ASOF
    assert [p["enforcement_date"] for p in meta["pending_amendments"]] == ["2027-04-01", "2028-12-23"]
    assert set(meta["pending_amendments"][0]) >= {"amendment_law_num", "promulgate_date", "enforcement_date", "enforcement_comment"}
    other = next(s.task.meta for s in steps if s.task.meta.get("law_id") == "321CONSTITUTION" and s.task.meta["role"] == "law_data")
    assert other["pending_amendments"] == []


def test_meta_is_stored_with_snapshot():
    manifest = FakeManifest()
    steps, _ = _drive(_routes(), manifest=manifest)
    step = next(s for s in steps if s.task.meta["role"] == "law_data")
    stored = manifest.snapshot_meta(step.snapshot.snapshot_id)
    assert stored["asof"] == ASOF and stored["law_id"] == step.task.meta["law_id"]


def test_asof_uses_jst_date_of_now():
    from datetime import datetime, timezone

    now = datetime(2026, 10, 19, 20, 0, tzinfo=timezone.utc)  # JST 已是 10-20 05:00
    steps, _ = _drive(_routes(), now=now)
    assert {s.task.meta["asof"] for s in steps if s.task.meta["role"] == "law_data"} == {ASOF}


def test_limit_caps_number_of_laws():
    routes = _routes()
    routes[_list_url(0, 2)] = routes[_list_url(0)]
    steps, fetcher = _drive(routes, limit=2)
    assert fetcher.requests[0] == _list_url(0, 2)
    assert [s.task.meta["law_id"] for s in steps if s.task.meta["role"] == "law_data"] == IDS[:2]
    assert not any(url.startswith(f"{API}/law_revisions/{IDS[2]}") for url in fetcher.requests)
    assert _list_url(2, 2) not in fetcher.requests


def test_incremental_skips_unchanged_laws_and_refetches_changed():
    manifest, raw = FakeManifest(), FakeRawStore()
    _drive(_routes(size=3), manifest=manifest, raw=raw, size=3)

    steps, fetcher = _drive(_routes(size=3), manifest=manifest, raw=raw, now=T0 + H, size=3)
    assert fetcher.requests == [_list_url(0, 3), _list_url(3, 3)]  # 没变：只翻列表，不抓历史和正文

    changed = _routes(size=3)
    page = json.loads(changed[_list_url(0, 3)])
    page["laws"][1]["current_revision_info"]["law_revision_id"] = "322AC0000000049_new"
    changed[_list_url(0, 3)] = json.dumps(page).encode()
    steps, fetcher = _drive(changed, manifest=manifest, raw=raw, now=T0 + 2 * H, size=3)
    assert [s.task.meta["law_id"] for s in steps if s.task.meta["role"] == "law_data"] == ["322AC0000000049"]
    assert f"{API}/law_revisions/321CONSTITUTION" not in fetcher.requests


def test_new_pending_amendment_alone_triggers_refetch():
    manifest, raw = FakeManifest(), FakeRawStore()
    _drive(_routes(), manifest=manifest, raw=raw)
    changed = _routes()
    page = json.loads(changed[_list_url(0)])
    page["laws"][0]["revision_info"]["law_revision_id"] = "321CONSTITUTION_future"  # 最新版变了，现行版没变
    changed[_list_url(0)] = json.dumps(page).encode()
    steps, _ = _drive(changed, manifest=manifest, raw=raw, now=T0 + H)
    assert [s.task.meta["law_id"] for s in steps if s.task.meta["role"] == "law_data"] == ["321CONSTITUTION"]


def test_single_law_404_does_not_affect_others():
    routes = _routes()
    routes[_data_url("322AC0000000049")] = FakeResponse(status=404, body=_fixture("law_data.404.999AC0000000999.json"))
    manifest, raw = FakeManifest(), FakeRawStore()
    steps, _ = _drive(routes, manifest=manifest, raw=raw)
    outcomes = {s.task.meta["law_id"]: s.snapshot.outcome for s in steps if s.task.meta["role"] == "law_data"}
    assert outcomes == {
        "321CONSTITUTION": FetchOutcome.ok,
        "322AC0000000049": FetchOutcome.http_error,
        "428AC1000000103": FetchOutcome.ok,
    }
    # 失败的不算已抓取：下次运行只重试它
    steps, _ = _drive(_routes(), manifest=manifest, raw=raw, now=T0 + H)
    assert [s.task.meta["law_id"] for s in steps if s.task.meta["role"] == "law_data"] == ["322AC0000000049"]


def test_revisions_404001_skips_that_law_only():
    routes = _routes()
    routes[f"{API}/law_revisions/322AC0000000049"] = FakeResponse(status=404, body=_fixture("law_revisions.404.428AC0000000103.json"))
    steps, fetcher = _drive(routes)
    assert _data_url("322AC0000000049") not in fetcher.requests
    assert [s.task.meta["law_id"] for s in steps if s.task.meta["role"] == "law_data"] == ["321CONSTITUTION", "428AC1000000103"]


@pytest.mark.parametrize(
    ("url", "body", "role"),
    [
        (_data_url("322AC0000000049"), "law_data.404.999AC0000000999.json", "law_data"),
        (f"{API}/law_revisions/322AC0000000049", "law_revisions.404.428AC0000000103.json", "law_revisions"),
    ],
)
def test_http_200_with_not_found_body_is_not_recorded_as_ok(url, body, role):
    routes = _routes()
    routes[url] = _fixture(body)  # HTTP 200，但正文是 404004 / 404001
    manifest, raw = FakeManifest(), FakeRawStore()
    steps, _ = _drive(routes, manifest=manifest, raw=raw)
    step = next(s for s in steps if s.task.url == url)
    assert step.snapshot.outcome is FetchOutcome.empty_body
    assert manifest.last_success(step.task.redacted_url) is None
    law_ids = {s.task.meta["law_id"]: s.snapshot.outcome for s in steps if s.task.meta["role"] == "law_data"}
    assert law_ids.get("321CONSTITUTION") is FetchOutcome.ok
    if role == "law_revisions":
        assert "322AC0000000049" not in law_ids
    else:
        # 没成功抓到正文，下次运行会重试
        steps, _ = _drive(_routes(), manifest=manifest, raw=raw, now=T0 + H)
        assert [s.task.meta["law_id"] for s in steps if s.task.meta["role"] == "law_data"] == ["322AC0000000049"]


def test_classify_leaves_normal_and_failed_results_alone():
    crawler = EgovLawCrawler()
    steps, _ = _drive(_routes())
    assert all(crawler.classify(s.task, s.result) is None for s in steps)


def test_invalid_law_id_in_list_is_rejected():
    routes = {_list_url(0): _list_page(["../evil"])}
    with pytest.raises(ValueError, match="law_id"):
        _drive(routes)


def test_plan_makes_first_list_task_only():
    ctx = CrawlContext(spec=_spec(), manifest=FakeManifest(), raw=FakeRawStore(), now=T0)
    assert [t.url for t in EgovLawCrawler().plan(ctx)] == [_list_url(0)]
