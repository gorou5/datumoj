"""e-Stat 统计表文件采集器：按清单里的 statInfId 从网站文件下载入口取文件（不用 e-Stat API）。

清单在来源配置的 ``scope.tables``（见 ``sources/jp-estat-files.yaml``），每项原样放进 FetchTask.meta、随快照
写进 manifest。表题、调查名、地区是否和清单一致由解析器 ``datumoj.parsers.estat_tables`` 按文件内容核对，
不一致时解析失败（文件名不可信：东京和大阪的下载名都是 ``02000.csv``）。
"""

from __future__ import annotations

from collections.abc import Iterable
from urllib.parse import urlencode, urljoin

from datumoj.core.contracts import CrawlContext, Crawler, FetchTask
from datumoj.parsers.estat_tables import LAYOUT_FORMATS, TableEntry


class EstatFilesCrawler(Crawler):
    kind = "estat_files"

    def plan(self, ctx: CrawlContext) -> Iterable[FetchTask]:
        tables = ctx.spec.scope.get("tables")
        if not isinstance(tables, list) or not tables:
            raise ValueError("scope.tables 必须是非空列表")
        entries = [TableEntry.model_validate(item) for item in tables]
        ids = [entry.stat_inf_id for entry in entries]
        if len(set(ids)) != len(ids):
            raise ValueError("scope.tables 里有重复的 stat_inf_id")
        for entry in entries[: ctx.limit]:
            query = urlencode({"statInfId": entry.stat_inf_id, "fileKind": LAYOUT_FORMATS[entry.layout][1]})
            url = urljoin(ctx.spec.base_url, f"/stat-search/file-download?{query}")
            yield FetchTask(url=url, meta=entry.model_dump())
