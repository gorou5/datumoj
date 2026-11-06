"""Plan e-Stat table snapshots and paginated catalog requests."""
import json
import os
import re
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

from datumoj.rikolto.core.contracts import CrawlContext, Crawler, FetchResult, FetchTask, NotFoundError
from datumoj.rikolto.core.models import FetchOutcome


class EstatAPICrawler(Crawler):
    kind = "estat_api"

    def plan(self, ctx: CrawlContext):
        ref = ctx.spec.needs_key
        if not ref:
            raise ValueError("needs_key")
        name = ref.removeprefix("env:")
        key = os.environ.get(name)
        if not key:
            raise ValueError(name)
        tables = ctx.spec.scope.get("tables")
        codes = ctx.spec.scope.get("stats_codes")
        if not isinstance(tables, list) or not isinstance(codes, list):
            raise ValueError("scope tables/stats_codes")
        tasks = []
        for table in tables:
            if not isinstance(table, dict):
                raise ValueError("table")
            sid, filters, unit = table.get("stats_data_id"), table.get("filters"), table.get("unit")
            if not isinstance(sid, str) or not re.fullmatch(r"[0-9]{10}", sid):
                raise ValueError("stats_data_id")
            if (not isinstance(filters, dict) or not isinstance(unit, str) or not unit
                    or any(not isinstance(k, str) or not re.fullmatch(r"cd(?:Tab|Cat(?:0[1-9]|1[0-5])|Area|Time)", k)
                           or not isinstance(v, str) for k, v in filters.items())):
                raise ValueError("filters or unit")
            params = {"lang": "J", "statsDataId": sid, **filters, "limit": "100000", "appId": key}
            tasks.append(FetchTask(url=urljoin(ctx.spec.base_url, "getStatsData") + "?" + urlencode(params),
                                   meta={"request_type": "data", "stats_data_id": sid, "unit": unit}))
        for code in codes:
            if not isinstance(code, str) or not re.fullmatch(r"[0-9]{8}", code):
                raise ValueError("stats_codes")
            params = {"lang": "J", "statsCode": code, "appId": key}
            tasks.append(FetchTask(url=urljoin(ctx.spec.base_url, "getStatsList") + "?" + urlencode(params),
                                   meta={"request_type": "list", "stats_code": code}))
        return tasks[:max(0, ctx.limit)] if ctx.limit is not None else tasks

    def after_fetch(self, task: FetchTask, result: FetchResult, ctx: CrawlContext):
        if task.meta.get("request_type") != "list":
            return ()
        content = result.content
        if result.outcome is FetchOutcome.not_modified:
            previous = ctx.manifest.last_success(task.redacted_url)
            if previous is None or previous.sha256 is None:
                return ()
            try:
                content = ctx.raw.get(previous.sha256)
            except NotFoundError:
                return ()
        elif result.outcome is not FetchOutcome.ok:
            return ()
        if not content:
            return ()
        try:
            root = json.loads(content)["GET_STATS_LIST"]
            if str(root["RESULT"]["STATUS"]) != "0":
                return ()
            next_key = root["DATALIST_INF"]["RESULT_INF"].get("NEXT_KEY")
            if next_key is None:
                return ()
            if not re.fullmatch(r"[0-9]+", str(next_key)) or int(next_key) <= 0:
                raise ValueError
            parts = urlsplit(task.url)
            params = dict(parse_qsl(parts.query))
            if int(next_key) <= int(params.get("startPosition", "1")):
                raise ValueError
            params["startPosition"] = str(next_key)
            return (FetchTask(url=urlunsplit(parts._replace(query=urlencode(params))), meta=task.meta),)
        except (ValueError, KeyError, TypeError):
            raise ValueError("invalid catalog pagination") from None
