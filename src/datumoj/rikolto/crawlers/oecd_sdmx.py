"""OECD SDMX: ask the contentconstraint first, fetch data only when its ``validFrom`` moved.

OECD allows 60 data downloads per hour. Every request, the contentconstraint ones included, is counted
from the manifest (all snapshots of this source in the last hour, failures and 304s too), so the budget
holds across requests and across runs. Out of budget the plan simply ends; what is left is fetched next
time. The fetcher's own retries and robots.txt are not in the manifest and cannot be counted here.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from datetime import timedelta
from urllib.parse import urlencode

from datumoj.rikolto.core.contracts import CrawlContext, Crawler, FetchResult, FetchTask
from datumoj.rikolto.core.models import FetchOutcome
from datumoj.rikolto.parsers.oecd_sdmx import read_code_labels, read_valid_from

MAX_REQUESTS_PER_HOUR = 60

_AGENCY = re.compile(r"[A-Za-z0-9_.]+")
_DATAFLOW = re.compile(r"[A-Za-z0-9_]+@[A-Za-z0-9_]+")
_VERSION = re.compile(r"[0-9]+(?:\.[0-9]+)*")
_KEY = re.compile(r"[A-Za-z0-9_.+]+")
_PERIOD = re.compile(r"[0-9]{4}(?:-[0-9]{2})?")
_EDITION = re.compile(r"[0-9]{6}")


def _flows(scope: dict) -> list[dict]:
    """Validate the whole scope before the first request."""
    flows = scope.get("dataflows")
    if not isinstance(flows, list) or not flows:
        raise ValueError("scope.dataflows 必须是非空列表")
    seen = set()
    for flow in flows:
        if not isinstance(flow, dict):
            raise ValueError("scope.dataflows 条目必须是对象")
        agency, ident, version = flow.get("agency"), flow.get("id"), flow.get("version")
        if not isinstance(agency, str) or not _AGENCY.fullmatch(agency):
            raise ValueError("scope.dataflows.agency 无效")
        if not isinstance(ident, str) or not _DATAFLOW.fullmatch(ident) or ident in seen:
            raise ValueError("scope.dataflows.id 必须是唯一的 <DSD>@<DF>")
        seen.add(ident)
        if ident.endswith("_CPC"):
            raise ValueError("不取 PPP 口径的 dataflow（_CPC）")
        if not isinstance(version, str) or not _VERSION.fullmatch(version):
            raise ValueError("scope.dataflows.version 必须是 x.y 形式的字符串")
        # The scope field is "query", not "key": the registry rejects plaintext values in fields named *key*.
        key = flow.get("query")
        if not isinstance(key, str) or not _KEY.fullmatch(key):
            raise ValueError("scope.dataflows.query (SDMX key) 只允许维度代码字符、. 和 +")
        if "PPP" in key:
            raise ValueError("不取 PPP 口径的 key")
        start, end = flow.get("startPeriod"), flow.get("endPeriod")
        if (not isinstance(start, str) or not _PERIOD.fullmatch(start)
                or (end is not None and (not isinstance(end, str) or not _PERIOD.fullmatch(end) or start > end))):
            raise ValueError("startPeriod / endPeriod 必须是 YYYY 或 YYYY-MM，且有序")
        editions = flow.get("editions")
        if editions is not None and (
            not isinstance(editions, list) or not editions
            or any(not isinstance(e, str) or not _EDITION.fullmatch(e) for e in editions)
            or len(set(editions)) != len(editions)
        ):
            raise ValueError("scope.dataflows.editions 必须是不重复的 YYYYMM 字符串列表")
    return flows


def _requests_used(ctx: CrawlContext) -> int:
    since = ctx.now - timedelta(hours=1)
    recent = [s for s in ctx.manifest.snapshots_for(ctx.spec.id) if s.fetched_at > since]
    # OECD blocks for a while after a 429; treat it as an exhausted budget instead of probing again.
    return MAX_REQUESTS_PER_HOUR if any(s.http_status == 429 for s in recent) else len(recent)


def _data_url(ctx: CrawlContext, flow: dict, key: str) -> str:
    params = {"startPeriod": flow["startPeriod"]}
    if flow.get("endPeriod"):
        params["endPeriod"] = flow["endPeriod"]
    params["format"] = "csv"
    base = ctx.spec.base_url.rstrip("/")
    return f"{base}/data/{flow['agency']},{flow['id']},{flow['version']}/{key}?{urlencode(params)}"


class OecdSdmxCrawler(Crawler):
    kind = "oecd_sdmx"

    def plan(self, ctx: CrawlContext) -> Iterable[FetchTask]:
        flows = _flows(ctx.spec.scope)
        base = ctx.spec.base_url.rstrip("/")
        planned = 0
        # Current-version dataflows first, then the revision editions.
        for flow in sorted(flows, key=lambda f: "editions" in f):
            meta = {k: flow[k] for k in ("agency", "id", "version", "query", "startPeriod", "endPeriod") if k in flow}
            if "editions" not in flow:
                steps = [(f"{base}/contentconstraint/{flow['agency']}/CR_A_{flow['id']}/", {"request_type": "constraint"})]
            else:
                steps = []
                for edition in flow["editions"]:
                    url = _data_url(ctx, flow, f"{flow['query']}.{edition}")
                    done = ctx.manifest.last_success(url)
                    if done is None or done.sha256 is None:
                        steps.append((url, {"request_type": "edition", "edition": edition}))
            for url, extra in steps:
                if (ctx.limit is not None and planned >= ctx.limit) or _requests_used(ctx) >= MAX_REQUESTS_PER_HOUR:
                    return
                planned += 1
                yield FetchTask(url=url, meta={**meta, **extra})

    def after_fetch(self, task: FetchTask, result: FetchResult, ctx: CrawlContext) -> Iterable[FetchTask]:
        kind = task.meta.get("request_type")
        if kind not in ("constraint", "structure"):
            return
        if result.outcome is FetchOutcome.ok:
            data = result.content
        elif result.outcome is FetchOutcome.not_modified:
            previous = ctx.manifest.last_success(task.redacted_url)
            if previous is None or previous.sha256 is None:
                raise ValueError(f"OECD {kind} 304 缺少可复用快照")
            data = ctx.raw.get(previous.sha256)
        else:
            return
        flow = task.meta
        if kind == "structure":
            # The data request is unchanged; the codelists only travel along as task meta.
            url = _data_url(ctx, flow, str(flow["query"]))
            if _requests_used(ctx) < MAX_REQUESTS_PER_HOUR:
                yield FetchTask(url=url, meta={
                    **{k: v for k, v in flow.items() if k != "request_type"},
                    "request_type": "data", "code_labels": read_code_labels(data)})
            return
        valid_from = read_valid_from(data, str(flow["agency"]), str(flow["id"]))
        url = _data_url(ctx, flow, str(flow["query"]))
        # Compare with the last successful data fetch (outcome ok), not the last constraint: a failed or
        # budget-cut data fetch must be retried although the constraint itself has not changed since.
        # A 304 on the data is not a success here: the runner sends the stored Last-Modified, which does not
        # follow the data, so a 304 would otherwise hide a new validFrom for good.
        fetched = ctx.manifest.last_success(url)
        if fetched is not None and ctx.manifest.snapshot_meta(fetched.snapshot_id).get("valid_from") == valid_from:
            return
        if _requests_used(ctx) + 1 >= MAX_REQUESTS_PER_HOUR:  # the structure and its data must both fit
            return
        # One more request for the codelists (structure only); the data request follows from its result.
        base = ctx.spec.base_url.rstrip("/")
        structure = f"{base}/dataflow/{flow['agency']}/{flow['id']}/{flow['version']}?references=all&detail=referencepartial"
        yield FetchTask(url=structure, meta={**flow, "request_type": "structure", "valid_from": valid_from})
