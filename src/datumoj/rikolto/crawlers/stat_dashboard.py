"""Crawler for the Statistics Dashboard API."""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from urllib.parse import urlencode, urljoin

from datumoj.rikolto.core.contracts import CrawlContext, Crawler, FetchTask
from datumoj.rikolto.core.models import FetchOutcome
from datumoj.rikolto.reference.areas import lookup

_INDICATOR_CODE = re.compile(r"^[0-9]{19}$")


def plan_batches(
    indicators: Iterable[str], regions: Iterable[str],
) -> list[tuple[tuple[str, ...], tuple[str, ...]]]:
    """Split both axes independently, then return their Cartesian product."""
    indicator_values, region_values = list(indicators), list(regions)
    if not indicator_values or not region_values:
        return []
    indicator_batches = [tuple(indicator_values[i : i + 5]) for i in range(0, len(indicator_values), 5)]
    region_batches = [tuple(region_values[i : i + 50]) for i in range(0, len(region_values), 50)]
    return [(indicator_batch, region_batch) for indicator_batch in indicator_batches for region_batch in region_batches]


def _all_region_codes() -> list[str]:
    national = lookup("00000")
    prefectures = [lookup(f"{number:02d}") for number in range(1, 48)]
    if len(national) != 1 or any(len(matches) != 1 for matches in prefectures):
        raise ValueError("DATUMOJ-IVY-302 地区码表缺少全国或都道府県记录")
    return [national[0].code5, *(matches[0].code5 for matches in prefectures)]


def _modified_from(ctx: CrawlContext, indicators: Sequence[str], regions: Sequence[str]) -> str | None:
    wanted_indicators, wanted_regions = list(indicators), list(regions)
    latest = None
    for snapshot in ctx.manifest.snapshots_for(ctx.spec.id):
        if snapshot.outcome is not FetchOutcome.ok:
            continue
        meta = ctx.manifest.snapshot_meta(snapshot.snapshot_id)
        if (
            meta.get("request_type") == "data"
            and meta.get("indicators") == wanted_indicators
            and meta.get("regions") == wanted_regions
            and (latest is None or snapshot.fetched_at >= latest.fetched_at)
        ):
            latest = snapshot
    return latest.fetched_at.strftime("%Y%m%d") if latest is not None else None


class StatDashboardCrawler(Crawler):
    kind = "stat_dashboard"

    def plan(self, ctx: CrawlContext) -> Iterable[FetchTask]:
        indicators = ctx.spec.scope.get("indicators")
        if not isinstance(indicators, list) or not indicators:
            raise ValueError("scope.indicators 必须是非空指标码列表")
        if any(not isinstance(code, str) or not _INDICATOR_CODE.fullmatch(code) for code in indicators):
            raise ValueError("scope.indicators 中的指标码必须是 19 位数字字符串")
        if len(set(indicators)) != len(indicators):
            raise ValueError("scope.indicators 中不能有重复指标码")

        base = ctx.spec.base_url.rstrip("/") + "/"
        count = 0
        if ctx.limit is not None and ctx.limit <= 0:
            return
        catalog_url = urljoin(base, "Json/getIndicatorInfo") + "?" + urlencode({"Lang": "JP"})
        yield FetchTask(url=catalog_url, meta={"request_type": "catalog"})
        count += 1

        for indicator_batch, region_batch in plan_batches(indicators, _all_region_codes()):
            if ctx.limit is not None and count >= ctx.limit:
                break
            params = {
                "Lang": "JP",
                "IndicatorCode": ",".join(indicator_batch),
                "RegionCode": ",".join(region_batch),
                "IsSeasonalAdjustment": "1",
                "MetaGetFlg": "Y",
                "SectionHeaderFlg": "1",
            }
            modified_from = _modified_from(ctx, indicator_batch, region_batch)
            if modified_from is not None:
                params["modifiedFrom"] = modified_from
            url = urljoin(base, "Json/getData") + "?" + urlencode(params)
            yield FetchTask(
                url=url,
                meta={
                    "request_type": "data",
                    "indicators": list(indicator_batch),
                    "regions": list(region_batch),
                },
            )
            count += 1
