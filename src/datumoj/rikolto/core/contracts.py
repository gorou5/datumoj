"""接口协议与插件发现（契约二，PLAN 7.1）。

各开发线只依赖这里的协议和 ``models.py`` 的数据模型，不 import 别的线的实现。
每个协议在 ``datumoj.rikolto.testing.fakes`` 里有一个内存版假实现，在 ``datumoj.rikolto.testing.contract_suites``
里有一套合格测试；真实实现通过同一套合格测试，就能直接替换假实现。

错误约定（所有协议通用）：
- 参数格式或取值不合法：抛 ``ValueError``（pydantic 的 ``ValidationError`` 也是 ``ValueError``）。
- 约定好的业务失败：抛本模块的 ``ContractError`` 子类，调用方按类型处理。
- “按键取一个对象”的 ``get*`` 方法取不到时返回 ``None``，不抛异常（另有说明的除外）。
- 抓取失败不抛异常，体现在 ``FetchResult.outcome`` 里，以便原样写进 manifest。

插件：采集器放在 ``datumoj/rikolto/crawlers/<名字>.py``，继承 ``Crawler`` 并在类体里声明 ``kind``；
解析器放在 ``datumoj/rikolto/parsers/<名字>.py``，继承 ``Parser`` 并声明 ``source_kind``、``content_type``。
``discover_crawlers()`` / ``discover_parsers()`` 扫描加载，不需要任何登记文件。
"""

from __future__ import annotations

import importlib
import pkgutil
import re
import warnings
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum
from hashlib import sha256 as _sha256
from typing import IO, Annotated, Any, ClassVar, Protocol, runtime_checkable

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, JsonValue, model_validator

from datumoj.rikolto.core.models import (
    FAILED_OUTCOMES,
    ChangeEvent,
    ChangeKind,
    Chunk,
    Citation,
    Dataset,
    Document,
    EvidenceState,
    FetchOutcome,
    Freshness,
    GenerationToken,
    ReleaseEntry,
    Hit,
    LicenseInfo,
    Observation,
    ObservationRowEvidence,
    Period,
    PublishState,
    RedactedUrl,
    Resource,
    Sha256,
    Snapshot,
    SnapshotOrder,
    Source,
    SourceId,
    Text,
    TimeBasis,
    ToolResult,
    redact_url,
)

# ---------- 错误 ----------


class ContractError(Exception):
    """契约约定的错误的基类。"""


class PreconditionFailed(ContractError):
    """条件写入或删除失败（对象已存在、不存在或 ETag 不匹配）；没有修改对象。"""


class NotFoundError(ContractError, LookupError):
    """按键取不到，且该方法约定要报错（例如 RawStore.get）。"""


class AmbiguousCitation(ContractError):
    """只给 SHA 时命中多个资源，无法唯一选择出处；调用方必须补快照身份。"""


class CorruptedBlob(ContractError):
    """raw 里的内容和它的 sha256 不一致。"""


class DuplicateError(ContractError):
    """唯一键重复，例如同一个 snapshot_id 写两次。"""


class SchemaVersionError(ContractError):
    """存储里的 schema 版本和代码不一致。"""


class RegistryError(ContractError):
    """来源注册表不合法；消息里写明文件和字段。"""


class UrlNotAllowed(ContractError):
    """URL 不允许抓取：不是 http(s)、host 不在 allowed_hosts、或指向私网/回环地址。"""


class ParseError(ContractError):
    """输入无法解析或不安全（损坏、含 DTD/实体声明、超过大小上限等）。"""


class QueryError(ContractError):
    """只读 SQL 查询失败（语法错误、列不存在等）。"""


class QueryRejected(QueryError):
    """不是单条只读 SELECT/WITH，或用了写操作、ATTACH、COPY、PRAGMA、SET、文件读取函数等。"""


class QueryTimeout(QueryError):
    """查询超过 timeout_s。"""


class TooManyRows(QueryError):
    """结果超过 max_rows。"""


class InvalidGenerationToken(QueryError, ValueError):
    """token 格式/版本非法或清单哈希不匹配；不得回退最新代次。"""


class GenerationUnavailable(QueryError):
    """代次未发布、不存在、已移除或依赖对象不可用；不得回退。"""


class ReleaseNotFound(QueryError):
    """固定代次中没有对应的来源发布标识（旧数据可用 snapshot_sha256）。"""


class AmbiguousRelease(QueryError):
    """发布标识跨来源或对应多个发布日期，无法确定截至边界。"""


class ReleaseDateUnavailable(QueryError):
    """显式发布标识缺少发布日期，无法安全确定来源发布顺序。"""


class EmbeddingError(ContractError):
    """嵌入模型调用失败（重试之后仍失败）。"""


class ModelMismatch(ContractError):
    """向量索引已经用别的嵌入模型或维度建好，拒绝混用。"""


class PluginError(ContractError):
    """插件模块导入失败，或插件类的声明不合法；消息里写明模块、类和原因。"""


class PluginConflict(PluginError):
    """两个插件声明了同一个键（同一个 kind，或同一个 (source_kind, content_type)）。"""


class PluginWarning(UserWarning):
    """``skip_broken=True`` 时跳过坏插件模块发出的警告。"""


# ---------- 公共类型 ----------

KIND_PATTERN = r"^[a-z][a-z0-9_]*$"
Kind = Annotated[str, Field(pattern=KIND_PATTERN)]
EnvRef = Annotated[str, Field(pattern=r"^env:[A-Z_][A-Z0-9_]*$")]
DateRange = tuple[date | None, date | None]
"""闭区间 (起, 止)，任一端为 None 表示不限。"""

NO_PARSER = "-"
"""没有匹配的解析器时，ParseRecord.parser 写这个值（status=no_parser）。"""


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


def _sha_hex(data: bytes) -> str:
    return _sha256(data).hexdigest()


def normalize_media_type(content_type: str | None) -> str | None:
    """``"Application/JSON; charset=UTF-8"`` → ``"application/json"``；空值 → None。"""
    if content_type is None:
        return None
    media = content_type.split(";", 1)[0].strip().lower()
    return media or None


# ---------- 来源注册表 ----------


