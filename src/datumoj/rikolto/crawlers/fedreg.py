"""Federal Register API: bounded date windows -> details -> official XML."""

from __future__ import annotations

import json
import logging
import re
from collections import deque
from collections.abc import Iterable
from datetime import date, timedelta
from urllib.parse import urlencode, urlsplit

from datumoj.rikolto.core.contracts import CrawlContext, Crawler, FetchResult, FetchTask, NotFoundError
from datumoj.rikolto.core.models import FetchOutcome, redact_url

log = logging.getLogger(__name__)
_TYPES = {"RULE", "PRORULE", "NOTICE"}
_LABELS = {"Rule": "RULE", "Proposed Rule": "PRORULE", "Notice": "NOTICE"}
_START = date(2026, 10, 1)


def _window(ctx: CrawlContext, start: date, end: date, page: int = 1, *, root: bool = False) -> FetchTask:
    if not 1 <= page <= 50:
        raise ValueError("Federal Register page must be between 1 and 50")
    params = [
        ("per_page", "2000"), ("page", str(page)), ("order", "oldest"),
        ("conditions[publication_date][gte]", start.isoformat()),
        ("conditions[publication_date][lte]", end.isoformat()),
        *(("conditions[type][]", kind) for kind in ctx.spec.scope["types"]),
    ]
    return FetchTask(
        url=ctx.spec.base_url.rstrip("/") + "/documents.json?" + urlencode(params),
        meta={"role": "search", "start": start.isoformat(), "end": end.isoformat(),
              "page": page, "root": root, "run": ctx.now.isoformat()},
    )


def _allowed(url: str, ctx: CrawlContext) -> bool:
    parts = urlsplit(url)
    return (
        parts.scheme in {"http", "https"} and parts.username is None
        and parts.hostname in ctx.spec.allowed_hosts
    )


def _children(task: FetchTask, data: bytes, ctx: CrawlContext) -> Iterable[FetchTask]:
    role = task.meta["role"]
    if role == "xml":
        return
    payload = json.loads(data)
    if not isinstance(payload, dict):
        raise ValueError("Federal Register response must be a JSON object")
    if role == "search":
        count, pages = payload.get("count"), payload.get("total_pages")
        if any(type(value) is not int or value < 0 for value in (count, pages)):
            raise ValueError("Federal Register response lacks valid count/total_pages")
        start, end = date.fromisoformat(task.meta["start"]), date.fromisoformat(task.meta["end"])
        if count >= 10000 or pages >= 50:
            if start == end:
                raise ValueError(f"Federal Register single day {start} exceeds 10000 records/50 pages")
            mid = start + (end - start) // 2
            yield _window(ctx, start, mid)
            yield _window(ctx, mid + timedelta(days=1), end)
            return
        records = payload.get("results")
        if not isinstance(records, list) or (count > 0 and (pages == 0 or not records)):
            raise ValueError("Federal Register response has incomplete results")
        for record in records:
            if not isinstance(record, dict):
                raise ValueError("Invalid Federal Register document record")
            raw_kind = record.get("type")
            kind = _LABELS.get(raw_kind, raw_kind) if isinstance(raw_kind, str) else None
            if kind not in ctx.spec.scope["types"]:
                raise ValueError("Federal Register returned an excluded document type")
            number = record.get("document_number")
            if not isinstance(number, str) or not re.fullmatch(r"\d{4}-\d+", number):
                raise ValueError("Invalid Federal Register document number")
            yield FetchTask(
                url=ctx.spec.base_url.rstrip("/") + f"/documents/{number}.json",
                meta={"role": "detail", "document_number": number, "run": ctx.now.isoformat()},
            )
        page = task.meta["page"]
        if page < pages:
            yield _window(ctx, start, end, page + 1)
    elif role == "detail":
        if payload.get("document_number") != task.meta["document_number"]:
            raise ValueError("Federal Register detail document number mismatch")
        raw_kind = payload.get("type")
        kind = _LABELS.get(raw_kind, raw_kind) if isinstance(raw_kind, str) else None
        if kind not in ctx.spec.scope["types"]:
            raise ValueError("Federal Register detail has excluded document type")
        url = payload.get("full_text_xml_url")
        if not isinstance(url, str) or not url:
            raise ValueError("Federal Register document lacks full_text_xml_url")
        if not _allowed(url, ctx):
            log.warning("Skipping Federal Register XML outside allowed_hosts: %s", redact_url(url))
            return
        fields = ("document_number", "title", "publication_date", "html_url", "type", "agencies", "cfr_references", "citation")
        yield FetchTask(url=url, meta={
            "role": "xml", "run": ctx.now.isoformat(),
            **{field: payload.get(field) for field in fields},
        })


