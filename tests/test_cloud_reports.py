"""Weekly cloud reports use an immutable R2 key and local replicas can rebuild them."""

from __future__ import annotations

import json
from contextlib import contextmanager
from datetime import UTC, date, datetime
from types import SimpleNamespace

import pytest
from moto import mock_aws
from typer.testing import CliRunner

from datumoj import cli, cloud
from datumoj.analysis.reports import previous_week_period, weekly_report
from datumoj.core.monitor import Alert
from datumoj.core.storage import load_storage_config
from datumoj.core.state_s3 import S3StateObjectStore
from datumoj.testing.fakes import FakeChangeLog, FakeChunkStore, FakeManifest, FakeRegistry
from datumoj.testing.samples import make_spec


_ENV = {
    "R2_ACCESS_KEY_ID": "report-test-access",
    "R2_SECRET_ACCESS_KEY": "report-test-secret",
    "R2_ENDPOINT": "https://s3.amazonaws.com",
    "DATUMOJ_S3_BUCKET": "report-test-bucket",
    "DATUMOJ_S3_PREFIX": "report-test-prefix",
    "DATUMOJ_S3_REGION": "us-east-1",
}


def _deps(data_dir):
    return SimpleNamespace(
        data_dir=data_dir,
        registry=FakeRegistry([make_spec("jp-test")]),
        manifest=FakeManifest(),
        chunks=FakeChunkStore(),
        change_log=FakeChangeLog(),
        require=lambda feature: None,
    )


def test_weekly_report_alert_order_is_stable(tmp_path):
    deps = _deps(tmp_path)
    alerts = [
        Alert(
            source_id="jp-test", rule="cloud_run_missing", severity="warning",
            message="超过一个 hourly 调度周期没有成功运行。",
            since=datetime(2026, 10, 20, tzinfo=UTC), details={"schedule": "hourly"},
        ),
        Alert(
            source_id="jp-test", rule="consecutive_failures", severity="error",
            message="连续运行失败。", since=datetime(2026, 10, 19, tzinfo=UTC), details={"count": 3},
        ),
    ]

    forward = weekly_report(deps, date(2026, 10, 19), date(2026, 10, 25), alerts)
    reversed_order = weekly_report(
        deps, date(2026, 10, 19), date(2026, 10, 25), list(reversed(alerts)),
    )

    assert forward == reversed_order


def test_previous_week_report_is_written_once_and_from_state_rebuilds_same_file(
    tmp_path, monkeypatch, caplog,
):
    for key, value in _ENV.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("DATUMOJ_RAW_BACKEND", "local")
    now = datetime(2024, 12, 30, 0, 0, tzinfo=UTC)
    replica = tmp_path / "replica"
    replica.mkdir()
    marker = {
        "generation": 3,
        "created_at": now.isoformat(),
        "pulled_at": now.isoformat(),
        "writer": {"kind": "actions", "run_id": "workflow-3"},
        "last_successful_runs": {},
    }
    (replica / "state-pull.json").write_text(json.dumps(marker), encoding="utf-8")
    deps = _deps(replica)
    secrets = tuple(_ENV.values())
    generation = SimpleNamespace(generation=3, created_at=now, last_successful_runs={})

    with cloud._quiet(), mock_aws():
        store = S3StateObjectStore(load_storage_config(data_dir=replica))
        store._client.create_bucket(Bucket=_ENV["DATUMOJ_S3_BUCKET"])
        key = cloud.publish_previous_week_report(store, deps, datetime.now(UTC), generation)
        first = store.get(key)
        assert first is not None
        first_bytes = first[0]

        # A later run with changed report inputs must leave the published object intact.
        deps.registry = FakeRegistry([make_spec("jp-changed")])
        assert cloud.publish_previous_week_report(store, deps, datetime.now(UTC), generation) == key
        assert store.get(key)[0] == first_bytes

        deps.registry = FakeRegistry([make_spec("jp-test")])
        monkeypatch.setattr(cli.wiring, "build", lambda _root: deps)
        local = CliRunner().invoke(
            cli.app,
            ["--data-dir", str(replica), "report", "weekly", "--from-state"],
        )
        start, end, week_key = previous_week_period(now.date())
        expected_path = replica / "reports" / "weekly" / f"{week_key}.md"

        assert key == f"reports/weekly/{week_key}.md"
        assert local.exit_code == 0, local.output
        assert local.output.strip() == str(expected_path)
        assert expected_path.read_bytes() == first_bytes
        report = first_bytes.decode()
        assert f"{start:%Y}\\-{start:%m}\\-{start:%d}" in report
        assert f"{end:%Y}\\-{end:%m}\\-{end:%d}" in report
        assert all(secret not in local.output for secret in secrets)
        assert all(secret not in caplog.text for secret in secrets)
        store._client.close()