class SourceSpec(_Model):
    """``sources/<id>.yaml`` 的内容。抓取范围、限速、上限、许可都从这里读。

    许可字段合在 ``license``（LicenseInfo）里：未核对前一律 ``unverified`` / ``internal_only``。
    凭据字段只写环境变量引用 ``env:NAME``，不能写明文 Key。
    """

    id: SourceId
    name: Text
    country: Annotated[str, Field(pattern=r"^[a-z]{2,4}$")]
    kind: Kind
    base_url: RedactedUrl
    allowed_hosts: Annotated[list[Text], Field(min_length=1)]
    rate_per_sec: Annotated[float, Field(gt=0, allow_inf_nan=False)] = 1.0
    max_total_bytes: Annotated[int, Field(gt=0)]
    max_files: Annotated[int, Field(gt=0)]
    schedule: str | None = None
    license: LicenseInfo = Field(default_factory=LicenseInfo)
    scope: Annotated[dict[str, JsonValue], Field(min_length=1)]
    needs_key: EnvRef | None = None
    retention_note: str | None = None

    def to_source(self) -> Source:
        return Source(
            source_id=self.id, name=self.name, country=self.country, kind=self.kind,
            base_url=self.base_url, license=self.license,
        )


@runtime_checkable
class Registry(Protocol):
    """来源注册表（只读）。实现：DATUMOJ-IVY-104。"""

    def get(self, source_id: str) -> SourceSpec | None:
        """按 id 取；没有返回 None（“超出范围”就靠它判断）。"""
        ...

    def all(self) -> list[SourceSpec]:
        """全部来源，按 id 升序。"""
        ...


# ---------- 状态对象存储 ----------


@runtime_checkable
class StateObjectStore(Protocol):
    """同步、对象级原子操作；key/prefix 均相对于配置的存储命名空间。

    ETag 是服务端返回的不透明比较令牌，不是 SHA256；调用方必须原样传回。
    条件检查与写入/删除必须是同一个原子操作，不能用先读再无条件写代替。
    条件不满足统一抛 PreconditionFailed，其他 I/O 错误不得伪装成条件失败。
    """

    def get(self, key: str) -> tuple[bytes, str] | None:
        """返回同一对象版本的字节与 ETag；不存在返回 None。"""
        ...

    def put(self, key: str, data: bytes) -> None:
        """无条件创建或替换；不得用于发布指针、清单或租约。"""
        ...

    def put_if_absent(self, key: str, data: bytes) -> str:
        """仅不存在时创建并返回 ETag；存在（即使同字节）抛 PreconditionFailed。"""
        ...

    def put_if_match(self, key: str, data: bytes, etag: str) -> str:
        """仅当前 ETag 匹配时替换并返回 ETag；不存在也抛 PreconditionFailed。"""
        ...

    def delete_if_match(self, key: str, etag: str) -> None:
        """仅当前 ETag 匹配时删除；不存在也抛 PreconditionFailed。"""
        ...

    def exists(self, key: str) -> bool:
        ...

    def list(self, prefix: str) -> list[str]:
        """列出以 prefix 字面开头的全部相对键，按键升序；空前缀列出全部。"""
        ...


# ---------- raw 存储 ----------


@runtime_checkable
class RawStore(Protocol):
    """原始字节按 sha256 内容寻址，不可变。实现：DATUMOJ-IVY-101。

    sha256 参数一律是 64 位小写十六进制，格式不对抛 ValueError。
    """

    def put(self, data: bytes | IO[bytes]) -> str:
        """存入 bytes 或二进制流（流式写，不整块读进内存），返回 sha256。内容已存在时不报错、不重写。"""
        ...

    def get(self, sha256: str) -> bytes:
        """取全部字节；不存在抛 NotFoundError。不校验哈希（校验用 verify）。"""
        ...

    def open(self, sha256: str) -> IO[bytes]:
        """以只读二进制流打开，调用方负责关闭；不存在抛 NotFoundError。"""
        ...

    def exists(self, sha256: str) -> bool:
        ...

    def verify(self, sha256: str) -> None:
        """重新计算哈希；不一致抛 CorruptedBlob，不存在抛 NotFoundError。"""
        ...


# ---------- manifest ----------


class ParseStatus(StrEnum):
    ok = "ok"
    failed = "failed"
    no_parser = "no_parser"


class ParseRecord(_Model):
    """解析状态表的一行，键是 (sha256, parser, version, context_fingerprint)。"""

    sha256: Sha256
    parser: Text
    version: Text
    status: ParseStatus
    context_fingerprint: Annotated[str, Field(pattern=r"^(?:[0-9a-f]{64})?$")] = ""
    error: str | None = None
    doc_keys: list[str] | None = Field(default_factory=list)
    """成功产出的全部文档键；空列表表示无文档，None 仅表示迁移记录的产物未知。"""

    @model_validator(mode="after")
    def _error_when_failed(self) -> ParseRecord:
        if self.status is ParseStatus.failed and not (self.error and self.error.strip()):
            raise ValueError("status=failed 时 error 必填")
        return self


class FailureSummary(_Model):
    """一个来源的失败汇总。“失败”指 outcome 不是 ok / not_modified 的抓取。"""

    source_id: SourceId
    counts: dict[FetchOutcome, int] = Field(default_factory=dict)
    """全部失败抓取按 outcome 计数（只含出现过的 outcome）。"""
    latest_failures: list[Snapshot] = Field(default_factory=list)
    """**最近一次**抓取是失败的那些资源，各取最近一次，按 fetched_at 降序。"""


