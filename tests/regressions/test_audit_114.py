"""D-02: observation tables retain the newest successfully parsed snapshot."""

from datetime import timedelta
from decimal import Decimal

import pyarrow.parquet as pq
import pytest

from datumoj.analysis.tools import AnalysisTools
from datumoj.core.contracts import ParseError
from datumoj.core.manifest import SqliteManifest
from datumoj.core.models import PublishState
from datumoj.core.state import StateManager
from datumoj.pipeline import PipelineDeps, parse_pending
from datumoj.store.chunks import SqliteChunkStore
from datumoj.store.observations import DuckDBObservationStore
from datumoj.testing.fakes import (
    FakeCatalogStore, FakeKeywordIndex, FakeParser, FakeRawStore, FakeRegistry,
    FakeRetriever, FakeStateObjectStore,
)
from datumoj.testing.samples import T0, make_observation, make_snapshot, make_spec

SOURCE = "jp-test"
TABLE = "version.txt"
KEY = f"{SOURCE}:{TABLE}"
URL = f"https://example.go.jp/{TABLE}"


@pytest.fixture
def env(tmp_path):
    class Parser(FakeParser):
        fail_ids = set()
        multiplier = 1

        def parse(self, snapshot, data, meta=None):
            if snapshot.snapshot_id in self.fail_ids:
                raise ParseError("synthetic parse failure")
            result = super().parse(snapshot, data, meta)
            observation = make_observation(
                "value", str(Decimal(data.decode()) * self.multiplier),
                source_id=SOURCE, dataset_key=f"{SOURCE}:ds", table_key=TABLE,
                snapshot_sha256=snapshot.sha256, fetched_at=snapshot.fetched_at,
            )
            return result.model_copy(update={"observations": [observation]})

    root = tmp_path / "data"
    root.mkdir()
    deps = PipelineDeps(
        manifest=SqliteManifest(root / "manifest.sqlite"),
        chunks=SqliteChunkStore(root / "chunks.sqlite"),
        keyword=FakeKeywordIndex(), raw=FakeRawStore(),
        observations=DuckDBObservationStore(root), catalog=FakeCatalogStore(),
        registry=FakeRegistry([make_spec()]), parsers={("fake", "text/plain"): Parser},
    )
    yield deps, Parser, root
    deps.manifest.close()
    deps.chunks.close()


def add(deps, sid, value, seconds, *, url=URL, source_id=SOURCE):
    data = value.encode()
    deps.raw.put(data)
    snapshot = make_snapshot(snapshot_id=sid, data=data, fetched_at=T0 + timedelta(seconds=seconds),
                             url=url, source_id=source_id)
    deps.manifest.record_snapshot(snapshot)
    return snapshot


def assert_value(deps, snapshot, value, root):
    tools = AnalysisTools(
        retriever=FakeRetriever(deps.chunks, deps.manifest, deps.registry), catalog=deps.catalog,
        observations=deps.observations, chunks=deps.chunks, manifest=deps.manifest,
        raw=deps.raw, registry=deps.registry, parsers=deps.parsers, data_dir=root,
    )
    result = tools.query_sql("SELECT source_id, table_key, obs_id, value FROM observations")
    assert result["data"]["rows"][0][-1] == value
    assert result["data"]["provenance"][0]["citation"]["snapshot_sha256"] == snapshot.sha256
    provenance = deps.observations.query_readonly("SELECT obs_id FROM observations").provenance
    assert len(provenance) == 1 and provenance[0].snapshot_sha256 == snapshot.sha256
    assert deps.observations.table_order(SOURCE, TABLE) == deps.manifest.snapshot_order(snapshot.snapshot_id)


def test_failed_old_snapshot_retry_does_not_replace_new_table(env):
    deps, parser, root = env
    a = add(deps, "A", "1", 0)
    parser.fail_ids = {"A"}
    assert "A" in parse_pending(deps).failed
    b = add(deps, "B", "2", 1)
    assert "B" in parse_pending(deps).parsed
    parser.fail_ids.clear()
    summary = parse_pending(deps)
    assert summary.parsed == ["A"] and not summary.failed
    assert_value(deps, b, "2", root)
    assert deps.chunks.get_document(KEY).snapshot_sha256 == b.sha256
    assert deps.chunks.get_document(KEY, a.sha256) is not None
    assert deps.manifest.publish_state("A") is PublishState.published


