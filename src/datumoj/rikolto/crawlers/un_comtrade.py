"""UN Comtrade: ask getDA first, fetch a reporter-partner-year only when its release record moved.

Manual source: nothing here runs on a schedule. The key never appears in a URL or in this module: the runner
sends it in the source's ``key_header`` at request time. This plugin only checks that the environment variable
is set, so a missing key fails the whole run before any request. Country codes are Comtrade's own, not M49
(USA 842, France 251). There is no fallback to the keyless preview endpoint.
"""

from __future__ import annotations

import os
from collections.abc import Iterable
from urllib.parse import urlencode

from datumoj.rikolto.core.contracts import CrawlContext, Crawler, FetchResult, FetchTask, SourceSpec
from datumoj.rikolto.core.models import FetchOutcome
from datumoj.rikolto.parsers.un_comtrade import FLOWS, read_releases

_M49_NOT_COMTRADE = {840: 842, 250: 251}
_TOTAL_ROW = {"cmdCode": "TOTAL", "partner2Code": 0, "customsCode": "C00", "motCode": 0}


def _scope(scope: dict) -> tuple[list[tuple[int, int]], list[int], list[str]]:
    """Validate the whole scope before the first request."""
    raw_pairs, start, end, flows = scope.get("pairs"), scope.get("start_year"), scope.get("end_year"), scope.get("flows")
    if not isinstance(raw_pairs, list) or not raw_pairs:
        raise ValueError("scope.pairs 必须是非空的 [报告方, 伙伴] 列表")
    pairs = []
    for pair in raw_pairs:
        if not isinstance(pair, list) or len(pair) != 2 or any(type(c) is not int or not 1 <= c <= 999 for c in pair):
            raise ValueError("scope.pairs 条目必须是两个 Comtrade 国家代码")
        if pair[0] == pair[1] or tuple(pair) in pairs or tuple(reversed(pair)) in pairs:
            raise ValueError("scope.pairs 的国家对不能重复或自对")
        if wrong := [c for c in pair if c in _M49_NOT_COMTRADE]:
            raise ValueError(f"scope.pairs 用了 M49 代码 {wrong}，Comtrade 要用 {[_M49_NOT_COMTRADE[c] for c in wrong]}")
        pairs.append((pair[0], pair[1]))
    if type(start) is not int or type(end) is not int or not 1962 <= start <= end <= 9999:
        raise ValueError("scope.start_year / end_year 必须是有序的年份")
    if (not isinstance(flows, list) or not flows or len(set(flows)) != len(flows)
            or any(not isinstance(f, str) or f not in FLOWS for f in flows)):
        raise ValueError("scope.flows 必须是不重复的 X / M 列表")
    if scope.get("cmdCode") != "TOTAL":
        raise ValueError("scope.cmdCode 只能是 TOTAL")
    return pairs, list(range(start, end + 1)), flows


def _require_key(spec: SourceSpec) -> None:
    # The messages fit the templates in core.errors, so the failure reason keeps the variable name (never a value).
    if spec.needs_key is None:
        raise ValueError("needs_key 必须是非空字符串")
    if spec.key_header is None:
        raise ValueError("key_header 必须是非空字符串")
    if not os.environ.get(spec.needs_key.removeprefix("env:")):
        raise ValueError(f"{spec.needs_key.removeprefix('env:')} 必须是非空字符串")


def _release_url(ctx: CrawlContext, reporter: int, year: int) -> str:
    query = urlencode({"reporterCode": reporter, "period": year})
    return f"{ctx.spec.base_url.rstrip('/')}/public/v1/getDA/C/A/HS?{query}"


def _data_url(ctx: CrawlContext, reporter: int, partner: int, year: int, flows: list[str]) -> str:
    params = {"reporterCode": reporter, "partnerCode": partner, "period": year, "flowCode": ",".join(flows),
              **_TOTAL_ROW}
    return f"{ctx.spec.base_url.rstrip('/')}/data/v1/get/C/A/HS?{urlencode(params, safe=',')}"


class UnComtradeCrawler(Crawler):
    kind = "un_comtrade"

    def plan(self, ctx: CrawlContext) -> Iterable[FetchTask]:
        pairs, years, flows = _scope(ctx.spec.scope)
        _require_key(ctx.spec)
        partners: dict[int, list[int]] = {}
        for a, b in pairs:
            partners.setdefault(a, []).append(b)
            partners.setdefault(b, []).append(a)
        planned = 0
        for reporter, others in partners.items():
            for year in years:
                if ctx.limit is not None and planned >= ctx.limit:
                    return
                planned += 1
                yield FetchTask(url=_release_url(ctx, reporter, year), meta={
                    "request_type": "release", "reporter": reporter, "year": year, "partners": others, "flows": flows,
                })

    def after_fetch(self, task: FetchTask, result: FetchResult, ctx: CrawlContext) -> Iterable[FetchTask]:
        if task.meta.get("request_type") != "release":
            return
        if result.outcome is FetchOutcome.ok:
            data = result.content
        elif result.outcome is FetchOutcome.not_modified:
            previous = ctx.manifest.last_success(task.redacted_url)
            if previous is None or previous.sha256 is None:
                raise ValueError("Comtrade getDA 304 缺少可复用快照")
            data = ctx.raw.get(previous.sha256)
        else:
            return
        meta = task.meta
        reporter, year = int(meta["reporter"]), int(meta["year"])
        releases = read_releases(data, reporter, year)
        if not releases:  # not released (yet): nothing to fetch, not a failure
            return
        for partner in meta["partners"]:
            url = _data_url(ctx, reporter, int(partner), year, list(meta["flows"]))
            # Compare with the release records stored with the last *successful* data fetch of this URL, so a
            # failed fetch is retried although getDA itself has not changed since.
            done = ctx.manifest.last_success(url)
            if done is not None and ctx.manifest.snapshot_meta(done.snapshot_id).get("releases") == releases:
                continue
            yield FetchTask(url=url, meta={
                "request_type": "data", "reporter": reporter, "partner": int(partner), "year": year,
                "releases": releases,
            })
