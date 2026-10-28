"""每个协议一个内存版假实现，行为符合协议，并且通过 ``contract_suites`` 里的合格测试。

各线开发时用它们代替别人的模块；``drive_crawler`` 是给采集器测试用的假执行器。
"""

from __future__ import annotations

import hashlib
import io
import json
import math
import re
import sqlite3
import time
import unicodedata
from collections import Counter, deque
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from threading import Lock
from typing import IO, Any

from pydantic import AwareDatetime, JsonValue, TypeAdapter

from datumoj.rikolto.core.contracts import (
    OBSERVATION_RELEASE_COLUMNS,
    AmbiguousRelease,
    CatalogFilter,
    CatalogHit,
    ContentState,
    CorruptedBlob,
    CrawlContext,
    Crawler,
    DateRange,
    DuplicateError,
    EmbeddingError,
    FailureSummary,
    FetchResult,
    FetchTask,
    GenerationUnavailable,
    HttpFetcher,
    IndexResult,
    InvalidGenerationToken,
    ModelMismatch,
    NotFoundError,
    ObservationCatalog,
    ParseError,
    Parser,
    ParseRecord,
    ParseResult,
    ParseStatus,
    PreconditionFailed,
    QueryError,
    QueryRejected,
    QueryResult,
    QueryTimeout,
    ReleaseDateUnavailable,
    ReleaseNotFound,
    ResourceEntry,
    ScoredChunk,
    SourceSpec,
    TableSummary,
    TooManyRows,
    UrlNotAllowed,
    citation_for_chunk,
    max_state,
)
from datumoj.rikolto.core.models import (
    META_PARENT_SHA256,
    META_PARENT_URL,
    ChangeEvent,
    ChangeKind,
    Chunk,
    Dataset,
    DocKind,
    Document,
    EvidenceState,
    FetchOutcome,
    Freshness,
    GapState,
    GenerationToken,
    ReleaseEntry,
    Hit,
    MatchedBy,
    Observation,
    ObservationIdentity,
    ObservationRef,
    ObservationRowEvidence,
    PublishState,
    Resource,
    Snapshot,
    SnapshotOrder,
    SourceText,
    SourceId,
    Text,
    StateGeneration,
    ToolResult,
    parent_ref,
    parse_context_fingerprint,
    redact_url,
)

__all__ = [
    "CrawlStep", "FakeCatalogStore", "FakeChangeLog", "FakeChunkStore", "FakeCrawler", "FakeEmbedder",
    "FakeHttpFetcher", "FakeKeywordIndex", "FakeManifest", "FakeObservationStore", "FakeParser", "FakeRawStore",
    "FakeRegistry", "FakeResponse", "FakeRetriever", "FakeStateObjectStore", "FakeVectorIndex",
    "UnregisteredUrl", "drive_crawler",
]

_SHA_RE = re.compile(r"[0-9a-f]{64}")
_SUCCESS = frozenset({FetchOutcome.ok, FetchOutcome.not_modified})
_JSON_DICT = TypeAdapter(dict[str, JsonValue])


def _check_sha(value: str) -> str:
    if not isinstance(value, str) or not _SHA_RE.fullmatch(value):
        raise ValueError(f"sha256 必须是 64 位小写十六进制：{value!r}")
    return value


def _norm(text: str) -> str:
    return unicodedata.normalize("NFKC", text).casefold().strip()


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _latest(snaps: Iterable[Snapshot]) -> Snapshot | None:
    # 输入按写入顺序；max 遇到相同 fetched_at 取第一个，所以倒过来取“后写入的”
    return max(reversed(list(snaps)), key=lambda s: s.fetched_at, default=None)


# ---------- Registry ----------


class FakeStateObjectStore:
    """线程安全的状态对象存储；递增 ETag 模拟不透明令牌，不模拟 S3 内容哈希。"""

    def __init__(self) -> None:
        self._objects: dict[str, tuple[bytes, str]] = {}
        self._revision = 0
        self._lock = Lock()

    def _write(self, key: str, data: bytes) -> str:
        self._revision += 1
        etag = f'"fake-{self._revision}"'
        self._objects[key] = (data, etag)
        return etag

    def get(self, key: str) -> tuple[bytes, str] | None:
        with self._lock:
            return self._objects.get(key)

    def put(self, key: str, data: bytes) -> None:
        with self._lock:
            self._write(key, data)

    def put_if_absent(self, key: str, data: bytes) -> str:
        with self._lock:
            if key in self._objects:
                raise PreconditionFailed("对象已存在")
            return self._write(key, data)

    def put_if_match(self, key: str, data: bytes, etag: str) -> str:
        with self._lock:
            current = self._objects.get(key)
            if current is None or current[1] != etag:
                raise PreconditionFailed("对象不存在或 ETag 不匹配")
            return self._write(key, data)

    def delete_if_match(self, key: str, etag: str) -> None:
        with self._lock:
            current = self._objects.get(key)
            if current is None or current[1] != etag:
                raise PreconditionFailed("对象不存在或 ETag 不匹配")
            del self._objects[key]

    def exists(self, key: str) -> bool:
        with self._lock:
            return key in self._objects

    def list(self, prefix: str) -> list[str]:
        with self._lock:
            return sorted(key for key in self._objects if key.startswith(prefix))


class FakeRegistry:
    def __init__(self, specs: Iterable[SourceSpec] = ()):
        self._specs: dict[str, SourceSpec] = {}
        for spec in specs:
            if spec.id in self._specs:
                raise ValueError(f"来源 id 重复：{spec.id}")
            self._specs[spec.id] = spec

    def get(self, source_id: str) -> SourceSpec | None:
        return self._specs.get(source_id)

    def all(self) -> list[SourceSpec]:
        return [self._specs[k] for k in sorted(self._specs)]


# ---------- RawStore ----------


class FakeRawStore:
    def __init__(self, root: str | Path = "/fake-data"):
        self.root = Path(root)
        self.blobs: dict[str, bytes] = {}

    def put(self, data: bytes | IO[bytes]) -> str:
        if not isinstance(data, bytes | bytearray | memoryview):
            buf = bytearray()
            for block in iter(lambda: data.read(1 << 20), b""):
                buf += block
            data = buf
        data = bytes(data)
        digest = hashlib.sha256(data).hexdigest()
        self.blobs.setdefault(digest, data)
        return digest

    def get(self, sha256: str) -> bytes:
        try:
            return self.blobs[_check_sha(sha256)]
        except KeyError:
            raise NotFoundError(f"raw 里没有 {sha256}") from None

    def open(self, sha256: str) -> IO[bytes]:
        return io.BytesIO(self.get(sha256))

    def exists(self, sha256: str) -> bool:
        return _check_sha(sha256) in self.blobs

    def verify(self, sha256: str) -> None:
        if hashlib.sha256(self.get(sha256)).hexdigest() != sha256:
            raise CorruptedBlob(f"raw 内容和哈希不一致：{sha256}")