def test_same_snapshot_new_parser_can_rewrite_observations(env):
    deps, parser, root = env
    b = add(deps, "B", "2", 1)
    assert parse_pending(deps).parsed == ["B"]
    parser.version = "2"
    parser.multiplier = 10
    assert parse_pending(deps).parsed == ["B"]
    assert_value(deps, b, "20", root)


def test_legacy_table_recovers_maximum_same_source_order_from_rows(env):
    deps, parser, root = env
    a = add(deps, "A", "1", 0)
    b = add(deps, "B", "2", 1)
    assert not parse_pending(deps).failed
    path = deps.observations.path_for(SOURCE, TABLE)
    # 模拟升级前的文件：行仍属于 B，但没有任何 datumoj 水位元数据。
    table = pq.read_table(path).replace_schema_metadata(None)
    pq.write_table(table, path)
    assert deps.observations.table_order(SOURCE, TABLE) is None
    # 同 SHA 的重复抓取取最大顺序；其他来源的更晚记录不得参与水位。
    b_again = add(deps, "B-again", "2", 2)
    add(deps, "foreign", "2", 100, source_id="jp-other")
    parser.fail_ids = {"B", "B-again"}
    parser.version = "2"
    summary = parse_pending(deps, SOURCE)
    assert "A" in summary.parsed
    assert_value(deps, b_again, "2", root)
    assert deps.chunks.get_document(KEY, a.sha256) is not None


@pytest.mark.parametrize("lookup_fails", [False, True])
def test_unrecoverable_legacy_table_only_allows_latest_resource_snapshot(env, monkeypatch, caplog, lookup_fails):
    deps, parser, root = env
    add(deps, "A", "1", 0)
    c = add(deps, "C", "3", 2)
    # 同来源另一个资源的更晚快照不应阻止 C。
    add(deps, "other-resource", "4", 100, url="https://example.go.jp/other.txt")
    legacy = make_observation("legacy", "2", source_id=SOURCE, dataset_key=f"{SOURCE}:ds", table_key=TABLE)
    deps.observations.write_table(SOURCE, TABLE, [legacy])
    original = deps.observations.write_table
    writes = []

    def track(source_id, table_key, observations, order=None):
        writes.append(order)
        return original(source_id, table_key, observations, order)

    monkeypatch.setattr(deps.observations, "write_table", track)
    if lookup_fails:
        def fail_read(*args, **kwargs):
            raise RuntimeError("synthetic read failure")

        monkeypatch.setattr(deps.observations, "query_readonly", fail_read)
    parser.fail_ids = {"other-resource"}
    summary = parse_pending(deps)
    assert {"A", "C"} <= set(summary.parsed)
    assert writes == [deps.manifest.snapshot_order("C")]
    assert any("跳过写入" in record.message and TABLE in record.message for record in caplog.records)
    monkeypatch.undo()
    assert_value(deps, c, "3", root)


@pytest.mark.parametrize("delete", [False, True])
def test_state_generation_push_restore_preserves_table_order(env, tmp_path, delete):
    deps, _, root = env
    a = add(deps, "A", "1", 0)
    b = add(deps, "B", "2", 1)
    assert not parse_pending(deps).failed
    order = deps.manifest.snapshot_order("B")
    if delete:
        deps.observations.write_table(SOURCE, TABLE, [], order=order)
    for sha, data in deps.raw.blobs.items():
        path = root / "raw" / sha[:2] / sha
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    manager = StateManager(FakeStateObjectStore(), root)
    generation = manager.push()
    assert any(copy.name.startswith("parquet/") for copy in generation.stores)
    target = tmp_path / "restored"
    assert manager.restore(target)["verified"]
    restored = DuckDBObservationStore(target)
    assert restored.table_order(SOURCE, TABLE) == order
    assert restored.query_readonly("SELECT value FROM observations").rows == ([] if delete else [(Decimal(2),)])
    assert not restored.write_table(SOURCE, TABLE, [], order=deps.manifest.snapshot_order(a.snapshot_id))
