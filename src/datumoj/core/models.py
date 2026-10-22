"""全项目共用的数据模型（契约一，PLAN 4.1）。

只有类型和纯函数：locator、URL 去 Key、出处渲染、JSON Schema 导出。
所有模型都是不可变的，并且拒绝未声明的字段。

导出 JSON Schema：``python -m datumoj.core.models schemas``。
"""

from __future__ import annotations

import json
import re
import sys
from collections.abc import Iterable, Mapping
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from hashlib import sha256 as _sha256
from pathlib import Path
from typing import Annotated, Any, Generic, Literal, TypeVar
from urllib.parse import unquote_plus

from pydantic import (
    AfterValidator,
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    TypeAdapter,
    model_validator,
)

# ---------- 枚举 ----------


class FetchOutcome(StrEnum):
    """一次抓取的结果。失败的抓取也要记录。"""

    ok = "ok"
    not_modified = "not_modified"
    http_error = "http_error"
    network_error = "network_error"
    too_large = "too_large"
    blocked_by_robots = "blocked_by_robots"
    empty_body = "empty_body"
    spa_shell = "spa_shell"


class DocKind(StrEnum):
    law = "law"
    html_page = "html_page"
    pdf = "pdf"
    dataset = "dataset"
    bulletin = "bulletin"


class ChangeKind(StrEnum):
    added = "added"
    modified = "modified"
    removed = "removed"
    fetch_failed = "fetch_failed"
    parse_failed = "parse_failed"


class FailureKind(StrEnum):
    source_unavailable = "source_unavailable"
    rate_limited = "rate_limited"
    auth_failed = "auth_failed"
    format_changed = "format_changed"
    policy = "policy"
    other = "other"


FAILED_OUTCOMES = frozenset(
    {FetchOutcome.http_error, FetchOutcome.network_error, FetchOutcome.too_large, FetchOutcome.blocked_by_robots}
)


class UsageScope(StrEnum):
    internal_only = "internal_only"
    redistributable = "redistributable"


class Redistribution(StrEnum):
    allowed = "allowed"
    forbidden = "forbidden"
    unverified = "unverified"


class TimeBasis(StrEnum):
    """出处的时间口径。来源不公布数据日期时用 no_data_date，绝不拿抓取日顶替。"""

    period = "period"
    published = "published"
    effective = "effective"
    no_data_date = "no_data_date"


class TimeGranularity(StrEnum):
    """观测值的时间粒度（地区粒度由 area_code 体现）。"""

    year = "year"
    fiscal_year = "fiscal_year"
    half_year = "half_year"
    quarter = "quarter"
    month = "month"
    week = "week"
    day = "day"
    other = "other"


class GapState(StrEnum):
    """查不到时是哪种缺口（PLAN 4.3 第 5 条）。"""

    ok = "ok"
    zero_hits = "zero_hits"
    not_ingested = "not_ingested"
    fetch_failed = "fetch_failed"
    out_of_scope = "out_of_scope"


class MatchedBy(StrEnum):
    keyword = "keyword"
    vector = "vector"


# ---------- locator ----------

LOCATOR_KEYS: tuple[str, ...] = (
    "art", "para", "item", "suppl", "table", "page", "sheet",
    "cell", "row", "entry", "section", "feature", "member",
)
# 值里的 % ; = 必须转义成 %25 %3B %3D（只有这三种，且大写），其余字符原样保留。
_ESCAPES = {"%": "%25", ";": "%3B", "=": "%3D"}
_UNESCAPES = {v[1:]: k for k, v in _ESCAPES.items()}
_KEY_RE = "|".join(LOCATOR_KEYS)
_VALUE_RE = r"(?:[^%;=\x00-\x1f\x7f]|%(?:25|3B|3D))+"
_SEGMENT_RE = re.compile(rf"({_KEY_RE})=({_VALUE_RE})")
LOCATOR_PATTERN = rf"^(?:{_KEY_RE})={_VALUE_RE}(?:;(?:{_KEY_RE})={_VALUE_RE})*$"


def _escape(value: str) -> str:
    return "".join(_ESCAPES.get(ch, ch) for ch in value)


