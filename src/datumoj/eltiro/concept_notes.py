"""Model-generated reference notes, stored separately from source-owned profiles."""

from dataclasses import dataclass
import json
from pathlib import Path
import sqlite3

from pydantic import TypeAdapter

from datumoj.eltiro.models import (
    Generation, ManualQueueItem, ModelCacheKey, ModelInput, ModelUse, SeriesIdentity,
    SeriesProfile, content_hash,
)
from datumoj.eltiro.model_match import _answer, _context, _one_sentence, _run_inputs
from datumoj.eltiro.match_store import series_key


@dataclass(frozen=True)
class ConceptNote:
    generation: int
    series: SeriesIdentity
    profile_sha256: str
    cache_key: ModelCacheKey
    text: str
    model_use: ModelUse
    label: str = "模型生成"


def _connect(data_root):
    path = Path(data_root) / "eltiro" / "concept_notes.sqlite"
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path)
    db.execute("""CREATE TABLE IF NOT EXISTS notes (
        generation INTEGER, series TEXT, profile_sha256 TEXT, cache_digest TEXT,
        cache_key TEXT NOT NULL, text TEXT NOT NULL, model_use TEXT NOT NULL, label TEXT NOT NULL,
        PRIMARY KEY (generation, series, profile_sha256, cache_digest))""")
    return db


def list_concept_notes(generation, *, data_root, consumer=None) -> list[ConceptNote]:
    """List internal model references in stable identity/cache order (never source descriptions)."""
    TypeAdapter(Generation).validate_python(generation)
    db = _connect(data_root)
    try:
        notes = []
        for row in db.execute("SELECT * FROM notes WHERE generation=? ORDER BY series, profile_sha256, cache_digest",
                              (generation,)):
            key = ModelCacheKey.model_validate_json(row[4])
            if consumer is None or key.consumer == consumer:
                notes.append(ConceptNote(row[0], SeriesIdentity.model_validate_json(row[1]), row[2], key,
                                         row[5], ModelUse.model_validate_json(row[6]), row[7]))
        return notes
    finally:
        db.close()


def generate_concept_notes(generation, *, demand, profiles, consumer, data_root, router,
                           cache, licenses, policy, generation_token, providers) -> list[ConceptNote]:
    """Generate notes only without source descriptions; use demand budget and trusted routing.

    profiles is a ProfileStore; no profile is written or used as authoritative explanation.
    Invalid/none outputs create no note. A repeated task must find its original model cache.
    """
    TypeAdapter(Generation).validate_python(generation)
    demand, consumer, _ = _context(demand, consumer, generation, generation_token,
                                   router, cache, licenses, policy)
    selected, inputs, seen = [], [], set()
    for raw in sorted(profiles.list(generation), key=lambda p: series_key(p.series)):
        profile = SeriesProfile.model_validate(raw.model_dump())
        key = series_key(profile.series)
        if profile.generation != generation or key in seen:
            raise ValueError("invalid profile generation or duplicate identity")
        seen.add(key)
        if (profile.series.source_id not in consumer.allowed_sources
                or (profile.descriptions and "descriptions" not in profile.model_generated_fields)):
            continue
        payload = {"task": "Explain the candidate concept in one sentence; never invent values.",
                   "output_contract": {"note": "one sentence"},
                   "candidate": profile.model_dump(mode="json")}
        inputs.append(ModelInput(id=f"note_{len(inputs)}", step="concept_note", difficulty="medium",
                                 prompt_version="v2", parts=[{"source_id": profile.series.source_id,
                                 "content": json.dumps(payload, sort_keys=True, ensure_ascii=False)}]))
        selected.append(profile)
    if not inputs:
        return []
    outcomes = _run_inputs("concept_note", demand, generation, data_root=data_root, consumer=consumer,
                           router=router, cache=cache, licenses=licenses, policy=policy,
                           generation_token=generation_token, providers=providers, inputs=inputs)
    db = _connect(data_root)
    notes = []
    try:
        with db:
            for profile, (decision, result) in zip(selected, outcomes, strict=True):
                if isinstance(result, ManualQueueItem):
                    continue
                answer = _answer(result, ("note",))
                if answer is None or not _one_sentence(answer["note"]):
                    continue
                note = ConceptNote(generation, profile.series,
                                   content_hash(profile.model_dump(mode="json", exclude={"generation"})),
                                   decision.cache_key(), answer["note"].strip(), result.fields["answer"].model_use)
                db.execute("INSERT OR IGNORE INTO notes VALUES (?,?,?,?,?,?,?,?)",
                           (generation, series_key(profile.series), note.profile_sha256, note.cache_key.digest,
                            note.cache_key.model_dump_json(), note.text, note.model_use.model_dump_json(), note.label))
                notes.append(note)
        return notes
    finally:
        db.close()
