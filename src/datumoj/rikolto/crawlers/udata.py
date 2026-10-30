"""Fetch udata dataset directory pages, never resource files."""

import json
import logging
import re
from collections.abc import Iterable
from urllib.parse import urlencode

from datumoj.rikolto.core.contracts import CrawlContext, Crawler, FetchResult, FetchTask
from datumoj.rikolto.core.models import FetchOutcome

log = logging.getLogger(__name__)


class UdataCrawler(Crawler):
    kind = "udata"

    def plan(self, ctx: CrawlContext) -> Iterable[FetchTask]:
        org = ctx.spec.scope.get("organization")
        size = ctx.spec.scope.get("page_size", 100)
        if not isinstance(org, str) or not re.fullmatch(r"[0-9a-fA-F]{24}", org):
            raise ValueError("scope.organization must be a 24-character hexadecimal id")
        if not isinstance(size, int) or isinstance(size, bool) or size <= 0:
            raise ValueError("scope.page_size must be a positive integer")
        yield self._page(ctx, org, size, 1, None)

    @staticmethod
    def _page(ctx: CrawlContext, org: str, size: int, page: int, cap: int | None) -> FetchTask:
        query = urlencode({"organization": org, "page_size": size, "page": page})
        return FetchTask(url=f"{ctx.spec.base_url.rstrip('/')}/datasets/?{query}",
                         meta={"organization": org, "page_size": size, "page": page, "page_cap": cap})

    def after_fetch(self, task: FetchTask, result: FetchResult, ctx: CrawlContext) -> Iterable[FetchTask]:
        content = result.content
        if result.outcome is FetchOutcome.not_modified:
            previous = ctx.manifest.last_success(task.url)
            content = ctx.raw.get(previous.sha256) if previous and previous.sha256 else None
        elif result.outcome is not FetchOutcome.ok:
            return ()
        try:
            payload = json.loads(content or b"")
        except (ValueError, UnicodeDecodeError):
            raise ValueError("udata page is not valid JSON") from None
        if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
            raise ValueError("udata page is missing data")
        total = payload.get("total")
        if not isinstance(total, int) or isinstance(total, bool) or total < 0:
            raise ValueError("udata page has invalid total")
        size, page = task.meta["page_size"], task.meta["page"]
        cap = task.meta.get("page_cap") or (total + size - 1) // size + 2
        if not payload.get("next_page"):
            return ()
        if page >= cap:
            log.warning("udata pagination stopped: page cap %s exceeded", cap)
            return ()
        return [self._page(ctx, task.meta["organization"], size, page + 1, cap)]