# ---------- Manifest ----------


class FakeManifest:
    def __init__(self) -> None:
        self._snaps: list[Snapshot] = []
        self._meta: dict[str, dict[str, JsonValue]] = {}
        self._parse: dict[tuple[str, str, str, str], ParseRecord] = {}
        self._writers: dict[tuple[str, str], str] = {}
        self._by_id: dict[str, Snapshot] = {}
        self._publish: dict[str, tuple[PublishState, str | None]] = {}
        self._publish_ids: dict[PublishState, set[str]] = {state: set() for state in PublishState}

    def record_snapshot(self, snapshot: Snapshot, meta: Mapping[str, JsonValue] | None = None) -> None:
        if snapshot.snapshot_id in self._meta:
            raise DuplicateError(f"snapshot_id 重复：{snapshot.snapshot_id}")
        checked = _JSON_DICT.validate_python(dict(meta or {}))
        self._meta[snapshot.snapshot_id] = json.loads(json.dumps(checked))
        self._snaps.append(snapshot)
        self._by_id[snapshot.snapshot_id] = snapshot

    def get_snapshot(self, snapshot_id: str) -> Snapshot | None:
        return self._by_id.get(snapshot_id)

    def record_publish_state(self, snapshot_id: str, state: PublishState, error: str | None = None) -> None:
        state = PublishState(state)
        if snapshot_id not in self._by_id:
            raise NotFoundError(f"没有快照 {snapshot_id}")
        previous = self.publish_state(snapshot_id)
        if previous is not None:
            self._publish_ids[previous].discard(snapshot_id)
        self._publish[snapshot_id] = (state, error)
        self._publish_ids[state].add(snapshot_id)

    def publish_state(self, snapshot_id: str) -> PublishState | None:
        record = self._publish.get(snapshot_id)
        return None if record is None else record[0]

    def snapshots_in_publish_state(self, state: PublishState) -> list[str]:
        return list(self._publish_ids[PublishState(state)])

    def snapshot_meta(self, snapshot_id: str) -> dict[str, JsonValue]:
        if snapshot_id not in self._meta:
            raise NotFoundError(f"没有快照 {snapshot_id}")
        return json.loads(json.dumps(self._meta[snapshot_id]))

    def snapshot_order(self, snapshot_id: str) -> SnapshotOrder | None:
        for seq, snap in enumerate(self._snaps, 1):
            if snap.snapshot_id == snapshot_id:
                return SnapshotOrder(fetched_at=snap.fetched_at, seq=seq)
        return None

    def _sorted(self, snaps: Iterable[Snapshot]) -> list[Snapshot]:
        return sorted(snaps, key=lambda s: s.fetched_at)  # 稳定排序：同一时间保持写入顺序

    def latest_snapshot(self, resource_url: str) -> Snapshot | None:
        url = redact_url(resource_url)
        return _latest(s for s in self._snaps if s.resource_url == url)

    def last_success(self, resource_url: str) -> Snapshot | None:
        url = redact_url(resource_url)
        return _latest(s for s in self._snaps if s.resource_url == url and s.outcome is FetchOutcome.ok)

    def snapshots_for(self, source_id: str) -> list[Snapshot]:
        return self._sorted(s for s in self._snaps if s.source_id == source_id)

    def snapshots_by_sha256(self, sha256: str) -> list[Snapshot]:
        return self._sorted(s for s in self._snaps if s.sha256 == sha256)

    def failure_summary(self, source_id: str) -> FailureSummary:
        snaps = [s for s in self._snaps if s.source_id == source_id]
        counts = Counter(s.outcome for s in snaps if s.outcome not in _SUCCESS)
        latest = [_latest(s for s in snaps if s.resource_url == url) for url in dict.fromkeys(s.resource_url for s in snaps)]
        failing = [s for s in latest if s is not None and s.outcome not in _SUCCESS]
        failing.sort(key=lambda s: s.fetched_at, reverse=True)
        return FailureSummary(source_id=source_id, counts=dict(counts), latest_failures=failing)

    def freshness(self, source_id: str) -> Freshness:
        ok = [s for s in self._snaps if s.source_id == source_id and s.outcome in _SUCCESS]
        if not ok:
            return Freshness()
        per_resource: dict[str, datetime] = {}
        for s in ok:
            per_resource[s.resource_url] = max(per_resource.get(s.resource_url, s.fetched_at), s.fetched_at)
        as_of = max(s.fetched_at for s in ok)
        return Freshness(as_of=as_of, oldest_fetched_at=min(per_resource.values()), by_source={source_id: as_of})

    def record_parse(self, record: ParseRecord) -> None:
        key = (record.sha256, record.parser, record.version, record.context_fingerprint)
        self._parse.pop(key, None)  # 覆盖也算最后一次写入
        self._parse[key] = record.model_copy(deep=True)
        if record.status is ParseStatus.ok:
            for doc_key in record.doc_keys or []:
                self._writers[doc_key, record.sha256] = record.context_fingerprint

    def get_parse(self, sha256: str, parser: str, version: str, context_fingerprint: str = "") -> ParseRecord | None:
        return self._parse.get((sha256, parser, version, context_fingerprint))

    def last_writer(self, doc_key: str, sha256: str) -> str | None:
        return self._writers.get((doc_key, sha256))

    def parse_stats(self, source_id: str, since: datetime | None = None) -> dict[ParseStatus, int]:
        if since is not None:
            since = TypeAdapter(AwareDatetime).validate_python(since)
        latest = {(r.sha256, r.context_fingerprint): r.status for r in self._parse.values()}
        counts: Counter[ParseStatus] = Counter()
        for snap in self._snaps:
            if snap.source_id != source_id or (since is not None and snap.fetched_at < since):
                continue
            context = parse_context_fingerprint(snap, self._meta[snap.snapshot_id])
            status = latest.get((snap.sha256, context), latest.get((snap.sha256, "")))
            if status is not None:
                counts[status] += 1
        return dict(counts)


