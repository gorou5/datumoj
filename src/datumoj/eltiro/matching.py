"""Deterministic matching and local vector recall against caller-frozen read views.

The host supplies registered demands, trusted consumer configuration and read views
already fixed to ``generation`` (as with generate_profiles). No state-store reads,
model discovery, cloud clients or Rikolto writes occur here.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
import re
import sqlite3
import unicodedata

from pydantic import TypeAdapter

from datumoj.eltiro.contracts import ProfileStore
from datumoj.eltiro.match_store import MatchResult, SQLiteMatchStore, series_key
from datumoj.eltiro.models import (
    Compatibility, ConsumerConfig, DemandSpec, Generation, MatchEvidence, MatchRecord,
    SeriesIdentity, SeriesProfile, content_hash,
)
from datumoj.rikolto.core.contracts import CatalogStore, ChunkStore, Embedder, ObservationStore
from datumoj.rikolto.core.models import GenerationToken

MAX_K = 50
# Codes are whole ASCII tokens: numeric, uppercase abbreviations, or structured IDs.
_CODE = re.compile(r"(?<![\w./:-])[A-Za-z0-9]+(?:[._:/-][A-Za-z0-9]+)*(?!\w|[._:/-][A-Za-z0-9])")


def _codes(text: str) -> set[str]:
    return {t for t in _CODE.findall(text)
            if any(c.isdigit() for c in t) or any(c in "._:/-" for c in t) or (t.isupper() and len(t) >= 2)}


def _normal(text: str) -> str:
    return unicodedata.normalize("NFKC", text).casefold().strip()


def _keywords(text: str) -> set[str]:
    # Latin words and CJK bigrams permit partial Japanese/Chinese phrase recall.
    words = set(re.findall(r"[^\W_]+", _normal(text)))
    for run in re.findall(r"[\u3040-\u30ff\u3400-\u9fff]+", _normal(text)):
        words.update(run[i:i + 2] for i in range(len(run) - 1))
    return words


def _profile_text(profile: SeriesProfile) -> str:
    return "\n".join(text for values in (profile.names, profile.descriptions)
                     for _, text in sorted(values.items()))


def _compatibility(indicator, profile, official):
    unit = "unknown"
    if indicator.unit is not None and profile.unit is not None:
        unit = "same" if indicator.unit.strip().casefold() == profile.unit.strip().casefold() else "different"
    return Compatibility(unit=unit, methodology="same" if official else "unknown",
                         survey="same" if official else "unknown")


class _EvidenceReader:
    def __init__(self, generation, observations, catalog, chunks):
        self.generation, self.observations = generation, observations
        self.catalog, self.chunks = catalog, chunks
        self.tables = {}
        self.documents = None

    def for_profile(self, profile: SeriesProfile) -> list[MatchEvidence]:
        series = profile.series
        items = []
        if profile.availability == "observations":
            key = (series.source_id, series.table_key)
            if key not in self.tables:
                summary = next((t for t in self.observations.describe().tables
                                if (t.source_id, t.table_key) == key), None)
                if summary is None:
                    self.tables[key] = []
                else:
                    where = " AND ".join(f"{k} = '{v.replace(chr(39), chr(39) * 2)}'"
                                         for k, v in zip(("source_id", "table_key"), key))
                    result = self.observations.query_readonly(
                        f"SELECT * FROM observations WHERE {where}", max_rows=max(1, summary.rows))
                    if result.generation_token is not None:
                        if GenerationToken.parse(result.generation_token).generation != self.generation:
                            raise ValueError("evidence generation mismatch")
                    self.tables[key] = result.provenance
            for row in self.tables[key]:
                identity = SeriesIdentity(source_id=row.source_id, table_key=row.table_key, indicator=row.indicator,
                                          indicator_code=row.indicator_code, fixed_dimensions=row.dims)
                if (identity == series and (profile.survey is None or row.survey == profile.survey)
                        and (profile.unit is None or (row.unit is not None
                             and row.unit.strip().casefold() == profile.unit.strip().casefold()))):
                    items.append((row.source_id, row.snapshot_sha256, row.locator))
        elif profile.availability == "catalog_only":
            if series.table_key.startswith("catalog:"):
                dataset = self.catalog.get_dataset(series.table_key.removeprefix("catalog:"))
                if (dataset is not None and dataset.source_id == series.source_id
                        and dataset.snapshot_sha256 is not None and dataset.locator is not None):
                    items.append((dataset.source_id, dataset.snapshot_sha256, dataset.locator))
        else:
            if self.documents is None:
                self.documents = []
                for chunk in self.chunks.iter_chunks():
                    document = self.chunks.get_document(chunk.doc_key, chunk.snapshot_sha256)
                    if document is not None:
                        self.documents.append((document, chunk))
            for document, chunk in self.documents:
                if (document.source_id == series.source_id and chunk.source_id == series.source_id
                        and series.table_key == f"document:{document.kind or 'unknown'}"
                        and document.snapshot_sha256 == chunk.snapshot_sha256):
                    items.append((chunk.source_id, chunk.snapshot_sha256, chunk.locator))
        # One source-owned locator is sufficient; stable choice does not invent hashes.
        return [MatchEvidence(profile_field="series", source_id=s, snapshot_sha256=h, locator=l)
                for s, h, l in sorted(set(items))[:1]]


def _checked_vectors(vectors, count, dim):
    if len(vectors) != count:
        raise ValueError("embedding count mismatch")
    output = []
    for vector in vectors:
        if (len(vector) != dim or any(isinstance(v, bool) or not isinstance(v, (float, int))
                                     or not math.isfinite(v) for v in vector)):
            raise ValueError("invalid embedding vector")
        norm = math.hypot(*vector)
        if not math.isfinite(norm):
            raise ValueError("invalid embedding norm")
        output.append([v / norm if norm else 0.0 for v in vector])
    return output


def _vectors(data_root, generation, embedder, texts):
    identity = {"generation": generation, "model": embedder.model, "dim": embedder.dim}
    path = Path(data_root) / "eltiro" / "vectors" / f"generation-{generation}" / (content_hash(identity) + ".sqlite")
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path)
    try:
        with db:
            db.execute("CREATE TABLE IF NOT EXISTS model (identity TEXT NOT NULL)")
            db.execute("CREATE TABLE IF NOT EXISTS vectors (digest TEXT PRIMARY KEY, vector TEXT NOT NULL)")
            stored = db.execute("SELECT identity FROM model").fetchall()
            if stored and (len(stored) != 1 or json.loads(stored[0][0]) != identity):
                raise ValueError("embedding model mismatch")
            if not stored:
                db.execute("INSERT INTO model VALUES (?)", (json.dumps(identity),))
            found, missing = {}, {}
            for text in texts:
                digest = content_hash(text)
                row = db.execute("SELECT vector FROM vectors WHERE digest=?", (digest,)).fetchone()
                if row:
                    found[digest] = _checked_vectors([json.loads(row[0])], 1, embedder.dim)[0]
                else:
                    missing[digest] = text
            if missing:
                embedded = _checked_vectors(embedder.embed(list(missing.values())), len(missing), embedder.dim)
                for digest, vector in zip(missing, embedded, strict=True):
                    db.execute("INSERT INTO vectors VALUES (?,?)", (digest, json.dumps(vector)))
                    found[digest] = vector
            return [found[content_hash(text)] for text in texts]
    finally:
        db.close()


def match_demand(
    demand: DemandSpec, generation: int, *, profiles: ProfileStore, consumer: ConsumerConfig,
    observations: ObservationStore, catalog: CatalogStore, chunks: ChunkStore,
    store: SQLiteMatchStore, embedder: Embedder | None = None,
    deterministic: bool = True, vector: bool = True, k: int = 10,
) -> MatchResult:
    """Persist one complete run; errors leave previous matches intact.

    ``embedder`` is a trusted host-injected LOCAL implementation of Embedder, never
    loaded from demand text. The host must freeze all three Rikolto views to the
    supplied generation; this function neither pins nor reads publication state.
    """
    demand = DemandSpec.model_validate(demand.model_dump())
    consumer = ConsumerConfig.model_validate(consumer.model_dump())
    TypeAdapter(Generation).validate_python(generation)
    if not isinstance(deterministic, bool) or not isinstance(vector, bool) or not (deterministic or vector):
        raise ValueError("at least one matching tier must be enabled")
    if type(k) is not int or not 1 <= k <= MAX_K:
        raise ValueError("invalid vector recall limit")
    if demand.consumer != consumer.id:
        raise ValueError("consumer mismatch")
    model = None
    if vector and embedder is not None:
        if (not isinstance(embedder.model, str) or not embedder.model.strip()
                or type(embedder.dim) is not int or embedder.dim < 1):
            raise ValueError("invalid embedding model identity")
        model = [embedder.model, embedder.dim]
    config = content_hash({"demand": demand.model_dump(mode="json"), "consumer": consumer.model_dump(mode="json"),
                           "deterministic": deterministic, "vector": vector, "model": model, "k": k})
    cached = store.load_result(demand.id, demand.version, generation, config)
    if cached is not None:
        return cached
    candidates = []
    seen = set()
    for raw in profiles.list(generation):
        profile = SeriesProfile.model_validate(raw.model_dump())
        key = series_key(profile.series)
        if profile.generation != generation or key in seen:
            raise ValueError("invalid profile generation or duplicate identity")
        seen.add(key)
        if profile.series.source_id in consumer.allowed_sources:
            candidates.append(profile)
    candidates.sort(key=lambda p: series_key(p.series))
    history = {}
    # Deliberately do not carry decisions across demand versions: concepts may change.
    for record in sorted(store.for_demand(demand.id, demand.version), key=lambda r: r.generation):
        if record.generation < generation:
            history[(record.indicator_key, series_key(record.series))] = record.confirmation
    reader = _EvidenceReader(generation, observations, catalog, chunks)
    texts = [_profile_text(p) for p in candidates]
    queries = ["\n".join([i.concept, *i.examples.positive]) for i in demand.indicators]
    vectors = None
    if model is not None:
        vectors = _vectors(store.path.parent.parent, generation, embedder, texts + queries)
    records, missing, evidence_cache = [], {}, {}
    for index, indicator in enumerate(demand.indicators):
        codes = _codes(queries[index])
        words = _keywords(queries[index])
        vector_scores = {}
        if vectors is not None:
            query = vectors[len(candidates) + index]
            scores = [(sum(a * b for a, b in zip(query, v, strict=True)), n)
                      for n, v in enumerate(vectors[:len(candidates)])]
            scores.sort(key=lambda item: (-item[0], series_key(candidates[item[1]].series)))
            vector_scores = {n: max(0.0, min(1.0, score)) for score, n in scores[:k]}
        for n, profile in enumerate(candidates):
            key = series_key(profile.series)
            indicator_code = bool(profile.series.indicator_code and profile.series.indicator_code in codes)
            concept = bool({c.code for c in profile.concept_ids} & codes)
            if "concept_ids" in profile.model_generated_fields:
                concept = False
            official = indicator_code or concept
            overlap = words & _keywords(texts[n])
            lexical = bool(overlap)
            prior = history.get((indicator.key, key), "pending")
            det_hit = deterministic and (official or concept or lexical)
            if not det_hit and n not in vector_scores and prior == "pending":
                continue
            if key not in evidence_cache:
                evidence_cache[key] = reader.for_profile(profile)
            evidence = evidence_cache[key]
            if not evidence:
                missing[key] = profile.series
                continue
            negative_text = _normal("\n".join([texts[n], profile.series.indicator_code or "",
                                               *(c.code for c in profile.concept_ids),
                                               profile.methodology or "", profile.survey or ""]))
            negatives = [i + 1 for i, example in enumerate(indicator.examples.negative)
                         if _normal(example) in negative_text
                         or bool(_codes(example) & {profile.series.indicator_code, *(c.code for c in profile.concept_ids)})]
            preferred = profile.series.source_id in indicator.preferred_sources
            compatibility = _compatibility(indicator, profile, official)
            reasons = []
            if det_hit:
                tier = "deterministic"
                confidence = (0.95 if indicator_code else 0.85 if concept else 0.4 + 0.4 * len(overlap) / len(words))
                reasons.append("official code exact" if indicator_code else "concept code exact" if concept else "keyword overlap")
            else:
                tier = "vector" if n in vector_scores else "deterministic"
                confidence = 0.8 * vector_scores[n] if n in vector_scores else 0.0
                reasons.append("local vector recall" if n in vector_scores else "carried human decision")
            confidence = round(min(1.0, confidence + (0.02 if preferred else 0.0)), 6)
            if official:
                reasons.append("official code defines methodology and survey")
            if negatives:
                reasons.append("counterexample #" + ",#".join(map(str, negatives)))
            if preferred:
                reasons.append("preferred source +0.02")
            if prior != "pending":
                reasons.append("human decision carried within demand version")
            auto_bind = bool(det_hit and official and not negatives and prior != "rejected"
                             and all(v == "same" for v in compatibility.model_dump().values()))
            records.append(MatchRecord(
                demand_id=demand.id, demand_version=demand.version, indicator_key=indicator.key,
                series=profile.series, tier=tier, confidence=confidence, reason="; ".join(reasons),
                evidence=evidence, generation=generation, confirmation=prior,
                compatibility=compatibility, auto_bind=auto_bind))
    records.sort(key=lambda r: ({"confirmed": 0, "pending": 1, "rejected": 2}[r.confirmation],
                                r.indicator_key, -r.confidence, series_key(r.series)))
    return store.save_result(demand.id, demand.version, generation, config, records,
                             [missing[key] for key in sorted(missing)], vector and embedder is None)
