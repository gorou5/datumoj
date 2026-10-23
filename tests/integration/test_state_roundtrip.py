"""Synthetic offline crawl → parse/index → R2/moto → verified new-directory restore."""

from __future__ import annotations

import json
import os
from datetime import timedelta
from pathlib import Path

import boto3
import httpx
from moto import mock_aws

from datumoj import wiring
from datumoj.core.contracts import ParseResult
from datumoj.core.http import PoliteHttpFetcher, Recorded, ReplayTransport
from datumoj.core.runner import CrawlDeps
from datumoj.core.state import StateManager
from datumoj.core.state_s3 import S3StateObjectStore
from datumoj.core.storage import StorageConfig
from datumoj.testing.fakes import FakeCrawler, FakeParser, FakeRegistry
from datumoj.testing.samples import T0, make_observation, make_spec


class SyntheticParser(FakeParser):
    """The second paragraph is also a tiny synthetic statistical table."""

    def parse(self, snapshot, data, meta=None):
        parsed = super().parse(snapshot, data, meta)
        return ParseResult(
            documents=parsed.documents, chunks=parsed.chunks,
            observations=[make_observation(
                "synthetic-population", data.decode().splitlines()[-1], source_id=snapshot.source_id,
                dataset_key=f"{snapshot.source_id}:population", table_key="a.txt", locator="section=2",
                snapshot_sha256=snapshot.sha256, fetched_at=snapshot.fetched_at,
            )],
        )


class CountingS3(S3StateObjectStore):
    def __init__(self, config):
        super().__init__(config)
        self.gets, self.uploads, self.lists = [], [], []

    def get(self, key):
        self.gets.append(key)
        return super().get(key)

    def put_if_absent(self, key, data):
        result = super().put_if_absent(key, data)
        self.uploads.append((key, len(data)))
        return result

    def list(self, prefix=""):
        self.lists.append(prefix)
        return super().list(prefix)

    def reset(self):
        self.gets.clear()
        self.uploads.clear()
        self.lists.clear()


def _close(deps):
    for name in ("manifest", "chunks", "keyword", "catalog", "change_log"):
        getattr(deps, name).close()


def _results(deps):
    hits = deps.tools.search_docs("合成人口", sources=["jp-test"])
    assert hits["data"]["hits"]
    chunk = next(c for c in deps.chunks.iter_chunks(["jp-test"]) if c.locator == "section=2")
    snapshot = next(s for s in deps.manifest.snapshots_by_sha256(chunk.snapshot_sha256) if s.source_id == "jp-test")
    cite = deps.tools.cite(chunk.snapshot_sha256, chunk.locator, snapshot_id=snapshot.snapshot_id)
    assert cite["data"]["status"] == "ok"
    sql = deps.tools.query_sql("SELECT source_id, table_key, obs_id, value FROM observations ORDER BY source_id")
    assert sql["data"]["rows"]
    # Compare complete JSON tool results, including evidence and missing dates.
    return json.loads(json.dumps({"search": hits, "cite": cite, "query_sql": sql}, default=str))