def _unescape(raw: str) -> str:
    return re.sub(r"%(25|3B|3D)", lambda mt: _UNESCAPES[mt.group(1)], raw)


def parse_locator(text: str) -> dict[str, str]:
    """``art=27;para=2`` → ``{"art": "27", "para": "2"}``，保留顺序。格式不对抛 ValueError。"""
    if not isinstance(text, str) or not text:
        raise ValueError("locator 不能为空")
    parts: dict[str, str] = {}
    for seg in text.split(";"):
        mt = _SEGMENT_RE.fullmatch(seg)
        if mt is None:
            key = seg.partition("=")[0]
            if key not in LOCATOR_KEYS:
                raise ValueError(f"locator key 非法：{key!r}，只能取 {', '.join(LOCATOR_KEYS)}")
            raise ValueError(f"locator 段格式错误：{seg!r}")
        key, value = mt.group(1), _unescape(mt.group(2))
        if key in parts:
            raise ValueError(f"locator key 重复：{key!r}")
        if value != value.strip():
            raise ValueError(f"locator 值首尾不能有空白：{seg!r}")
        parts[key] = value
    return parts


def format_locator(parts: Mapping[str, str | int] | Iterable[tuple[str, str | int]]) -> str:
    """``parse_locator`` 的逆操作，``format_locator(parse_locator(s)) == s``。"""
    items = list(parts.items()) if isinstance(parts, Mapping) else list(parts)
    if not items:
        raise ValueError("locator 不能为空")
    seen: set[str] = set()
    segs = []
    for key, value in items:
        if key not in LOCATOR_KEYS:
            raise ValueError(f"locator key 非法：{key!r}，只能取 {', '.join(LOCATOR_KEYS)}")
        if key in seen:
            raise ValueError(f"locator key 重复：{key!r}")
        seen.add(key)
        if isinstance(value, bool) or not isinstance(value, str | int):
            raise ValueError(f"locator 值必须是 str 或 int：{key}={value!r}")
        segs.append(f"{key}={_escape(str(value))}")
    text = ";".join(segs)
    parse_locator(text)  # 空值、空白、控制字符在这里统一拦下
    return text


def locator_has_prefix(locator: str, prefix: str) -> bool:
    """按段比较：``art=27;para=2`` 以 ``art=27`` 开头，但 ``art=270`` 不以 ``art=27`` 开头。"""
    have, want = list(parse_locator(locator).items()), list(parse_locator(prefix).items())
    return have[: len(want)] == want


def _check_locator(value: str) -> str:
    parse_locator(value)
    return value


# ---------- URL 去 Key ----------

REDACTED = "REDACTED"
_SECRET_PARAMS = frozenset({
    "appid", "app_id", "api_key", "apikey", "api-key", "key", "token", "access_token", "auth", "auth_token",
    "secret", "client_secret", "password", "passwd", "pwd", "signature", "sig", "subscription-key",
})
_SECRET_SUFFIXES = ("_key", "-key", "apikey", "_token", "-token", "_secret", "-secret", "password")


def _is_secret_param(name: str) -> bool:
    n = unquote_plus(name).lower()
    return n in _SECRET_PARAMS or n.endswith(_SECRET_SUFFIXES)


def _redact_params(query: str) -> str:
    out = []
    for part in query.split("&"):
        name, sep, _ = part.partition("=")
        out.append(f"{name}={REDACTED}" if sep and _is_secret_param(name) else part)
    return "&".join(out)


def redact_url(url: str) -> str:
    """把 appId、api_key、key、token 等参数值和 userinfo 里的密码换成 REDACTED。

    其余部分逐字保留（不重新编码），所以对已去 Key 的 URL 是恒等变换。
    """
    base, hash_sep, fragment = url.partition("#")
    head, q_sep, query = base.partition("?")
    scheme_end = head.find("://")
    if scheme_end != -1:
        start = scheme_end + 3
        slash = head.find("/", start)
        end = len(head) if slash == -1 else slash
        authority = head[start:end]
        if "@" in authority:
            userinfo, host = authority.rsplit("@", 1)
            if ":" in userinfo:
                authority = f"{userinfo.split(':', 1)[0]}:{REDACTED}@{host}"
        head = head[:start] + authority + head[end:]
    if query:
        query = _redact_params(query)
    if "=" in fragment:
        fragment = _redact_params(fragment)
    return head + q_sep + query + hash_sep + fragment