@runtime_checkable
class Manifest(Protocol):
    """抓取记录库，包括失败的抓取；外加解析状态表。实现：DATUMOJ-IVY-102。

    “最近一次”按 fetched_at 比较，相同时以后写入的为准。列表一律按 fetched_at 升序。
    """

    def record_snapshot(self, snapshot: Snapshot, meta: Mapping[str, JsonValue] | None = None) -> None:
        """在事务里写入一次抓取。snapshot_id 已存在抛 DuplicateError。

        meta 是采集器随任务给出的附加信息（``FetchTask.meta``，例如施行基准日、未施行修改列表），
        原样保存，供解析器使用；必须能 JSON 序列化，否则抛 ValueError。
        """
        ...

    def snapshot_meta(self, snapshot_id: str) -> dict[str, JsonValue]:
        """取 record_snapshot 时保存的 meta（没给过就是空 dict）；snapshot_id 不存在抛 NotFoundError。"""
        ...

    def snapshot_order(self, snapshot_id: str) -> SnapshotOrder | None:
        """记录的 fetched_at + 持久化写入序号；未知快照返回 None。"""
        ...

    def get_snapshot(self, snapshot_id: str) -> Snapshot | None:
        """按唯一 snapshot_id 取一次观察；未知返回 None，不扫描来源历史。"""
        ...

    def record_publish_state(self, snapshot_id: str, state: PublishState, error: str | None = None) -> None:
        """保存快照发布状态及最近错误；覆盖时清除旧错误。未知快照抛 NotFoundError。"""
        ...

    def publish_state(self, snapshot_id: str) -> PublishState | None:
        """未记录发布状态（包括迁移来的快照）或未知快照返回 None。"""
        ...

    def snapshots_in_publish_state(self, state: PublishState) -> list[str]:
        """只查询该状态的 snapshot_id，用于有界恢复；顺序未指定。"""
        ...

    def latest_snapshot(self, resource_url: str) -> Snapshot | None:
        """该 URL 最近一次抓取（不论成败）。resource_url 按去 Key 后的形式比较。"""
        ...

    def last_success(self, resource_url: str) -> Snapshot | None:
        """该 URL 最近一次 outcome=ok 的抓取（条件请求的 etag/last_modified 从这里取）。"""
        ...

    def snapshots_for(self, source_id: str) -> list[Snapshot]:
        """该来源的全部抓取。"""
        ...

    def snapshots_by_sha256(self, sha256: str) -> list[Snapshot]:
        """内容是这个 sha256 的全部抓取（出处回溯用）。"""
        ...

    def failure_summary(self, source_id: str) -> FailureSummary:
        ...

    def freshness(self, source_id: str) -> Freshness:
        """as_of = 成功抓取（ok / not_modified）里最新的 fetched_at；
        oldest_fetched_at = 各资源“最近一次成功抓取”里最旧的那个；by_source = {source_id: as_of}。
        没有成功抓取时返回全空的 Freshness。
        """
        ...

    def record_parse(self, record: ParseRecord) -> None:
        """写入解析状态；同一 (sha256, parser, version, context_fingerprint) 再写就覆盖。

        空指纹仅供旧调用方兼容和 v1 迁移，不能命中显式上下文。"""
        ...

    def get_parse(self, sha256: str, parser: str, version: str, context_fingerprint: str = "") -> ParseRecord | None:
        ...

    def last_writer(self, doc_key: str, sha256: str) -> str | None:
        """最后成功写入该文档版本的上下文指纹，按 record_parse 写入顺序；未知返回 None。

        失败、无解析器和产物未知的记录不更新写入者；覆盖解析状态也不抹除成功写入者。
        """
        ...

    def parse_stats(self, source_id: str, since: datetime | None = None) -> dict[ParseStatus, int]:
        """按快照 fetched_at >= since（带时区）统计该来源的最新解析状态。

        每条快照计一次；同一 sha256 + 上下文取最后写入的状态（跨解析器/版本）。
        没有匹配的上下文记录时，兼容旧管线的空指纹记录（仍按 SHA 统计）；
        未解析或无 sha256 的快照不计入；只返回出现过的状态。
        """
        ...


# ---------- 变化日志 ----------


@runtime_checkable
class ChangeLog(Protocol):
    def append(self, events: Iterable[ChangeEvent]) -> None:
        """按 event_id 幂等追加；重复 ID 保留第一次写入的事件。"""
        ...

    def list(self, since: datetime | None = None, until: datetime | None = None,
             source_ids: Sequence[str] | None = None, kinds: Sequence[ChangeKind] | None = None,
             limit: int = 1000) -> list[ChangeEvent]:
        """按 detected_at 升序，同一时间按写入顺序；闭区间 since <= detected_at <= until。

        时间边界必须带时区；None 不过滤，空的筛选列表不匹配任何事件。
        limit 必须非负，0 返回空列表。
        """
        ...


# ---------- HTTP ----------


class FetchResult(_Model):
    """一次 HTTP 抓取的结果。所有 URL 都已去 Key；content 不进 repr，JSON 里用 base64。"""

    model_config = ConfigDict(extra="forbid", frozen=True, ser_json_bytes="base64", val_json_bytes="base64")

    url: RedactedUrl
    """请求的 URL（去 Key 后）；作为 Snapshot.resource_url，增量比较都按它。"""
    final_url: RedactedUrl | None = None
    """跟随重定向后的最终 URL；没有重定向时为 None。"""
    outcome: FetchOutcome
    fetched_at: AwareDatetime
    http_status: Annotated[int, Field(ge=100, le=599)] | None = None
    content: bytes | None = Field(default=None, repr=False)
    content_type: str | None = None
    etag: str | None = None
    last_modified: str | None = None
    error: str | None = None

    @model_validator(mode="after")
    def _consistent(self) -> FetchResult:
        if self.outcome is FetchOutcome.ok and self.content is None:
            raise ValueError("outcome=ok 时 content 必填")
        if self.outcome is FetchOutcome.not_modified and self.content is not None:
            raise ValueError("outcome=not_modified 时 content 必须为空")
        if self.outcome in FAILED_OUTCOMES:
            if self.content is not None:
                raise ValueError(f"outcome={self.outcome.value} 时 content 必须为空")
            if not (self.error and self.error.strip()):
                raise ValueError(f"outcome={self.outcome.value} 时 error 必填")
        if self.outcome is FetchOutcome.http_error and self.http_status is None:
            raise ValueError("outcome=http_error 时 http_status 必填")
        return self

    @property
    def sha256(self) -> str | None:
        return None if self.content is None else _sha_hex(self.content)

    def to_snapshot(self, snapshot_id: str, source_id: str, *, license: str | None = None) -> Snapshot:
        """转成写进 manifest 的 Snapshot（有 content 时带 sha256 和 size）。"""
        return Snapshot(
            snapshot_id=snapshot_id, source_id=source_id, resource_url=self.url, fetched_at=self.fetched_at,
            http_status=self.http_status, outcome=self.outcome, sha256=self.sha256,
            size=None if self.content is None else len(self.content), content_type=self.content_type,
            etag=self.etag, last_modified=self.last_modified, license=license, error=self.error,
        )


