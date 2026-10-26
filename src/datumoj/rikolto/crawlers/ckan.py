"""Generic CKAN crawler: paged ``package_search`` (or the activity list for incremental runs) -> whitelisted resource files."""

from __future__ import annotations

import json
import logging
import unicodedata
from collections.abc import Iterable, Mapping
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote, urlencode, urlsplit

from datumoj.rikolto.core.contracts import CrawlContext, Crawler, FetchResult, FetchTask, NotFoundError
from datumoj.rikolto.core.models import FetchOutcome

DEFAULT_ROWS = 100
DEFAULT_ACTIVITY_LIMIT = 100
DEFAULT_MAX_FILE_BYTES = 50 * 1024 * 1024
DEFAULT_DATASTORE_LIMIT = 1000
DEFAULT_DATASTORE_MAX_ROWS = 100_000
_API_ROLES = ("package_search", "activity", "package_show")
_HTML_FORMATS = frozenset({"html", "htm", "xhtml", "php", "asp", "aspx", "jsp"})
_HTML_HEADS = (b"<!doctype html", b"<html")
log = logging.getLogger(__name__)


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
    - 可选 ``datastore: true`` 会读取 ``resources[].datastore_active`` 资源，按 ``datastore_limit``（默认 1000）分页，
      最多读取 ``datastore_max_rows``（默认 100000）条；
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
        since, self._previous, fallback = self._load_previous(ctx)
        if ctx.spec.scope.get("incremental", True) is not False and since is not None:
            limit = _positive_int(ctx.spec.scope, "activity_limit", DEFAULT_ACTIVITY_LIMIT)
            url = f"{self._base(ctx)}/recently_changed_packages_activity_list?limit={limit}"
            yield FetchTask(url=url, meta={"role": "activity", "since": since.isoformat(), "limit": limit,
                                           "run_started": self._started(ctx)})
        else:
            yield self._search_task(ctx, 0, fallback)

    def after_fetch(self, task: FetchTask, result: FetchResult, ctx: CrawlContext) -> Iterable[FetchTask]:
        role = task.meta.get("role")
        if role == "activity":
            return self._after_activity(task, result, ctx)
        if role == "datastore":
            content = result.content
            if result.outcome is FetchOutcome.not_modified:
                previous = ctx.manifest.last_success(task.url)
                if previous is None or previous.sha256 is None:
                    return ()
                content = ctx.raw.get(previous.sha256)
            elif result.outcome is not FetchOutcome.ok:
                return ()
            data = _json_object(content)
            if not _api_ok(data):
                return ()
            return self._after_datastore(task, data["result"], ctx)
        content = result.content
        if role == "package_search" and result.outcome is FetchOutcome.not_modified:
            previous = ctx.manifest.last_success(task.url)
            if previous is None or previous.sha256 is None:
                return ()
            content = ctx.raw.get(previous.sha256)
        elif result.outcome is not FetchOutcome.ok or content is None or role not in _API_ROLES:
            return ()
        data = _json_object(content)
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
        if role in (*_API_ROLES, "datastore"):
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

    def _search_task(
        self, ctx: CrawlContext, start: int, fallback: str | None = None,
        previous_page: tuple[int, int] | None = None,
    ) -> FetchTask:
        scope = ctx.spec.scope
        rows = _positive_int(scope, "rows", DEFAULT_ROWS)
        query = urlencode({"q": scope.get("q", "*:*"), "rows": rows, "start": start, "sort": "id asc"}, quote_via=quote)
        meta: dict[str, Any] = {
            "role": "package_search", "start": start, "rows": rows, "run_started": self._started(ctx),
            "dataset_limit": ctx.limit, "max_files_limit": ctx.spec.max_files,
            "max_total_bytes_limit": ctx.spec.max_total_bytes,
        }
        if previous_page is not None:
            meta["previous_start"], meta["previous_count"] = previous_page
        if fallback:
            meta["incremental_fallback"] = fallback
        return FetchTask(url=f"{self._base(ctx)}/package_search?{query}", meta=meta)

    @staticmethod
    def _load_previous(ctx: CrawlContext) -> tuple[datetime | None, dict[str, str | None], str | None]:
        """(最近可用的运行基准, 资源 URL -> 上次成功下载时的数据集 metadata_modified, 全量回退原因)。"""
        previous: dict[str, str | None] = {}
        runs: dict[datetime, list[tuple[Any, dict[str, Any]]]] = {}
        for snap in ctx.manifest.snapshots_for(ctx.spec.id):
            meta = ctx.manifest.snapshot_meta(snap.snapshot_id)
            role = meta.get("role")
            if role == "resource" and snap.outcome is FetchOutcome.ok:
                previous[snap.resource_url] = meta.get("package_modified")  # type: ignore[assignment]
            started = _utc(meta.get("run_started")) if isinstance(meta.get("run_started"), str) else None
            if started is not None and role in ("package_search", "package_show", "activity", "resource"):
                runs.setdefault(started, []).append((snap, meta))

        full_runs = [started for started, entries in runs.items()
                     if any(meta.get("role") == "package_search" for _, meta in entries)]
        if not full_runs:
            return None, previous, None

        latest_full = max(full_runs)
        pages = [(snap, meta) for snap, meta in runs[latest_full] if meta.get("role") == "package_search"]
        complete, failed = CkanCrawler._full_run_complete(ctx, pages, runs[latest_full])
        if not complete:
            reason = "上次全量未完成：分页请求失败" if failed else "上次全量未完成：分页未走到末页"
            return None, previous, reason

        since = latest_full
        for started, entries in runs.items():
            if started <= latest_full or any(meta.get("role") == "package_search" for _, meta in entries):
                continue
            if any(meta.get("role") in ("activity", "package_show") and snap.outcome is FetchOutcome.ok
                   for snap, meta in entries):
                since = max(since, started)
        return since, previous, None

    @staticmethod
    def _full_run_complete(
        ctx: CrawlContext,
        pages: list[tuple[Any, dict[str, Any]]],
        run: list[tuple[Any, dict[str, Any]]],
    ) -> tuple[bool, bool]:
        """从该次全量的 package_search 快照验证分页连续且记录了末页。"""
        if any(snap.outcome not in (FetchOutcome.ok, FetchOutcome.not_modified) for snap, _ in pages):
            return False, True
        by_start: dict[int, tuple[Any, dict[str, Any], list[Any] | None]] = {}
        missing_raw = False
        for snap, meta in pages:
            start, rows = meta.get("start"), meta.get("rows")
            if (not isinstance(start, int) or isinstance(start, bool) or start < 0
                    or not isinstance(rows, int) or isinstance(rows, bool) or rows <= 0 or start in by_start):
                return False, False
            raw_missing = False
            body_snapshot = snap
            if snap.outcome is FetchOutcome.not_modified:
                body_snapshot = ctx.manifest.last_success(snap.resource_url)
                if body_snapshot is None or body_snapshot.sha256 is None:
                    missing_raw = True
                    raw_missing = True
            try:
                data = _json_object(ctx.raw.get(body_snapshot.sha256)) if body_snapshot and body_snapshot.sha256 else None
            except NotFoundError:
                data = None
                missing_raw = True
                raw_missing = True
            result = data.get("result") if _api_ok(data) else None
            results = result.get("results") if isinstance(result, dict) else None
            if not isinstance(results, list) and not raw_missing:
                return False, False
            by_start[start] = (snap, meta, results if isinstance(results, list) else None)

        if missing_raw:
            return CkanCrawler._metadata_full_run_complete(ctx, pages, run), False

        reached_empty_page = any(not results for _, _, results in by_start.values())
        if not reached_empty_page and CkanCrawler._run_hit_cap(ctx, pages, run):
            return False, False

        dataset_limit = next((meta.get("dataset_limit") for _, meta in pages
                              if meta.get("dataset_limit") is not None), None)
        if isinstance(dataset_limit, int) and not isinstance(dataset_limit, bool):
            seen = {package["id"] for _, _, results in by_start.values() for package in results
                    if isinstance(package, dict) and isinstance(package.get("id"), str)}
            if len(seen) >= dataset_limit:
                return False, False

        expected = 0
        while expected in by_start:
            _, meta, results = by_start.pop(expected)
            count = len(results)
            if count == 0:
                return not by_start, False
            next_start = expected + count
            if count < meta["rows"] and next_start not in by_start:
                return not by_start, False
            expected = next_start
        return False, False

    @staticmethod
    def _metadata_full_run_complete(
        ctx: CrawlContext,
        pages: list[tuple[Any, dict[str, Any]]],
        run: list[tuple[Any, dict[str, Any]]],
    ) -> bool:
        """raw 不在本次上下文中时，用分页续页 meta 和执行上限做保守核验。"""
        if any(meta.get("dataset_limit") is not None for _, meta in pages):
            return False
        if CkanCrawler._run_hit_cap(ctx, pages, run):
            return False
        ordered = sorted(pages, key=lambda item: item[1]["start"])
        if len(ordered) < 2 or ordered[0][1]["start"] != 0:
            return False
        for (_, previous_meta), (_, current_meta) in zip(ordered, ordered[1:]):
            previous_start, previous_count = current_meta.get("previous_start"), current_meta.get("previous_count")
            if (not isinstance(previous_start, int) or isinstance(previous_start, bool)
                    or not isinstance(previous_count, int) or isinstance(previous_count, bool)
                    or previous_count <= 0 or previous_start != previous_meta["start"]
                    or current_meta["start"] != previous_start + previous_count):
                return False
        return all(snap.outcome is FetchOutcome.ok for snap, _ in pages)

    @staticmethod
    def _run_hit_cap(
        ctx: CrawlContext,
        pages: list[tuple[Any, dict[str, Any]]],
        run: list[tuple[Any, dict[str, Any]]],
    ) -> bool:
        file_caps = [meta["max_files_limit"] for _, meta in pages
                     if isinstance(meta.get("max_files_limit"), int)
                     and not isinstance(meta.get("max_files_limit"), bool)]
        byte_caps = [meta["max_total_bytes_limit"] for _, meta in pages
                     if isinstance(meta.get("max_total_bytes_limit"), int)
                     and not isinstance(meta.get("max_total_bytes_limit"), bool)]
        max_files = min(file_caps, default=ctx.spec.max_files)
        max_bytes = min(byte_caps, default=ctx.spec.max_total_bytes)
        return (len(run) >= max_files or sum(snap.size or 0 for snap, _ in run) >= max_bytes
                or any(snap.outcome is FetchOutcome.too_large for snap, _ in run))

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
            tasks.append(self._search_task(ctx, next_start, previous_page=(task.meta["start"], count)))
        return tasks

    def _datastore_task(
        self,
        ctx: CrawlContext,
        *,
        dataset_id: str,
        resource_id: str,
        resource_url: str | None,
        license_id: str | None,
        offset: int = 0,
        limit: int | None = None,
    ) -> FetchTask:
        scope = ctx.spec.scope
        page_limit = limit or _positive_int(scope, "datastore_limit", DEFAULT_DATASTORE_LIMIT)
        max_rows = _positive_int(scope, "datastore_max_rows", DEFAULT_DATASTORE_MAX_ROWS)
        page_limit = min(page_limit, max_rows - offset)
        query = urlencode(
            {"resource_id": resource_id, "limit": page_limit, "sort": "_id asc", "offset": offset},
            quote_via=quote,
        )
        return FetchTask(url=f"{self._base(ctx)}/datastore_search?{query}", meta={
            "role": "datastore", "resource_id": resource_id, "resource_url": resource_url,
            "dataset_id": dataset_id, "license": license_id, "limit": page_limit,
            "offset": offset, "max_rows": max_rows, "run_started": self._started(ctx),
        })

    def _after_datastore(self, task: FetchTask, payload: Any, ctx: CrawlContext) -> Iterable[FetchTask]:
        result = payload if isinstance(payload, dict) else {}
        records = result.get("records")
        if not isinstance(records, list):
            log.warning("CKAN DataStore 响应缺少 records 数组，停止分页：dataset_id=%s resource_id=%s",
                        task.meta.get("dataset_id"), task.meta.get("resource_id"))
            return ()
        count = len(records)
        if count == 0:
            return ()

        offset, limit, max_rows = task.meta["offset"], task.meta["limit"], task.meta["max_rows"]
        next_offset = offset + count
        total = result.get("total")
        total = total if isinstance(total, int) and not isinstance(total, bool) and total >= 0 else None
        if total is not None and next_offset >= total:
            return ()
        if next_offset >= max_rows:
            log.warning(
                "CKAN DataStore 达到 scope.datastore_max_rows=%s，停止分页；dataset_id=%s resource_id=%s",
                max_rows, task.meta.get("dataset_id"), task.meta.get("resource_id"),
            )
            return ()
        # CKAN 有时会在 total 尚未到达时返回短页；有可信 total 时按实际条数继续，
        # 否则短页作为末页，避免在没有终止依据时无限请求。
        if count < limit and total is None:
            return ()

        return (self._datastore_task(
            ctx,
            dataset_id=task.meta["dataset_id"],
            resource_id=task.meta["resource_id"],
            resource_url=task.meta.get("resource_url"),
            license_id=task.meta.get("license"),
            offset=next_offset,
            limit=limit,
        ),)

    def _resource_tasks(self, package: Any, ctx: CrawlContext) -> list[FetchTask]:
        if not isinstance(package, dict) or not isinstance(package.get("id"), str):
            return []
        if package["id"] in self._seen or (ctx.limit is not None and len(self._seen) >= ctx.limit):
            return []
        self._seen.add(package["id"])
        scope = ctx.spec.scope
        datastore = scope.get("datastore", False)
        if not isinstance(datastore, bool):
            raise ValueError("scope.datastore 必须是布尔值")
        formats = _str_list(scope, "formats")
        orgs = _str_list(scope, "organizations")
        max_bytes = _positive_int(scope, "max_file_bytes", DEFAULT_MAX_FILE_BYTES)
        wanted = None if formats is None else {_norm_format(f) for f in formats}
        org = package.get("organization") if isinstance(package.get("organization"), dict) else {}
        if orgs is not None and not ({org.get("name"), org.get("title")} & set(orgs)):
            return []
        tasks = []
        for res in package.get("resources") or []:
            if datastore and isinstance(res, dict) and res.get("datastore_active") is True:
                resource_id = res.get("id")
                if isinstance(resource_id, str) and resource_id:
                    resource_url = _clean(res.get("url")) or _clean(res.get("download_url"))
                    tasks.append(self._datastore_task(
                        ctx, dataset_id=package["id"], resource_id=resource_id,
                        resource_url=resource_url,
                        license_id=_clean(res.get("license_id")) or _clean(package.get("license_id")),
                    ))
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
                "package_modified": modified, "max_file_bytes": max_bytes, "run_started": self._started(ctx),
            }))
        return tasks
