"""Eurostat SDMX 2.1: DSD (including referenced codelists), then filtered data.

The runner owns robots, host checks, rate limits, budgets and raw snapshots.
Structure metadata travels with the data task so reparsing needs no network.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from hashlib import sha256
from urllib.parse import urlencode

from datumoj.rikolto.core.contracts import CrawlContext, Crawler, FetchResult, FetchTask
from datumoj.rikolto.core.models import FetchOutcome
from datumoj.rikolto.parsers.eurostat import read_structure

_CODE = re.compile(r"[A-Za-z0-9_]+")
_KEY = re.compile(r"[A-Za-z0-9_.+]+")


class EurostatCrawler(Crawler):
    kind = "eurostat_sdmx"

    def plan(self, ctx: CrawlContext) -> Iterable[FetchTask]:
        datasets = ctx.spec.scope.get("datasets")
        if not isinstance(datasets, list) or not datasets:
            raise ValueError("scope.datasets 必须是非空列表")
        seen = set()
        # Validate the entire scope before yielding any requests.
        for entry in datasets:
            if not isinstance(entry, dict):
                raise ValueError("scope.datasets 条目必须是对象")
            code, key = entry.get("id"), entry.get("query")
            if not isinstance(code, str) or not _CODE.fullmatch(code) or code in seen:
                raise ValueError("scope.datasets.id 必须是唯一的数据集代码")
            seen.add(code)
            if not isinstance(key, str) or not _KEY.fullmatch(key):
                raise ValueError("scope.datasets.query (SDMX key) 只允许维度代码字符、. 和 +")
            start, end = entry.get("startPeriod"), entry.get("endPeriod")
            if any(not isinstance(v, str) or not re.fullmatch(r"[0-9]{4}", v) for v in (start, end)) or start > end:
                raise ValueError("startPeriod / endPeriod 必须是有序的四位年份字符串")
        base = ctx.spec.base_url.rstrip("/")
        for entry in datasets[:ctx.limit]:
            yield FetchTask(
                url=f"{base}/datastructure/ESTAT/{entry['id'].upper()}?references=all",
                meta={"request_type": "dsd", **entry},
            )

    def after_fetch(self, task: FetchTask, result: FetchResult, ctx: CrawlContext) -> Iterable[FetchTask]:
        if task.meta.get("request_type") != "dsd":
            return
        if result.outcome is FetchOutcome.ok:
            data = result.content
        elif result.outcome is FetchOutcome.not_modified:
            previous = ctx.manifest.last_success(task.redacted_url)
            if previous is None or previous.sha256 is None:
                raise ValueError("Eurostat DSD 304 缺少可复用快照")
            data = ctx.raw.get(previous.sha256)
        else:
            return
        code = str(task.meta["id"])
        structure = read_structure(data, code)
        params = urlencode({name: task.meta[name] for name in ("startPeriod", "endPeriod")})
        base = ctx.spec.base_url.rstrip("/")
        yield FetchTask(
            url=f"{base}/data/{code}/{task.meta['query']}?{params}",
            meta={
                **task.meta, "request_type": "data", "structure": structure,
                "dsd_url": task.redacted_url, "dsd_sha256": sha256(data).hexdigest(),
            },
        )