class FakeChangeLog:
    def __init__(self) -> None:
        self._events: dict[str, ChangeEvent] = {}

    def append(self, events: Iterable[ChangeEvent]) -> None:
        for event in events:
            self._events.setdefault(event.event_id, event)

    def list(self, since: datetime | None = None, until: datetime | None = None,
             source_ids: Sequence[str] | None = None, kinds: Sequence[ChangeKind] | None = None,
             limit: int = 1000) -> list[ChangeEvent]:
        if limit < 0:
            raise ValueError("limit 必须非负")
        if since is not None:
            since = TypeAdapter(AwareDatetime).validate_python(since)
        if until is not None:
            until = TypeAdapter(AwareDatetime).validate_python(until)
        events = (
            e for e in self._events.values()
            if (since is None or e.detected_at >= since) and (until is None or e.detected_at <= until)
            and (source_ids is None or e.source_id in source_ids) and (kinds is None or e.kind in kinds)
        )
        return sorted(events, key=lambda e: e.detected_at)[:limit]


# ---------- HTTP ----------


class UnregisteredUrl(LookupError):
    """假 HTTP / 回放没有登记这个 URL（测试本身写漏了，不是抓取失败）。"""


@dataclass(frozen=True)
class FakeResponse:
    """假服务器对一个 URL 的行为。HttpFetcherSuite 的实现方用 ``respond`` 接到自己的 transport 上。"""

    status: int = 200
    body: bytes = b""
    headers: Mapping[str, str] = field(default_factory=dict)
    network_error: str | None = None
    """设了就表示连接失败（例如 "connection refused"）。"""

    def respond(self, if_none_match: str | None = None, if_modified_since: str | None = None) -> tuple[int, dict, bytes]:
        """按条件请求头给出 (状态码, 响应头, 正文)：ETag 或 Last-Modified 匹配时回 304、空正文。"""
        headers = {k.lower(): v for k, v in self.headers.items()}
        if self.status == 200 and (
            (if_none_match is not None and headers.get("etag") == if_none_match)
            or (if_modified_since is not None and headers.get("last-modified") == if_modified_since)
        ):
            return 304, headers, b""
        return self.status, headers, self.body


class FakeHttpFetcher:
    """按登记的路由回答；路由键和请求都按去 Key 后的 URL 比较。没登记的 URL 抛 UnregisteredUrl。

    不限速、不重试、不跟随重定向（3xx 记为 http_error）。``requests`` 记录每次请求（去 Key 后）。
    """

    def __init__(
        self,
        routes: Mapping[str, FakeResponse | bytes],
        *,
        max_bytes: int | None = None,
        now: Callable[[], datetime] = _utcnow,
    ):
        self.routes = {redact_url(u): r if isinstance(r, FakeResponse) else FakeResponse(body=r) for u, r in routes.items()}
        self.max_bytes = max_bytes
        self.now = now
        self.requests: list[str] = []

    def fetch(
        self, url: str, etag: str | None = None, last_modified: str | None = None,
        *, size: int | None = None, head_probe: bool = False,
    ) -> FetchResult:
        red = redact_url(url)
        if not url.lower().startswith(("http://", "https://")):
            raise UrlNotAllowed(f"只允许 http(s)：{red}")
        self.requests.append(red)
        if red not in self.routes:
            raise UnregisteredUrl(f"假 HTTP 没有登记这个 URL：{red}")
        resp = self.routes[red]
        base: dict[str, Any] = dict(url=red, fetched_at=self.now())
        if resp.network_error:
            return FetchResult(**base, outcome=FetchOutcome.network_error, error=resp.network_error)
        if head_probe and last_modified is not None and size is not None:
            headers = {k.lower(): v for k, v in resp.headers.items()}
            length = headers.get("content-length", "")
            try:
                same_size = length.isascii() and length.isdecimal() and int(length) == size
            except ValueError:
                same_size = False
            if 200 <= resp.status < 300 and headers.get("last-modified") == last_modified and same_size:
                return FetchResult(
                    **base, outcome=FetchOutcome.not_modified, http_status=resp.status,
                    etag=headers.get("etag"), last_modified=headers.get("last-modified"),
                )
        status, headers, body = resp.respond(etag, last_modified)
        base.update(http_status=status, etag=headers.get("etag"), last_modified=headers.get("last-modified"))
        if status == 304:
            return FetchResult(**base, outcome=FetchOutcome.not_modified)
        if status >= 300:
            return FetchResult(**base, outcome=FetchOutcome.http_error, error=f"HTTP {status}：{red}")
        if self.max_bytes is not None and len(body) > self.max_bytes:
            return FetchResult(**base, outcome=FetchOutcome.too_large, error=f"超过 {self.max_bytes} 字节")
        return FetchResult(**base, outcome=FetchOutcome.ok, content=body, content_type=headers.get("content-type"))


# ---------- 采集器与解析器 ----------


class FakeCrawler(Crawler):
    """种子是 ``spec.scope["urls"]``；抓到的正文里以 ``link: `` 开头的行是追加的任务（演示 after_fetch）。"""

    kind = "fake"

    def plan(self, ctx: CrawlContext) -> Iterable[FetchTask]:
        for url in ctx.spec.scope.get("urls", []):
            yield FetchTask(url=url, meta={"role": "seed"})

    def after_fetch(self, task: FetchTask, result: FetchResult, ctx: CrawlContext) -> Iterable[FetchTask]:
        if result.outcome is not FetchOutcome.ok or result.content is None:
            return []
        lines = result.content.decode("utf-8", "replace").splitlines()
        return [FetchTask(url=ln[6:].strip(), meta={"role": "link"}) for ln in lines if ln.startswith("link: ")]


class FakeParser(Parser):
    """UTF-8 纯文本：一个快照一个文档，按空行分段，每段一个块（locator ``section=<序号>``）。

    doc_key 是 ``<source_id>:<meta["doc_id"] 或 URL 最后一段>``；标题是第一段的第一行。
    """

    name = "fake-text"
    version = "1"
    source_kind = "fake"
    content_type = "text/plain"

    def parse(self, snapshot: Snapshot, data: bytes, meta: Mapping[str, JsonValue] | None = None) -> ParseResult:
        if snapshot.sha256 is None:
            raise ParseError(f"快照 {snapshot.snapshot_id} 没有正文")
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ParseError(f"不是 UTF-8 文本：{exc}") from exc
        paras = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip() and not p.strip().startswith("link: ")]
        if not paras:
            raise ParseError("没有正文")
        doc_id = (meta or {}).get("doc_id") or snapshot.resource_url.rstrip("/").rsplit("/", 1)[-1]
        doc_key = f"{snapshot.source_id}:{doc_id}"
        title = paras[0].splitlines()[0][:100]
        parent = parent_ref(meta or {})
        doc = Document(
            doc_key=doc_key, source_id=snapshot.source_id, snapshot_sha256=snapshot.sha256, title=title, lang="ja",
            content_type="text/plain", kind=DocKind.bulletin,
            metadata={} if parent is None else {META_PARENT_URL: parent[0], META_PARENT_SHA256: parent[1]},
        )
        chunks = [
            Chunk(
                chunk_id=f"{doc_key}#section={i}", doc_key=doc_key, locator=f"section={i}", text=p, title_path=[title],
                snapshot_sha256=snapshot.sha256, lang="ja",
            )
            for i, p in enumerate(paras, 1)
        ]
        return ParseResult(documents=[doc], chunks=chunks)