@runtime_checkable
class HttpFetcher(Protocol):
    """礼貌的 HTTP 客户端。实现：DATUMOJ-IVY-103。

    限速、重试、大小上限、允许的 host 在构造时配置（来自 SourceSpec）。
    """

    def fetch(
        self, url: str, etag: str | None = None, last_modified: str | None = None,
        *, size: int | None = None, head_probe: bool = False,
    ) -> FetchResult:
        """抓一个 URL（url 可以带 Key；结果和错误信息里的 URL 一律去 Key）。

        - 200 → ok；带 etag/last_modified 做条件请求，304 → not_modified；
        - head_probe=True 且 last_modified、size 都非 None 时先发 HEAD；仅 2xx 且
          Last-Modified、Content-Length 都相同时返回 not_modified（保留 HEAD 状态码和验证头）；
          否则回退条件 GET。HEAD 同样遵守 robots、限速、重试、逐跳地址检查，不读取正文。
          默认不发 HEAD，size 不影响原有条件 GET；
        - 其他 HTTP 错误（重试用尽后）→ http_error，带 http_status；连接失败、超时 → network_error；
        - 超过 max_bytes → too_large（边下边判，立即中止）；robots.txt 禁止 → blocked_by_robots，不发请求；
        - 重定向到不允许的地址 → network_error，error 写明被拦下的重定向。
        这些情况都不抛异常。只有 url 本身不允许抓取时抛 UrlNotAllowed。
        """
        ...


# ---------- 采集器插件 ----------


class FetchTask(_Model):
    """采集器交给执行器的一个抓取任务。"""

    url: str
    """要抓的 URL；可以带 Key（只在内存里，落盘和输出前一律 redact_url）。"""
    meta: dict[str, JsonValue] = Field(default_factory=dict)
    """采集器自定的附加信息：任务类型、施行基准日、未施行修改等。执行器原样交给 after_fetch，
    并随快照写进 manifest（``Manifest.record_snapshot(..., meta)``），解析器由此拿到。
    父快照用 models.META_PARENT_URL / META_PARENT_SHA256；URL 必须先去 Key，
    解析器把这两个键原样放入 Document.metadata（校验用 models.parent_ref）。"""

    @model_validator(mode="after")
    def _http_url(self) -> FetchTask:
        if not self.url.lower().startswith(("http://", "https://")):
            raise ValueError(f"只接受 http(s) URL：{redact_url(self.url)!r}")
        return self

    @property
    def redacted_url(self) -> str:
        return redact_url(self.url)


@dataclass(frozen=True)
class CrawlContext:
    """执行器给采集器的上下文。manifest 和 raw 只用来读（增量判断），写入由执行器负责。"""

    spec: SourceSpec
    manifest: Manifest
    raw: RawStore
    now: datetime
    limit: int | None = None
    """本次运行的条数上限（例如法令部数），由采集器解释；执行器另按 spec.max_files 兜底。"""


class Crawler(Protocol):
    """采集器插件：只决定“抓什么”，执行（限速、存 raw、写 manifest、上限）由执行器 DATUMOJ-IVY-105 负责。

    插件类继承本类，在类体里声明 ``kind``（等于来源的 SourceSpec.kind），且能无参实例化。
    执行器约定：同一次运行里同一 URL（去 Key 后）只抓一次；plan / after_fetch 抛出的异常记为该来源的
    运行失败并写进报告，不吞掉。
    """

    kind: ClassVar[str]

    def plan(self, ctx: CrawlContext) -> Iterable[FetchTask]:
        """初始任务；可以是生成器（执行器边取边抓）。增量判断（没变的不抓）在这里做。"""
        ...

    def after_fetch(self, task: FetchTask, result: FetchResult, ctx: CrawlContext) -> Iterable[FetchTask]:
        """每次抓取之后调用（失败的也调用），返回追加的任务，例如下一页、列表里的每一项。"""
        return ()

    def classify(self, task: FetchTask, result: FetchResult) -> FetchOutcome | None:
        """覆盖执行器默认的“空正文 / SPA 外壳”判断；返回 None 表示用默认判断。"""
        return None


# ---------- 解析器插件 ----------


class ParseResult(_Model):
    """一次解析的产物。每个 chunk 的 doc_key 都必须在 documents 里。"""

    documents: list[Document] = Field(default_factory=list)
    chunks: list[Chunk] = Field(default_factory=list)
    observations: list[Observation] = Field(default_factory=list)
    datasets: list[Dataset] = Field(default_factory=list)
    resources: list[Resource] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _consistent(self) -> ParseResult:
        _unique("doc_key", [d.doc_key for d in self.documents])
        _unique("chunk_id", [c.chunk_id for c in self.chunks])
        _unique("ObservationIdentity", [o.identity for o in self.observations])
        _unique("dataset_key", [d.dataset_key for d in self.datasets])
        _unique("resource", [(r.dataset_key, r.resource_id) for r in self.resources])
        docs = {d.doc_key for d in self.documents}
        orphans = sorted({c.doc_key for c in self.chunks} - docs)
        if orphans:
            raise ValueError(f"chunk 的 doc_key 不在 documents 里：{orphans[:3]}")
        return self

    def check_against(self, snapshot: Snapshot) -> None:
        """确认产物都来自这个快照：source_id 一致，snapshot_sha256 一致，观测值的 fetched_at 等于抓取时间。

        不一致抛 ValueError。流水线写库前调用；ParserSuite 也用它检查解析器。
        """
        src, sha = snapshot.source_id, snapshot.sha256
        problems = []
        for d in self.documents:
            if d.source_id != src or d.snapshot_sha256 != sha:
                problems.append(f"document {d.doc_key}")
        for c in self.chunks:
            if c.source_id != src or c.snapshot_sha256 != sha:
                problems.append(f"chunk {c.chunk_id}")
        for o in self.observations:
            if o.source_id != src or o.snapshot_sha256 != sha or o.fetched_at != snapshot.fetched_at:
                problems.append(f"observation {o.obs_id}")
        problems += [f"dataset {d.dataset_key}" for d in self.datasets if d.source_id != src]
        problems += [f"resource {r.resource_id}" for r in self.resources if r.source_id != src]
        if problems:
            raise ValueError(f"解析产物和快照 {snapshot.snapshot_id} 不一致：{problems[:5]}")


def _unique(name: str, keys: list[Any]) -> None:
    seen: set[Any] = set()
    for key in keys:
        if key in seen:
            raise ValueError(f"{name} 重复：{key!r}")
        seen.add(key)


