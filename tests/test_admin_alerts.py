from __future__ import annotations

from types import SimpleNamespace

from starlette.testclient import TestClient

from datumoj.admin import create_app
from datumoj.testing.fakes import FakeManifest, FakeRawStore, FakeRegistry
from datumoj.testing.samples import make_spec


def _app(tmp_path, alerts):
    spec = make_spec("jp-one", schedule="hourly")
    deps = SimpleNamespace(
        registry=FakeRegistry([spec]), manifest=FakeManifest(), raw=FakeRawStore(tmp_path),
    )
    row = {
        "source_id": spec.id, "enabled": False, "effective_enabled": False,
        "schedule": spec.schedule, "last_run": None, "next_due": None,
        "running": False, "alerts": alerts,
    }
    scheduler = SimpleNamespace(status=lambda: {"enabled": False, "sources": [row], "alert_count": len(alerts)})
    return create_app(tmp_path, deps, scheduler, token="test-token")


def test_admin_shows_alerts_sorted_by_severity_and_escapes_all_text(tmp_path):
    app = _app(tmp_path, [
        {
            "source_id": "jp-one", "rule": "warning-rule", "severity": "warning",
            "message": "普通提醒", "since": "2026-10-20T08:00:00+09:00",
        },
        {
            "source_id": "jp-one", "rule": "<b>bad-rule</b>", "severity": "error",
            "message": "<script>alert(1)</script>", "since": "2026-10-20T07:00:00+09:00",
        },
    ])

    with TestClient(app) as client:
        page = client.get("/").text

    section = page.index('<section class="alerts"')
    table = page.index('<div class="table-wrap">')
    error = page.index("<strong>错误</strong>")
    warning = page.index("<strong>警告</strong>")
    assert section < error < warning < table
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in page
    assert "&lt;b&gt;bad-rule&lt;/b&gt;" in page
    assert "<script>alert(1)</script>" not in page
    assert "<b>bad-rule</b>" not in page


def test_admin_shows_empty_alert_state(tmp_path):
    app = _app(tmp_path, [])

    with TestClient(app) as client:
        page = client.get("/").text

    assert "没有提醒。" in page
