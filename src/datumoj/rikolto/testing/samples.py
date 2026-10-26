"""造测试数据的小工具：合格测试和各线的测试都可以用。全部是合法的契约对象，参数可以覆盖。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from hashlib import sha256
from typing import Any

from datumoj.rikolto.core.contracts import SourceSpec
from datumoj.rikolto.core.models import (
    FAILED_OUTCOMES,
    Chunk,
    Dataset,
    Document,
    FetchOutcome,
    Observation,
    Resource,
    Snapshot,
    TimeGranularity,
)

JST = timezone(timedelta(hours=9))
T0 = datetime(2026, 10, 20, 9, 0, tzinfo=JST)


def sha(data: bytes) -> str:
    return sha256(data).hexdigest()


def make_spec(source_id: str = "jp-test", kind: str = "fake", **kw: Any) -> SourceSpec:
    base: dict[str, Any] = dict(
        id=source_id, name=f"测试来源 {source_id}", country="jp", kind=kind,
        base_url="https://example.go.jp/", allowed_hosts=["example.go.jp"],
        max_total_bytes=10_000_000, max_files=100, scope={"urls": ["https://example.go.jp/a.txt"]},
    )
    base.update(kw)
    return SourceSpec(**base)


def make_snapshot(
    source_id: str = "jp-test",
    url: str = "https://example.go.jp/a.txt",
    data: bytes | None = b"hello",
    *,
    fetched_at: datetime = T0,
    outcome: FetchOutcome = FetchOutcome.ok,
    snapshot_id: str | None = None,
    **kw: Any,
) -> Snapshot:
    """outcome 是失败类或 not_modified 时忽略 data 并自动补 error / 状态码；data=None 表示没有正文。"""
    if outcome in FAILED_OUTCOMES or outcome is FetchOutcome.not_modified:
        data = None
    base: dict[str, Any] = dict(
        snapshot_id=snapshot_id or f"{source_id}-{fetched_at.isoformat()}-{sha(url.encode())[:8]}",
        source_id=source_id, resource_url=url, fetched_at=fetched_at, outcome=outcome,
        content_type="text/plain",
    )
    if data is not None:
        base.update(sha256=sha(data), size=len(data), http_status=200)
    if outcome is FetchOutcome.http_error:
        base.update(http_status=503, error="HTTP 503")
    elif outcome in (FetchOutcome.network_error, FetchOutcome.too_large, FetchOutcome.blocked_by_robots):
        base.update(error=outcome.value)
    elif outcome is FetchOutcome.not_modified:
        base.update(http_status=304)
    base.update(kw)
    return Snapshot(**base)


def make_document(doc_key: str, snapshot_sha256: str, title: str = "テスト文書", **kw: Any) -> Document:
    base: dict[str, Any] = dict(
        doc_key=doc_key, source_id=doc_key.split(":", 1)[0], snapshot_sha256=snapshot_sha256, title=title, lang="ja",
    )
    base.update(kw)
    return Document(**base)


def make_chunk(doc_key: str, snapshot_sha256: str, locator: str, text: str, **kw: Any) -> Chunk:
    base: dict[str, Any] = dict(
        chunk_id=f"{doc_key}#{locator}", doc_key=doc_key, locator=locator, text=text,
        snapshot_sha256=snapshot_sha256, lang="ja",
    )
    base.update(kw)
    return Chunk(**base)


def make_observation(obs_id: str, value: str | Decimal | None, *, note: str | None = None, **kw: Any) -> Observation:
    base: dict[str, Any] = dict(
        obs_id=obs_id, source_id="jp-stat", dataset_key="jp-stat:pop", table_key="t1", survey="国勢調査",
        indicator="人口", area_code="13000", area_label="東京都", time_start=date(2020, 1, 1),
        time_end=date(2020, 12, 31), time_label="2020年", granularity=TimeGranularity.year, unit="人",
        value=None if value is None else Decimal(value), note=note, snapshot_sha256="cd" * 32,
        locator="row=1", fetched_at=T0,
    )
    base.update(kw)
    return Observation(**base)


def make_dataset(dataset_key: str, title: str, **kw: Any) -> Dataset:
    base: dict[str, Any] = dict(
        dataset_key=dataset_key, source_id=dataset_key.split(":", 1)[0], title=title, kind="dataset",
    )
    base.update(kw)
    return Dataset(**base)


def make_resource(dataset_key: str, resource_id: str, url: str, **kw: Any) -> Resource:
    base: dict[str, Any] = dict(
        resource_id=resource_id, dataset_key=dataset_key, source_id=dataset_key.split(":", 1)[0], url=url,
    )
    base.update(kw)
    return Resource(**base)


@dataclass
class ParseSample:
    """ParserSuite 的一个样本：快照、raw 里的原始字节、采集器随快照保存的 meta。"""

    snapshot: Snapshot
    data: bytes
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass
class RetrievalCorpus:
    """RetrieverSuite 交给实现方的语料：注册表里的来源、manifest 里的抓取、已解析的文档。"""

    specs: list[SourceSpec] = field(default_factory=list)
    snapshots: list[Snapshot] = field(default_factory=list)
    documents: list[tuple[Document, list[Chunk]]] = field(default_factory=list)