def _check_url(value: str) -> str:
    if not value.lower().startswith(("http://", "https://")):
        raise ValueError(f"只接受 http(s) URL：{value!r}")
    if redact_url(value) != value:
        raise ValueError("URL 含未去除的凭据，先用 redact_url() 处理")
    return value


# ---------- 公共字段类型 ----------


def _not_blank(value: str) -> str:
    if not value.strip():
        raise ValueError("不能为空白")
    return value


Text = Annotated[str, Field(min_length=1), AfterValidator(_not_blank)]
SourceId = Annotated[str, Field(pattern=r"^[a-z]{2,4}(?:-[a-z0-9]+)+$")]
# <source_id>:<来源内 ID>，跨次抓取稳定
DocKey = Annotated[str, Field(pattern=r"^[a-z]{2,4}(?:-[a-z0-9]+)+:\S(?:.*\S)?$")]
Sha256 = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
Lang = Annotated[str, Field(pattern=r"^[a-z]{2,3}(?:-[A-Za-z0-9]{1,8})*$")]
Locator = Annotated[str, Field(pattern=LOCATOR_PATTERN), AfterValidator(_check_locator)]
RedactedUrl = Annotated[str, AfterValidator(_check_url)]


def _key_source(key: str) -> str:
    return key.split(":", 1)[0]


def _check_key_belongs(key: str, source_id: str, field: str) -> None:
    if _key_source(key) != source_id:
        raise ValueError(f"{field}={key!r} 的前缀与 source_id={source_id!r} 不一致")


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class FailureClass(_Model):
    kind: FailureKind
    retryable: bool


# 失败分类表：明确的 outcome 优先于 HTTP 状态；成功永远不算失败。
_OUTCOME_FAILURES = {
    FetchOutcome.network_error: (FailureKind.source_unavailable, True),
    FetchOutcome.spa_shell: (FailureKind.format_changed, False),
    FetchOutcome.empty_body: (FailureKind.format_changed, False),
    FetchOutcome.too_large: (FailureKind.policy, False),
    FetchOutcome.blocked_by_robots: (FailureKind.policy, False),
}
_HTTP_FAILURES = {
    408: (FailureKind.source_unavailable, True),
    404: (FailureKind.source_unavailable, False),
    410: (FailureKind.source_unavailable, False),
    429: (FailureKind.rate_limited, True),
    401: (FailureKind.auth_failed, False),
    403: (FailureKind.auth_failed, False),
    407: (FailureKind.auth_failed, False),
}
# HTTP 500..599 → source_unavailable / True；其余和未知 → other / False。


def classify_failure(outcome: FetchOutcome, http_status: int | None) -> FailureClass | None:
    """分类抓取失败；outcome 描述正文/策略失败时，以 outcome 为准。"""
    if outcome in (FetchOutcome.ok, FetchOutcome.not_modified):
        return None
    fallback = (FailureKind.source_unavailable, True) if http_status is not None and 500 <= http_status <= 599 else (
        FailureKind.other, False
    )
    kind, retryable = _OUTCOME_FAILURES.get(outcome, _HTTP_FAILURES.get(http_status, fallback))
    return FailureClass(kind=kind, retryable=retryable)


def classify_parse_failure() -> FailureClass:
    return FailureClass(kind=FailureKind.format_changed, retryable=False)


META_PARENT_URL = "parent_url"
META_PARENT_SHA256 = "parent_sha256"


def parent_ref(meta: Mapping[str, Any]) -> tuple[str, str] | None:
    """父页面引用，随 FetchTask.meta 写入 manifest，解析器原样复制到 Document.metadata。

    不完整、格式不合法或 URL 尚未去 Key 时返回 None，不替调用方修补出处。
    """
    url, digest = meta.get(META_PARENT_URL), meta.get(META_PARENT_SHA256)
    if not isinstance(url, str) or not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
        return None
    try:
        _check_url(url)
    except ValueError:
        return None
    return url, digest


