"""WTO merchandise trade annual bulk ZIP: one conditional GET on stats.wto.org.

The runner owns robots, host checks, rate limits and conditional requests
(ETag / If-Modified-Since from the last snapshot); a 304 stores no new snapshot.
The scope filter travels with the task so reparsing needs no network.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from urllib.parse import urljoin

from datumoj.rikolto.core.contracts import CrawlContext, Crawler, FetchTask

_PATH = re.compile(r"[A-Za-z0-9_]+(/[A-Za-z0-9_]+)*\.zip")
_CODE = re.compile(r"[A-Za-z0-9_]+")


def _codes(value: object, name: str, pattern: str | None = None) -> list[str]:
    if (not isinstance(value, list) or not value or len(set(value)) != len(value)
            or any(not isinstance(v, str) or not re.fullmatch(pattern or _CODE.pattern, v) for v in value)):
        raise ValueError(f"scope.{name} 必须是非空、不重复的代码列表")
    return value


class WTOBulkCrawler(Crawler):
    kind = "wto_bulk"

    def plan(self, ctx: CrawlContext) -> Iterable[FetchTask]:
        scope = ctx.spec.scope
        path = scope.get("path")
        if not isinstance(path, str) or not _PATH.fullmatch(path):
            raise ValueError("scope.path 必须是相对路径的 .zip 文件")
        indicators = _codes(scope.get("indicators"), "indicators")
        reporters = _codes(scope.get("reporters"), "reporters", r"[0-9]{3}")
        partner, product = scope.get("partner"), scope.get("product")
        if any(not isinstance(v, str) or not _CODE.fullmatch(v) for v in (partner, product)):
            raise ValueError("scope.partner / scope.product 必须是代码")
        start, end = scope.get("start_year"), scope.get("end_year")
        if type(start) is not int or type(end) is not int or not 1 <= start <= end <= 9999:
            raise ValueError("start_year / end_year 必须是有序的年份")
        if ctx.limit is not None and ctx.limit < 1:
            return
        yield FetchTask(
            url=urljoin(ctx.spec.base_url.rstrip("/") + "/", path),
            meta={"request_type": "bulk", "indicators": indicators, "reporters": reporters,
                  "partner": partner, "product": product, "start_year": start, "end_year": end},
        )
