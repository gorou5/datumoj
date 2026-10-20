"""Crawler for the e-Gov Law API v2: paged law list -> amendment history -> current full text (XML)."""

from __future__ import annotations

import base64
import binascii
import json
import re
from collections.abc import Iterable, Mapping
from datetime import date, timedelta, timezone
from typing import Any

from datumoj.core.contracts import CrawlContext, Crawler, FetchResult, FetchTask
from datumoj.core.models import FetchOutcome

JST = timezone(timedelta(hours=9))
DEFAULT_PAGE_SIZE = 100
NOT_FOUND_CODES = frozenset({"404001", "404004"})  # law_revisions / law_data：法令不存在
_LAW_ID = re.compile(r"[0-9A-Za-z_]+")
_PAYLOAD_KEYS = ("law_full_text", "revisions", "laws")
_SUCCESS = (FetchOutcome.ok, FetchOutcome.not_modified)


def _json_object(raw: bytes | None, what: str) -> dict[str, Any]:
    try:
        data = json.loads(raw or b"")
    except (ValueError, UnicodeDecodeError) as exc:
        raise ValueError(f"{what}不是合法的 JSON") from exc
    if not isinstance(data, dict):
        raise ValueError(f"{what}的 JSON 顶层不是对象")
    return data


def _error_code(data: Mapping[str, Any]) -> str | None:
    """e-Gov 错误体 ``{"code", "message"}`` 的 code；正常载荷返回 None。"""
    code = data.get("code")
    if code is None or any(key in data for key in _PAYLOAD_KEYS):
        return None
    return str(code)


def extract_law_xml(raw_json_bytes: bytes) -> bytes:
    """从 law_data 的 JSON 响应里取出 base64 编码的法令 XML；输入损坏时抛 ValueError。"""
    data = _json_object(raw_json_bytes, "law_data 响应")
    code = _error_code(data)
    if code is not None:
        kind = "法令不存在" if code in NOT_FOUND_CODES else "错误响应"
        raise ValueError(f"law_data 返回{kind}（code={code}），没有法令正文")
    encoded = data.get("law_full_text")
    if not isinstance(encoded, str) or not encoded.strip():
        raise ValueError("law_data 响应缺少 law_full_text")
    try:
        xml = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError("law_full_text 不是合法的 base64") from exc
    if not xml.lstrip().startswith(b"<"):
        raise ValueError("law_full_text 解码后不是 XML")
    return xml


def _date(value: Any, field: str) -> date | None:
    if value is None:
        return None
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"修订历史的 {field} 不是 YYYY-MM-DD：{value!r}") from exc


def pending_amendments(revisions: Iterable[Mapping[str, Any]], asof: date) -> list[dict[str, Any]]:
    """已公布但在 asof 尚未施行的修改（按施行日升序，施行日未定的排最后）。

    不看 current_revision_status 来判断“已施行”（有的法令没有 CurrentEnforced 条目），而是比较施行日和 asof；
    施行日未定（null，如“政令で定める日”）时，只有状态为 UnEnforced 才算未施行。
    """
    found: list[tuple[date | None, dict[str, Any]]] = []
    for rev in revisions:
        promulgated = _date(rev.get("amendment_promulgate_date"), "amendment_promulgate_date")
        enforced = _date(rev.get("amendment_enforcement_date"), "amendment_enforcement_date")
        if promulgated is not None and promulgated > asof:
            continue
        if enforced is None:
            if rev.get("current_revision_status") != "UnEnforced":
                continue
        elif enforced <= asof:
            continue
        found.append((enforced, {
            "amendment_law_num": rev.get("amendment_law_num"),
            "promulgate_date": promulgated.isoformat() if promulgated else None,
            "enforcement_date": enforced.isoformat() if enforced else None,
            "enforcement_comment": rev.get("amendment_enforcement_comment"),
            "amendment_law_title": rev.get("amendment_law_title"),
            "law_revision_id": rev.get("law_revision_id"),
        }))
    found.sort(key=lambda item: (item[0] is None, item[0] or date.min, item[1]["promulgate_date"] or ""))
    return [item for _, item in found]


def _signature(entry: Mapping[str, Any]) -> list[str | None]:
    """列表项的修订信息：现行版与最新版的 (law_revision_id, updated)；任何一项变了都要重抓。"""
    current = entry.get("current_revision_info") or entry.get("revision_info") or {}
    latest = entry.get("revision_info") or current
    return [current.get("law_revision_id"), current.get("updated"), latest.get("law_revision_id"), latest.get("updated")]