class ChangeEvent(_Model):
    """一条变化。ID 是六个身份字段的规范 JSON 的 SHA-256，不含时间、摘要和位置。"""

    event_id: Sha256
    source_id: SourceId
    kind: ChangeKind
    doc_key: DocKey | None = None
    resource_url: RedactedUrl | None = None
    old_sha256: Sha256 | None = None
    new_sha256: Sha256 | None = None
    detected_at: AwareDatetime
    summary: Annotated[Text, Field(pattern=r"^[^\r\n\x0b\x0c\x85\u2028\u2029]+$")]
    locator: Locator | None = None

    @staticmethod
    def _event_id(source_id: str, kind: ChangeKind, doc_key: str | None, resource_url: str | None,
                  old_sha256: str | None, new_sha256: str | None) -> str:
        identity = [source_id, kind, doc_key, resource_url, old_sha256, new_sha256]
        return _sha256(json.dumps(identity, ensure_ascii=False, separators=(",", ":")).encode("utf-8")).hexdigest()

    @classmethod
    def make(cls, *, source_id: str, kind: ChangeKind, detected_at: datetime, summary: str,
             doc_key: str | None = None, resource_url: str | None = None, old_sha256: str | None = None,
             new_sha256: str | None = None, locator: str | None = None) -> ChangeEvent:
        return cls(
            event_id=cls._event_id(source_id, kind, doc_key, resource_url, old_sha256, new_sha256),
            source_id=source_id, kind=kind, doc_key=doc_key, resource_url=resource_url,
            old_sha256=old_sha256, new_sha256=new_sha256, detected_at=detected_at, summary=summary, locator=locator,
        )

    @model_validator(mode="after")
    def _consistent(self) -> ChangeEvent:
        if self.doc_key is None and self.resource_url is None:
            raise ValueError("doc_key 和 resource_url 至少一个必填")
        if self.doc_key is not None:
            _check_key_belongs(self.doc_key, self.source_id, "doc_key")
        if self.kind is ChangeKind.added and (self.new_sha256 is None or self.old_sha256 is not None):
            raise ValueError("added 必须有 new_sha256 且没有 old_sha256")
        if self.kind is ChangeKind.removed and self.old_sha256 is None:
            raise ValueError("removed 必须有 old_sha256")
        if self.kind is ChangeKind.modified and (
            self.old_sha256 is None or self.new_sha256 is None or self.old_sha256 == self.new_sha256
        ):
            raise ValueError("modified 必须有不同的 old_sha256 和 new_sha256")
        if self.event_id != self._event_id(
            self.source_id, self.kind, self.doc_key, self.resource_url, self.old_sha256, self.new_sha256
        ):
            raise ValueError("event_id 与变化身份字段不一致")
        return self


# ---------- 来源与目录 ----------


class LicenseInfo(_Model):
    """许可。核对之前一律 unverified / internal_only；可再分发必须先确认 redistribution=allowed。"""

    name: str | None = None
    url: str | None = None
    attribution: str | None = None
    usage_scope: UsageScope = UsageScope.internal_only
    redistribution: Redistribution = Redistribution.unverified
    verified_at: date | None = None
    notes: str | None = None

    @model_validator(mode="after")
    def _scope_needs_allowed(self) -> LicenseInfo:
        if self.usage_scope is UsageScope.redistributable and self.redistribution is not Redistribution.allowed:
            raise ValueError(f"redistribution={self.redistribution.value} 时 usage_scope 只能是 internal_only")
        return self


class Source(_Model):
    source_id: SourceId
    name: Text
    country: Annotated[str, Field(pattern=r"^[a-z]{2,4}$")]
    kind: Text
    base_url: RedactedUrl
    license: LicenseInfo = Field(default_factory=LicenseInfo)