@pytest.mark.parametrize("source_failed", [False, True])
def test_cloud_run_only_claims_weekly_key_after_a_successful_round(
    tmp_path, monkeypatch, source_failed,
):
    spec = make_spec("jp-test", schedule="hourly")
    deps = SimpleNamespace(
        registry=FakeRegistry([spec]), require=lambda _feature: None,
    )
    generation = SimpleNamespace(
        generation=1, created_at=datetime(2026, 10, 20, tzinfo=UTC), last_successful_runs={},
    )
    manager = SimpleNamespace(
        store=SimpleNamespace(get=lambda _key: None), writer=SimpleNamespace(kind="nas"),
        list=lambda: [], push=lambda: generation,
        before_write=None, before_publish=None,
    )

    class FakeLeases:
        def __init__(self, _store):
            pass

        def acquire(self, *_args, **_kwargs):
            return SimpleNamespace(holder="test", token="test")

        @contextmanager
        def maintain(self, _lease):
            yield lambda: None

        def renew(self, *_args, **_kwargs):
            return None

        def release(self, *_args, **_kwargs):
            return None

    class FakeScheduler:
        def __init__(self, _root, sources, _execute):
            self.sources = {source.id: source for source in sources}

        def due(self):
            return [spec.id]

    published = []
    # This unit test isolates weekly-report publication; S3 probing is covered
    # with real conditional operations in test_state_s3 and test_cloud_run.
    monkeypatch.setattr(cloud, "probe_conditional_writes", lambda _store: {"supported": True, "check": "complete"})
    monkeypatch.setattr(cloud.wiring, "build_state", lambda _root: manager)
    monkeypatch.setattr(cloud.wiring, "build", lambda _root: deps)
    monkeypatch.setattr(cloud, "LeaseManager", FakeLeases)
    monkeypatch.setattr(cloud, "Scheduler", FakeScheduler)
    monkeypatch.setattr(cloud, "_source", lambda *_args: ({
        "source_id": spec.id, "exit_status": int(source_failed), "failures": {},
        "new": 0, "unchanged": 0, "parsed": 0,
    }, 100))
    monkeypatch.setattr(cloud, "_recover", lambda *_args: None)
    monkeypatch.setattr(cloud, "maintain_vectors", lambda *_args: None)
    monkeypatch.setattr(
        cloud, "publish_previous_week_report",
        lambda *_args: published.append("reports/weekly/2026-42.md") or "reports/weekly/2026-42.md",
    )

    result = cloud.run(work_dir=tmp_path / "work")

    assert result["status"] == ("failed" if source_failed else "completed")
    assert result["weekly_report_key"] == (None if source_failed else "reports/weekly/2026-42.md")
    assert published == ([] if source_failed else ["reports/weekly/2026-42.md"])


def test_pull_cli_persists_generation_monitor_metadata(tmp_path, monkeypatch):
    root = tmp_path / "replica"
    pulled_at = datetime(2026, 10, 22, 0, 10, tzinfo=UTC)
    def pull_state(data_dir, selected_generation):
        data_dir.mkdir(parents=True, exist_ok=True)
        (data_dir / "state-pull.json").write_text(json.dumps({
            "generation": 4, "created_at": "2026-10-22T00:00:00Z",
            "pulled_at": pulled_at.isoformat(),
            "writer": {"kind": "nas", "run_id": "writer-run-4"},
            "last_successful_runs": {
                "jp-test": {"run_id": "source-run-3", "finished_at": "2026-10-22T00:00:00Z"},
            },
        }), encoding="utf-8")
        return {"generation": 4, "created_at": "2026-10-22T00:00:00Z", "pulled_at": pulled_at.isoformat()}

    spec = make_spec("jp-test", schedule="hourly")
    scheduler = SimpleNamespace(
        data_dir=root, sources={spec.id: spec}, now=lambda: pulled_at,
        status=lambda: {"enabled": True, "sources": [{
            "source_id": spec.id, "effective_enabled": True, "alerts": [],
        }], "alert_count": 0},
        _read_history=lambda: ([], []),
    )

    monkeypatch.setattr(cli, "pull_state", pull_state)
    monkeypatch.setattr(cli, "_scheduler_at", lambda *_args, **_kwargs: (None, scheduler))

    result = CliRunner().invoke(cli.app, ["pull", "--data-dir", str(root)])

    assert result.exit_code == 0, result.output
    marker = json.loads((root / "state-pull.json").read_text(encoding="utf-8"))
    assert marker["writer"] == {"kind": "nas", "run_id": "writer-run-4"}
    assert marker["last_successful_runs"]["jp-test"]["run_id"] == "source-run-3"
    payload = json.loads(result.output)
    assert payload["cloud"]["writer"] == {"kind": "nas"}
    assert payload["cloud"]["last_successful_runs"] == {
        "jp-test": {"finished_at": "2026-10-22T00:00:00+00:00"},
    }
    assert payload["cloud"]["pulled_at"] == pulled_at.isoformat()
