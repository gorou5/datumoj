"""IMF WEO on api.imf.org (SDMX 2.1, SDMX-CSV): one request per dataflow x key, only what the yaml lists.

The runner owns robots, host checks, rate limits, budgets and raw snapshots. Requests for one key go
oldest release first and the current ``WEO`` flow last, because the store keeps the last parsed
snapshot of a table as its current rows and that must be the newest release.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from urllib.parse import urlencode

from datumoj.rikolto.core.contracts import CrawlContext, Crawler, FetchTask
from datumoj.rikolto.parsers.imf_sdmx import AGENCY, DATAFLOW, parse_key

ACCEPT = "application/vnd.sdmx.data+csv;version=1.0.0"
_YEAR = re.compile(r"[0-9]{4}")


def _age(flow: str) -> tuple[int, int]:
    """Sort key: vintages by release (year, APR < OCT), the current flow after all of them."""
    year, month = DATAFLOW.fullmatch(flow).groups()
    return (int(year), 1 if month == "APR" else 2) if year else (9999, 0)


class ImfSdmxCrawler(Crawler):
    kind = "imf_sdmx"

    def plan(self, ctx: CrawlContext) -> Iterable[FetchTask]:
        scope = ctx.spec.scope
        flows, series = scope.get("dataflows"), scope.get("series")
        # Validate the whole scope before yielding any request.
        if (not isinstance(flows, list) or not flows
                or any(not isinstance(f, str) or not DATAFLOW.fullmatch(f) for f in flows)
                or len(set(flows)) != len(flows)):
            raise ValueError("scope.dataflows 必须是非空、不重复的 WEO 或 WEO_<YYYY>_<APR|OCT>_VINTAGE 列表")
        if not isinstance(series, list) or not series:
            raise ValueError("scope.series 必须是非空列表")
        keys = set()
        for entry in series:
            if not isinstance(entry, dict):
                raise ValueError("scope.series 条目必须是对象")
            key, start, end = entry.get("query"), entry.get("startPeriod"), entry.get("endPeriod")
            parse_key(key)  # ValueError for characters outside dimension codes, '.' and '+'
            if key in keys:
                raise ValueError("scope.series.query（SDMX key）不能重复")
            keys.add(key)
            if any(not isinstance(v, str) or not _YEAR.fullmatch(v) for v in (start, end)) or start > end:
                raise ValueError("startPeriod / endPeriod 必须是有序的四位年份字符串")
        base = ctx.spec.base_url.rstrip("/")
        tasks = [
            FetchTask(
                url=f"{base}/data/{AGENCY},{flow}/{entry['query']}?"
                    + urlencode({"startPeriod": entry["startPeriod"], "endPeriod": entry["endPeriod"]}),
                headers={"Accept": ACCEPT},
                meta={"dataflow": flow, "key": entry["query"],
                      "startPeriod": entry["startPeriod"], "endPeriod": entry["endPeriod"]},
            )
            for entry in series for flow in sorted(flows, key=_age)
        ]
        return tasks[:ctx.limit]
