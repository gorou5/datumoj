"""按注册表和抓取记录区分检索缺口，不用其他来源冒充命中。"""

from collections.abc import Sequence

from datumoj.core.contracts import Manifest, Registry
from datumoj.core.models import FetchOutcome, GapState, Hit, Snapshot

_SUCCESS = {FetchOutcome.ok, FetchOutcome.not_modified}
_GAP_ORDER = (GapState.out_of_scope, GapState.fetch_failed, GapState.not_ingested, GapState.zero_hits)


def _available(snapshots: Sequence[Snapshot]) -> str:
    # Manifest 按抓取时间升序返回，同一时间后写入的在后。
    snapshot = next((s for s in reversed(snapshots) if s.outcome in _SUCCESS and s.sha256), None)
    if snapshot is None:
        return "最近的可查内容：本来源无可回溯的成功快照"
    return (
        f"最近的可查内容：{snapshot.source_id} {snapshot.resource_url}，"
        f"快照 {snapshot.snapshot_id} sha256={snapshot.sha256}，抓取时间 {snapshot.fetched_at.isoformat()}"
    )


def gap_state(
    source_ids: Sequence[str] | None,
    hits: Sequence[Hit],
    *,
    registry: Registry,
    manifest: Manifest,
) -> tuple[GapState, str | None]:
    """返回状态及逐来源说明；最近内容只是快照线索，不充当查询命中。"""
    if hits:
        return GapState.ok, None
    if not source_ids:
        snapshots = sorted(
            (s for spec in registry.all() for s in manifest.snapshots_for(spec.id)),
            key=lambda s: s.fetched_at,
        )
        return GapState.zero_hits, f"全部来源检索零命中；{_available(snapshots)}"

    states = []
    details = []
    for source_id in dict.fromkeys(source_ids):
        if registry.get(source_id) is None:
            state, reason = GapState.out_of_scope, "注册表里没有这个来源；最近的可查内容：本来源不在范围内"
        else:
            snapshots = manifest.snapshots_for(source_id)
            if any(s.outcome in _SUCCESS for s in snapshots):
                state, reason = GapState.zero_hits, "已有成功快照，检索零命中"
            elif snapshots:
                last = snapshots[-1]
                state = GapState.fetch_failed
                reason = f"最近一次抓取失败：{last.outcome.value} {last.error or ''}".strip()
            else:
                state, reason = GapState.not_ingested, "还没有成功快照"
            reason = f"{reason}；{_available(snapshots)}"
        states.append(state)
        details.append(f"{source_id}: {state.value}（{reason}）")
    return min(states, key=_GAP_ORDER.index), "；".join(details)