@dataclass(frozen=True)
class CrawlStep:
    task: FetchTask
    result: FetchResult
    snapshot: Snapshot


def drive_crawler(
    crawler: Crawler,
    spec: SourceSpec,
    fetcher: HttpFetcher,
    *,
    manifest: FakeManifest | None = None,
    raw: FakeRawStore | None = None,
    limit: int | None = None,
    now: datetime | None = None,
    max_steps: int = 10_000,
) -> list[CrawlStep]:
    """假执行器，给采集器测试用（不用等 DATUMOJ-IVY-105）。

    依次抓 plan 和 after_fetch 产出的任务（追加的任务优先）；同一 URL 只抓一次；条件请求取 manifest 里
    上次成功的 etag/last_modified；有正文就写 raw；每次抓取连同 task.meta 写 manifest。
    ``crawler.classify`` 给出结果时照用，否则不做 SPA 判断。抓够 ``spec.max_files`` 个就停；
    超过 max_steps 抛 AssertionError（多半是分页没有终止）。
    """
    manifest = manifest if manifest is not None else FakeManifest()
    raw = raw if raw is not None else FakeRawStore()
    ctx = CrawlContext(spec=spec, manifest=manifest, raw=raw, now=now or _utcnow(), limit=limit)
    plan = iter(crawler.plan(ctx))
    queue: deque[FetchTask] = deque()
    seen: set[str] = set()
    steps: list[CrawlStep] = []
    while len(steps) < spec.max_files:
        task = queue.popleft() if queue else next(plan, None)
        if task is None:
            break
        if task.redacted_url in seen:
            continue
        seen.add(task.redacted_url)
        if len(seen) > max_steps:
            raise AssertionError(f"任务超过 {max_steps} 个，分页可能没有终止")
        prev = manifest.last_success(task.redacted_url)
        result = fetcher.fetch(task.url, etag=prev and prev.etag, last_modified=prev and prev.last_modified)
        override = crawler.classify(task, result)
        if override is not None and override is not result.outcome:
            result = FetchResult.model_validate({**result.model_dump(), "outcome": override})
        if result.content is not None:
            raw.put(result.content)
        snap = result.to_snapshot(f"{spec.id}-{len(manifest.snapshots_for(spec.id)) + 1:06d}", spec.id)
        manifest.record_snapshot(snap, task.meta)
        steps.append(CrawlStep(task, result, snap))
        queue.extend(crawler.after_fetch(task, result, ctx))
    return steps


# ---------- 文档存储与索引 ----------


class FakeChunkStore:
    def __init__(self) -> None:
        self._versions: dict[str, dict[str, tuple[Document, list[Chunk]]]] = {}
        self._current: dict[str, str] = {}
        self._orders: dict[str, SnapshotOrder] = {}

    def put_document(self, document: Document, chunks: Sequence[Chunk], *, publish: bool = True) -> None:
        ids: set[str] = set()
        for c in chunks:
            if c.doc_key != document.doc_key or c.snapshot_sha256 != document.snapshot_sha256:
                raise ValueError(f"块 {c.chunk_id} 不属于文档 {document.doc_key} 的这个快照")
            if c.chunk_id in ids:
                raise ValueError(f"chunk_id 重复：{c.chunk_id}")
            ids.add(c.chunk_id)
        versions = self._versions.setdefault(document.doc_key, {})
        versions.pop(document.snapshot_sha256, None)
        versions[document.snapshot_sha256] = (document, list(chunks))
        if publish:
            self._current[document.doc_key] = document.snapshot_sha256

    def current_order(self, doc_key: str) -> SnapshotOrder | None:
        return self._orders.get(doc_key)

    def publish_current(self, doc_key: str, snapshot_sha256: str, order: SnapshotOrder) -> bool:
        if self._version(doc_key, snapshot_sha256) is None:
            raise NotFoundError(f"没有文档版本 {doc_key} / {snapshot_sha256}")
        current = self.current_order(doc_key)
        if current is not None and order <= current:
            return False
        self._current[doc_key] = snapshot_sha256
        self._orders[doc_key] = order
        return True

    def _version(self, doc_key: str, snapshot_sha256: str | None) -> tuple[Document, list[Chunk]] | None:
        sha = snapshot_sha256 or self._current.get(doc_key)
        return self._versions.get(doc_key, {}).get(sha) if sha else None

    def get_document(self, doc_key: str, snapshot_sha256: str | None = None) -> Document | None:
        v = self._version(doc_key, snapshot_sha256)
        return v[0] if v else None

    def get_chunk(self, chunk_id: str, snapshot_sha256: str | None = None) -> Chunk | None:
        for doc_key, versions in self._versions.items():
            v = versions.get(snapshot_sha256) if snapshot_sha256 else versions.get(self._current.get(doc_key))
            for c in v[1] if v else []:
                if c.chunk_id == chunk_id:
                    return c
        return None

    def chunks_for(self, doc_key: str, snapshot_sha256: str | None = None) -> list[Chunk]:
        v = self._version(doc_key, snapshot_sha256)
        return list(v[1]) if v else []

    def iter_chunks(self, source_ids: Sequence[str] | None = None) -> Iterator[Chunk]:
        for doc_key, sha in self._current.items():
            if source_ids is None or doc_key.split(":", 1)[0] in source_ids:
                yield from self._versions[doc_key][sha][1]


def _check_query(query: str, k: int) -> str:
    q = _norm(query) if isinstance(query, str) else ""
    if not q:
        raise ValueError("查询不能为空白")
    if k < 1:
        raise ValueError("k 必须 ≥ 1")
    return q


def _keyword_score(chunk: Chunk, q: str) -> float:
    return _norm(chunk.text).count(q) + 2 * _norm(" ".join(chunk.title_path)).count(q)