class Dataset(_Model):
    """数据集、法令、公告等（目录层）。license 原样记录来源给的值。"""

    dataset_key: DocKey
    source_id: SourceId
    title: Text
    kind: Text
    description: str | None = None
    publisher: str | None = None
    url: RedactedUrl | None = None
    license: str | None = None
    tags: list[str] = Field(default_factory=list)
    published_at: date | None = None
    modified_at: AwareDatetime | None = None
    extras: dict[str, JsonValue] = Field(default_factory=dict)
    snapshot_sha256: Sha256 | None = None
    locator: Locator | None = None

    @model_validator(mode="after")
    def _key_matches(self) -> Dataset:
        _check_key_belongs(self.dataset_key, self.source_id, "dataset_key")
        if self.locator is not None and self.snapshot_sha256 is None:
            raise ValueError("有 locator 时 snapshot_sha256 必填")
        return self


class Resource(_Model):
    """数据集下的资源文件或端点。"""

    resource_id: Text
    dataset_key: DocKey
    source_id: SourceId
    url: RedactedUrl
    name: str | None = None
    format: str | None = None
    media_type: str | None = None
    license: str | None = None
    size: Annotated[int, Field(ge=0)] | None = None
    modified_at: AwareDatetime | None = None

    @model_validator(mode="after")
    def _key_matches(self) -> Resource:
        _check_key_belongs(self.dataset_key, self.source_id, "dataset_key")
        return self


class Snapshot(_Model):
    """一次抓取（成功或失败）。失败时 sha256 为空、error 必填。"""

    snapshot_id: Text
    source_id: SourceId
    resource_url: RedactedUrl
    fetched_at: AwareDatetime
    http_status: Annotated[int, Field(ge=100, le=599)] | None = None
    outcome: FetchOutcome
    sha256: Sha256 | None = None
    size: Annotated[int, Field(ge=0)] | None = None
    content_type: str | None = None
    etag: str | None = None
    last_modified: str | None = None
    license: str | None = None
    error: str | None = None

    @model_validator(mode="after")
    def _outcome_consistent(self) -> Snapshot:
        if self.outcome is FetchOutcome.ok and (self.sha256 is None or self.size is None):
            raise ValueError("outcome=ok 时 sha256 和 size 必填")
        if self.outcome in FAILED_OUTCOMES:
            if self.sha256 is not None:
                raise ValueError(f"outcome={self.outcome.value} 时 sha256 必须为空")
            if not (self.error and self.error.strip()):
                raise ValueError(f"outcome={self.outcome.value} 时 error 必填")
        if self.outcome is FetchOutcome.http_error and self.http_status is None:
            raise ValueError("outcome=http_error 时 http_status 必填")
        return self


# ---------- 文档轨道 ----------


class Document(_Model):
    """一次快照解析出的一份文档（正文在 Chunk 里）。"""

    doc_key: DocKey
    source_id: SourceId
    snapshot_sha256: Sha256
    title: Text
    lang: Lang
    kind: DocKind | None = None
    content_type: str | None = None
    published_at: date | None = None
    effective_at: date | None = None
    metadata: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _key_matches(self) -> Document:
        _check_key_belongs(self.doc_key, self.source_id, "doc_key")
        return self


class Chunk(_Model):
    """检索单元。text 是来源正文，交给模型前必须包进 SourceText。"""

    chunk_id: Text
    doc_key: DocKey
    locator: Locator
    text: Text
    title_path: list[str] = Field(default_factory=list)
    snapshot_sha256: Sha256
    lang: Lang
    published_at: date | None = None
    effective_at: date | None = None

    @property
    def source_id(self) -> str:
        return _key_source(self.doc_key)


# ---------- 结构化轨道 ----------


class Observation(_Model):
    """一个观测值。注记（“-”“…”“x”“秘匿”）进 note，value 为空，绝不补零。"""

    obs_id: Text
    source_id: SourceId
    dataset_key: DocKey
    table_key: Text
    survey: Text
    indicator: Text
    indicator_code: str | None = None
    area_code: str | None = None
    area_label: str | None = None
    time_start: date | None = None
    time_end: date | None = None
    time_label: Text
    granularity: TimeGranularity
    dims: dict[str, str] = Field(default_factory=dict)
    unit: str | None
    value: Annotated[Decimal, Field(allow_inf_nan=False)] | None
    note: str | None = None
    snapshot_sha256: Sha256
    locator: Locator
    fetched_at: AwareDatetime

    @model_validator(mode="after")
    def _consistent(self) -> Observation:
        _check_key_belongs(self.dataset_key, self.source_id, "dataset_key")
        if self.value is None and not (self.note and self.note.strip()):
            raise ValueError("value 为空时 note 必须非空")
        if self.time_start and self.time_end and self.time_start > self.time_end:
            raise ValueError("time_start 不能晚于 time_end")
        return self


