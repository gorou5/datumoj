"""Named KSJ files; runner supplies robots, allowlist, rate and run budgets."""

from collections.abc import Iterable
from urllib.parse import urlsplit

from datumoj.rikolto.core.contracts import CrawlContext, Crawler, FetchResult, FetchTask
from datumoj.rikolto.core.models import FetchOutcome
from datumoj.rikolto.parsers._zip import MAX_FILE_BYTES


class KsjCrawler(Crawler):
    kind = "ksj_file"

    def plan(self, ctx: CrawlContext) -> Iterable[FetchTask]:
        files = ctx.spec.scope.get("files")
        if not isinstance(files, list) or not files:
            raise ValueError("scope.files must be a nonempty URL list")
        for url in files:
            if not isinstance(url, str):
                raise ValueError("scope.files entries must be URLs")
            parts = urlsplit(url)
            if (parts.scheme != "https" or parts.hostname not in ctx.spec.allowed_hosts
                    or parts.username or parts.password or parts.port not in (None, 443)):
                raise ValueError("KSJ URL must use HTTPS on an allowed host")
        for url in files[:ctx.limit]:
            yield FetchTask(url=url)

    def classify(self, task: FetchTask, result: FetchResult) -> FetchOutcome | None:
        if result.outcome is FetchOutcome.ok and result.content is not None and len(result.content) > MAX_FILE_BYTES:
            return FetchOutcome.too_large
        return None