def _in_sources(doc_key: str, source_ids: Sequence[str] | None) -> bool:
    return source_ids is None or doc_key.split(":", 1)[0] in source_ids


class FakeKeywordIndex:
    """子串匹配；score = 正文出现次数 + 2 × 标题路径出现次数。"""

    def __init__(self) -> None:
        self._chunks: dict[str, Chunk] = {}

    def add(self, chunks: Sequence[Chunk]) -> None:
        for c in chunks:
            self._chunks[c.chunk_id] = c

    def delete(self, doc_key: str) -> None:
        self._chunks = {k: c for k, c in self._chunks.items() if c.doc_key != doc_key}

    def clear(self) -> None:
        self._chunks.clear()

    def search(self, query: str, k: int = 10, source_ids: Sequence[str] | None = None) -> IndexResult:
        q = _check_query(query, k)
        scored = [
            ScoredChunk(chunk_id=c.chunk_id, doc_key=c.doc_key, score=s)
            for c in self._chunks.values()
            if _in_sources(c.doc_key, source_ids) and (s := _keyword_score(c, q))
        ]
        scored.sort(key=lambda h: (-h.score, h.chunk_id))
        warnings = ["查询少于 3 个字，已退回逐条扫描"] if len(q) < 3 else []
        return IndexResult(items=scored[:k], warnings=warnings)


class FakeEmbedder:
    """确定性的哈希嵌入：字和相邻两字各哈希到一个维度，再归一化。相同文本得到相同向量。"""

    def __init__(self, dim: int = 64, model: str = "fake-hash-v1"):
        self.dim = dim
        self.model = model
        self.embedded = 0
        """累计嵌入的文本条数。"""

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        out = []
        for text in texts:
            t = _norm(text)
            vec = [0.0] * self.dim
            for gram in [*t, *(t[i : i + 2] for i in range(len(t) - 1))]:
                h = hashlib.sha256(gram.encode()).digest()
                vec[int.from_bytes(h[:4], "big") % self.dim] += 1.0 if h[4] & 1 else -1.0
            norm = math.sqrt(sum(x * x for x in vec))
            out.append([x / norm for x in vec] if norm else [1.0] + [0.0] * (self.dim - 1))
        self.embedded += len(texts)
        return out


def _text_hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


class FakeVectorIndex:
    """storage 是个普通 dict；多个实例共用同一个 storage 就相当于打开同一份索引。"""

    def __init__(self, embedder: Any, storage: dict | None = None):
        self.embedder = embedder
        self._storage = storage if storage is not None else {}
        want = {"model": embedder.model, "dim": embedder.dim}
        have = self._storage.setdefault("meta", want)
        if have != want:
            raise ModelMismatch(f"索引用 {have['model']}（{have['dim']} 维）建成，不能用 {want['model']}（{want['dim']} 维）")
        self._rows: dict[str, tuple[str, str, list[float]]] = self._storage.setdefault("rows", {})

    def add(self, chunks: Sequence[Chunk]) -> int:
        todo = []
        for c in chunks:
            row = self._rows.get(c.chunk_id)
            if row is None or row[1] != _text_hash(c.text):
                todo.append(c)
        vectors = self.embedder.embed([c.text for c in todo]) if todo else []
        if len(vectors) != len(todo):
            raise EmbeddingError("嵌入结果条数和输入不一致")
        for c, v in zip(todo, vectors, strict=True):
            self._rows[c.chunk_id] = (c.doc_key, _text_hash(c.text), v)
        return len(todo)

    def delete(self, doc_key: str) -> None:
        for cid in [cid for cid, row in self._rows.items() if row[0] == doc_key]:
            del self._rows[cid]

    def clear(self) -> None:
        self._rows.clear()

    def search(self, query: str, k: int = 10, source_ids: Sequence[str] | None = None) -> IndexResult:
        _check_query(query, k)
        qv = self.embedder.embed([query])[0]
        scored = [
            ScoredChunk(chunk_id=cid, doc_key=doc_key, score=sum(a * b for a, b in zip(qv, v, strict=True)))
            for cid, (doc_key, _, v) in self._rows.items()
            if _in_sources(doc_key, source_ids)
        ]
        scored.sort(key=lambda h: (-h.score, h.chunk_id))
        return IndexResult(items=scored[:k])


def _in_range(chunk: Chunk, date_range: DateRange) -> bool:
    lo, hi = date_range
    return any(
        d is not None and (lo is None or d >= lo) and (hi is None or d <= hi)
        for d in (chunk.published_at, chunk.effective_at)
    )


_GAP_ORDER = [GapState.out_of_scope, GapState.fetch_failed, GapState.not_ingested, GapState.zero_hits]


class FakeRetriever:
    """只用关键词（子串）的检索器，多个查询按 RRF（k=60）融合；出处、新鲜度、缺口判断按协议来。"""

    def __init__(self, chunks: Any, manifest: Any, registry: Any):
        self.chunks = chunks
        self.manifest = manifest
        self.registry = registry

    def search(
        self,
        queries: Sequence[str],
        k: int = 10,
        source_ids: Sequence[str] | None = None,
        date_range: DateRange | None = None,
    ) -> ToolResult[list[Hit]]:
        qs = [_norm(q) for q in queries if isinstance(q, str) and q.strip()]
        if not qs or k < 1:
            raise ValueError("queries 不能为空白，k 必须 ≥ 1")
        pool = [c for c in self.chunks.iter_chunks(source_ids) if date_range is None or _in_range(c, date_range)]
        fused: dict[str, float] = {}
        by_id = {c.chunk_id: c for c in pool}
        for q in qs:
            ranked = sorted((c for c in pool if _keyword_score(c, q)), key=lambda c: (-_keyword_score(c, q), c.chunk_id))
            for rank, c in enumerate(ranked, 1):
                fused[c.chunk_id] = fused.get(c.chunk_id, 0.0) + 1 / (60 + rank)
        hits: list[Hit] = []
        used: list[Snapshot] = []
        warnings: list[str] = []
        for cid in sorted(fused, key=lambda i: (-fused[i], i)):
            if len(hits) >= k:
                break
            c = by_id[cid]
            snap = _latest(
                s for s in self.manifest.snapshots_by_sha256(c.snapshot_sha256)
                if s.outcome is FetchOutcome.ok and s.source_id == c.source_id
            )
            if snap is None:
                warnings.append(f"{cid} 找不到来源快照，已跳过")
                continue
            spec = self.registry.get(c.source_id)
            hits.append(Hit(
                chunk_id=cid, doc_key=c.doc_key, locator=c.locator, score=fused[cid],
                snippet=SourceText(content=c.text[:300]), citation=citation_for_chunk(c, snap, spec and spec.license),
                matched_by=[MatchedBy.keyword],
            ))
            used.append(snap)
        if hits:
            return ToolResult[list[Hit]](data=hits, gap_state=GapState.ok, freshness=_freshness(used), warnings=warnings)
        state, detail = self._gap(source_ids)
        return ToolResult[list[Hit]](data=[], gap_state=state, gap_detail=detail, freshness=Freshness(), warnings=warnings)

    def _gap(self, source_ids: Sequence[str] | None) -> tuple[GapState, str]:
        if not source_ids:
            return GapState.zero_hits, "全部来源检索零命中"
        states: dict[str, tuple[GapState, str]] = {}
        for sid in dict.fromkeys(source_ids):
            snaps = self.manifest.snapshots_for(sid)
            if self.registry.get(sid) is None:
                states[sid] = (GapState.out_of_scope, "注册表里没有这个来源")
            elif any(s.outcome in _SUCCESS for s in snaps):
                states[sid] = (GapState.zero_hits, "已入库，检索零命中")
            elif snaps and (last := _latest(snaps)) is not None:
                states[sid] = (GapState.fetch_failed, f"最近一次抓取失败：{last.outcome.value} {last.error or ''}".strip())
            else:
                states[sid] = (GapState.not_ingested, "还没有成功抓取")
        state = min((s for s, _ in states.values()), key=_GAP_ORDER.index)
        return state, "；".join(f"{sid}: {reason}" for sid, (_, reason) in states.items())


