"""Round deadlines must retain partial data and emit only safe live progress."""

import io
import json
import logging
import sys

import pytest

from datumoj.rikolto import cloud, wiring
from datumoj.rikolto.core.schedule import Scheduler, write_control
from datumoj.rikolto.testing.samples import T0, make_spec
from tests.test_cloud_run import ENV, invoke, released
from tests.test_cloud_run import world as world  # noqa: PLC0414 -- Shared offline fixture.


class ProgressStream(io.StringIO):
    def __init__(self):
        super().__init__()
        self.flushed_lines = []

    def flush(self):
        self.flushed_lines.append(self.getvalue().splitlines()[-1])


def test_progress_bypasses_quiet_flushes_and_never_exposes_plugin_exception(world, monkeypatch):
    stream = ProgressStream()
    monkeypatch.setattr(sys, "__stderr__", stream)
    secrets = ("https://synthetic.invalid/private", "/synthetic/private/file", "synthetic-secret-122")
    original_build = wiring.build

    def build(root):
        deps = original_build(root)
        crawl = deps.run_crawl

        def run(spec, *args):
            assert json.loads(stream.flushed_lines[-1])["event"] == "source_start"
            assert json.loads(stream.flushed_lines[-1])["source_id"] == spec.id
            if spec.id == "jp-cloud1":
                text = " ".join(secrets)
                print(text)
                print(text, file=sys.stderr)
                logging.getLogger(__name__).error("%s", text)
                raise RuntimeError(text)
            return crawl(spec, *args)

        deps.run_crawl = run
        return deps

    monkeypatch.setattr(wiring, "build", build)
    result, _ = invoke(world)
    assert result["generation"] == 1 and result["exit_status"] == 1
    lines = stream.getvalue().splitlines()
    assert stream.flushed_lines == lines
    events = [json.loads(line) for line in lines]
    assert [row["event"] for row in events] == [
        "source_start", "source_end", "source_start", "source_end", "source_start", "source_end",
        "publish_start", "publish_end", "round_end",
    ]
    keys = {"event", "source_id", "elapsed_s", "new", "unchanged", "parsed", "failures", "stop_reason"}
    assert all(set(row) == keys for row in events)
    assert [row["elapsed_s"] for row in events] == sorted(row["elapsed_s"] for row in events)
    assert [row["source_id"] for row in events[:6]] == [spec.id for spec in world.specs for _ in range(2)]
    assert events[3]["failures"] == {"crawl_failed": 1}
    assert events[3]["stop_reason"] == "source_failed"
    assert events[-1]["failures"] == {"crawl_failed": 1}
    assert events[-1]["new"] == events[-1]["parsed"] == 2
    assert all(value not in stream.getvalue() for value in (*secrets, *ENV.values(), *world.tokens))
    released(world)


@pytest.mark.parametrize("enabled,schedule,expected", [(True, "daily", ["jp-retry"]),
                                                     (False, "daily", []), (True, "manual", [])])
def test_deadline_retry_respects_control_and_manual_schedule(tmp_path, enabled, schedule, expected):
    spec = make_spec("jp-retry", schedule=schedule)
    scheduler = Scheduler(tmp_path, [spec], lambda _: None, now=lambda: T0)
    write_control(tmp_path, {"enabled": True, "sources": {spec.id: {"enabled": enabled}}})
    record = {"source_id": spec.id, "run_id": "synthetic-deadline", "state": "final",
              "started_at": T0.isoformat(), "ended_at": T0.isoformat(), "status": "incomplete",
              "exit_status": 0, "new": 0, "unchanged": 0, "failures": {}, "stop_reason": "deadline"}
    scheduler.append_run(record)
    assert scheduler.due() == []  # Core's attempt-based scheduling is unchanged.
    assert cloud._due(scheduler, tmp_path) == expected
    scheduler.append_run({**record, "run_id": "synthetic-completed", "status": "completed", "stop_reason": None})
    assert cloud._due(scheduler, tmp_path) == []


@pytest.mark.parametrize("reserve", [-1, float("inf"), float("nan")])
def test_invalid_reserve_rejected_before_storage(tmp_path, monkeypatch, reserve):
    def build(_root):
        pytest.fail("Invalid budget must not reach storage")

    monkeypatch.setattr(wiring, "build_state", build)
    result = cloud.run(work_dir=tmp_path / "work", reserve_minutes=reserve)
    assert result["reason"] == "configuration_failed" and result["exit_status"] == 1
    assert not (tmp_path / "work").exists()
