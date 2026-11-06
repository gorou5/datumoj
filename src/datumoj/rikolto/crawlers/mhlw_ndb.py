"""Download the selected NDB questionnaire ZIP through the polite runner."""

from collections.abc import Iterable
from urllib.parse import urlsplit

from datumoj.rikolto.core.contracts import CrawlContext, Crawler, FetchResult, FetchTask
from datumoj.rikolto.core.models import FetchOutcome
from datumoj.rikolto.parsers._zip import MAX_FILE_BYTES
from datumoj.rikolto.parsers.mhlw_ndb import MEMBER


class MhlwNdbCrawler(Crawler):
    kind = "mhlw_ndb"

    def plan(self, ctx: CrawlContext) -> Iterable[FetchTask]:
        url = ctx.spec.scope.get("url")
        if not isinstance(url, str):
            raise ValueError("scope.url must be an HTTPS URL")
        parts = urlsplit(url)
        if (parts.scheme != "https" or parts.hostname != "www.mhlw.go.jp"
                or parts.hostname not in ctx.spec.allowed_hosts or parts.username or parts.password
                or parts.port not in (None, 443) or parts.path != "/content/12400000/001711941.zip"):
            raise ValueError("NDB URL must use HTTPS on the allowed MHLW host")
        if ctx.spec.scope.get("member") != MEMBER:
            raise ValueError(f"scope.member must be {MEMBER!r}")
        if ctx.limit is None or ctx.limit > 0:
            yield FetchTask(url=url, meta={"member": MEMBER})

    def classify(self, task: FetchTask, result: FetchResult) -> FetchOutcome | None:
        if result.outcome is FetchOutcome.ok and result.content is not None and len(result.content) > MAX_FILE_BYTES:
            return FetchOutcome.too_large
        return None