class Parser(Protocol):
    """解析器插件：一个快照的原始字节 → ParseResult。纯函数式，不读写任何存储。

    插件类继承本类，声明 ``name``、``version``（改了解析逻辑就升版本，流水线据此重解析）、
    ``source_kind``（来源的 kind）和 ``content_type``（小写、不带参数的媒体类型，或 ``"*"`` 表示不看
    Content-Type——响应头常常不可信，按内容判断的解析器就用 ``"*"``），且能无参实例化。
    """

    name: ClassVar[str]
    version: ClassVar[str]
    source_kind: ClassVar[str]
    content_type: ClassVar[str]

    def parse(self, snapshot: Snapshot, data: bytes, meta: Mapping[str, JsonValue] | None = None) -> ParseResult:
        """data 是 raw 里的原始字节，meta 是采集器随快照保存的附加信息（Manifest.snapshot_meta）。

        产物必须通过 ``ParseResult.check_against(snapshot)``；同样的输入两次解析结果相同。
        输入损坏或不安全抛 ParseError；能解析但有局部问题写进 warnings。
        """
        ...


# ---------- 文档存储与索引 ----------


@runtime_checkable
class ChunkStore(Protocol):
    """文档和检索块的存储。实现：DATUMOJ-IVY-501。

    chunk_id 在同一个文档的不同快照之间保持稳定（建议 ``<doc_key>#<locator>``），同一快照内唯一。
    默认 put 兼容最近写入即 current；新管线用 publish=False + publish_current 按快照顺序发布。
    旧快照的文档和块保留，可以按 snapshot_sha256 取回。
    """

    def put_document(self, document: Document, chunks: Sequence[Chunk], *, publish: bool = True) -> None:
        """写入一个文档的一个快照版本，并设为当前版本；同一快照再写就整体替换。

        chunks 必须都属于 document.doc_key、snapshot_sha256 和 document 相同、chunk_id 不重复，否则 ValueError。
        块的顺序就是文档内顺序。publish=False 只暂存版本，不切换 current。
        publish=True 兼容旧调用方，无条件切换 current，但不清除已发布水位。
        """
        ...

    def publish_current(self, doc_key: str, snapshot_sha256: str, order: SnapshotOrder) -> bool:
        """原子比较水位并发布已暂存版本。严格更新才返回 True（相等也返回 False）。

        版本不存在抛 NotFoundError；current 和水位在同一事务持久化。
        初始/旧库水位为 None，首次发布即可覆盖。水位按 doc_key 隔离。
        """
        ...

    def current_order(self, doc_key: str) -> SnapshotOrder | None:
        """已发布水位；旧写入接口不改变它，调用方须统一使用单调发布入口。"""
        ...

    def get_document(self, doc_key: str, snapshot_sha256: str | None = None) -> Document | None:
        """不给 snapshot_sha256 时取当前版本。"""
        ...

    def get_chunk(self, chunk_id: str, snapshot_sha256: str | None = None) -> Chunk | None:
        """不给 snapshot_sha256 时取当前版本里的块（当前版本已没有这个块则返回 None）。"""
        ...

    def chunks_for(self, doc_key: str, snapshot_sha256: str | None = None) -> list[Chunk]:
        """文档的全部块，按文档内顺序；没有返回空列表。"""
        ...

    def iter_chunks(self, source_ids: Sequence[str] | None = None) -> Iterator[Chunk]:
        """当前版本的全部块（重建索引用），可按来源过滤。"""
        ...


class ScoredChunk(_Model):
    chunk_id: Text
    doc_key: str
    score: Annotated[float, Field(allow_inf_nan=False)]
    """越大越相关；不同索引之间不可比。"""


class IndexResult(_Model):
    """单个索引的检索结果，按 score 降序，最多 k 条。"""

    items: list[ScoredChunk] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    """例如“查询过短，已退回 LIKE 扫描”“查询过短且超时，结果不完整”。"""


@runtime_checkable
class KeywordIndex(Protocol):
    """日文关键词索引（不分词）。实现：DATUMOJ-IVY-501（SQLite FTS5 trigram）。

    索引 Chunk 的 text 和 title_path；查询和正文都先做 NFKC 归一化（全角/半角不敏感）。
    替换一个文档 = 先 delete(doc_key) 再 add(新块)。
    """

    def add(self, chunks: Sequence[Chunk]) -> None:
        """按 chunk_id 插入或覆盖。"""
        ...

    def delete(self, doc_key: str) -> None:
        """删掉该文档的全部条目；不存在也不报错。"""
        ...

    def clear(self) -> None:
        """清空（全量重建前用）。"""
        ...

    def search(self, query: str, k: int = 10, source_ids: Sequence[str] | None = None) -> IndexResult:
        """包含查询串的块；一两个字的短查询也要能查（可以退回 LIKE，并在 warnings 里标注）。

        空白查询或 k < 1 抛 ValueError。
        """
        ...


@runtime_checkable
class Embedder(Protocol):
    """文本嵌入模型。实现：DATUMOJ-IVY-502（Ollama bge-m3；测试用确定性的 HashEmbedder）。"""

    model: str
    """模型名，向量索引用它防止混用。"""
    dim: int

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        """每条文本一个 dim 维向量，顺序和输入一致；空输入返回空列表。失败抛 EmbeddingError。"""
        ...


@runtime_checkable
class VectorIndex(Protocol):
    """向量索引。实现：DATUMOJ-IVY-502（LanceDB）。

    记录建索引时的 embedder.model 和 dim；之后用别的模型或维度打开同一份索引抛 ModelMismatch。
    """

    def add(self, chunks: Sequence[Chunk]) -> int:
        """按 chunk_id 插入或覆盖；chunk_id 和文本都没变的块跳过不嵌入。返回本次实际嵌入的块数。"""
        ...

    def delete(self, doc_key: str) -> None:
        ...

    def clear(self) -> None:
        ...

    def search(self, query: str, k: int = 10, source_ids: Sequence[str] | None = None) -> IndexResult:
        """最相近的 k 个块，score 是余弦相似度。空白查询或 k < 1 抛 ValueError。"""
        ...


