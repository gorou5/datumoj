"""Incremental matching of active demands against caller-selected generations."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date

from pydantic import TypeAdapter

from datumoj.eltiro.alerts import SQLiteAlertStore, generate_alerts
from datumoj.eltiro.contracts import DemandRegistry, ProfileStore
from datumoj.eltiro.match_store import MatchResult, SQLiteMatchStore, series_key
from datumoj.eltiro.matching import (
    _codes,
    _EvidenceReader,
    _keywords,
    _matching_config,
    _profile_text,
    _recall_scores,
    _vectors,
    match_demand,
)
from datumoj.eltiro.model_match import _context, _model_config
from datumoj.eltiro.models import ConsumerConfig, DemandSpec, Generation, SeriesProfile, content_hash
from datumoj.rikolto.core.contracts import CatalogStore, ChunkStore, Embedder, ObservationStore


@dataclass(frozen=True)
class RefreshCounts:
    added: int
    modified: int
    deleted: int
    rematched: int
    carried: int
    alerts: int


def _profiles(store, generation):
    result = {}
    for raw in store.list(generation):
        profile = SeriesProfile.model_validate(raw.model_dump())
        key = series_key(profile.series)
        if profile.generation != generation or key in result:
            raise ValueError("invalid profile generation or duplicate identity")
        result[key] = profile
    return result


def _digest(profile):
    # Moving the same content to another generation is not a modification.
    return content_hash(profile.model_dump(mode="json", exclude={"generation"}))


class _ReverseIndex:
    def __init__(self):
        self.codes = defaultdict(set)
        self.words = defaultdict(set)
        self.sources = defaultdict(set)
        self.series = defaultdict(set)

    def add(self, demand, history, previous):
        for indicator in demand.indicators:
            query = "\n".join([indicator.concept, *indicator.examples.positive])
            for code in _codes(query):
                self.codes[code].add(demand.id)
            for word in _keywords(query):
                self.words[word].add(demand.id)
            for source in indicator.preferred_sources:
                self.sources[source].add(demand.id)
        # Include every historical human decision in this demand version, even
        # when the series disappeared and the immediately previous run is empty.
        for record in history:
            if record.confirmation != "pending":
                self.series[series_key(record.series)].add(demand.id)
        for series in [r.series for r in previous.records] + previous.missing_evidence:
            self.series[series_key(series)].add(demand.id)

    def affected(self, profile):
        found = set(self.series[series_key(profile.series)])
        found.update(self.sources[profile.series.source_id])
        for code in {profile.series.indicator_code, *(c.code for c in profile.concept_ids)}:
            found.update(self.codes[code])
        for word in _keywords(_profile_text(profile)):
            found.update(self.words[word])
        return found


def _vector_affected(demand, old, new, allowed, generation, store, embedder, k):
    queries = ["\n".join([i.concept, *i.examples.positive]) for i in demand.indicators]
    recalls = []
    for profiles in (old, new):
        candidates = [p for key, p in sorted(profiles.items()) if p.series.source_id in allowed]
        vectors = _vectors(store.path.parent.parent, generation, embedder,
                           [_profile_text(p) for p in candidates] + queries)
        recalls.append([
            {series_key(candidates[n].series): score for n, score in
             _recall_scores(candidates, vectors[:len(candidates)], query, k).items()}
            for query in vectors[len(candidates):]
        ])
    # Full matching keeps top k even at zero cosine. Comparing exact recall sets
    # also catches a deleted top-k candidate promoting an unchanged replacement.
    return recalls[0] != recalls[1]


def _carry_safe(demand, previous, history, new, reader, deterministic):
    latest = {}
    for record in sorted(history, key=lambda r: r.generation):
        latest[(record.indicator_key, series_key(record.series))] = record.confirmation
    current_keys = {(r.indicator_key, series_key(r.series)) for r in previous.records}
    for record in previous.records:
        key = series_key(record.series)
        if key not in new or reader.for_profile(new[key]) != record.evidence:
            return False
        status = latest.get((record.indicator_key, key), "pending")
        if (status != record.confirmation or
                (status != "pending") != ("human decision carried within demand version" in record.reason)):
            return False
    for series in previous.missing_evidence:
        key = series_key(series)
        if key not in new or reader.for_profile(new[key]):
            return False
    if deterministic:
        for (indicator, key), status in latest.items():
            if (status != "pending" and key in new and (indicator, key) not in current_keys
                    and reader.for_profile(new[key])):
                return False
    # Confirmation edits can alter ordering within a group; do not copy stale order.
    ordered = sorted(previous.records, key=lambda r: (
        {"confirmed": 0, "pending": 1, "rejected": 2}[r.confirmation],
        r.indicator_key, -r.confidence, series_key(r.series)))
    return ordered == previous.records


def refresh(
    new_generation: int, previous_generation: int, *, registry: DemandRegistry,
    profiles: ProfileStore, consumers: Mapping[str, ConsumerConfig], store: SQLiteMatchStore,
    alerts: SQLiteAlertStore, observations: ObservationStore, catalog: CatalogStore, chunks: ChunkStore,
    as_of: date, embedder: Embedder | None = None, deterministic: bool = True,
    vector: bool = True, k: int = 10,
    model: bool = False, router=None, cache=None, licenses=None, policy=None,
    generation_token=None, providers=None,
) -> RefreshCounts:
    """Refresh active demands only; all Rikolto views must be frozen to new_generation.

    No publication-state discovery, execution, outbox writes or push.
    Optional model judgments use the same frozen plan/cache rules as full matching.
    Completed per-demand runs and alert identities make interrupted retries safe.
    """
    for generation in (new_generation, previous_generation):
        TypeAdapter(Generation).validate_python(generation)
    if new_generation <= previous_generation:
        raise ValueError("new generation must follow previous generation")
    if type(as_of) is not date:
        raise ValueError("as_of must be a date")
    demands = [DemandSpec.model_validate(d.model_dump()) for d in registry.list_active()]
    contexts = {}
    index = _ReverseIndex()
    for demand in demands:
        consumer = ConsumerConfig.model_validate(consumers[demand.consumer].model_dump())
        config, vector_model = _matching_config(demand, consumer, deterministic, vector, embedder, k)
        config = _model_config(config, model, policy, generation_token)
        if model:
            _context(demand, consumer, new_generation, generation_token, router, cache, licenses, policy)
        try:
            previous = store.load_result(demand.id, demand.version, previous_generation, config)
        except ValueError:
            previous = None  # Changed trusted configuration requires a fresh run.
        history = [r for r in store.for_demand(demand.id, demand.version) if r.generation < new_generation]
        baseline = previous or MatchResult(store.for_demand(demand.id, demand.version, previous_generation), [], False)
        index.add(demand, history, baseline)
        contexts[demand.id] = (consumer, config, vector_model, previous, baseline, history)
    old, new = _profiles(profiles, previous_generation), _profiles(profiles, new_generation)
    added, deleted = new.keys() - old.keys(), old.keys() - new.keys()
    modified = {key for key in old.keys() & new.keys() if _digest(old[key]) != _digest(new[key])}
    affected = set()
    for key in added | deleted | modified:
        for side in (old, new):
            if key in side:
                profile = side[key]
                affected.update(d for d in index.affected(profile)
                                if profile.series.source_id in contexts[d][0].allowed_sources)
    reader = _EvidenceReader(new_generation, observations, catalog, chunks)
    rematched = carried = alert_count = 0
    for demand in demands:
        consumer, config, vector_model, previous, baseline, history = contexts[demand.id]
        current = store.load_result(demand.id, demand.version, new_generation, config)
        if current is not None and model:
            current = match_demand(demand, new_generation, profiles=profiles, consumer=consumer,
                                   observations=observations, catalog=catalog, chunks=chunks, store=store,
                                   embedder=embedder, deterministic=deterministic, vector=vector, k=k,
                                   model=model, router=router, cache=cache, licenses=licenses, policy=policy,
                                   generation_token=generation_token, providers=providers)
        if current is None:
            selected = model or demand.id in affected or previous is None
            vector_changes = any(
                (new.get(key) or old[key]).series.source_id in consumer.allowed_sources
                and (key not in old or key not in new or _profile_text(old[key]) != _profile_text(new[key]))
                for key in added | deleted | modified)
            if not selected and vector_model is not None and vector_changes:
                selected = _vector_affected(demand, old, new, consumer.allowed_sources,
                                            new_generation, store, embedder, k)
            if not selected:
                allowed = {key: p for key, p in new.items() if p.series.source_id in consumer.allowed_sources}
                selected = not _carry_safe(demand, previous, history, allowed, reader, deterministic)
            if selected:
                current = match_demand(demand, new_generation, profiles=profiles, consumer=consumer,
                                       observations=observations, catalog=catalog, chunks=chunks,
                                       store=store, embedder=embedder, deterministic=deterministic, vector=vector, k=k,
                                       **({"model": model, "router": router, "cache": cache, "licenses": licenses,
                                               "policy": policy, "generation_token": generation_token, "providers": providers}
                                          if model else {}))
                rematched += 1
            else:
                records = [r.model_copy(update={"generation": new_generation}) for r in previous.records]
                current = store.save_result(demand.id, demand.version, new_generation, config, records,
                                            previous.missing_evidence, previous.vector_skipped)
                carried += 1
        alert_count += generate_alerts(demand, new_generation, baseline, current,
                                       old_profiles=old, new_profiles=new, as_of=as_of, store=alerts)
    return RefreshCounts(len(added), len(modified), len(deleted), rematched, carried, alert_count)
