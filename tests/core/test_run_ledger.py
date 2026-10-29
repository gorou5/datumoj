from __future__ import annotations

import json
import threading
from datetime import timedelta
from types import SimpleNamespace

from starlette.testclient import TestClient

from datumoj.rikolto.admin import create_app
from datumoj.rikolto.core.monitor import evaluate_alerts
from datumoj.rikolto.core.schedule import Scheduler, _locked, set_enabled
from datumoj.rikolto.testing.fakes import FakeManifest, FakeRawStore, FakeRegistry
from datumoj.rikolto.testing.samples import T0, make_spec


def _scheduler(tmp_path, *, execute=None, schedule="manual"):
    spec = make_spec("jp-test", schedule=schedule)
    scheduler = Scheduler(
        tmp_path, [spec], execute or (lambda _: SimpleNamespace(
            new=2, unchanged=1, failures={}, stop_reason=None,
        )), now=lambda: T0,
    )
    set_enabled(tmp_path, True)
    set_enabled(tmp_path, True, [spec.id])
    return scheduler, spec


def _running_record(run_id="run-1", *, started_at=None):
    return {
        "source_id": "jp-test", "started_at": (started_at or T0).isoformat(),
        "new": None, "unchanged": None, "failures": {}, "stop_reason": None,
        "exit_status": 0, "status": "running", "ended_at": None,
        "run_id": run_id, "state": "running",
    }


def _legacy_record(*, started_at=T0, success=True):
    return {
        "source_id": "jp-test", "started_at": started_at.isoformat(),
        "ended_at": (started_at + timedelta(minutes=1)).isoformat(),
        "new": 1, "unchanged": 0, "failures": {}, "stop_reason": None,
        "exit_status": 0 if success else 1,
        "status": "completed" if success else "failed",
    }


def test_run_writes_running_then_final_with_the_same_run_id(tmp_path):
    started = threading.Event()
    release = threading.Event()
    scheduler, _ = _scheduler(
        tmp_path,
        execute=lambda _: (started.set(), release.wait(2), SimpleNamespace(
            new=2, unchanged=1, failures={}, stop_reason="done",
        ))[-1],
    )
    worker = threading.Thread(target=scheduler.run_now, args=("jp-test",), daemon=True)
    worker.start()
    try:
        assert started.wait(1)
        rows = [json.loads(line) for line in (tmp_path / "runs.jsonl").read_text().splitlines()]
        assert len(rows) == 1
        assert rows[0]["state"] == "running"
        assert rows[0]["status"] == "running"
        run_id = rows[0]["run_id"]
    finally:
        release.set()
        worker.join(2)
    assert not worker.is_alive()

    rows = [json.loads(line) for line in (tmp_path / "runs.jsonl").read_text().splitlines()]
    assert [row["state"] for row in rows] == ["running", "final"]
    assert rows[1]["run_id"] == run_id
    assert rows[1]["status"] == "completed"
    assert rows[1]["new"] == 2 and rows[1]["unchanged"] == 1
    assert rows[1]["failures"] == {} and rows[1]["stop_reason"] == "done"
    assert rows[1]["exit_status"] == 0 and rows[1]["ended_at"] == T0.isoformat()


def test_orphan_running_record_is_interrupted_after_lock_release(tmp_path):
    scheduler, _ = _scheduler(tmp_path)
    (tmp_path / "runs.jsonl").write_text(json.dumps(_running_record()), encoding="utf-8")

    row = scheduler.status()["sources"][0]

    assert row["last_run"]["state"] == "interrupted"
    assert row["last_run"]["status"] == "interrupted"
    assert row["last_run"]["exit_status"] == 1
    assert row["running"] is False


def test_orphan_running_record_stays_running_while_source_lock_is_held(tmp_path):
    scheduler, spec = _scheduler(tmp_path)
    (tmp_path / "runs.jsonl").write_text(json.dumps(_running_record()), encoding="utf-8")
    locked = threading.Event()
    release = threading.Event()

    def hold_lock():
        with _locked(scheduler._lock_path(spec.id)):
            locked.set()
            release.wait(2)

    worker = threading.Thread(target=hold_lock, daemon=True)
    worker.start()
    try:
        assert locked.wait(1)
        row = scheduler.status()["sources"][0]
        assert row["last_run"]["state"] == "running"
        assert row["running"] is True
    finally:
        release.set()
        worker.join(2)
    assert not worker.is_alive()