class EgovLawCrawler(Crawler):
    """法令列表逐页 -> 每部法令的修订历史 -> 现行版全文（XML）。

    scope 可选键：``page_size``（列表每页条数，默认 100）。``ctx.limit`` 限制法令部数。
    施行基准日 asof 取 ``ctx.now`` 的 JST 日期，写进 ``FetchTask.meta``（格式见任务 201/202 共用约定）。
    """

    kind = "egov_law"

    def __init__(self) -> None:
        self._previous: dict[str, Any] | None = None

    # ----- 任务 -----

    def plan(self, ctx: CrawlContext) -> Iterable[FetchTask]:
        self._previous = self._load_previous(ctx)
        yield self._list_task(ctx, 0)

    def after_fetch(self, task: FetchTask, result: FetchResult, ctx: CrawlContext) -> Iterable[FetchTask]:
        role = task.meta.get("role")
        if result.outcome is not FetchOutcome.ok or result.content is None:
            return ()
        data = _json_object(result.content, f"e-Gov 响应（{task.redacted_url}）")
        if _error_code(data) is not None:
            return ()
        if role == "law_list":
            return self._after_list(task, data, ctx)
        if role == "law_revisions":
            return self._after_revisions(task, data, ctx)
        return ()

    def classify(self, task: FetchTask, result: FetchResult) -> FetchOutcome | None:
        """HTTP 200 但正文是 404001 / 404004 之类的错误体：法令不存在，不是抓取成功。"""
        if result.outcome is not FetchOutcome.ok or result.content is None:
            return None
        try:
            data = json.loads(result.content)
        except (ValueError, UnicodeDecodeError):
            return None
        if isinstance(data, dict) and _error_code(data) is not None:
            return FetchOutcome.empty_body
        return None

    # ----- 内部 -----

    @staticmethod
    def _base(ctx: CrawlContext) -> str:
        return ctx.spec.base_url.rstrip("/")

    @staticmethod
    def _page_size(ctx: CrawlContext) -> int:
        size = ctx.spec.scope.get("page_size", DEFAULT_PAGE_SIZE)
        if not isinstance(size, int) or isinstance(size, bool) or size <= 0:
            raise ValueError("scope.page_size 必须是正整数")
        return min(size, ctx.limit) if ctx.limit is not None else size

    def _list_task(self, ctx: CrawlContext, offset: int) -> FetchTask:
        size = self._page_size(ctx)
        url = f"{self._base(ctx)}/laws?limit={size}&offset={offset}"
        return FetchTask(url=url, meta={"role": "law_list", "offset": offset, "page_size": size})

    def _load_previous(self, ctx: CrawlContext) -> dict[str, Any]:
        """law_id -> 上次成功抓取现行版全文时的列表修订信息（snapshots_for 升序，后面的覆盖前面的）。"""
        previous: dict[str, Any] = {}
        for snap in ctx.manifest.snapshots_for(ctx.spec.id):
            if snap.outcome not in _SUCCESS:
                continue
            meta = ctx.manifest.snapshot_meta(snap.snapshot_id)
            if meta.get("role") == "law_data" and isinstance(meta.get("law_id"), str):
                previous[meta["law_id"]] = meta.get("list_signature")
        return previous

    def _after_list(self, task: FetchTask, data: Mapping[str, Any], ctx: CrawlContext) -> Iterable[FetchTask]:
        laws = data.get("laws")
        if not isinstance(laws, list):
            raise ValueError(f"法令列表缺少 laws 数组：{task.redacted_url}")
        if self._previous is None:
            self._previous = self._load_previous(ctx)
        offset, page_size = task.meta["offset"], task.meta["page_size"]
        tasks: list[FetchTask] = []
        for index, entry in enumerate(laws):
            if ctx.limit is not None and offset + index >= ctx.limit:
                break
            law_id = (entry.get("law_info") or {}).get("law_id") if isinstance(entry, dict) else None
            if not isinstance(law_id, str) or not _LAW_ID.fullmatch(law_id):
                raise ValueError(f"法令列表里的 law_id 不合法：{law_id!r}")
            signature = _signature(entry)
            if self._previous.get(law_id) == signature:
                continue  # 修订信息没变，不抓正文
            tasks.append(FetchTask(
                url=f"{self._base(ctx)}/law_revisions/{law_id}",
                meta={"role": "law_revisions", "law_id": law_id, "list_signature": signature,
                      "law_revision_id": signature[0]},
            ))
        # 只按本页实际条数判断结束：空页 total_count 是 0，末页和空页都没有 next_offset 键
        count = len(laws)
        next_offset = offset + count
        if count > 0 and count >= page_size and (ctx.limit is None or next_offset < ctx.limit):
            tasks.append(self._list_task(ctx, next_offset))
        return tasks

    def _after_revisions(self, task: FetchTask, data: Mapping[str, Any], ctx: CrawlContext) -> Iterable[FetchTask]:
        revisions = data.get("revisions")
        if not isinstance(revisions, list):
            raise ValueError(f"修订历史缺少 revisions 数组：{task.redacted_url}")
        law_id = task.meta["law_id"]
        asof = ctx.now.astimezone(JST).date()
        meta = {
            "role": "law_data", "law_id": law_id, "asof": asof.isoformat(),
            "pending_amendments": pending_amendments(revisions, asof),
            "law_revision_id": task.meta.get("law_revision_id"),
            "list_signature": task.meta.get("list_signature"),
        }
        url = f"{self._base(ctx)}/law_data/{law_id}?asof={asof.isoformat()}&response_format=json&law_full_text_format=xml"
        return [FetchTask(url=url, meta=meta)]