# ---------- 出处 ----------


class Period(_Model):
    """统计期间；label 保留原文（如“令和2年度”“2020年10月1日現在”）。"""

    start: date | None = None
    end: date | None = None
    label: Text

    @model_validator(mode="after")
    def _ordered(self) -> Period:
        if self.start and self.end and self.start > self.end:
            raise ValueError("period.start 不能晚于 period.end")
        return self


def _one_line(text: str) -> str:
    return " ".join(text.split())


class Citation(_Model):
    """出处 = source_id + resource_url + snapshot_sha256 + fetched_at + locator，外加时间口径与许可。

    fetched_at 只是抓取日，永远不能当数据日期用。
    """

    source_id: SourceId
    resource_url: RedactedUrl
    snapshot_sha256: Sha256
    fetched_at: AwareDatetime
    locator: Locator
    published_at: date | None = None
    effective_at: date | None = None
    period: Period | None = None
    license: str | None = None
    attribution: str | None = None
    usage_scope: UsageScope | None = None
    time_basis: TimeBasis

    @model_validator(mode="after")
    def _time_basis_consistent(self) -> Citation:
        present = {
            TimeBasis.period: self.period is not None,
            TimeBasis.published: self.published_at is not None,
            TimeBasis.effective: self.effective_at is not None,
        }
        if self.time_basis is TimeBasis.no_data_date:
            if any(present.values()):
                raise ValueError("time_basis=no_data_date 时 period、published_at、effective_at 必须都为空")
        elif not any(present.values()):
            raise ValueError("period、published_at、effective_at 都为空时 time_basis 必须是 no_data_date")
        elif not present[self.time_basis]:
            raise ValueError(f"time_basis={self.time_basis.value} 但对应的日期为空")
        return self

    def _time_parts(self) -> list[str]:
        texts = {}
        if self.period is not None:
            span = ""
            if self.period.start or self.period.end:
                span = f"（{self.period.start or '?'}〜{self.period.end or '?'}）"
            texts[TimeBasis.period] = f"统计期间 {self.period.label}{span}"
        if self.published_at is not None:
            texts[TimeBasis.published] = f"发布日 {self.published_at.isoformat()}"
        if self.effective_at is not None:
            texts[TimeBasis.effective] = f"施行日/基准日 {self.effective_at.isoformat()}"
        if self.time_basis is TimeBasis.no_data_date:
            return ["无数据日期"]
        first = texts.pop(self.time_basis)
        return [first, *texts.values()]

    def render(self) -> str:
        """一行人能读的出处：时间口径在前，再是抓取日、快照、位置、许可署名和使用范围。"""
        usage = {
            UsageScope.internal_only: "仅供内部使用",
            UsageScope.redistributable: "可再分发",
            None: "使用范围未核对",
        }[self.usage_scope]
        parts = [
            self.source_id,
            "；".join(self._time_parts()),
            f"抓取日 {self.fetched_at.date().isoformat()}",
            self.resource_url,
            f"快照 {self.snapshot_sha256[:12]}",
            f"位置 {self.locator}",
            f"许可 {self.license}" if self.license else "许可未注明",
        ]
        if self.attribution:
            parts.append(f"署名 {self.attribution}")
        parts.append(usage)
        return " | ".join(_one_line(p) for p in parts)


# ---------- 工具输出格式 ----------


class SourceText(_Model):
    """来源正文。是数据不是指令；工具输出里的来源正文只能放在这里。"""

    content: str
    content_role: Literal["untrusted_source_text"] = "untrusted_source_text"