def test_bad_middle_tail_and_required_field_rows_are_warned_and_append_adds_newline(tmp_path):
    scheduler, _ = _scheduler(tmp_path)
    good = _legacy_record()
    ledger = tmp_path / "runs.jsonl"
    ledger.write_bytes(
        (json.dumps(good) + "\n").encode()
        + b"{not json}\n"
        + b'{"source_id":"jp-test"}\n'
        + b'{"source_id":"jp-test","started_at":'
    )

    status = scheduler.status()
    assert [warning["line"] for warning in status["ledger_warnings"]] == [2, 3, 4]
    assert "行内容" not in json.dumps(status["ledger_warnings"], ensure_ascii=False)
    assert status["sources"][0]["last_run"]["state"] == "final"
    assert status["sources"][0]["last_run"]["started_at"] == T0.isoformat()

    scheduler.run_now("jp-test")
    raw = ledger.read_bytes()
    assert b'{"source_id":"jp-test","started_at":\n{' in raw
    assert scheduler.status()["ledger_warnings"] == [
        {"line": 2, "reason": "不是合法 JSON：Expecting property name enclosed in double quotes"},
        {"line": 3, "reason": "缺少必填字段：exit_status, started_at, status"},
        {"line": 4, "reason": "不是合法 JSON：Expecting value"},
    ]


def test_legacy_record_without_run_id_or_state_is_read_as_final(tmp_path):
    scheduler, _ = _scheduler(tmp_path)
    old = _legacy_record()
    (tmp_path / "runs.jsonl").write_text(json.dumps(old) + "\n", encoding="utf-8")

    status = scheduler.status()

    assert status["sources"][0]["last_run"] == {**old, "state": "final"}
    assert status["ledger_warnings"] == []


def test_monitor_ignores_running_and_counts_interrupted_as_failure():
    failures = [
        {**_legacy_record(started_at=T0 - timedelta(hours=3), success=False)},
        {**_legacy_record(started_at=T0 - timedelta(hours=2), success=False)},
    ]
    running = _running_record(started_at=T0 - timedelta(hours=1))
    spec = make_spec("jp-test")

    assert evaluate_alerts(spec, [*failures, running], None, T0, enabled=False) == []
    interrupted = {
        **running, "started_at": (T0 - timedelta(hours=1)).isoformat(),
        "state": "interrupted", "status": "completed", "exit_status": 0,
    }
    alerts = evaluate_alerts(spec, [*failures, interrupted], None, T0, enabled=False)
    assert [alert.rule for alert in alerts] == ["consecutive_failures"]
    assert alerts[0].since == T0 - timedelta(hours=3)


def _admin_app(tmp_path, run_now=None):
    spec = make_spec("jp-test", schedule="manual")
    deps = SimpleNamespace(
        registry=FakeRegistry([spec]), manifest=FakeManifest(), raw=FakeRawStore(tmp_path),
    )
    scheduler = Scheduler(tmp_path, [spec], lambda _: None, now=lambda: T0)
    return create_app(tmp_path, deps, scheduler, token="test-token", run_now=run_now)


def test_admin_reports_thread_creation_failure_with_escaped_reason(tmp_path, monkeypatch):
    from datumoj.rikolto.admin import web

    class BrokenThread:
        def __init__(self, **_):
            raise RuntimeError("<thread failed>")

    monkeypatch.setattr(web, "threading", SimpleNamespace(Thread=BrokenThread, Event=threading.Event))
    set_enabled(tmp_path, True)
    set_enabled(tmp_path, True, ["jp-test"])

    with TestClient(_admin_app(tmp_path)) as client:
        response = client.post("/", data={
            "csrf": "test-token", "action": "run", "source_id": "jp-test",
        })

    assert response.status_code == 500
    assert "后台运行失败：RuntimeError: &lt;thread failed&gt;" in response.text
    assert "<thread failed>" not in response.text
    assert "已开始" not in response.text


def test_admin_reports_first_worker_step_failure(tmp_path, monkeypatch):
    from datumoj.rikolto.admin import web

    class InlineThread:
        def __init__(self, *, target, **_):
            self.target = target

        def start(self):
            self.target()

    def fail_first_step(_):
        raise RuntimeError("<worker failed>")

    monkeypatch.setattr(web, "threading", SimpleNamespace(Thread=InlineThread, Event=threading.Event))
    set_enabled(tmp_path, True)
    set_enabled(tmp_path, True, ["jp-test"])

    with TestClient(_admin_app(tmp_path, run_now=fail_first_step)) as client:
        response = client.post("/", data={
            "csrf": "test-token", "action": "run", "source_id": "jp-test",
        }, follow_redirects=False)

    assert response.status_code == 500
    assert "后台运行失败：RuntimeError: &lt;worker failed&gt;" in response.text
    assert "<worker failed>" not in response.text
    assert "已开始" not in response.text


def test_admin_displays_ledger_warnings(tmp_path):
    app = _admin_app(tmp_path)
    (tmp_path / "runs.jsonl").write_text("{broken\n", encoding="utf-8")

    with TestClient(app) as client:
        response = client.get("/")

    assert response.status_code == 200
    assert "运行账本警告" in response.text
    assert "第 1 行：不是合法 JSON" in response.text


def test_admin_displays_an_interrupted_run_as_interrupted(tmp_path):
    app = _admin_app(tmp_path)
    (tmp_path / "runs.jsonl").write_text(json.dumps(_running_record()) + "\n", encoding="utf-8")

    with TestClient(app) as client:
        response = client.get("/")

    assert response.status_code == 200
    assert "运行中断" in response.text
    assert "失败 0" not in response.text