def _freshness(snaps: Sequence[Snapshot]) -> Freshness:
    by_source: dict[str, datetime] = {}
    for s in snaps:
        by_source[s.source_id] = max(by_source.get(s.source_id, s.fetched_at), s.fetched_at)
    times = [s.fetched_at for s in snaps]
    return Freshness(as_of=max(times), oldest_fetched_at=min(times), by_source=by_source)


# ---------- 目录 ----------


def _norm_format(value: str | None) -> str:
    return unicodedata.normalize("NFKC", value or "").strip().lower().lstrip(".").strip()


def _norm_publisher(value: str | None) -> str:
    return unicodedata.normalize("NFKC", value or "").strip()


class FakeCatalogStore:
    def __init__(self) -> None:
        self._datasets: dict[str, Dataset] = {}
        self._resources: dict[str, dict[str, ResourceEntry]] = {}

    def upsert(self, datasets: Sequence[Dataset] = (), resources: Sequence[Resource] = ()) -> None:
        known = set(self._datasets) | {d.dataset_key for d in datasets}
        for r in resources:
            if r.dataset_key not in known:
                raise ValueError(f"资源 {r.resource_id} 所属的数据集 {r.dataset_key} 不存在")
        for d in datasets:
            self._datasets[d.dataset_key] = d
        for r in resources:
            entries = self._resources.setdefault(r.dataset_key, {})
            old = entries.get(r.resource_id)
            entries[r.resource_id] = ResourceEntry(resource=r, state=old.state if old else ContentState.catalog_only)

    def get_dataset(self, dataset_key: str) -> Dataset | None:
        return self._datasets.get(dataset_key)

    def resources_for(self, dataset_key: str) -> list[ResourceEntry]:
        return list(self._resources.get(dataset_key, {}).values())

    def set_content_state(self, resource_url: str, state: ContentState) -> int:
        n = 0
        for entries in self._resources.values():
            for rid, e in entries.items():
                if e.resource.url == resource_url:
                    entries[rid] = ResourceEntry(resource=e.resource, state=state)
                    n += 1
        return n

    def _passes(self, d: Dataset, f: CatalogFilter) -> bool:
        if f.source_ids is not None and d.source_id not in f.source_ids:
            return False
        if f.publishers is not None and _norm_publisher(d.publisher) not in {_norm_publisher(p) for p in f.publishers}:
            return False
        if f.formats is not None:
            want = {_norm_format(x) for x in f.formats}
            if not any(_norm_format(e.resource.format) in want for e in self.resources_for(d.dataset_key)):
                return False
        if f.modified_since is not None and (d.modified_at is None or d.modified_at < f.modified_since):
            return False
        return True

    def search(self, query: str, filters: CatalogFilter | None = None, k: int = 20) -> list[CatalogHit]:
        if k < 1:
            raise ValueError("k 必须 ≥ 1")
        q = _norm(query or "")
        hits = []
        for d in self._datasets.values():
            if filters is not None and not self._passes(d, filters):
                continue
            score = 0.0
            if q:
                score = 3 * _norm(d.title).count(q) + _norm(d.description or "").count(q)
                score += 2 * sum(_norm(t).count(q) for t in d.tags)
                if not score:
                    continue
            entries = self.resources_for(d.dataset_key)
            hits.append(CatalogHit(dataset=d, resources=entries, state=max_state(e.state for e in entries), score=score))
        hits.sort(key=lambda h: (-h.score, h.dataset.dataset_key))
        return hits[:k]


# ---------- 观测值 ----------

_DECIMAL_TYPE = "DATUMOJ_FAKE_DECIMAL"
sqlite3.register_converter(_DECIMAL_TYPE, lambda b: Decimal(b.decode()))
_STRINGS = re.compile(r"'(?:[^']|'')*'|\"(?:[^\"]|\"\")*\"")
_COMMENTS = re.compile(r"--[^\n]*|/\*.*?\*/", re.S)
_FORBIDDEN = re.compile(
    r"\b(insert|update|delete|drop|create|alter|attach|detach|copy|pragma|set|reset|install|load|export|import"
    r"|call|vacuum|checkpoint)\b|\bread_\w+|\b\w+_scan\s*\(|\bglob\s*\(",
    re.I,
)
_ALLOWED_ACTIONS = {sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ, sqlite3.SQLITE_FUNCTION, sqlite3.SQLITE_RECURSIVE}


def _guard_sql(sql: str) -> None:
    bare = _COMMENTS.sub(" ", _STRINGS.sub("''", sql)).strip().rstrip(";").strip()
    if not re.match(r"(select|with)\b", bare, re.I):
        raise QueryRejected("只允许单条 SELECT / WITH 查询")
    if ";" in bare:
        raise QueryRejected("不允许多条语句")
    if mt := _FORBIDDEN.search(bare):
        raise QueryRejected(f"不允许使用 {mt.group(0)!r}")