@runtime_checkable
class Retriever(Protocol):
    """混合检索。实现：DATUMOJ-IVY-503（FTS + 向量，RRF 融合）。"""

    def search(
        self,
        queries: Sequence[str],
        k: int = 10,
        source_ids: Sequence[str] | None = None,
        date_range: DateRange | None = None,
    ) -> ToolResult[list[Hit]]:
        """多个查询一起检索，按 chunk_id 去重，按 score 降序，最多 k 条。

        - 每条 Hit 的 citation 用 ``citation_for_chunk`` 生成（带许可署名），snippet 是 SourceText；
        - date_range 按块的 published_at / effective_at 过滤（任一落在区间内即保留，两者都空的块排除）；
        - freshness 由命中块所用的快照算出；向量不可用退回纯关键词时在 warnings 里写明 degraded；
        - 有命中 → gap_state=ok。没有命中时按指定的 source_ids 判断（没指定就是 zero_hits）：
          有来源不在注册表 → out_of_scope；有来源没有成功快照且最近一次抓取失败 → fetch_failed
          （gap_detail 写原因）；有来源没有成功快照 → not_ingested；都已入库 → zero_hits。
          多种情况同时出现时按这个先后取第一个，gap_detail 列出各来源的状态和最近的可查内容。
        queries 为空或全是空白、k < 1 抛 ValueError。
        """
        ...


# ---------- 目录 ----------


class ContentState(StrEnum):
    """资源正文的入库程度（回答时要区分“目录查到”和“正文已读到”）。按这个顺序递进。"""

    catalog_only = "catalog_only"
    content_fetched = "content_fetched"
    observations_loaded = "observations_loaded"


_STATE_RANK = {s: i for i, s in enumerate(ContentState)}


def max_state(states: Iterable[ContentState]) -> ContentState:
    return max(states, key=_STATE_RANK.__getitem__, default=ContentState.catalog_only)


class ResourceEntry(_Model):
    resource: Resource
    state: ContentState = ContentState.catalog_only


class CatalogFilter(_Model):
    """目录检索的过滤条件；各项之间是“且”，列表内是“或”，None 表示不过滤。"""

    source_ids: list[str] | None = None
    publishers: list[str] | None = None
    """按 Dataset.publisher 精确匹配（NFKC、去首尾空白后比较）。"""
    formats: list[str] | None = None
    """数据集里至少一个资源的格式匹配；比较前规范化（NFKC、小写、去首尾空白和前导点）。"""
    modified_since: AwareDatetime | None = None
    """Dataset.modified_at >= 该时间；没有 modified_at 的数据集被排除。"""


class CatalogHit(_Model):
    dataset: Dataset
    resources: list[ResourceEntry] = Field(default_factory=list)
    state: ContentState
    """资源里入库程度最高的那个；没有资源时是 catalog_only。"""
    score: Annotated[float, Field(allow_inf_nan=False)]


@runtime_checkable
class CatalogStore(Protocol):
    """数据集和资源的目录库。实现：DATUMOJ-IVY-402（SQLite + FTS5）。

    许可、格式等字段原样保存；规范化值只用于过滤。
    """

    def upsert(self, datasets: Sequence[Dataset] = (), resources: Sequence[Resource] = ()) -> None:
        """按 dataset_key、(dataset_key, resource_id) 插入或覆盖；覆盖不改变资源已有的 state。
        资源所属的数据集必须已存在（之前写过或在本批里），否则 ValueError。
        """
        ...

    def get_dataset(self, dataset_key: str) -> Dataset | None:
        ...

    def resources_for(self, dataset_key: str) -> list[ResourceEntry]:
        """数据集的资源及入库程度，按写入顺序。"""
        ...

    def set_content_state(self, resource_url: str, state: ContentState) -> int:
        """把 URL 等于 resource_url 的资源标成 state（流水线解析快照后调用），返回更新的资源数。"""
        ...

    def search(self, query: str, filters: CatalogFilter | None = None, k: int = 20) -> list[CatalogHit]:
        """检索标题、说明、标签（NFKC 归一化，大小写不敏感），按 score 降序，最多 k 条。

        query 为空白时只按 filters 列出。k < 1 抛 ValueError。
        """
        ...


# ---------- 观测值 ----------

OBSERVATION_COLUMNS: dict[str, str] = {
    "obs_id": "VARCHAR", "source_id": "VARCHAR", "dataset_key": "VARCHAR", "table_key": "VARCHAR",
    "survey": "VARCHAR", "indicator": "VARCHAR", "indicator_code": "VARCHAR", "area_code": "VARCHAR",
    "area_label": "VARCHAR", "time_start": "DATE", "time_end": "DATE", "time_label": "VARCHAR",
    "granularity": "VARCHAR", "dims": "VARCHAR（JSON 文本）", "unit": "VARCHAR", "value": "DECIMAL",
    "note": "VARCHAR", "snapshot_sha256": "VARCHAR", "locator": "VARCHAR", "fetched_at": "TIMESTAMPTZ",
}
"""旧只读视图的稳定列集，保留历史 Parquet 兼容；value 缺失为 NULL，绝不补零。"""

OBSERVATION_RELEASE_COLUMNS: dict[str, str] = {
    **OBSERVATION_COLUMNS, "release_id": "VARCHAR", "released_at": "DATE",
}
"""发布感知视图的完整列集；301 读取旧文件时须为新增可选列提供 NULL。"""


class QueryResult(_Model):
    columns: list[str]
    rows: list[tuple[Any, ...]]
    """value 列是 Decimal 或 None。"""
    provenance: list[Observation] = Field(default_factory=list)
    """按 ObservationIdentity 去重、按首次出现排序；禁止仅按 obs_id 关联/去重。

    发布感知查询必须保留五个完整身份列才能证明逐行出处；不完整时不得猜测。
    """
    warnings: list[str] = Field(default_factory=list)
    generation_token: str | None = None
    row_evidence: list[ObservationRowEvidence] = Field(default_factory=list)
    """与 rows 一一对应，每行给出所读代次和可证明的完整身份；聚合允许空身份。"""
    evidence_state: EvidenceState | None = None
    evidence_inputs: dict[str, JsonValue] | None = None
    """沿用 307；包含 SQL、generation、as_of_release，不能将聚合伪装成逐行出处。

    以上字段的空缺省值仅兼容尚未接入 301 的实现；发布感知实现必须填写。
    """

    @model_validator(mode="after")
    def _generation_evidence(self) -> QueryResult:
        if self.generation_token is not None:
            token = GenerationToken.parse(self.generation_token)
            if len(self.row_evidence) != len(self.rows):
                raise ValueError("row_evidence 必须与 rows 一一对应")
            if any(row.generation != token.generation for row in self.row_evidence):
                raise ValueError("行代次必须与 generation_token 一致")
            if self.evidence_state is None or self.evidence_inputs is None:
                raise ValueError("发布感知结果必须提供证据状态和输入范围")
        elif self.row_evidence:
            raise ValueError("有行代次时必须提供 generation_token")
        return self


