from __future__ import annotations

from datumoj.rikolto.core.schedule import Scheduler
from datumoj.rikolto.testing.samples import T0, make_spec


def test_status_survives_a_truncated_final_ledger_line(tmp_path):
    (tmp_path / "runs.jsonl").write_bytes(b'{"source_id":"jp-test","started_at":')
    scheduler = Scheduler(tmp_path, [make_spec("jp-test", schedule="manual")], lambda _: None, now=lambda: T0)

    status = scheduler.status()

    assert status["sources"][0]["last_run"] is None
    assert status["ledger_warnings"][0]["line"] == 1
    assert status["ledger_warnings"][0]["reason"].startswith("不是合法 JSON：")
