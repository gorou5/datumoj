"""MAPLE m1: real cloud CLI, moto state, Lance vectors and in-memory MCP.

Only source discovery/parsing and embeddings are synthetic. HTTP uses replay;
all state, raw, SQLite, Parquet, vector and MCP implementations are production.
Evidence defaults to tmp_path; exporting it requires DATUMOJ_EVIDENCE_DIR.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
from datetime import UTC, datetime, timedelta
from functools import partial
from pathlib import Path

import boto3
import httpx
import pytest
from botocore.client import BaseClient
from botocore.httpsession import URLLib3Session
from mcp import Client
from moto import mock_aws
from typer.testing import CliRunner

from datumoj import cli, cloud, mcp_server, wiring
from datumoj.core import registry, schedule, state
from datumoj.core.http import PoliteHttpFetcher, Recorded, ReplayTransport
from datumoj.core.lease import LeaseManager
from datumoj.core.models import SourceText, TimeBasis
from datumoj.core.runner import CrawlDeps
from datumoj.core.state_s3 import S3StateObjectStore
from datumoj.core.storage import load_storage_config
from datumoj.testing.fakes import FakeCrawler, FakeEmbedder, FakeParser, FakeRegistry
from datumoj.testing.samples import make_observation, make_spec


ENV = {
    "R2_ACCESS_KEY_ID": "synthetic-maple-access",
    "R2_SECRET_ACCESS_KEY": "synthetic-maple-secret",
    "R2_ENDPOINT": "https://s3.amazonaws.com",
    "DATUMOJ_S3_BUCKET": "synthetic-maple-bucket",
    "DATUMOJ_S3_PREFIX": "synthetic-maple-prefix",
    "DATUMOJ_S3_REGION": "us-east-1",
    "DATUMOJ_RAW_BACKEND": "s3",
}
HOURLY, DAILY = "jp-hourly", "jp-daily"
SQL = "SELECT source_id, table_key, obs_id, value, note FROM observations ORDER BY source_id, obs_id"


class SyntheticParser(FakeParser):
    """A bulletin plus a tiny table with both a number and an annotated NULL."""

    def parse(self, snapshot, data, meta=None):
        parsed = super().parse(snapshot, data, meta)
        common = dict(
            source_id=snapshot.source_id, dataset_key=f"{snapshot.source_id}:population",
            table_key="population", locator="section=2", snapshot_sha256=snapshot.sha256,
            fetched_at=snapshot.fetched_at,
        )
        observations = [
            make_observation("population", data.decode().splitlines()[2], **common),
            make_observation("withheld", None, note="秘匿", **common),
        ]
        return parsed.model_copy(update={"observations": observations})


async def _answers(client):
    async def call(name, args=None):
        result = await client.call_tool(name, args or {})
        assert not result.is_error, result.content
        return result.structured_content.get("result", result.structured_content)

    search = await call("search_docs", {"queries": "合成人口", "sources": [HOURLY]})
    assert search["gap_state"] == "ok"
    hit = search["data"]["hits"][0]
    assert SourceText.model_validate(hit["snippet"]).content
    citation = hit["citation"]
    assert citation["time_basis"] == TimeBasis.no_data_date.value
    cite = await call("cite", {key: citation[key] for key in (
        "snapshot_sha256", "locator", "snapshot_id", "resource_url",
    )})
    assert cite["data"]["status"] == "ok"
    sql = await call("query_sql", {"sql": SQL})
    assert sql["gap_state"] == "ok" and sql["data"]["rows"]
    assert len(sql["data"]["provenance"]) == 4
    rows = [dict(zip(sql["data"]["columns"], (
        SourceText.model_validate(cell).content if isinstance(cell, dict) else cell for cell in row
    ), strict=True)) for row in sql["data"]["rows"]]
    assert all(row["value"] is None and row["note"] == "秘匿"
               for row in rows if row["obs_id"] == "withheld")
    assert {row["source_id"] for row in rows} == {HOURLY, DAILY}
    assert next(row for row in rows if row["source_id"] == HOURLY
                and row["obs_id"] == "population")["value"] == "124"
    return {"search_docs": search, "cite": cite, "query_sql": sql,
            "version": await call("version")}


def test_two_cloud_generations_pull_mcp_and_operational_guards(tmp_path, monkeypatch, caplog):
    for key, value in ENV.items():
        monkeypatch.setenv(key, value)
    for key in ("DATUMOJ_VECTORS", "DATUMOJ_VECTOR_LIMIT", "DATUMOJ_S3_ENDPOINT",
                "DATUMOJ_LEASE_TOKEN", "DATUMOJ_LEASE_HOLDER"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("GITHUB_RUN_ID", "synthetic-m1")
    clock = [datetime.now(UTC) - timedelta(hours=2)]

    class FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return clock[0].astimezone(tz)

    monkeypatch.setattr(state, "datetime", FrozenDatetime)
    monkeypatch.setattr(cloud, "utcnow", lambda: clock[0])
    scheduler_type = schedule.Scheduler
    monkeypatch.setattr(schedule, "Scheduler", partial(scheduler_type, now=lambda: clock[0]))
    monkeypatch.setattr(cloud, "Scheduler", partial(scheduler_type, now=lambda: clock[0]))

    def forbidden(*args, **kwargs):
        pytest.fail("m1 permits only ReplayTransport and moto, never network access")

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", forbidden)
    monkeypatch.setattr(URLLib3Session, "send", forbidden)
    specs = [make_spec(sid, schedule=interval, rate_per_sec=10000,
                       scope={"urls": [f"https://example.go.jp/{sid}.txt"]})
             for sid, interval in ((HOURLY, "hourly"), (DAILY, "daily"))]
    monkeypatch.setattr(registry, "load_registry", lambda _: FakeRegistry(specs))
    bodies = {}
    for spec in specs:
        body = tmp_path / f"{spec.id}.synthetic.txt"
        body.write_text("合成人口\n\n123", encoding="utf-8")
        bodies[spec.id] = body
    # A larger daily source makes the incremental byte comparison meaningful.
    padding = "\n\n".join(hashlib.sha256(str(i).encode()).hexdigest() * 128 for i in range(64))
    bodies[DAILY].write_text("合成日次資料\n\n123\n\n" + padding, encoding="utf-8")
    replay_calls, uploads, commands, tokens = [], [], [], []

    class ReplayFactory:
        @staticmethod
        def from_spec(spec):
            replay_calls.append(spec.id)
            transport = ReplayTransport({
                "https://example.go.jp/robots.txt": Recorded(status=404),
                spec.scope["urls"][0]: Recorded(bodies[spec.id], headers={"content-type": "text/plain"}),
            })
            return PoliteHttpFetcher.from_spec(
                spec, transport=transport, now=lambda: clock[0],
                clock=lambda: 0.0, sleep=lambda _: None,
            )

    embedder = FakeEmbedder()
    original_build = wiring.build

    overrides = {
        "registry": FakeRegistry(specs), "embedder": embedder,
        "crawlers": {"fake": FakeCrawler}, "parsers": {("fake", "text/plain"): SyntheticParser},
        "fetcher": ReplayFactory,
        "crawl_deps_type": partial(CrawlDeps, now=lambda: clock[0], clock=lambda: 0.0, sleep=lambda _: None),
    }
    monkeypatch.setattr(wiring, "build", lambda root: original_build(root, overrides))
    api_call = BaseClient._make_api_call

    def observed_call(self, operation, params):
        result = api_call(self, operation, params)
        if operation == "PutObject":
            body = params["Body"]
            if isinstance(body, bytes):
                size = len(body)
            else:
                position = body.tell()
                body.seek(0, 2)
                size = body.tell()
                body.seek(position)
            key = params["Key"].removeprefix(ENV["DATUMOJ_S3_PREFIX"] + "/")
            uploads.append({"key": key, "bytes": size})
        return result

    monkeypatch.setattr(BaseClient, "_make_api_call", observed_call)
    runner = CliRunner()

    def invoke(args, exit_code=0):
        result = runner.invoke(cli.app, args)
        assert all(value not in result.output for value in (
            ENV["R2_ACCESS_KEY_ID"], ENV["R2_SECRET_ACCESS_KEY"], ENV["R2_ENDPOINT"],
            ENV["DATUMOJ_S3_BUCKET"], *tokens, "合成人口", "合成日次資料",
        ))
        # Never export argv: it includes private temporary paths.
        commands.append({"command": args[0:2] if args[0] == "cloud" else args[:1],
                         "exit_code": result.exit_code, "output": result.output})
        assert result.exit_code == exit_code, result.output
        return json.loads(result.output)

    def run(name, exit_code=0):
        return invoke(["cloud", "run", "--work-dir", str(tmp_path / name)], exit_code)

    with mock_aws():
        client = boto3.client("s3", region_name="us-east-1", aws_access_key_id="testing",
                              aws_secret_access_key="testing")
        client.create_bucket(Bucket=ENV["DATUMOJ_S3_BUCKET"])
        store = S3StateObjectStore(load_storage_config(data_dir=tmp_path / "unused"))
        try:
            assert store.get("current.json") is None
            first = run("cloud-1")
            first_uploads = list(uploads)
            assert first["generation"] == 1 and first["status"] == "completed"
            assert first["due"] == first["new"] == first["parsed"] == 2
            assert set(replay_calls) == {HOURLY, DAILY}
            assert store.get("write-lease.json") is None
            embedded_first = embedder.embedded
            assert embedded_first == 68  # hourly: 2 chunks; daily: 2 + 64 chunks.

            clock[0] += timedelta(hours=1)
            bodies[HOURLY].write_text("合成人口\n\n124", encoding="utf-8")
            uploads.clear()
            replay_calls.clear()
            second = run("cloud-2")
            second_uploads = list(uploads)
            assert second["generation"] == 2 and second["status"] == "completed"
            assert second["due"] == second["new"] == second["parsed"] == 1
            assert replay_calls == [HOURLY]
            assert embedder.embedded - embedded_first == 2  # Only the new hourly document version.
            generations = state.StateManager(store, tmp_path / "unused").list()
            assert [g.generation for g in generations] == [2, 1]
            assert generations[0].previous_generation == 1
            assert generations[0].writer.kind == "actions"
            assert set(generations[0].last_successful_runs) == {HOURLY, DAILY}
            coverage = generations[0].vector_coverage
            assert coverage.embedded_chunks == 68 and coverage.pending_chunks == 0
            vector_keys = {c.key for c in generations[0].stores if c.name.startswith("vectors/")}
            assert vector_keys
            first_keys = {row["key"] for row in first_uploads}
            second_keys = {row["key"] for row in second_uploads}
            assert vector_keys.intersection(first_keys) - second_keys  # Immutable Lance data reused.
            assert not first_keys.intersection(second_keys) & {
                row["key"] for row in second_uploads if row["key"].startswith(("copies/", "raw/"))
            }
            sizes = [sum(row["bytes"] for row in batch) for batch in (first_uploads, second_uploads)]
            assert sizes[1] < sizes[0] / 2, sizes  # Actual successful PUT bytes, including raw/lease/report.
            report_key = second["weekly_report_key"]
            report_bytes = store.get(report_key)[0]
            assert report_key == first["weekly_report_key"] and report_bytes

            local = tmp_path / "local"
            writes_before_pull = len(uploads)
            pulled = invoke(["pull", "--data-dir", str(local)])
            assert pulled["generation"] == 2 and pulled["verified"]
            assert json.loads((local / "state-pull.json").read_text())["writer"]["kind"] == "actions"
            # Replicas deliberately serve keyword retrieval without an embedding
            # service. Compare the full MCP responses in that same supported mode.
            async def read_cloud():
                root = tmp_path / "cloud-2"
                monkeypatch.setenv("DATUMOJ_DATA_DIR", str(root))
                def factory():
                    return original_build(root, {**overrides, "vectors": None}).tools

                async with Client(mcp_server.create_server(factory)) as session:
                    answers = await _answers(session)
                return answers

            expected = asyncio.run(read_cloud())
            monkeypatch.setenv("DATUMOJ_DATA_DIR", str(local))

            async def read_local():
                async with Client(mcp_server.create_server(wiring.build_tools)) as session:
                    return await _answers(session)

            actual = asyncio.run(read_local())
            for name in ("search_docs", "cite", "query_sql"):
                assert actual[name] == expected[name], name
            assert actual["version"]["data"]["data_generation"] == 2
            assert actual["version"]["data"]["data_generated_at"] == generations[0].created_at.isoformat()
            assert actual["version"]["data"]["package"] == expected["version"]["data"]["package"]
            local_report = tmp_path / "weekly.md"
            report = runner.invoke(cli.app, ["report", "weekly", "--from-state", "--out", str(local_report)])
            assert report.exit_code == 0, report.output
            assert local_report.read_bytes() == report_bytes
            assert len(uploads) == writes_before_pull  # Pull/MCP/report never write to R2.

            before = store.get("current.json")
            leases = LeaseManager(store)
            lease = leases.acquire("local/synthetic-m1")
            tokens.append(lease.token)
            try:
                replay_calls.clear()
                skipped = run("cloud-skipped")
                assert skipped["status"] == "skipped" and skipped["generation"] is None
                assert not replay_calls and not (tmp_path / "cloud-skipped").exists()
                leases.check(lease)
                assert store.get("current.json") == before
            finally:
                leases.release(lease.holder, lease.token)

            clock[0] += timedelta(hours=1)
            bodies[HOURLY].write_text("合成人口\n\n125", encoding="utf-8")
            original_put = S3StateObjectStore.put_if_absent
            partial_uploads = []

            def fail_mid_upload(self, key, data):
                if key.startswith("copies/") and partial_uploads:
                    raise OSError(" ".join(ENV.values()))
                result = original_put(self, key, data)
                if key.startswith("copies/"):
                    partial_uploads.append(key)
                return result

            with monkeypatch.context() as patch:
                patch.setattr(S3StateObjectStore, "put_if_absent", fail_mid_upload)
                failed = run("cloud-failed", exit_code=1)
            assert partial_uploads and failed["reason"] == "publish_failed"
            assert failed["generation"] is None and store.get("current.json") == before
            assert [g.generation for g in state.StateManager(store, local).list()] == [2, 1]
            assert store.get("write-lease.json") is None
            assert invoke(["pull", "--data-dir", str(local)])["generation"] == 2
            assert asyncio.run(read_local()) == actual  # Neither new SQL values nor failed chunks leak.

            # Omit another scheduled round; status reads the pulled ledger and
            # generation metadata, rather than manufacturing a failed run.
            clock[0] += timedelta(hours=2, seconds=1)
            status = invoke(["schedule", "status"])
            hourly = next(row for row in status["sources"] if row["source_id"] == HOURLY)
            daily = next(row for row in status["sources"] if row["source_id"] == DAILY)
            assert "cloud_run_missing" in {alert["rule"] for alert in hourly["alerts"]}
            assert "cloud_run_missing" not in {alert["rule"] for alert in daily["alerts"]}
            assert "cloud_stalled" in {alert["rule"] for alert in status["cloud_alerts"]}
            assert status["cloud"]["generation"] == 2
            assert store.get(report_key)[0] == report_bytes

            evidence = {"commands": commands, "upload_bytes": sizes,
                        "first_uploads": first_uploads, "second_uploads": second_uploads,
                        "mcp_equal": ["search_docs", "cite", "query_sql"],
                        "mcp_generation": actual["version"]["data"]["data_generation"],
                        "embedded_chunks": coverage.embedded_chunks, "weekly_report_key": report_key,
                        "local_weekly_report_equal": True,
                        "lease_skipped": skipped["status"], "failed_round": failed["reason"],
                        "effective_generation_after_failure": 2,
                        "cloud_alerts": [a["rule"] for a in status["cloud_alerts"]]}
            rendered = json.dumps(evidence, ensure_ascii=False, indent=2) + "\n"
            assert all(value not in rendered + caplog.text for value in (
                ENV["R2_ACCESS_KEY_ID"], ENV["R2_SECRET_ACCESS_KEY"], ENV["R2_ENDPOINT"],
                ENV["DATUMOJ_S3_BUCKET"], *tokens, str(tmp_path), "合成人口", "合成日次資料",
            ))
            output = Path(os.environ.get("DATUMOJ_EVIDENCE_DIR") or tmp_path / "evidence")
            output.mkdir(parents=True, exist_ok=True)
            (output / "maple-m1.json").write_text(rendered, encoding="utf-8")
        finally:
            store._client.close()
            client.close()
