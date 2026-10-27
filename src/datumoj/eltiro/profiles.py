"""Deterministic, source-only series profiles; no model, ChangeLog or state-store access.

generate_profiles receives a published generation number and caller-frozen read views
for that generation (describe/catalog/chunks cannot themselves pin a generation).
It scans readable content to fingerprint every unit, rebuilding only cache misses.
Unknown language is ``und``; invalid/oversized or contradictory metadata fails
instead of silently discarding source information. No translation, unit
conversion, or scale inference is performed.
"""

from collections import defaultdict
from hashlib import sha256
import json

from pydantic import TypeAdapter

from datumoj.eltiro.models import ConceptId, Frequency, Generation, Scale, SeriesIdentity, SeriesProfile
from datumoj.eltiro.profile_store import SQLiteProfileStore
from datumoj.rikolto.core.contracts import CatalogStore, ChunkStore, ObservationStore, Registry
from datumoj.rikolto.core.models import Dataset, Document, Observation


def _json(value) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str)


def _fingerprint(parts) -> str:
    return sha256(_json(sorted(_json(part) for part in parts)).encode()).hexdigest()


def _one(values, field):
    values = set(values)
    if len(values) > 1:
        raise ValueError(f"conflicting source {field}")
    return next(iter(values), None)


def _texts(dataset: Dataset):
    """Read explicit multilingual metadata; never infer a language from a source id."""
    meta = dataset.extras
    lang = meta.get("language") or meta.get("lang") or "und"
    names = dict(meta.get("title_translated") or {})
    descriptions = dict(meta.get("notes_translated") or {})
    names.setdefault(lang, dataset.title)
    if dataset.description:
        descriptions.setdefault(lang, dataset.description)
    return names, descriptions


def _source_metadata(datasets):
    """Only explicit metadata fields, not parsing of titles/units into multipliers."""
    descriptions, concepts, scales = {}, {}, []
    for dataset in datasets:
        _, texts = _texts(dataset)
        for lang, text in texts.items():
            if lang in descriptions and descriptions[lang] != text:
                raise ValueError("conflicting source descriptions")
            descriptions[lang] = text
        for value in dataset.extras.get("concept_ids", []):
            concept = ConceptId.model_validate(value)
            concepts[(concept.scheme, concept.code)] = concept
        # These keys are already emitted by Rikolto's e-Stat and Dashboard parsers.
        for field, scheme in (("stat_inf_id", "e-Stat"), ("indicator_code", dataset.source_id)):
            if dataset.extras.get(field):
                concept = ConceptId(scheme=scheme, code=dataset.extras[field])
                concepts[(concept.scheme, concept.code)] = concept
        raw_scale = dataset.extras.get("scale")
        if isinstance(raw_scale, dict) and raw_scale.get("evidence") and raw_scale.get("multiplier") is not None:
            scales.append(Scale.model_validate(raw_scale))
    scale = Scale()
    if scales:
        if any(item != scales[0] for item in scales):
            raise ValueError("conflicting source scales")
        scale = scales[0]
    return descriptions, [concepts[key] for key in sorted(concepts)], scale


def _observation_profiles(generation, rows: list[Observation], datasets):
    grouped = defaultdict(list)
    for row in rows:
        key = (row.source_id, row.table_key, row.indicator, row.indicator_code, _json(row.dims))
        grouped[_json(key)].append(row)
    profiles = []
    frequencies = {"year": "annual", "fiscal_year": "annual", "quarter": "quarterly",
                   "month": "monthly", "week": "weekly", "day": "daily"}
    for key in sorted(grouped):
        items = grouped[key]
        row = items[0]
        metadata = [datasets[k] for k in sorted({item.dataset_key for item in items}) if k in datasets]
        descriptions, concepts, scale = _source_metadata(metadata)
        if row.indicator_code:
            concept = ConceptId(scheme=row.source_id, code=row.indicator_code)
            if concept not in concepts:
                concepts.insert(0, concept)
        languages = {d.extras.get("language") or d.extras.get("lang") or "und" for d in metadata}
        language = next(iter(languages)) if len(languages) == 1 else "und"
        names = {}
        for dataset in metadata:
            # Dataset translations describe this indicator only when its title agrees.
            if dataset.title == row.indicator:
                for lang, text in _texts(dataset)[0].items():
                    if lang in names and names[lang] != text:
                        raise ValueError("conflicting source names")
                    names[lang] = text
        names.setdefault(language, row.indicator)
        unit = _one([item.unit for item in items if item.unit is not None], "units")
        if unit is None:
            unit = _one([d.extras["unit"] for d in metadata if d.extras.get("unit") is not None], "units")
        kinds = {frequencies.get(item.granularity, "irregular") for item in items}
        frequency = next(iter(kinds)) if len(kinds) == 1 else "irregular"
        profiles.append(SeriesProfile(
            series=SeriesIdentity(source_id=row.source_id, table_key=row.table_key, indicator=row.indicator,
                                  indicator_code=row.indicator_code, fixed_dimensions=row.dims),
            names=names, descriptions=descriptions, concept_ids=concepts,
            survey=_one([item.survey for item in items], "surveys"), unit=unit, scale=scale,
            methodology=_one([d.extras["methodology"] for d in metadata if d.extras.get("methodology")],
                             "methodology"),
            areas=sorted({item.area_code or item.area_label for item in items if item.area_code or item.area_label}),
            time_start=min((item.time_start for item in items if item.time_start), default=None),
            time_end=max((item.time_end for item in items if item.time_end), default=None),
            frequency=frequency, availability="observations", generation=generation,
        ))
    return profiles


