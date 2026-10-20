"""Generic CKAN crawler: paged ``package_search`` (or the activity list for incremental runs) -> whitelisted resource files."""

from __future__ import annotations

import json
import unicodedata
from collections.abc import Iterable, Mapping
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote, urlencode, urlsplit

from datumoj.core.contracts import CrawlContext, Crawler, FetchResult, FetchTask
from datumoj.core.models import FetchOutcome

DEFAULT_ROWS = 100
DEFAULT_ACTIVITY_LIMIT = 100
DEFAULT_MAX_FILE_BYTES = 50 * 1024 * 1024
_API_ROLES = ("package_search", "activity", "package_show")
_HTML_FORMATS = frozenset({"html", "htm", "xhtml", "php", "asp", "aspx", "jsp"})
_HTML_HEADS = (b"<!doctype html", b"<html")


def _norm_format(value: Any) -> str:
    """``".CSV"`` / ``"ＣＳＶ"`` / ``" csv "`` -> ``"csv"``；空值 -> ``""``。"""
    return unicodedata.normalize("NFKC", str(value or "")).strip().lstrip(".").strip().lower()


def _clean(value: Any) -> str | None:
    """许可等字段：空串、单个空格都当缺失；有值则原样（去首尾空白）。"""
    text = value.strip() if isinstance(value, str) else ""
    return text or None


def _host_allowed(host: str, allowed: Iterable[str]) -> bool:
    """与契约一致：精确匹配，或 ``*.example.jp`` 通配其子域名。"""
    host = host.lower()
    return any(host == a.lower() or (a.startswith("*.") and host.endswith(a[1:].lower())) for a in allowed)


