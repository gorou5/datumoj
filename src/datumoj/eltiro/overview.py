"""Read-only Eltiro operations overview for the admin page and the weekly report.

Only opaque identifiers, counts, fixed rule names and timestamps leave this module:
never demand concepts/purpose, match reasons, model text or source prose. Nothing is
created or migrated: missing state reads as empty, and SQLite opens with ``mode=ro``.
Each part fails independently with a fixed reason; exception text is never exposed.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import stat
from collections import Counter
from datetime import datetime
from pathlib import Path
from urllib.request import pathname2url

from datumoj.eltiro.models import MatchRecord, SeriesIdentity

SCHEMA = "eltiro.overview/v1"
MAX_RUNS = 50
MAX_ITEMS = 50
MAX_FILE_BYTES = 1024 * 1024
UNAVAILABLE = "读取失败（本地状态无效或不可读）"
# Fixed display labels shared by the admin page and the weekly report.
ALERT_LABELS = {
    "binding_stale": "绑定序列停更",
    "definition_changed": "口径变化",
    "new_candidate": "新候选",
    "gap_filled": "缺口转为可得",
}
GAP_LABELS = {
    "missing": "没有候选",
    "catalog_only": "只有目录",
    "document_only": "只有文档",
    "pending_confirmation": "候选待确认",
    "below_min_sources": "来源数不足",
    "rejected_only": "候选全被否决",
}
PART_LABELS = {"demands": "在用需求", "runs": "运行记录", "pending": "待确认匹配", "alerts": "提醒", "gaps": "缺口报告"}
_RUN_ID = re.compile(r"run-[0-9a-f]{32}\Z")
_GAP_REPORT = re.compile(r"gaps-g([1-9][0-9]{0,17})\.json\Z")


def _regular(path: Path) -> bool:
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        return False
    if not stat.S_ISREG(info.st_mode):
        raise ValueError("state path is not a regular file")
    return True


def _read_only(path: Path) -> sqlite3.Connection | None:
    if not _regular(path):
        return None
    connection = sqlite3.connect(f"file:{pathname2url(str(path))}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    return connection


def _read_json(directory: int, name: str):
    descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
    with os.fdopen(descriptor, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_FILE_BYTES:
            raise ValueError("invalid state file")
        return json.loads(stream.read(MAX_FILE_BYTES + 1))


def _series(series: SeriesIdentity) -> dict:
    return {"source_id": series.source_id, "table_key": series.table_key,
            "indicator_code": series.indicator_code}


def _demands(state: Path) -> list[dict]:
    connection = _read_only(state / "demands.sqlite3")
    if connection is None:
        return []
    try:
        rows = connection.execute(
            "SELECT id, version, consumer, registered_at FROM demands WHERE active = 1 ORDER BY id",
        ).fetchall()
    finally:
        connection.close()
    return [{"demand_id": row["id"], "demand_version": int(row["version"]), "consumer": row["consumer"],
             "registered_at": row["registered_at"]} for row in rows]


def _pending(state: Path, active: set[tuple[str, int]]) -> list[dict]:
    connection = _read_only(state / "matches.sqlite")
    if connection is None:
        return []
    try:
        # Only the queue is parsed: confirmed, rejected and auto-bound rows never reach Python.
        payloads = [row[0] for row in connection.execute(
            "SELECT payload FROM matches WHERE json_extract(payload, '$.confirmation') = 'pending' "
            "AND json_extract(payload, '$.auto_bind') = 0 ORDER BY position",
        )]
    finally:
        connection.close()
    pending = []
    for payload in payloads:
        record = MatchRecord.model_validate_json(payload)
        if (record.demand_id, record.demand_version) not in active:
            continue  # Inactive demand versions are not "in use"; their queue waits.
        if record.confirmation == "pending" and not record.auto_bind:
            pending.append({"demand_id": record.demand_id, "demand_version": record.demand_version,
                            "indicator_key": record.indicator_key, "generation": record.generation,
                            "tier": record.tier, "confidence": record.confidence,
                            "series": _series(record.series)})
    pending.sort(key=lambda item: (item["demand_id"], item["demand_version"], item["indicator_key"],
                                   -item["generation"], -item["confidence"]))
    return pending


def _alerts(state: Path) -> list[dict]:
    connection = _read_only(state / "alerts.sqlite")
    if connection is None:
        return []
    try:
        payloads = [row[0] for row in connection.execute("SELECT payload FROM alerts WHERE acked = 0 ORDER BY rowid")]
    finally:
        connection.close()
    alerts = []
    for payload in payloads:
        data = json.loads(payload)
        alerts.append({"alert_id": str(data["alert_id"]), "consumer": str(data["consumer"]),
                       "demand_id": str(data["demand"]), "demand_version": int(data["demand_version"]),
                       "indicator_key": str(data["indicator_key"]), "kind": str(data["kind"]),
                       "generation": int(data["generation"]),
                       "series": _series(SeriesIdentity.model_validate(data["series"]))})
    return alerts


def _runs(state: Path, since: datetime | None) -> list[dict]:
    path = state / "runs"
    try:
        directory = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    except FileNotFoundError:
        return []
    runs = []
    try:
        for name in os.listdir(directory):
            if not _RUN_ID.fullmatch(name):
                continue  # Staging directories (".run-…") are unpublished.
            child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
            try:
                record = _read_json(child, "record.json")
            finally:
                os.close(child)
            runs.append({"run_id": name, "demand_id": str(record["demand_id"]),
                         "demand_version": int(record["demand_version"]),
                         "generation": int(record["generation"]),
                         "started_at": str(record["started_at"]), "finished_at": str(record["finished_at"]),
                         "row_count": int(record["row_count"]), "gap_count": int(record["gap_count"])})
    finally:
        os.close(directory)
    runs.sort(key=lambda run: (run["finished_at"], run["run_id"]), reverse=True)
    if since is None:
        return runs[:MAX_RUNS]
    # A reporting window needs every run in it, not only the newest MAX_RUNS.
    return [run for run in runs if _finished_after(run["finished_at"], since)]


def _finished_after(value: str, since: datetime) -> bool:
    try:
        moment = datetime.fromisoformat(value)
    except ValueError:
        return True  # Unknown time: keep it visible rather than hide it from the window.
    return moment.utcoffset() is None or moment >= since


def _gaps(state: Path) -> dict | None:
    path = state / "reports"
    try:
        directory = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    except FileNotFoundError:
        return None
    try:
        reports = [(int(match.group(1)), name) for name in os.listdir(directory)
                   if (match := _GAP_REPORT.fullmatch(name))]
        if not reports:
            return None
        generation, name = max(reports)
        report = _read_json(directory, name)
    finally:
        os.close(directory)
    if report.get("schema") != "eltiro.gaps.aggregate/v1" or report.get("generation") != generation:
        raise ValueError("unexpected gap report")
    indicators = []
    kinds: Counter[str] = Counter()
    for group in report["by_concept"]:
        for item in group["demands"]:  # The concept text itself is deliberately dropped.
            gap_kinds = sorted({str(gap["kind"]) for gap in item["gaps"]})
            kinds.update(gap_kinds)
            indicators.append({"consumer": str(item["consumer"]), "demand_id": str(item["demand_id"]),
                               "demand_version": int(item["demand_version"]),
                               "indicator_key": str(item["indicator_key"]), "kinds": gap_kinds})
    indicators.sort(key=lambda item: (item["demand_id"], item["demand_version"], item["indicator_key"]))
    sources = [{"source_id": str(group["source_id"]), "indicators": len(group["demands"]),
                "reasons": sorted({str(reason) for item in group["demands"] for reason in item["reasons"]})}
               for group in report["by_source"]]
    return {"generation": generation, "file": f"eltiro/reports/{name}",
            "active_demands": int(report["active_demands"]), "indicator_count": len(indicators),
            "by_kind": dict(sorted(kinds.items())), "indicators": indicators[:MAX_ITEMS],
            "sources": sources[:MAX_ITEMS], "source_count": len(sources)}


def eltiro_overview(data_dir: str | Path, *, runs_since: datetime | None = None) -> dict | None:
    """Return a JSON-compatible snapshot, or ``None`` when ``<data_dir>/eltiro`` is absent.

    ``runs_since`` (aware datetime) returns every run finished at or after it instead of
    the newest MAX_RUNS. ``errors`` lists the parts that could not be read; those parts hold their empty
    value and must be rendered as unknown, not as "nothing to do".
    """
    state = Path(data_dir).absolute() / "eltiro"
    try:
        info = os.lstat(state)
    except FileNotFoundError:
        return None
    if not stat.S_ISDIR(info.st_mode):
        return {"schema": SCHEMA, "demands": [], "runs": [], "pending": [], "pending_count": 0,
                "alerts": [], "alert_count": 0, "alerts_by_kind": {}, "gaps": None,
                "errors": ["demands", "runs", "pending", "alerts", "gaps"]}
    errors: list[str] = []

    def part(name, read, empty):
        try:
            return read()
        except Exception:  # noqa: BLE001 -- 概览各部分独立失败，只报部分名，不泄露异常原文。
            errors.append(name)
            return empty

    demands = part("demands", lambda: _demands(state), [])
    active = {(item["demand_id"], item["demand_version"]) for item in demands}
    runs = part("runs", lambda: _runs(state, runs_since), [])
    if "demands" in errors:
        # The queue is defined over active demands: unknown demands make it unknown, not empty.
        errors.append("pending")
        pending = []
    else:
        pending = part("pending", lambda: _pending(state, active), [])
    alerts = part("alerts", lambda: _alerts(state), [])
    gaps = part("gaps", lambda: _gaps(state), None)
    return {"schema": SCHEMA, "demands": demands, "runs": runs,
            "pending": pending[:MAX_ITEMS], "pending_count": len(pending),
            "alerts": alerts[:MAX_ITEMS], "alert_count": len(alerts),
            "alerts_by_kind": dict(sorted(Counter(alert["kind"] for alert in alerts).items())),
            "gaps": gaps, "errors": errors}