class FakeObservationStore:
    """数据放在内存里；每次查询临时建一个只读的 SQLite 内存库执行（不是 DuckDB，只用通用 SQL）。"""

    def __init__(self) -> None:
        self._tables: dict[tuple[str, str], list[Observation]] = {}
        self._orders: dict[tuple[str, str], SnapshotOrder] = {}
        self._history: dict[
            tuple[ObservationIdentity, SnapshotOrder | None], tuple[Observation, SnapshotOrder | None]
        ] = {}
        self._generations: dict[
            int, tuple[str, list[tuple[Observation, SnapshotOrder | None]], set[tuple[str, str, str]]]
        ] = {}
        self._current_generation: int | None = None

    def publish_generation(self, manifest: StateGeneration) -> str:
        """测试夹具的发布点：固定历史副本，哈希实际 model_dump_json 清单字节。

        不模拟远端上传；调用者保证该清单代表完整有效状态。生产发布由 301 接入。
        """
        if manifest.generation != (self._current_generation or 0) + 1:
            raise ValueError("测试代次必须连续发布，不能覆盖已有清单")
        digest = hashlib.sha256(manifest.model_dump_json().encode()).hexdigest()
        history = [(o.model_copy(deep=True), order) for o, order in self._history.values()]
        current_refs = {(o.source_id, o.table_key, o.obs_id) for o in self._all()}
        self._generations[manifest.generation] = (digest, history, current_refs)
        self._current_generation = manifest.generation
        return self.pin_generation()

    def discard_generation(self, generation: int) -> None:
        """测试夹具：模拟历史清单或任一依赖已不可用。"""
        self._generations.pop(generation, None)

    def pin_generation(self) -> str:
        number = self._current_generation
        if number is None or number not in self._generations:
            raise GenerationUnavailable("没有可用的已发布代次")
        return str(GenerationToken(generation=number, manifest_sha256=self._generations[number][0]))

    def table_order(self, source_id: str, table_key: str) -> SnapshotOrder | None:
        return self._orders.get((source_id, table_key))

    def write_table(
        self, source_id: str, table_key: str, observations: Sequence[Observation], order: SnapshotOrder | None = None,
    ) -> bool:
        ids: set[ObservationIdentity] = set()
        for o in observations:
            if o.source_id != source_id or o.table_key != table_key:
                raise ValueError(f"观测值 {o.obs_id} 不属于表 {source_id}/{table_key}")
            if o.identity in ids:
                raise ValueError(f"ObservationIdentity 重复：{o.identity}")
            ids.add(o.identity)
        key = (source_id, table_key)
        current = self.table_order(source_id, table_key)
        if order is not None and current is not None and order < current:
            return False
        for observation in observations:
            self._history[(observation.identity, order)] = (observation.model_copy(deep=True), order)
        if observations:
            self._tables[key] = list(observations)
        else:
            self._tables.pop(key, None)
        if order is None:
            self._orders.pop(key, None)
        else:
            self._orders[key] = order
        return True

    def _all(self) -> list[Observation]:
        return [o for key in sorted(self._tables) for o in self._tables[key]]

    def _release_generation(self, raw_token):
        try:
            token = GenerationToken.parse(raw_token)
        except (ValueError, TypeError) as exc:
            raise InvalidGenerationToken("generation_token 格式无效") from exc
        version = self._generations.get(token.generation)
        if version is None:
            raise GenerationUnavailable("指定代次未发布或不可用")
        if version[0] != token.manifest_sha256:
            raise InvalidGenerationToken("generation_token 清单哈希不匹配")
        return token, version

    def list_releases(self, *, generation_token: str, source_id: str,
                      table_key: str | None = None, timeout_s: float = 10) -> list[ReleaseEntry]:
        if timeout_s <= 0:
            raise ValueError("timeout_s 必须 > 0")
        TypeAdapter(SourceId).validate_python(source_id)
        if table_key is not None:
            TypeAdapter(Text).validate_python(table_key)
        deadline = time.monotonic() + timeout_s
        _, version = self._release_generation(generation_token)
        for observation, order in version[1]:
            if time.monotonic() > deadline:
                raise QueryTimeout("发布枚举超时")
            if order is None:
                raise QueryError("发布感知读取需要持久化 SnapshotOrder")
            if observation.release_id is not None and observation.released_at is None:
                raise ReleaseDateUnavailable("显式来源发布缺少 released_at")
        entries = {}
        for o, order in version[1]:
            if time.monotonic() > deadline:
                raise QueryTimeout("发布枚举超时")
            if o.source_id != source_id or (table_key is not None and o.table_key != table_key):
                continue
            day = o.released_at if o.release_id is not None else o.fetched_at.astimezone(UTC).date()
            key = (o.release_id or o.snapshot_sha256, day)
            previous = entries.get(key)
            if previous is None or previous.order < order:
                entries[key] = ReleaseEntry(source_id=source_id, table_key=table_key, release_key=key[0],
                                            released_at=day, order=order.model_copy(deep=True))
        result = sorted(entries.values(), key=lambda e: (e.released_at, e.order, e.release_key))
        if time.monotonic() > deadline:
            raise QueryTimeout("发布枚举超时")
        return result

    @staticmethod
    def _select_releases(history, cutoff):
        cutoff = TypeAdapter(date | str | ReleaseEntry | None).validate_python(cutoff, strict=True)
        if isinstance(cutoff, str) and not cutoff.strip():
            raise ValueError("as_of_release 不能是空发布标识")
        ordered = []
        for observation, order in history:
            if order is None:
                raise QueryError("截至查询需要持久化 SnapshotOrder")
            if observation.release_id is not None and observation.released_at is None:
                raise ReleaseDateUnavailable("显式来源发布缺少 released_at")
            day = (observation.released_at if observation.release_id is not None
                   else observation.fetched_at.astimezone(UTC).date())
            ordered.append((observation, day, order))
        boundary = None
        if isinstance(cutoff, ReleaseEntry):
            ordered = [(o, day, order) for o, day, order in ordered if o.source_id == cutoff.source_id
                       and (cutoff.table_key is None or o.table_key == cutoff.table_key)]
            if not any((o.release_id or o.snapshot_sha256) == cutoff.release_key and day == cutoff.released_at
                       and order == cutoff.order for o, day, order in ordered):
                raise ReleaseNotFound("该代次没有指定来源发布项")
            boundary = (cutoff.released_at, cutoff.order)
        if isinstance(cutoff, str):
            matches = [(o, day, order) for o, day, order in ordered
                       if (o.release_id if o.release_id is not None else o.snapshot_sha256) == cutoff]
            if not matches:
                raise ReleaseNotFound("该代次没有指定发布标识")
            if len({(o.source_id, day) for o, day, _ in matches}) != 1:
                raise AmbiguousRelease("发布标识跨来源或对应多个日期")
            boundary = max((day, order) for _, day, order in matches)
        latest = {}
        for observation, day, order in ordered:
            if isinstance(cutoff, date) and day > cutoff:
                continue
            if boundary is not None and (day, order) > boundary:
                continue
            ref = ObservationRef(source_id=observation.source_id, table_key=observation.table_key,
                                 obs_id=observation.obs_id)
            previous = latest.get(ref)
            if previous is None or (day, order) > (previous[1], previous[2]):
                latest[ref] = (observation, day, order)
            elif (day, order) == (previous[1], previous[2]) and observation.identity != previous[0].identity:
                raise AmbiguousRelease("同一引用的发布排序相同，无法选择版本")
        return [entry[0] for entry in latest.values()]

    def query_readonly(
        self, sql: str, max_rows: int = 1000, timeout_s: float = 10, *,
        generation_token: str | None = None, as_of_release: date | str | ReleaseEntry | None = None,
    ) -> QueryResult:
        if max_rows < 1 or timeout_s <= 0:
            raise ValueError("max_rows 必须 ≥ 1，timeout_s 必须 > 0")
        token = None
        if generation_token is not None or self._current_generation is not None:
            raw_token = self.pin_generation() if generation_token is None else generation_token
            token, version = self._release_generation(raw_token)
            history = version[1]
            if as_of_release is None:
                history = [(o, order) for o, order in history if (o.source_id, o.table_key, o.obs_id) in version[2]]
            obs = self._select_releases(history, as_of_release)
        elif as_of_release is not None:
            raise GenerationUnavailable("截至查询需要已发布代次")
        else:
            obs = self._all()  # 兼容尚未接入代次发布的旧测试/调用方。
        _guard_sql(sql)
        conn = sqlite3.connect(":memory:", detect_types=sqlite3.PARSE_DECLTYPES)
        try:
            cols = list(OBSERVATION_RELEASE_COLUMNS)
            types = {c: (_DECIMAL_TYPE if c == "value" else "TEXT") for c in cols}
            conn.execute(f"CREATE TABLE observations ({', '.join(f'{c} {types[c]}' for c in cols)})")
            conn.executemany(
                f"INSERT INTO observations VALUES ({', '.join('?' * len(cols))})", [_obs_row(o) for o in obs]
            )
            conn.set_authorizer(lambda action, *_: sqlite3.SQLITE_OK if action in _ALLOWED_ACTIONS else sqlite3.SQLITE_DENY)
            deadline = time.monotonic() + timeout_s
            conn.set_progress_handler(lambda: int(time.monotonic() > deadline), 1000)
            try:
                cur = conn.execute(sql)
                rows = cur.fetchmany(max_rows + 1)
            except sqlite3.OperationalError as exc:
                if "interrupted" in str(exc):
                    raise QueryTimeout(f"查询超过 {timeout_s} 秒") from exc
                if "not authorized" in str(exc):
                    raise QueryRejected(str(exc)) from exc
                raise QueryError(str(exc)) from exc
            except sqlite3.Error as exc:
                raise QueryError(str(exc)) from exc
            if len(rows) > max_rows:
                raise TooManyRows(f"结果超过 {max_rows} 行，请加条件或聚合")
            columns = [d[0] for d in cur.description]
        finally:
            conn.close()
        provenance: list[Observation] = []
        row_evidence = []
        warnings = []
        identity_columns = tuple(ObservationIdentity.model_fields)
        supplied = [name for name in identity_columns if name in columns]
        seen = set()
        linked = 0
        lookup: dict[tuple[Any, ...], list[Observation]] = {}
        # 发布感知路径必须含完整身份列，不能凭 obs_id 推测关联。
        if "obs_id" in supplied and (token is None or len(supplied) == len(identity_columns)):
            for observation in obs:
                key = tuple(getattr(observation, name) for name in supplied)
                lookup.setdefault(key, []).append(observation)
        indexes = [columns.index(name) for name in supplied]
        for row in rows:
            matches = lookup.get(tuple(row[index] for index in indexes), [])
            identities = []
            if len(matches) == 1:
                observation = matches[0]
                identities = [observation.identity]
                linked += 1
                if observation.identity not in seen:
                    provenance.append(observation.model_copy(deep=True))
                    seen.add(observation.identity)
            if token is not None:
                row_evidence.append(ObservationRowEvidence(identities=identities, generation=token.generation))
        if token is None:
            return QueryResult(columns=columns, rows=rows, provenance=provenance, warnings=warnings)
        state = (EvidenceState.complete if not rows or linked == len(rows) else
                 EvidenceState.partial if linked else EvidenceState.unavailable)
        if state is not EvidenceState.complete:
            warnings.append("无法证明全部逐行出处；请保留五个 ObservationIdentity 身份列")
        return QueryResult(
            columns=columns, rows=rows, provenance=provenance, warnings=warnings,
            generation_token=str(token), row_evidence=row_evidence, evidence_state=state,
            evidence_inputs={"sql": sql, "generation": token.generation,
                             "as_of_release": as_of_release.isoformat() if isinstance(as_of_release, date)
                             else as_of_release.model_dump(mode="json") if isinstance(as_of_release, ReleaseEntry)
                             else as_of_release},
        )

    def describe(self) -> ObservationCatalog:
        tables = []
        for (source_id, table_key), obs in sorted(self._tables.items()):
            starts = [o.time_start for o in obs if o.time_start]
            ends = [o.time_end for o in obs if o.time_end]
            tables.append(TableSummary(
                source_id=source_id, table_key=table_key, dataset_keys=sorted({o.dataset_key for o in obs}),
                surveys=sorted({o.survey for o in obs}), indicators=sorted({o.indicator for o in obs}),
                area_count=len({o.area_code for o in obs if o.area_code}), time_start=min(starts, default=None),
                time_end=max(ends, default=None), rows=len(obs),
            ))
        return ObservationCatalog(columns=dict(OBSERVATION_RELEASE_COLUMNS), tables=tables)


def _obs_row(o: Observation) -> tuple[Any, ...]:
    d = o.model_dump(mode="json")
    d["dims"] = json.dumps(o.dims, ensure_ascii=False, sort_keys=True)
    d["value"] = None if o.value is None else str(o.value)
    return tuple(d[c] for c in OBSERVATION_RELEASE_COLUMNS)