class TableSummary(_Model):
    source_id: SourceId
    table_key: Text
    dataset_keys: list[str]
    surveys: list[str]
    indicators: list[str]
    area_count: Annotated[int, Field(ge=0)]
    time_start: date | None = None
    time_end: date | None = None
    rows: Annotated[int, Field(ge=0)]


class ObservationCatalog(_Model):
    """describe() 的结果，给模型看“库里有什么”。"""

    columns: dict[str, str] = Field(default_factory=lambda: dict(OBSERVATION_COLUMNS))
    tables: list[TableSummary] = Field(default_factory=list)


@runtime_checkable
class ObservationStore(Protocol):
    """统计观测值库；发布感知实现由 WISTERIA-301 接入。

    发布历史按 ObservationIdentity 保留；ObservationRef 标识一个逻辑观测值。
    数据代次是 StateGeneration，与来源 release_id 不同。禁止仅按 obs_id 关联或去重。
    """

    def write_table(
        self, source_id: str, table_key: str, observations: Sequence[Observation], order: SnapshotOrder | None = None,
    ) -> bool:
        """整体替换 (source_id, table_key) 这张表；空列表 = 删掉这张表。
        观测值的 source_id / table_key 必须和参数一致、ObservationIdentity 不重复，否则 ValueError。
        替换 current 不删除发布历史；新实现须保存每次成功写入的发布版本及 SnapshotOrder。
        无 order 的旧入口仍兼容；显式截至读取缺少持久化顺序时必须报 QueryError，不能臆造。
        order 早于表水位时返回 False；相等、更新或尚无水位时替换并返回 True。
        数据和水位原子生效；有序删表保留水位，防止旧数据复活。
        不传 order 时无条件替换并清除水位，兼容旧调用方。
        """
        ...

    def table_order(self, source_id: str, table_key: str) -> SnapshotOrder | None:
        """读取持久化的表级水位；表不存在或升级前未记录水位时返回 None。"""
        ...

    def query_readonly(
        self, sql: str, max_rows: int = 1000, timeout_s: float = 10, *,
        generation_token: str | None = None, as_of_release: date | str | ReleaseEntry | None = None,
    ) -> QueryResult:
        """在只读视图 ``observations`` 上执行单条 SELECT/WITH。

        发布感知视图列见 OBSERVATION_RELEASE_COLUMNS；旧实现保留 OBSERVATION_COLUMNS。

        generation_token=None 在查询开始原子固定最新已发布代次，否则严格读指定代次。
        非法格式/哈希不符 → InvalidGenerationToken；代次未发布、不存在或不可用 →
        GenerationUnavailable。两者都不得回退最新代次，包括空查询结果时。
        as_of_release=None 取每个 ObservationRef 最新发布；date 为闭区间（含当天全部发布）；
        str 为该代次内 release_id（旧数据用 snapshot_sha256），闭区间截至该次发布。
        不存在 → ReleaseNotFound；跨来源/多个发布日期 → AmbiguousRelease。
        先在固定代次的历史视图按 (发布日期, SnapshotOrder) 选每个引用的最近版本，再执行 SQL。
        同标识的重复抓取取最大 SnapshotOrder；同日多次发布同样依 SnapshotOrder 排序。
        无 release_id 时按快照 SHA 和 fetched_at 的 UTC 日排序，即使 released_at 非空也不替代。
        有 release_id 无 released_at → ReleaseDateUnavailable；不把抓取时间伪装成发布日期。
        ReleaseEntry 按其来源、可选表和精确排序位置限定闭区间；不存在的项 → ReleaseNotFound。
        不受同名发布跨来源歧义影响。as_of_release 与 token 可同时用，具体规则见 ADR 0007。
        每行附 row_evidence，聚合沿用 EvidenceState 并记录截至条件；不能证明关联时为空身份。

        写操作、多语句、ATTACH、COPY、PRAGMA、SET、读文件的函数 → QueryRejected；
        超时 → QueryTimeout；结果超过 max_rows → TooManyRows；其他 SQL 错误 → QueryError。
        """
        ...

    def describe(self) -> ObservationCatalog:
        """按表列出调查、指标、地区数、时间范围、行数，按 (source_id, table_key) 排序。"""
        ...


@runtime_checkable
class ObservationReleaseStore(ObservationStore, Protocol):
    """固定代次读取能力；301 实现继承新合格测试。旧 ObservationStore 运行时检查不变。"""

    def pin_generation(self) -> str:
        """原子固定当前已发布代次及清单字节哈希，返回 str(GenerationToken)。

        无有效代次抛 GenerationUnavailable；不能选择未发布的候选或读取变化中的在线库。
        token 不保证永久保留；每次查询仍须检查该代次清单与全部依赖可用。
        """
        ...

    def list_releases(
        self, *, generation_token: str, source_id: str, table_key: str | None = None,
        timeout_s: float = 10,
    ) -> list[ReleaseEntry]:
        """枚举固定代次内来源（可选限定表）的发布，按 (发布日期, SnapshotOrder) 升序。

        同来源/发布键/日合并重复抓取，取最大 SnapshotOrder；同日不同发布各自成项。
        旧数据以 snapshot_sha256 为发布键，fetched_at 的 UTC 日为兼容日。
        每项可原样用于同 token 的 as_of_release，限定该来源和可选表。
        token 必传且与 query_readonly 同样校验，不可用时不回退；空范围返回 []。
        超时、缺发布日期、缺顺序等错误与发布感知查询一致。
        """
        ...


# ---------- 出处 ----------


def citation_for_chunk(chunk: Chunk, snapshot: Snapshot, license: LicenseInfo | None = None) -> Citation:
    """块的出处。时间口径：有 effective_at 用 effective，否则有 published_at 用 published，否则 no_data_date。"""
    if snapshot.sha256 != chunk.snapshot_sha256 or snapshot.source_id != chunk.source_id:
        raise ValueError(f"快照 {snapshot.snapshot_id} 不是块 {chunk.chunk_id} 的来源快照")
    if chunk.effective_at is not None:
        basis = TimeBasis.effective
    elif chunk.published_at is not None:
        basis = TimeBasis.published
    else:
        basis = TimeBasis.no_data_date
    return Citation(
        snapshot_id=snapshot.snapshot_id,
        source_id=chunk.source_id, resource_url=snapshot.resource_url, snapshot_sha256=chunk.snapshot_sha256,
        fetched_at=snapshot.fetched_at, locator=chunk.locator, published_at=chunk.published_at,
        effective_at=chunk.effective_at, time_basis=basis, **_license_fields(license),
    )


