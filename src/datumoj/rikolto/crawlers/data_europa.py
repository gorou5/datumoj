"""data.europa.eu catalogue repo pagination and dataset metadata."""

import json
from urllib.parse import quote, urlencode

from datumoj.rikolto.core.contracts import CrawlContext, Crawler, FetchResult, FetchTask
from datumoj.rikolto.core.models import FetchOutcome


class DataEuropaCrawler(Crawler):
    kind = "data_europa_repo"

    def plan(self, ctx: CrawlContext):
        self.seen: set[str] = set()
        limit = ctx.spec.scope.get("limit", 100)
        if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
            raise ValueError("scope.limit must be a positive integer")
        if ctx.spec.scope.get("catalogue") != "publ":
            raise ValueError("only the publ catalogue is supported")
        yield self._page(ctx, limit, 0)

    def _page(self, ctx, limit, offset):
        query = urlencode({"valueType": "identifiers", "limit": limit, "offset": offset})
        return FetchTask(url=f"{ctx.spec.base_url.rstrip('/')}/catalogues/publ/datasets?{query}",
                         meta={"role": "identifiers", "limit": limit, "offset": offset})

    def after_fetch(self, task: FetchTask, result: FetchResult, ctx: CrawlContext):
        if task.meta.get("role") != "identifiers":
            return ()
        content = result.content
        if result.outcome is FetchOutcome.not_modified:
            previous = ctx.manifest.last_success(task.url)
            content = ctx.raw.get(previous.sha256) if previous and previous.sha256 else None
        elif result.outcome is not FetchOutcome.ok:
            return ()
        try:
            ids = json.loads(content or b"")
        except (ValueError, UnicodeDecodeError) as exc:
            raise ValueError("invalid catalogue identifiers JSON") from exc
        if not isinstance(ids, list) or not all(isinstance(i, str) and i for i in ids):
            raise ValueError("catalogue identifiers must be an array of nonempty strings")
        tasks = []
        for identifier in ids:
            if identifier in self.seen or (ctx.limit is not None and len(self.seen) >= ctx.limit):
                continue
            self.seen.add(identifier)
            tasks.append(FetchTask(url=f"{ctx.spec.base_url.rstrip('/')}/datasets/{quote(identifier, safe='')}",
                                   meta={"role": "dataset", "dataset_id": identifier}))
        if len(ids) >= task.meta["limit"] and (ctx.limit is None or len(self.seen) < ctx.limit):
            tasks.append(self._page(ctx, task.meta["limit"], task.meta["offset"] + len(ids)))
        return tasks
