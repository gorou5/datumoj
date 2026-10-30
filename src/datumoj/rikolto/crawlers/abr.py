"""Selected ABR town text archive; robots and rate limits belong to the runner."""

from collections.abc import Iterable
from urllib.parse import urlsplit

from datumoj.rikolto.core.contracts import CrawlContext, Crawler, FetchResult, FetchTask
from datumoj.rikolto.core.models import FetchOutcome
from datumoj.rikolto.parsers._zip import MAX_FILE_BYTES
from datumoj.rikolto.parsers.abr import MUNICIPALITY_CODE


class AbrCrawler(Crawler):
    kind = "abr_town"

    def plan(self, ctx: CrawlContext) -> Iterable[FetchTask]:
        url = ctx.spec.scope.get("url")
        if not isinstance(url, str):
            raise ValueError("scope.url must be an HTTPS URL")
        parts = urlsplit(url)
        if (parts.scheme != "https" or parts.hostname != "data.address-br.digital.go.jp"
                or parts.hostname not in ctx.spec.allowed_hosts or parts.username or parts.password
                or parts.port not in (None, 443)
                or parts.path != f"/mt_town/city/mt_town_city{MUNICIPALITY_CODE}.csv.zip"):
            raise ValueError("ABR URL must select the town archive on the allowed download host")
        if ctx.spec.scope.get("municipality_code") != MUNICIPALITY_CODE:
            raise ValueError(f"scope.municipality_code must be {MUNICIPALITY_CODE!r}")
        if ctx.limit is None or ctx.limit > 0:
            yield FetchTask(url=url, meta={"municipality_code": MUNICIPALITY_CODE})

    def classify(self, task: FetchTask, result: FetchResult) -> FetchOutcome | None:
        if result.outcome is FetchOutcome.ok and result.content is not None and len(result.content) > MAX_FILE_BYTES:
            return FetchOutcome.too_large
        return None