class Hit(_Model):
    chunk_id: Text
    doc_key: DocKey
    locator: Locator
    score: Annotated[float, Field(allow_inf_nan=False)]
    snippet: SourceText
    citation: Citation
    matched_by: Annotated[list[MatchedBy], Field(min_length=1)]

    @model_validator(mode="after")
    def _consistent(self) -> Hit:
        if len(set(self.matched_by)) != len(self.matched_by):
            raise ValueError("matched_by 不能重复")
        if self.locator != self.citation.locator:
            raise ValueError("locator 与 citation.locator 不一致")
        _check_key_belongs(self.doc_key, self.citation.source_id, "doc_key")
        return self


class Freshness(_Model):
    """数据新鲜度。as_of 是结果所用快照里最新的抓取时间，即“数据截止时间”。"""

    as_of: AwareDatetime | None = None
    oldest_fetched_at: AwareDatetime | None = None
    by_source: dict[SourceId, AwareDatetime] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _ordered(self) -> Freshness:
        if self.oldest_fetched_at is not None:
            if self.as_of is None or self.oldest_fetched_at > self.as_of:
                raise ValueError("oldest_fetched_at 不能晚于 as_of，且有 oldest_fetched_at 时 as_of 必填")
        return self


T = TypeVar("T")


def _contains_chunk(obj: Any) -> bool:
    if isinstance(obj, Chunk):
        return True
    if isinstance(obj, BaseModel):
        return any(_contains_chunk(getattr(obj, name)) for name in type(obj).model_fields)
    if isinstance(obj, Mapping):
        return any(_contains_chunk(v) for v in obj.values())
    if isinstance(obj, list | tuple | set | frozenset):
        return any(_contains_chunk(v) for v in obj)
    return False


class ToolResult(_Model, Generic[T]):
    """所有分析工具的返回外壳。

    data 里的来源正文必须是 SourceText；不能直接放 Chunk（其 text 未标注）。
    warnings 和 gap_detail 是说明性文字，不得拼接来源正文。
    """

    data: T
    gap_state: GapState
    freshness: Freshness
    warnings: list[str] = Field(default_factory=list)
    gap_detail: str | None = None

    @model_validator(mode="after")
    def _consistent(self) -> ToolResult[T]:
        if self.gap_state is GapState.fetch_failed and not (self.gap_detail and self.gap_detail.strip()):
            raise ValueError("gap_state=fetch_failed 时 gap_detail 必须写明原因")
        if _contains_chunk(self.data):
            raise ValueError("data 里不能直接放 Chunk，来源正文要包进 SourceText")
        return self


# ---------- JSON Schema 导出 ----------

SCHEMA_TYPES: dict[str, Any] = {
    "FetchOutcome": FetchOutcome,
    "DocKind": DocKind,
    "ChangeKind": ChangeKind,
    "ChangeEvent": ChangeEvent,
    "FailureKind": FailureKind,
    "FailureClass": FailureClass,
    "UsageScope": UsageScope,
    "Redistribution": Redistribution,
    "TimeBasis": TimeBasis,
    "TimeGranularity": TimeGranularity,
    "GapState": GapState,
    "MatchedBy": MatchedBy,
    "LicenseInfo": LicenseInfo,
    "Source": Source,
    "Dataset": Dataset,
    "Resource": Resource,
    "Snapshot": Snapshot,
    "Document": Document,
    "Chunk": Chunk,
    "Observation": Observation,
    "Period": Period,
    "Citation": Citation,
    "SourceText": SourceText,
    "Hit": Hit,
    "Freshness": Freshness,
    "ToolResult": ToolResult,
    "SearchResult": ToolResult[list[Hit]],
}


def export_schemas(dir: str | Path) -> list[Path]:
    """把 SCHEMA_TYPES 导出为 ``<dir>/<名字>.schema.json``，并删掉目录里多余的 ``*.schema.json``。"""
    out = Path(dir)
    out.mkdir(parents=True, exist_ok=True)
    written = []
    for name, tp in SCHEMA_TYPES.items():
        schema = TypeAdapter(tp).json_schema()
        path = out / f"{name}.schema.json"
        path.write_text(json.dumps(schema, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        written.append(path)
    for stale in set(out.glob("*.schema.json")) - set(written):
        stale.unlink()
    return written


if __name__ == "__main__":
    export_schemas(sys.argv[1] if len(sys.argv) > 1 else "schemas")