def _catalog_profile(generation, dataset):
    names, descriptions = _texts(dataset)
    _, concepts, scale = _source_metadata([dataset])
    return SeriesProfile(
        series=SeriesIdentity(source_id=dataset.source_id, table_key=f"catalog:{dataset.dataset_key}",
                              indicator=dataset.title),
        names=names, descriptions=descriptions, concept_ids=concepts, scale=scale,
        unit=dataset.extras.get("unit"), methodology=dataset.extras.get("methodology"),
        frequency=TypeAdapter(Frequency).validate_python(dataset.extras.get("frequency", "irregular")),
        availability="catalog_only", generation=generation,
    )


def _document_profile(generation, source, kind, documents: list[Document]):
    names, descriptions = {}, {}
    # Shortcut: aggregate by source and document kind; topic clustering is deferred.
    for document in sorted(documents, key=lambda d: d.doc_key):
        # One representative source title/description per language, in stable key order.
        names.setdefault(document.lang, document.title)
        if document.metadata.get("description"):
            descriptions.setdefault(document.lang, document.metadata["description"])
    return SeriesProfile(
        series=SeriesIdentity(source_id=source, table_key=f"document:{kind}", indicator=kind),
        names=names, descriptions=descriptions, frequency="irregular",
        availability="document_only", generation=generation,
    )


def generate_profiles(
    generation: int, *, observations: ObservationStore, catalog: CatalogStore,
    chunks: ChunkStore, registry: Registry, store: SQLiteProfileStore,
    previous_generation: int | None = None,
) -> list[SeriesProfile]:
    """Replace one Eltiro generation atomically; return detached, deterministic profiles.

    Read views must remain consistent for the entire call. All input content is data.
    Incremental reads still scan fingerprints; only changed units materialize profiles.
    Missing previous-generation cache entries safely trigger a full rebuild of that unit.
    Exceptions propagate without replacing the destination generation.
    """
    TypeAdapter(Generation).validate_python(generation)
    if previous_generation is not None:
        TypeAdapter(Generation).validate_python(previous_generation)
    # CatalogStore has no cursor: increase its documented result limit until exhausted.
    limit = 128
    while True:
        hits = catalog.search("", k=limit)
        if len(hits) < limit:
            break
        limit *= 2
    datasets = {hit.dataset.dataset_key: hit.dataset for hit in hits}
    units, used_datasets, sources = {}, set(), set()

    def cached(key, fingerprint):
        if previous_generation is None:
            return None
        previous = store.unchanged_unit(previous_generation, key, fingerprint)
        if previous is None:
            return None
        return [SeriesProfile.model_validate({**p.model_dump(), "generation": generation}) for p in previous]

    observation_catalog = observations.describe()
    columns = ", ".join(sorted(Observation.model_fields.keys() & observation_catalog.columns.keys()))
    for table in sorted(observation_catalog.tables, key=lambda t: (t.source_id, t.table_key)):
        source, table_key = table.source_id, table.table_key
        sources.add(source)
        # SQL parameters are unavailable in the read contract; quote both string literals.
        where = " AND ".join(f"{column} = '{value.replace(chr(39), chr(39) * 2)}'"
                             for column, value in (("source_id", source), ("table_key", table_key)))
        # Named columns avoid relying on provenance for fingerprints, including duplicate
        # obs_ids across tables. Hash row contents too: same-snapshot reparsing can change metadata.
        result = observations.query_readonly(f"SELECT {columns} FROM observations WHERE {where}",
                                             max_rows=max(1, table.rows))
        used = {row[result.columns.index("dataset_key")] for row in result.rows}
        used_datasets.update(used)
        metadata = [datasets[k].model_dump(mode="json") for k in sorted(used) if k in datasets]
        fingerprint = _fingerprint([table.model_dump(mode="json"), metadata,
                                    sorted(_json(dict(zip(result.columns, row))) for row in result.rows)])
        key = _json(["observations", source, table_key])
        profiles = cached(key, fingerprint)
        if profiles is None:
            detail = observations.query_readonly(f"SELECT * FROM observations WHERE {where}",
                                                 max_rows=max(1, table.rows))
            if len(detail.provenance) != len(detail.rows):
                raise ValueError("cannot prove complete observation provenance")
            profiles = _observation_profiles(generation, detail.provenance, datasets)
        units[key] = fingerprint, profiles

    for key in sorted(datasets):
        dataset = datasets[key]
        sources.add(dataset.source_id)
        if key in used_datasets:
            continue
        fingerprint = _fingerprint([dataset.model_dump(mode="json")])
        unit_key = _json(["catalog", key])
        profiles = cached(unit_key, fingerprint)
        units[unit_key] = fingerprint, profiles if profiles is not None else [_catalog_profile(generation, dataset)]

    documents, blocks = {}, defaultdict(list)
    for chunk in chunks.iter_chunks():
        key = (chunk.doc_key, chunk.snapshot_sha256)
        if key not in documents:
            document = chunks.get_document(*key)
            if document is None:
                raise ValueError("chunk has no source document")
            documents[key] = document
        document = documents[key]
        group = (document.source_id, document.kind or "unknown")
        blocks[group].append(chunk.model_dump(mode="json"))
    groups = defaultdict(list)
    for document in documents.values():
        groups[(document.source_id, document.kind or "unknown")].append(document)
    for (source, kind), items in sorted(groups.items()):
        sources.add(source)
        fingerprint = _fingerprint([d.model_dump(mode="json") for d in items] + blocks[(source, kind)])
        key = _json(["documents", source, kind])
        profiles = cached(key, fingerprint)
        units[key] = fingerprint, (profiles if profiles is not None
                                   else [_document_profile(generation, source, kind, items)])

    for source in sorted(sources):
        if registry.get(source) is None:
            raise ValueError(f"unregistered profile source: {source}")
    store.write_units(generation, units)
    return store.list(generation)
