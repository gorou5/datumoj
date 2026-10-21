from __future__ import annotations

import hashlib
import re
import sys
import threading
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

from starlette.testclient import TestClient
from typer.testing import CliRunner

from datumoj import cli
from datumoj.admin import create_app
from datumoj.core.models import FetchOutcome, Snapshot
from datumoj.core.schedule import Scheduler, read_control, set_enabled
from datumoj.testing.fakes import FakeManifest, FakeRawStore, FakeRegistry
from datumoj.testing.samples import T0, make_spec


def _admin(tmp_path, *, names=None, execute=None, token="test-token"):
    names = names or {"jp-one": "测试来源一", "jp-two": "测试来源二"}
    specs = [
        make_spec(source_id, schedule="hourly" if index == 0 else "manual").model_copy(update={"name": name})
        for index, (source_id, name) in enumerate(names.items())
    ]
    deps = SimpleNamespace(registry=FakeRegistry(specs), manifest=FakeManifest(), raw=FakeRawStore(tmp_path))
    calls = []

    def crawl(spec):
        calls.append(spec.id)
        return (execute or (lambda _: SimpleNamespace(
            new=2, unchanged=3, failures={}, stop_reason="达到上限",
        )))(spec)

    scheduler = Scheduler(tmp_path, specs, crawl, now=lambda: T0)
    app = create_app(tmp_path, deps, scheduler, token=token)
    return app, deps, scheduler, calls


def _csrf(html: str) -> str:
    match = re.search(r'name="csrf" value="([^"]+)"', html)
    assert match is not None
    return match.group(1)


def test_page_lists_sources_status_and_post_forms(tmp_path):
    app, deps, scheduler, _ = _admin(tmp_path)
    payload = b"snapshot data"
    sha = hashlib.sha256(payload).hexdigest()
    deps.raw.put(payload)
    deps.manifest.record_snapshot(Snapshot(
        snapshot_id="snapshot-1", source_id="jp-one", resource_url="https://example.go.jp/a.txt",
        fetched_at=T0, outcome=FetchOutcome.ok, sha256=sha, size=len(payload),
    ))
    set_enabled(tmp_path, True)
    set_enabled(tmp_path, True, ["jp-one"])
    scheduler.run_now("jp-one")

    with TestClient(app) as client:
        response = client.get("/")
    assert response.status_code == 200
    assert "测试来源一" in response.text and "测试来源二" in response.text
    assert "总开关" in response.text and "开启" in response.text
    assert "每小时" in response.text and "手动" in response.text
    assert "新增 2" in response.text and "未变 3" in response.text and "停止：达到上限" in response.text
    assert (T0 + timedelta(hours=1)).isoformat() in response.text
    assert "<td>否</td>" in response.text
    assert "快照数" in response.text and "1</td>" in response.text and f"{len(payload)} B" in response.text
    assert response.text.count(">立即运行</button>") == 2
    assert 'method="post"' in response.text
    assert _csrf(response.text) == "test-token"


def test_switch_posts_update_control_and_get_does_not(tmp_path):
    app, _, _, _ = _admin(tmp_path)
    with TestClient(app) as client:
        before = client.get("/")
        assert before.status_code == 200
        assert not (tmp_path / "control.json").exists()
        token = _csrf(before.text)
        master = client.post("/", data={"csrf": token, "action": "master", "enabled": "on"}, follow_redirects=False)
        assert master.status_code == 303
        assert read_control(tmp_path)["enabled"] is True
        source = client.post("/", data={
            "csrf": token, "action": "source", "source_id": "jp-one", "enabled": "on",
        }, follow_redirects=False)
        assert source.status_code == 303
        assert read_control(tmp_path)["sources"]["jp-one"]["enabled"] is True
        client.post("/", data={"csrf": token, "action": "master", "enabled": "off"})
        assert read_control(tmp_path)["enabled"] is False
        client.post("/", data={
            "csrf": token, "action": "source", "source_id": "jp-one", "enabled": "off",
        })
        assert read_control(tmp_path)["sources"]["jp-one"]["enabled"] is False
        after = client.get("/")
        assert after.status_code == 200
        assert read_control(tmp_path)["enabled"] is False


