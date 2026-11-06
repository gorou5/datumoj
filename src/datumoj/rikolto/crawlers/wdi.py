"""Plan complete, single-page World Development Indicators snapshots."""
from collections.abc import Iterable
from urllib.parse import urlencode, urljoin

from datumoj.rikolto.core.contracts import CrawlContext, Crawler, FetchTask

_COUNTRIES = {"JPN", "USA", "GBR", "DEU", "FRA", "NLD"}
_INDICATORS = {"SP.POP.TOTL", "NY.GDP.MKTP.CD", "NY.GDP.MKTP.CN", "SP.DYN.LE00.IN"}
_PER_PAGE = 1000


class WDICrawler(Crawler):
    kind = "worldbank_wdi"

    def plan(self, ctx: CrawlContext) -> Iterable[FetchTask]:
        scope = ctx.spec.scope
        countries, indicators = scope.get("countries"), scope.get("indicators")
        start, end = scope.get("start_year"), scope.get("end_year")
        if (not isinstance(countries, list) or not countries
                or any(not isinstance(c, str) or c not in _COUNTRIES for c in countries)
                or len(set(countries)) != len(countries)):
            raise ValueError("scope.countries 必须是选定的国家代码")
        if (not isinstance(indicators, dict) or not indicators or not set(indicators) <= _INDICATORS
                or any(not isinstance(v, str) or not v.strip() for v in indicators.values())):
            raise ValueError("scope.indicators 必须是选定指标及其单位")
        if (type(start) is not int or type(end) is not int or not 1 <= start <= end <= 9999
                or len(countries) * (end - start + 1) > _PER_PAGE):
            raise ValueError("年份范围无效或超过单页容量")
        base = ctx.spec.base_url.rstrip("/") + "/"
        tasks = []
        for code, unit in indicators.items():
            metadata_url = urljoin(base, f"indicator/{code}") + "?" + urlencode({"format": "json", "source": 2})
            tasks.append(FetchTask(url=metadata_url, meta={"request_type": "metadata", "indicator": code}))
            params = {"format": "json", "source": 2, "date": f"{start}:{end}", "per_page": _PER_PAGE}
            data_url = urljoin(base, f"country/{';'.join(countries)}/indicator/{code}") + "?" + urlencode(params)
            tasks.append(FetchTask(url=data_url, meta={"request_type": "data", "indicators": {code: unit},
                                                         "countries": countries, "start_year": start, "end_year": end}))
        return tasks[:max(0, ctx.limit)] if ctx.limit is not None else tasks
