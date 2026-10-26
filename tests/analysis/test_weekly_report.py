"""Weekly report rendering against contract fakes only."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace

from typer.testing import CliRunner

from datumoj.rikolto import cli
from datumoj.rikolto.analysis.reports import weekly_report
from datumoj.rikolto.core.models import ChangeEvent, ChangeKind, DocKind
from datumoj.rikolto.core.monitor import Alert
from datumoj.rikolto.testing.fakes import FakeChangeLog, FakeChunkStore, FakeManifest, FakeRegistry
from datumoj.rikolto.testing.samples import T0, make_chunk, make_document, make_snapshot, make_spec


def _deps(tmp_path):
    return SimpleNamespace(
        data_dir=tmp_path,
        registry=FakeRegistry([make_spec("jp-test"), make_spec("jp-other")]),
        manifest=FakeManifest(),
        chunks=FakeChunkStore(),
        change_log=FakeChangeLog(),
        require=lambda feature: None,
    )


def test_weekly_report_groups_changes_cites_snapshots_and_lists_alerts(tmp_path):
    deps = _deps(tmp_path)
    old = make_snapshot("jp-test", "https://example.go.jp/notice", b"old", snapshot_id="old")
    updated = make_snapshot("jp-test", "https://example.go.jp/notice", b"updated", snapshot_id="updated")
    removed = make_snapshot("jp-test", "https://example.go.jp/removed", b"removed", snapshot_id="removed")
    pdf = make_snapshot("jp-test", "https://example.go.jp/report.pdf", b"pdf", snapshot_id="pdf")
    parsed = make_snapshot("jp-test", "https://example.go.jp/parse", b"parse", snapshot_id="parse")
    for snapshot in (old, updated, removed, pdf, parsed):
        deps.manifest.record_snapshot(snapshot)

    updated_doc = make_document("jp-test:notice", updated.sha256, "# [恶意](javascript:run)")
    deps.chunks.put_document(updated_doc, [
        make_chunk(updated_doc.doc_key, updated.sha256, "section=1", "更新内容"),
    ])
    removed_doc = make_document("jp-test:removed", removed.sha256, "已删除文档")
    deps.chunks.put_document(removed_doc, [
        make_chunk(removed_doc.doc_key, removed.sha256, "section=2", "旧文档"),
    ])
    pdf_doc = make_document(
        "jp-test:pdf", pdf.sha256, "报告 PDF", kind=DocKind.pdf,
        metadata={"pages": "12", "needs_ocr_pages": "3"},
    )
    deps.chunks.put_document(pdf_doc, [make_chunk(pdf_doc.doc_key, pdf.sha256, "page=1", "PDF")])
    parsed_doc = make_document("jp-test:parse", parsed.sha256, "解析异常文档")
    deps.chunks.put_document(parsed_doc, [make_chunk(parsed_doc.doc_key, parsed.sha256, "section=1", "正文")])

    deps.change_log.append([
        ChangeEvent.make(
            source_id="jp-test", kind=ChangeKind.modified, doc_key=updated_doc.doc_key,
            resource_url=updated.resource_url, old_sha256=old.sha256, new_sha256=updated.sha256,
            detected_at=T0, summary="# [注入](javascript:alert(1))",
        ),
        ChangeEvent.make(
            source_id="jp-test", kind=ChangeKind.removed, doc_key=removed_doc.doc_key,
            resource_url=removed.resource_url, old_sha256=removed.sha256,
            detected_at=T0 + timedelta(hours=1), summary="文档已删除", locator="section=2",
        ),
        ChangeEvent.make(
            source_id="jp-test", kind=ChangeKind.added, doc_key=pdf_doc.doc_key,
            resource_url=pdf.resource_url, new_sha256=pdf.sha256,
            detected_at=T0 + timedelta(hours=2), summary="新增 PDF",
        ),
        ChangeEvent.make(
            source_id="jp-test", kind=ChangeKind.fetch_failed,
            resource_url="https://example.go.jp/missing", detected_at=T0 + timedelta(hours=3),
            summary="首次抓取失败",
        ),
        ChangeEvent.make(
            source_id="jp-test", kind=ChangeKind.parse_failed, doc_key=parsed_doc.doc_key,
            resource_url=parsed.resource_url, new_sha256=parsed.sha256,
            detected_at=T0 + timedelta(hours=4), summary="解析器报错",
        ),
    ])
    alert = Alert(
        source_id="jp-test", rule="retention_gap", severity="error", message="出现保留缺口",
        since=T0, details={"duration_seconds": 100},
    )

    report = weekly_report(deps, date(2026, 10, 20), date(2026, 10, 20), [alert])

    assert "| 测试来源 jp\\-test | 1 | 1 | 1 | 1 | 1 |" in report
    assert "### 新增（1）" in report and "### 修改（1）" in report
    assert "### 删除（1）" in report and "### 抓取失败（1）" in report
    assert "### 解析失败（1）" in report
    assert "\\# \\[恶意\\]\\(javascript:run\\)" in report
    assert "\\# \\[注入\\]\\(javascript:alert\\(1\\)\\)" in report
    assert "整份文档引用（位置取首个已存 locator）：jp\\-test" in report
    assert "位置 section=1" in report and "快照 " + updated.sha256[:12] in report
    assert "没有快照，无法还原" in report
    assert "https://example\\.go\\.jp/missing" in report
    assert "失败类型：fetch_failed" in report
    assert "检测时间：2026\\-10\\-20T12:00:00\\+09:00" in report
    assert "needs_ocr 3/12 页" in report
    assert "统计口径：本周有变化的 PDF" in report
    assert "本周有缺口的来源" in report and "retention\\_gap" in report
    assert "出处：jp\\-test" in report and "许可未注明" in report


def test_weekly_report_no_events_is_explicit_and_does_not_count_unrelated_pdfs(tmp_path):
    deps = _deps(tmp_path)
    snapshot = make_snapshot("jp-test", "https://example.go.jp/old.pdf", b"old pdf", snapshot_id="old-pdf")
    deps.manifest.record_snapshot(snapshot)
    doc = make_document(
        "jp-test:old-pdf", snapshot.sha256, kind=DocKind.pdf,
        metadata={"pages": "10", "needs_ocr_pages": "10"},
    )
    deps.chunks.put_document(doc, [make_chunk(doc.doc_key, snapshot.sha256, "page=1", "PDF")])

    report = weekly_report(deps, date(2026, 10, 20), date(2026, 10, 20))

    assert "本周没有变化" in report
    assert "needs_ocr 0/0 页" in report
    assert "| 测试来源 jp\\-test | 0 | 0 | 0 | 0 | 0 |" in report
    assert "| 测试来源 jp\\-other | 0 | 0 | 0 | 0 | 0 |" in report
    assert all(f"{label}（0）" in report for label in ("新增", "修改", "删除", "抓取失败", "解析失败"))
    assert "无 retention_gap 提醒" in report


def test_missing_manifest_snapshot_is_reported_without_fabricating_citation(tmp_path):
    deps = _deps(tmp_path)
    orphan = make_snapshot("jp-test", "https://example.go.jp/orphan", b"not in manifest", snapshot_id="orphan")
    deps.change_log.append([
        ChangeEvent.make(
            source_id="jp-test", kind=ChangeKind.added, doc_key="jp-test:orphan",
            resource_url=orphan.resource_url, new_sha256=orphan.sha256,
            detected_at=T0, summary="缺失 manifest 记录",
        ),
    ])

    report = weekly_report(deps, date(2026, 10, 20), date(2026, 10, 20))

    assert "没有快照，无法还原" in report
    assert "https://example\\.go\\.jp/orphan" in report
    assert orphan.sha256[:12] not in report
    assert "位置 section=" not in report


def test_weekly_cli_writes_report_and_uses_scheduler_status_alerts(tmp_path, monkeypatch):
    deps = _deps(tmp_path)
    alert = Alert(
        source_id="jp-test", rule="retention_gap", severity="error", message="调度状态提醒",
        since=T0, details={"duration_seconds": 100},
    )
    monkeypatch.setattr(cli.wiring, "build", lambda _data_dir: deps)
    monkeypatch.setattr(
        cli.scheduling.Scheduler, "status",
        lambda _scheduler: {"sources": [{"alerts": [alert.model_dump(mode="json")]}]},
    )
    target = tmp_path / "nested" / "weekly.md"

    result = CliRunner().invoke(
        cli.app,
        ["report", "weekly", "--since", "2026-10-20", "--until", "2026-10-20", "--out", str(target)],
    )

    assert result.exit_code == 0, result.output
    assert result.output.strip() == str(target)
    assert "调度状态提醒" in target.read_text(encoding="utf-8")

    result = CliRunner().invoke(cli.app, ["report", "weekly"])
    current = datetime.now(UTC).date()
    iso_year, iso_week, _ = current.isocalendar()
    default_target = tmp_path / "reports" / f"weekly-{iso_year}-W{iso_week:02d}.md"
    assert result.exit_code == 0, result.output
    assert result.output.strip() == str(default_target)
    assert default_target.exists()