def test_invalid_csrf_and_unknown_source_are_rejected_without_changes(tmp_path):
    app, _, _, _ = _admin(tmp_path)
    with TestClient(app) as client:
        no_token = client.post("/", data={"action": "master", "enabled": "on"})
        bad_token = client.post("/", data={"csrf": "wrong", "action": "master", "enabled": "on"})
        unicode_token = client.post("/", data={"csrf": "错误令牌", "action": "master", "enabled": "on"})
        assert no_token.status_code == bad_token.status_code == unicode_token.status_code == 403
        valid = _csrf(client.get("/").text)
        unknown = client.post("/", data={
            "csrf": valid, "action": "source", "source_id": "unknown", "enabled": "on",
        })
        assert unknown.status_code == 404
    assert read_control(tmp_path) == {"enabled": False, "sources": {}}
    assert not (tmp_path / "control.json").exists()


def test_run_now_respects_switches_and_runs_in_background(tmp_path):
    started = threading.Event()
    release = threading.Event()
    completed = threading.Event()

    def crawl(_):
        started.set()
        assert release.wait(5)
        completed.set()
        return SimpleNamespace(new=1, unchanged=0, failures={}, stop_reason=None)

    app, _, _, calls = _admin(tmp_path, execute=crawl)
    token = "test-token"
    with TestClient(app) as client:
        closed = client.post("/", data={
            "csrf": token, "action": "run", "source_id": "jp-one",
        })
        assert closed.status_code == 409 and "总开关关闭" in closed.text
        assert not calls

        set_enabled(tmp_path, True)
        set_enabled(tmp_path, True, ["jp-one"])
        response = client.post("/", data={"csrf": token, "action": "run", "source_id": "jp-one"}, follow_redirects=False)
        assert response.status_code == 303
        assert started.wait(1)
        assert not release.is_set()
        assert client.get("/?notice=run-started").text.find("已开始") >= 0
        release.set()
        assert completed.wait(2)
        assert calls == ["jp-one"]


def test_run_now_rejects_a_source_with_an_existing_lock(tmp_path):
    started = threading.Event()
    release = threading.Event()

    def crawl(_):
        started.set()
        assert release.wait(5)
        return SimpleNamespace(new=1, unchanged=0, failures={}, stop_reason=None)

    app, _, scheduler, calls = _admin(tmp_path, execute=crawl)
    set_enabled(tmp_path, True)
    set_enabled(tmp_path, True, ["jp-one"])
    worker = threading.Thread(target=scheduler.run_now, args=("jp-one",), daemon=True)
    worker.start()
    try:
        assert started.wait(1)
        with TestClient(app) as client:
            response = client.post("/", data={
                "csrf": "test-token", "action": "run", "source_id": "jp-one",
            })
        assert response.status_code == 409 and "正在运行" in response.text
        assert calls == ["jp-one"]
    finally:
        release.set()
        worker.join(2)
    assert not worker.is_alive()


def test_source_name_is_html_escaped(tmp_path):
    app, _, _, _ = _admin(tmp_path, names={"jp-one": "<script>alert(1)</script>"})
    with TestClient(app) as client:
        page = client.get("/").text
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in page
    assert "<script>alert(1)</script>" not in page


def test_failure_reason_is_shown_and_escaped(tmp_path):
    app, _, scheduler, _ = _admin(
        tmp_path,
        names={"jp-one": "测试来源"},
        execute=lambda _: SimpleNamespace(
            new=1, unchanged=0, failures={"network_error": 2}, stop_reason="<script>blocked</script>",
        ),
    )
    set_enabled(tmp_path, True)
    set_enabled(tmp_path, True, ["jp-one"])
    scheduler.run_now("jp-one")
    with TestClient(app) as client:
        page = client.get("/").text
    assert "失败 2（network_error × 2）" in page
    assert "&lt;script&gt;blocked&lt;/script&gt;" in page
    assert "<script>blocked</script>" not in page


def test_admin_cli_binds_only_loopback_and_prints_url(tmp_path, monkeypatch):
    app, deps, scheduler, _ = _admin(tmp_path)
    monkeypatch.setattr(cli, "_scheduler_at", lambda root: (deps, scheduler))
    called = {}

    def fake_run(web_app, **kwargs):
        called.update(app=web_app, **kwargs)

    monkeypatch.setitem(sys.modules, "uvicorn", SimpleNamespace(run=fake_run))
    result = CliRunner().invoke(cli.app, ["admin", "--port", "9876", "--data-dir", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert "http://127.0.0.1:9876/" in result.output
    assert called["host"] == "127.0.0.1" and called["port"] == 9876
    assert set(called) == {"app", "host", "port"}
    assert called["app"] is not None
