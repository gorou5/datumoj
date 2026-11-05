"""Reproduce the JMA BudgetExceeded that keeps coming back round after round (offline, no network).

Usage:  uv run --frozen python comms/repro_jma_budget.py

What it shows, in three parts:
  A. JMA's own parse time: real fixtures (4.6 MB long feed, 686 KB largest entry) take far less than 60 s.
  B. The timed window covers the raw read: one slow raw.get() puts budget_exceeded:duration_seconds on a
     snapshot that parses quickly. Scaled down: max_seconds=0.5 stands in for 60 s, a 0.6 s sleep stands
     in for a slow S3 GET.
  C. The failure is permanent and gets reported again every round: once the read is fast again, later
     rounds call neither the parser nor raw.get, yet summary.failed still holds the snapshot. cloud._source
     then marks every jp-jma run failed (parse_index_failed=1, exit_status=1). After 3 runs,
     monitor.evaluate_alerts raises consecutive_failures. The change log has only one parse_failed event.
"""

from __future__ import annotations

import time
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

from datumoj.rikolto import cloud
from datumoj.rikolto.core.models import ChangeKind
from datumoj.rikolto.core.monitor import evaluate_alerts
from datumoj.rikolto.parsers.jma_xml import JmaXmlParser
from datumoj.rikolto.pipeline import PipelineDeps, parse_pending
from datumoj.rikolto.testing.fakes import (
    FakeCatalogStore,
    FakeChangeLog,
    FakeChunkStore,
    FakeKeywordIndex,
    FakeManifest,
    FakeObservationStore,
    FakeRawStore,
    FakeRegistry,
)
from datumoj.rikolto.testing.samples import T0, make_snapshot, make_spec

FIXTURES = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "jma"
ENTRY = "20261020112204_0_VPWP50_050000.xml"
ENTRY_URL = f"https://www.data.jma.go.jp/developer/xml/data/{ENTRY}"


def part_a() -> None:
    print("== A. JmaXmlParser 实测解析耗时（真实 fixture） ==")
    parser = JmaXmlParser()
    for name, meta in (("regular_l.snapshot1.xml", None), (ENTRY, {"entry_id": ENTRY_URL})):
        data = (FIXTURES / name).read_bytes()
        url = "https://www.data.jma.go.jp/developer/xml/feed/regular_l.xml" if meta is None else ENTRY_URL
        snap = make_snapshot(source_id="jp-jma", url=url, data=data, content_type="application/xml")
        started = time.perf_counter()
        for _ in range(5):
            parser.parse(snap, data, meta)
        print(f"  {name:<40} {len(data):>9} B  {(time.perf_counter() - started) / 5 * 1000:8.1f} ms/次")


class SlowOnceRaw(FakeRawStore):
    """Only the first get() is slow, like a single S3 GET that stalls or trickles."""

    def __init__(self, delay: float):
        super().__init__()
        self.delay, self.gets = delay, 0

    def get(self, sha256: str) -> bytes:
        self.gets += 1
        if self.gets == 1:
            time.sleep(self.delay)
        return super().get(sha256)


class CountingJma(JmaXmlParser):
    calls = 0

    def parse(self, snapshot, data, meta=None):
        type(self).calls += 1
        return super().parse(snapshot, data, meta)


def part_b_c() -> None:
    spec = make_spec(
        "jp-jma", kind="jma_feed", allowed_hosts=["www.data.jma.go.jp"], schedule="hourly",
        # 0.5 s stands in for the default 60 s. The scope override is only for scaling; without it the
        # pipeline still uses ParseBudget() (60 s), and the mechanism is the same.
        scope={"feeds": ["https://www.data.jma.go.jp/developer/xml/feed/regular_l.xml"],
               "parse_budget": {"max_seconds": 0.5}},
    )
    raw, manifest, changelog = SlowOnceRaw(delay=0.6), FakeManifest(), FakeChangeLog()
    deps = PipelineDeps(
        manifest=manifest, raw=raw, registry=FakeRegistry([spec]), chunks=FakeChunkStore(),
        keyword=FakeKeywordIndex(), observations=FakeObservationStore(), catalog=FakeCatalogStore(),
        change_log=changelog, parsers={("jma_feed", "*"): CountingJma},
    )
    data = (FIXTURES / ENTRY).read_bytes()
    raw.put(data)
    snap = make_snapshot(source_id="jp-jma", url=ENTRY_URL, data=data, content_type="application/xml",
                         snapshot_id="jma-entry-1")
    manifest.record_snapshot(snap, {"resource_type": "entry", "entry_id": ENTRY_URL})

    print("\n== B. 慢读取计入解析预算 ==")
    first = parse_pending(deps, "jp-jma")
    print(f"  第 1 轮 failed={first.failed}  parser 调用={CountingJma.calls}  raw.get={raw.gets}")

    print("\n== C. 粘滞失败逐轮重复计数（之后 raw 已恢复快速） ==")
    ledger: list[dict] = []
    stub = SimpleNamespace(
        crawler_classes={"jma_feed": lambda: None}, fetcher_for=lambda spec: None,
        run_crawl=lambda *a, **k: SimpleNamespace(new=0, unchanged=1, failures={}),
        crawl_deps_type=lambda **kw: None, parse_pending=parse_pending, pipeline_deps=deps, manifest=manifest, raw=raw,
    )
    scheduler = SimpleNamespace(append_run=lambda record: ledger.append(dict(record)))
    clock = [T0]
    original_utcnow = cloud.utcnow
    cloud.utcnow = lambda: clock[0]
    try:
        for round_no in range(2, 5):
            clock[0] = T0 + timedelta(hours=round_no)
            record, _ = cloud._source(stub, spec, scheduler, None)
            print(f"  第 {round_no} 轮 cloud._source: status={record['status']} exit={record['exit_status']} "
                  f"failures={record['failures']}  parser 调用累计={CountingJma.calls}  raw.get 累计={raw.gets}")
    finally:
        cloud.utcnow = original_utcnow

    finals = [row for row in ledger if row["state"] == "final"]
    alerts = evaluate_alerts(spec, finals, manifest, T0 + timedelta(hours=5), enabled=True)
    events = changelog.list(kinds=[ChangeKind.parse_failed])
    print(f"  monitor 提醒: {[a.rule for a in alerts]}")
    print(f"  change_log 中 parse_failed 事件数: {len(events)}（失败只真正发生过 1 次）")


if __name__ == "__main__":
    part_a()
    part_b_c()