def test_state_roundtrip_and_incremental_second_source(tmp_path, monkeypatch, caplog):
    monkeypatch.setenv("R2_ACCESS_KEY_ID", "synthetic-roundtrip-id")
    monkeypatch.setenv("R2_SECRET_ACCESS_KEY", "synthetic-roundtrip-secret")
    monkeypatch.setenv("DATUMOJ_VECTORS", "off")

    def forbid_network(*args, **kwargs):
        raise AssertionError("all HTTP must use ReplayTransport")

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", forbid_network)
    specs = [make_spec(sid, scope={"urls": [f"https://example.go.jp/{sid}/a.txt"]})
             for sid in ("jp-test", "jp-other")]
    overrides = {"registry": FakeRegistry(specs), "vectors": None,
                 "parsers": {("fake", "text/plain"): SyntheticParser}}
    original = wiring.build(tmp_path / "original", overrides)
    restored = None
    evidence = {}
    try:
        def crawl(spec, value, day):
            body = tmp_path / f"{spec.id}-{day}.synthetic.txt"
            body.write_text(f"合成人口\n\n{value}")
            replay = ReplayTransport({
                "https://example.go.jp/robots.txt": Recorded(status=404),
                spec.scope["urls"][0]: Recorded(body, headers={"content-type": "text/plain"}),
            })
            fetcher = PoliteHttpFetcher.from_spec(
                spec, transport=replay, now=lambda: T0 + timedelta(days=day),
                clock=lambda: 0.0, sleep=lambda _: None,
            )
            try:
                result = original.run_crawl(spec, FakeCrawler(), CrawlDeps(
                    raw=original.raw, manifest=original.manifest, fetcher=fetcher,
                    clock=lambda: 0.0, sleep=lambda _: None,
                ))
                assert result.new == 1 and not result.failures
            finally:
                fetcher.close()
            parsed = original.parse_pending(original.pipeline_deps, spec.id)
            assert len(parsed.parsed) == 1 and not parsed.failed

        for spec in specs:
            crawl(spec, "123", 0)
        assert original.reindex(original.pipeline_deps) == 4
        expected = _results(original)
        with mock_aws():
            client = boto3.client("s3", region_name="us-east-1", aws_access_key_id="testing", aws_secret_access_key="testing")
            client.create_bucket(Bucket="synthetic-roundtrip-bucket")
            config = StorageConfig(s3_endpoint="https://s3.amazonaws.com", s3_bucket="synthetic-roundtrip-bucket",
                                   s3_region="us-east-1", s3_prefix="state-test")
            store = CountingS3(config)
            try:
                manager = StateManager(store, original.data_dir)
                first = manager.push()
                first_keys = {key for key, _ in store.uploads}
                report = manager.restore(tmp_path / "restored")
                assert report["sampled_chunks"] == 4 and report["sampled_observations"] == 2
                restored = wiring.build(tmp_path / "restored", overrides)
                actual = _results(restored)
                assert actual == expected
                store.reset()
                # One of two sources changes; only its raw and derived stores change.
                crawl(specs[0], "124", 1)
                second = manager.push()
                uploads = list(store.uploads)
                assert store.lists == ["copies/sha256/", "raw/"]
                assert not any(key.startswith(("copies/", "raw/")) for key in store.gets)
                changed = {c.key for c in second.stores} - {c.key for c in first.stores}
                assert {key for key, _ in uploads if key.startswith("copies/")} == changed
                assert len([key for key, _ in uploads if key.startswith("raw/")]) == 1
                assert not first_keys.intersection(key for key, _ in uploads)
                untouched = next(c for c in first.stores if c.name.startswith("parquet/jp-other/"))
                assert untouched in second.stores and untouched.key not in changed
                store.reset()
                manager.push()
                assert store.lists == ["copies/sha256/", "raw/"]
                assert not any(key.startswith(("copies/", "raw/")) for key in store.gets)
                assert not any(key.startswith(("copies/", "raw/")) for key, _ in store.uploads)
                store.reset()
                remote = manager.restore(tmp_path / "remote", raw="remote")
                assert remote["verified"] and remote["raw_mode"] == "remote"
                assert not any(key.startswith("raw/") for key in store.gets)
                assert not (tmp_path / "remote" / "raw").exists()
                evidence = {"restore": report, "equal_results": actual == expected,
                            "second_generation": second.generation, "second_uploads": uploads,
                            "remote_restore": remote}
            finally:
                store._client.close()
                client.close()
        rendered = json.dumps(evidence) + caplog.text
        assert all(secret not in rendered for secret in (
            "synthetic-roundtrip-id", "synthetic-roundtrip-secret", "https://s3.amazonaws.com",
            "synthetic-roundtrip-bucket",
        ))
        # Default evidence never touches tracked results/ or fixture paths.
        output = Path(os.environ.get("DATUMOJ_EVIDENCE_DIR", str(tmp_path / "evidence")))
        output.mkdir(parents=True, exist_ok=True)
        (output / "state-roundtrip.json").write_text(json.dumps(evidence, indent=2) + "\n")
    finally:
        _close(original)
        if restored is not None:
            _close(restored)