def citation_for_observation(obs: Observation, snapshot: Snapshot, license: LicenseInfo | None = None) -> Citation:
    """观测值的出处，时间口径是统计期间（period.label 保留原文 time_label）。"""
    if snapshot.sha256 != obs.snapshot_sha256 or snapshot.source_id != obs.source_id:
        raise ValueError(f"快照 {snapshot.snapshot_id} 不是观测值 {obs.obs_id} 的来源快照")
    return Citation(
        snapshot_id=snapshot.snapshot_id,
        source_id=obs.source_id, resource_url=snapshot.resource_url, snapshot_sha256=obs.snapshot_sha256,
        fetched_at=obs.fetched_at, locator=obs.locator,
        period=Period(start=obs.time_start, end=obs.time_end, label=obs.time_label),
        time_basis=TimeBasis.period, **_license_fields(license),
    )


def _license_fields(license: LicenseInfo | None) -> dict[str, Any]:
    if license is None:
        return {}
    return {"license": license.name, "attribution": license.attribution, "usage_scope": license.usage_scope}


# ---------- 插件发现 ----------


def discover_crawlers(package: str = "datumoj.rikolto.crawlers", *, skip_broken: bool = False) -> dict[str, type[Crawler]]:
    """扫描 package 下的每个模块，收集在类体里声明了 ``kind`` 的 Crawler 子类：``{kind: 类}``。

    模块导入失败或声明不合法：默认扫完全部模块后抛 PluginError，一次列出所有坏模块（模块名、异常类型、消息）；
    ``skip_broken=True`` 时跳过坏模块，每个发一条 PluginWarning，其余插件照常返回。
    两个类声明同一个 kind 一律抛 PluginConflict（不受 skip_broken 影响）。
    """
    found: dict[str, type[Crawler]] = {}
    for cls in _scan(package, Crawler, ("kind",), _check_crawler, skip_broken):
        _claim(found, cls.kind, cls, "kind")
    return found


def discover_parsers(
    package: str = "datumoj.rikolto.parsers", *, skip_broken: bool = False
) -> dict[tuple[str, str], type[Parser]]:
    """扫描 package 下的每个模块，收集声明了 ``source_kind`` 或 ``content_type`` 的 Parser 子类：
    ``{(source_kind, content_type): 类}``。错误处理同 discover_crawlers。按键选用解析器见 select_parser。
    """
    found: dict[tuple[str, str], type[Parser]] = {}
    for cls in _scan(package, Parser, ("source_kind", "content_type"), _check_parser, skip_broken):
        _claim(found, (cls.source_kind, cls.content_type), cls, "(source_kind, content_type)")
    return found


def select_parser(
    parsers: Mapping[tuple[str, str], type[Parser]], source_kind: str, content_type: str | None
) -> type[Parser] | None:
    """按 (来源 kind, 快照的 Content-Type) 选解析器：先精确匹配（媒体类型规范化后），再退回 ``"*"``。"""
    media = normalize_media_type(content_type)
    if media is not None and (source_kind, media) in parsers:
        return parsers[(source_kind, media)]
    return parsers.get((source_kind, "*"))


def _claim(found: dict[Any, Any], key: Any, cls: type, label: str) -> None:
    if key in found:
        raise PluginConflict(f"{label}={key!r} 有两个实现：{_qualname(found[key])} 和 {_qualname(cls)}")
    found[key] = cls


def _qualname(cls: type) -> str:
    return f"{cls.__module__}.{cls.__qualname__}"


def _check_crawler(cls: type) -> None:
    kind = getattr(cls, "kind", None)
    if not isinstance(kind, str) or not re.fullmatch(KIND_PATTERN, kind):
        raise PluginError(f"{_qualname(cls)}: kind 必须是匹配 {KIND_PATTERN} 的字符串，实际是 {kind!r}")


def _check_parser(cls: type) -> None:
    kind, ctype = getattr(cls, "source_kind", None), getattr(cls, "content_type", None)
    if not isinstance(kind, str) or not re.fullmatch(KIND_PATTERN, kind):
        raise PluginError(f"{_qualname(cls)}: source_kind 必须是匹配 {KIND_PATTERN} 的字符串，实际是 {kind!r}")
    if not isinstance(ctype, str) or (ctype != "*" and normalize_media_type(ctype) != ctype):
        raise PluginError(f"{_qualname(cls)}: content_type 必须是小写、不带参数的媒体类型或 '*'，实际是 {ctype!r}")
    for attr in ("name", "version"):
        value = getattr(cls, attr, None)
        if not isinstance(value, str) or not value.strip():
            raise PluginError(f"{_qualname(cls)}: 必须声明非空字符串 {attr}")


def _scan(package: str, base: type, markers: tuple[str, ...], check: Any, skip_broken: bool) -> list[type]:
    try:
        pkg = importlib.import_module(package)
    except Exception as exc:
        raise PluginError(f"插件包 {package} 导入失败：{type(exc).__name__}: {exc}") from exc
    found: list[type] = []
    errors: list[str] = []
    for info in sorted(pkgutil.iter_modules(getattr(pkg, "__path__", [])), key=lambda i: i.name):
        name = f"{package}.{info.name}"
        try:
            module = importlib.import_module(name)
        except Exception as exc:
            errors.append(f"{name}: 导入失败：{type(exc).__name__}: {exc}")
            continue
        candidates = [
            obj for obj in vars(module).values()
            if isinstance(obj, type) and obj.__module__ == name and obj is not base and base in obj.__mro__
            and any(m in obj.__dict__ for m in markers)
        ]
        try:
            for cls in candidates:
                check(cls)
        except PluginError as exc:
            errors.append(f"{name}: {exc}")
            continue
        found.extend(candidates)
    if errors and not skip_broken:
        raise PluginError(f"{package} 下有 {len(errors)} 个插件模块不可用：\n" + "\n".join(errors))
    for err in errors:
        warnings.warn(f"跳过插件模块 {err}", PluginWarning, stacklevel=3)
    return found