def _json_object(raw: bytes | None) -> dict[str, Any] | None:
    try:
        data = json.loads(raw or b"")
    except (ValueError, UnicodeDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _api_ok(data: Mapping[str, Any] | None) -> bool:
    return data is not None and data.get("success") is True and "result" in data


def _positive_int(scope: Mapping[str, Any], key: str, default: int) -> int:
    value = scope.get(key, default)
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ValueError(f"scope.{key} 必须是正整数")
    return value


def _str_list(scope: Mapping[str, Any], key: str) -> list[str] | None:
    value = scope.get(key)
    if value is None:
        return None
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise ValueError(f"scope.{key} 必须是字符串列表")
    return value


def _utc(text: str) -> datetime | None:
    """CKAN 时间戳没有时区后缀，按 UTC。"""
    try:
        parsed = datetime.fromisoformat(text)
    except (TypeError, ValueError):
        return None
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed


class CkanCrawler(Crawler):
    """CKAN 门户的数据集元数据 + 白名单内的资源文件。

    ``spec.base_url`` 是 action 基址（如 ``https://data.e-gov.go.jp/data/api/action``）。scope 可选键：

    - ``q``（默认 ``*:*``）、``rows``（每页条数，默认 100）、``incremental``（默认 true）、``activity_limit``；
    - 白名单：``formats``（规范化后比较）、``organizations``（组织 name 或 title）、``max_file_bytes``
      （默认 50 MiB）；资源所在 host 也必须在 ``allowed_hosts`` 内。不在白名单的资源只留元数据（已在
      数据集快照里），不下载。

    翻页只看实际返回条数：空页才停。增量用 recently_changed_packages_activity_list，活动列表不可用、
    解析失败或窗口不够时退回全量，原因写进全量任务的 ``meta["incremental_fallback"]``。
    ``ctx.limit`` 限制处理的数据集个数。
    """

    kind = "ckan"

    def __init__(self) -> None:
        self._seen: set[str] = set()
        self._previous: dict[str, str | None] = {}

    # ----- 任务 -----

    def plan(self, ctx: CrawlContext) -> Iterable[FetchTask]:
        self._seen = set()
        since, self._previous = self._load_previous(ctx)
        if ctx.spec.scope.get("incremental", True) is not False and since is not None:
            limit = _positive_int(ctx.spec.scope, "activity_limit", DEFAULT_ACTIVITY_LIMIT)
            url = f"{self._base(ctx)}/recently_changed_packages_activity_list?limit={limit}"
            yield FetchTask(url=url, meta={"role": "activity", "since": since.isoformat(), "limit": limit,
                                           "run_started": self._started(ctx)})
        else:
            yield self._search_task(ctx, 0)

    def after_fetch(self, task: FetchTask, result: FetchResult, ctx: CrawlContext) -> Iterable[FetchTask]:
        role = task.meta.get("role")
        if role == "activity":
            return self._after_activity(task, result, ctx)
        if result.outcome is not FetchOutcome.ok or result.content is None or role not in _API_ROLES:
            return ()
        data = _json_object(result.content)
        if not _api_ok(data):
            return ()
        if role == "package_search":
            return self._after_search(task, data["result"], ctx)
        return self._resource_tasks(data["result"], ctx)  # package_show

    def classify(self, task: FetchTask, result: FetchResult) -> FetchOutcome | None:
        """实际正文超过单文件上限 -> too_large；API 的 success=false、指向 404 页的 HTML 都不算抓取成功。"""
        if result.outcome is not FetchOutcome.ok or result.content is None:
            return None
        role = task.meta.get("role")
        if role in _API_ROLES:
            return None if _api_ok(_json_object(result.content)) else FetchOutcome.empty_body
        if role != "resource":
            return None
        limit = task.meta.get("max_file_bytes")
        if isinstance(limit, int) and len(result.content) > limit:
            return FetchOutcome.too_large
        head = result.content[:512].lstrip().lower()
        if _norm_format(task.meta.get("format")) not in _HTML_FORMATS and head.startswith(_HTML_HEADS):
            return FetchOutcome.empty_body  # 失效链接常 302 到一个 HTML 错误页
        return None

    # ----- 内部 -----

    @staticmethod
    def _base(ctx: CrawlContext) -> str:
        return ctx.spec.base_url.rstrip("/")

    @staticmethod
    def _started(ctx: CrawlContext) -> str:
        return ctx.now.astimezone(timezone.utc).isoformat()

    def _search_task(self, ctx: CrawlContext, start: int, fallback: str | None = None) -> FetchTask:
        scope = ctx.spec.scope
        rows = _positive_int(scope, "rows", DEFAULT_ROWS)
        query = urlencode({"q": scope.get("q", "*:*"), "rows": rows, "start": start, "sort": "id asc"}, quote_via=quote)
        meta: dict[str, Any] = {"role": "package_search", "start": start, "rows": rows, "run_started": self._started(ctx)}
        if fallback:
            meta["incremental_fallback"] = fallback
        return FetchTask(url=f"{self._base(ctx)}/package_search?{query}", meta=meta)

    @staticmethod
    def _load_previous(ctx: CrawlContext) -> tuple[datetime | None, dict[str, str | None]]:
        """(上次元数据运行的开始时间, 资源 URL -> 上次成功下载时的数据集 metadata_modified)。"""
        since: datetime | None = None
        previous: dict[str, str | None] = {}
        for snap in ctx.manifest.snapshots_for(ctx.spec.id):
            if snap.outcome is not FetchOutcome.ok:
                continue
            meta = ctx.manifest.snapshot_meta(snap.snapshot_id)
            if meta.get("role") in ("package_search", "package_show") and isinstance(meta.get("run_started"), str):
                started = _utc(meta["run_started"])
                if started is not None and (since is None or started > since):
                    since = started
            elif meta.get("role") == "resource":
                previous[snap.resource_url] = meta.get("package_modified")  # type: ignore[assignment]
        return since, previous

    def _after_activity(self, task: FetchTask, result: FetchResult, ctx: CrawlContext) -> Iterable[FetchTask]:
        since = _utc(task.meta["since"])
        data = _json_object(result.content) if result.outcome is FetchOutcome.ok else None
        items = data.get("result") if _api_ok(data) else None
        if not isinstance(items, list):
            reason = f"活动列表不可用（{result.outcome.value}）"
            return [self._search_task(ctx, 0, reason)]
        stamps = [_utc(i.get("timestamp")) if isinstance(i, dict) else None for i in items]
        if any(s is None for s in stamps):
            return [self._search_task(ctx, 0, "活动列表的时间戳无法解析")]
        if len(items) >= task.meta["limit"] and min(stamps) > since:  # 最旧一条仍比上次新：窗口可能漏了
            return [self._search_task(ctx, 0, "活动列表窗口不足以覆盖上次运行以来的变更")]
        ids: dict[str, None] = {}  # 去重并保持顺序：同一数据集会连续出现多次
        for item, stamp in zip(items, stamps):
            object_id, kind = item.get("object_id"), str(item.get("activity_type", ""))
            if stamp >= since and isinstance(object_id, str) and object_id and not kind.startswith("deleted"):
                ids[object_id] = None
        tasks = []
        for object_id in ids:
            if ctx.limit is not None and len(tasks) >= ctx.limit:
                break
            url = f"{self._base(ctx)}/package_show?id={quote(object_id, safe='')}"
            tasks.append(FetchTask(url=url, meta={"role": "package_show", "dataset_id": object_id,
                                                  "run_started": task.meta["run_started"]}))
        return tasks

    def _after_search(self, task: FetchTask, payload: Any, ctx: CrawlContext) -> Iterable[FetchTask]:
        results = payload.get("results") if isinstance(payload, dict) else None
        if not isinstance(results, list):
            raise ValueError(f"package_search 响应缺少 results 数组：{task.redacted_url}")
        tasks: list[FetchTask] = []
        for package in results:
            tasks.extend(self._resource_tasks(package, ctx))
        count = len(results)  # 只按实际返回条数判断：空页才停
        next_start = task.meta["start"] + count
        if count > 0 and (ctx.limit is None or len(self._seen) < ctx.limit):
            tasks.append(self._search_task(ctx, next_start))
        return tasks

    def _resource_tasks(self, package: Any, ctx: CrawlContext) -> list[FetchTask]:
        if not isinstance(package, dict) or not isinstance(package.get("id"), str):
            return []
        if package["id"] in self._seen or (ctx.limit is not None and len(self._seen) >= ctx.limit):
            return []
        self._seen.add(package["id"])
        scope = ctx.spec.scope
        formats = _str_list(scope, "formats")
        orgs = _str_list(scope, "organizations")
        max_bytes = _positive_int(scope, "max_file_bytes", DEFAULT_MAX_FILE_BYTES)
        wanted = None if formats is None else {_norm_format(f) for f in formats}
        org = package.get("organization") if isinstance(package.get("organization"), dict) else {}
        if orgs is not None and not ({org.get("name"), org.get("title")} & set(orgs)):
            return []
        tasks = []
        for res in package.get("resources") or []:
            url = _clean(res.get("url")) or _clean(res.get("download_url")) if isinstance(res, dict) else None
            if not url or not url.lower().startswith(("http://", "https://")):
                continue
            fmt = _norm_format(res.get("format"))
            if wanted is not None and fmt not in wanted:
                continue
            if not _host_allowed(urlsplit(url).hostname or "", ctx.spec.allowed_hosts):
                continue
            size = res.get("size")
            if isinstance(size, str) and size.strip().isdigit():
                size = int(size)
            if isinstance(size, int) and not isinstance(size, bool) and size > max_bytes:
                continue  # 声明的大小就超限：不下载（声明常不准，实际超限由 classify 判）
            modified = package.get("metadata_modified")
            if url in self._previous and self._previous[url] == modified:
                continue  # 数据集没变，上次已成功下载
            tasks.append(FetchTask(url=url, meta={
                "role": "resource", "dataset_id": package["id"], "dataset_name": package.get("name"),
                "resource_id": res.get("id"), "format": res.get("format"), "declared_size": size if isinstance(size, int) else None,
                "license": _clean(res.get("license_id")) or _clean(package.get("license_id")),
                "package_modified": modified, "max_file_bytes": max_bytes,
            }))
        return tasks