def _covered_until(ctx: CrawlContext) -> date | None:
    """Replay each run's task graph from immutable snapshots; partial runs never advance."""
    runs, bodies = {}, {}
    for snap in ctx.manifest.snapshots_for(ctx.spec.id):
        meta = ctx.manifest.snapshot_meta(snap.snapshot_id)
        if snap.outcome is FetchOutcome.ok:
            bodies[snap.resource_url] = snap.sha256
        sha = bodies.get(snap.resource_url) if snap.outcome is FetchOutcome.not_modified else snap.sha256
        if isinstance(meta.get("run"), str):
            runs.setdefault(meta["run"], {})[snap.resource_url] = (snap, meta, sha)
    covered = None
    for records in runs.values():
        roots = [(snap, meta) for snap, meta, _ in records.values() if meta.get("root") is True]
        if len(roots) != 1:
            continue
        root, meta = roots[0]
        queue, seen = deque([root.resource_url]), set()
        try:
            while queue:
                url = queue.popleft()
                if url in seen:
                    continue
                seen.add(url)
                snap, task_meta, sha = records[url]
                if snap.outcome not in {FetchOutcome.ok, FetchOutcome.not_modified}:
                    raise ValueError("Incomplete run")
                if sha is None:
                    raise ValueError("Missing successful body")
                task = FetchTask(url=url, meta=task_meta)
                queue.extend(child.redacted_url for child in _children(task, ctx.raw.get(sha), ctx))
            end = date.fromisoformat(meta["end"])
        except (KeyError, ValueError, NotFoundError):
            continue
        covered = end if covered is None else max(covered, end)
    return covered


class FedregCrawler(Crawler):
    kind = "fedreg"

    def plan(self, ctx: CrawlContext) -> Iterable[FetchTask]:
        scope = ctx.spec.scope
        kinds = scope.get("types")
        if not isinstance(kinds, list) or not kinds or any(not isinstance(kind, str) or kind not in _TYPES for kind in kinds):
            raise ValueError("scope.types must contain only RULE, PRORULE, NOTICE")
        if scope.get("start_date") != _START.isoformat() or scope.get("lookback_days") != 7:
            raise ValueError("Federal Register scope requires start_date=2026-10-01 and lookback_days=7")
        if not _allowed(ctx.spec.base_url, ctx):
            raise ValueError("Federal Register API outside allowed_hosts")
        if ctx.limit is not None and ctx.limit <= 0:
            return
        covered = _covered_until(ctx)
        start = max(_START, covered - timedelta(days=7)) if covered is not None else _START
        if start <= ctx.now.date():
            yield _window(ctx, start, ctx.now.date(), root=True)

    def after_fetch(self, task: FetchTask, result: FetchResult, ctx: CrawlContext) -> Iterable[FetchTask]:
        data = result.content
        if result.outcome is FetchOutcome.not_modified:
            previous = ctx.manifest.last_success(task.redacted_url)
            if previous is not None and previous.sha256 is not None:
                data = ctx.raw.get(previous.sha256)
        if data is not None:
            yield from _children(task, data, ctx)

    def classify(self, task: FetchTask, result: FetchResult) -> FetchOutcome | None:
        # JSON and XML are structured payloads, not HTML/SPA shells.
        return result.outcome
