"""Offline gap reports from read-only contracts; report JSON objects are detached.

The host supplies current consumer authorization and generation-pinned stores.
Only report files are written; no matcher, model, intake or Rikolto execution occurs.
"""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path

from pydantic import TypeAdapter

from datumoj.eltiro.contracts import DemandRegistry, MatchStore, ProfileStore
from datumoj.eltiro.models import ConsumerConfig, DemandSpec, Generation, MatchRecord, SeriesProfile
from datumoj.rikolto.core.contracts import Registry

REASONS = {
    "missing": "No authorized candidate in this demand version and generation.",
    "catalog_only": "Candidates include catalog entries but no observations.",
    "document_only": "Candidates include documents but no observations.",
    "pending_confirmation": "Observation candidates await confirmation; none is bound.",
    "below_min_sources": "Distinct bound observation sources are fewer than min_sources.",
    "rejected_only": "Every authorized candidate was rejected by a person.",
}


def _context(demand, generation, consumer):
    demand = DemandSpec.model_validate(demand.model_dump())
    generation = TypeAdapter(Generation).validate_python(generation)
    consumer = ConsumerConfig.model_validate(consumer.model_dump())
    if consumer.id != demand.consumer:
        raise ValueError("consumer does not own demand")
    return demand, generation, consumer


def _report(demand, generation, consumer, matches, profiles, registered):
    grouped = {indicator.key: [] for indicator in demand.indicators}
    for raw in matches.for_demand(demand.id, demand.version, generation):
        record = MatchRecord.model_validate(raw.model_dump())
        if ((record.demand_id, record.demand_version, record.generation)
                != (demand.id, demand.version, generation)
                or record.indicator_key not in grouped
                or record.series.source_id not in consumer.allowed_sources):
            continue
        raw_profile = profiles.get(generation, record.series)
        if raw_profile is None:
            raise ValueError("matched series has no profile in requested generation")
        profile = SeriesProfile.model_validate(raw_profile.model_dump())
        if profile.generation != generation or profile.series != record.series:
            raise ValueError("matched profile identity or generation mismatch")
        grouped[record.indicator_key].append((record, profile))

    indicators = []
    for indicator in demand.indicators:
        candidates = grouped[indicator.key]
        availability = {profile.availability for _, profile in candidates}
        observations = [record for record, profile in candidates if profile.availability == "observations"]
        bound = sorted({record.series.source_id for record in observations
                        if record.confirmation != "rejected"
                        and (record.confirmation == "confirmed" or record.auto_bind)})
        kinds = []
        if not candidates:
            kinds.append("missing")
        if "observations" not in availability:
            kinds.extend(kind for kind in ("catalog_only", "document_only") if kind in availability)
        if any(record.confirmation == "pending" for record in observations) and not bound:
            kinds.append("pending_confirmation")
        if len(bound) < indicator.min_sources:
            kinds.append("below_min_sources")
        if candidates and all(record.confirmation == "rejected" for record, _ in candidates):
            kinds.append("rejected_only")
        possible = {}
        if kinds:
            for record, profile in candidates:
                if profile.availability in ("catalog_only", "document_only"):
                    possible.setdefault(record.series.source_id, set()).add(profile.availability)
            # These ids are supplied by this demand, including planned, unregistered sources.
            for source in indicator.preferred_sources:
                if source not in registered:
                    possible.setdefault(source, set()).add("preferred_unregistered")
        indicators.append({
            "key": indicator.key, "concept": indicator.concept, "min_sources": indicator.min_sources,
            "bound_sources": bound, "gaps": [{"kind": kind, "reason": REASONS[kind]} for kind in kinds],
            "possible_sources": [{"source_id": source, "reasons": sorted(possible[source])}
                                 for source in sorted(possible)],
        })
    return {"schema": "eltiro.gaps/v1", "demand_id": demand.id, "demand_version": demand.version,
            "generation": generation, "indicators": indicators}


def _destination(data_dir, directory, filename, consumers):
    root = Path(data_dir).resolve()
    parent = (root / "eltiro" / directory).resolve()
    if not parent.is_relative_to(root):
        raise ValueError("report directory escapes data directory")
    for consumer in consumers:
        outbox = (root / consumer.outbox).resolve()
        if not outbox.is_relative_to(root) or (parent / filename).is_relative_to(outbox):
            raise ValueError("report directory overlaps consumer outbox or invalid outbox")
    return parent / filename


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # Same-directory temp file makes replacement atomic on the local filesystem.
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=".gap-", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _json(report):
    return json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def gap_report(
    demand: DemandSpec, generation: int, *, consumer: ConsumerConfig, matches: MatchStore,
    profiles: ProfileStore, registry: Registry, data_dir: str | Path,
) -> dict:
    """Return a JSON-compatible object and atomically publish its per-demand JSON.

    Consumer-facing content contains only this demand's indicator concepts and allowed
    candidates, plus its own unregistered preferences. Invalid context/profile raises
    ValueError; storage/I/O errors propagate, preserving existing files on write failure.
    """
    demand, generation, consumer = _context(demand, generation, consumer)
    path = _destination(data_dir, "gaps", f"{demand.id}-v{demand.version}-g{generation}.json", [consumer])
    report = _report(demand, generation, consumer, matches, profiles, {s.id for s in registry.all()})
    _atomic_write(path, _json(report))
    return report


def aggregate_gaps(
    generation: int, *, demands: DemandRegistry, consumers: Mapping[str, ConsumerConfig],
    matches: MatchStore, profiles: ProfileStore, registry: Registry, data_dir: str | Path,
) -> dict:
    """Aggregate all active demands for the host; write only reports/gaps-gN.{json,md}.

    Group concepts by exact demand text, with no inferred equivalence. Registered sources
    outside authorization are planning leads only, with relevance explicitly unknown.
    Each file is atomic; the JSON/Markdown pair is not a multi-file transaction.
    Missing consumer configuration raises ValueError before any report is published.
    """
    generation = TypeAdapter(Generation).validate_python(generation)
    configs = {key: ConsumerConfig.model_validate(value.model_dump()) for key, value in consumers.items()}
    if any(key != config.id for key, config in configs.items()):
        raise ValueError("consumer configuration key mismatch")
    json_path = _destination(data_dir, "reports", f"gaps-g{generation}.json", configs.values())
    md_path = _destination(data_dir, "reports", f"gaps-g{generation}.md", configs.values())
    registered = {source.id for source in registry.all()}
    by_concept, by_source = {}, {}
    active = sorted(demands.list_active(), key=lambda d: (d.id, d.version))
    for raw in active:
        if raw.consumer not in configs:
            raise ValueError("active demand has no consumer configuration")
        demand, _, consumer = _context(raw, generation, configs[raw.consumer])
        report = _report(demand, generation, consumer, matches, profiles, registered)
        for indicator in report["indicators"]:
            if not indicator["gaps"]:
                continue
            ref = {"consumer": demand.consumer, "demand_id": demand.id, "demand_version": demand.version,
                   "indicator_key": indicator["key"], "min_sources": indicator["min_sources"],
                   "bound_sources": indicator["bound_sources"], "gaps": indicator["gaps"]}
            possible = list(indicator["possible_sources"])
            possible.extend({"source_id": source, "reasons": ["registered_not_allowed"]}
                            for source in sorted(registered - set(consumer.allowed_sources)))
            concept = indicator["concept"]
            by_concept.setdefault(concept, []).append({**ref, "possible_sources": possible})
            for suggestion in possible:
                by_source.setdefault(suggestion["source_id"], []).append({
                    **ref, "concept": concept, "reasons": suggestion["reasons"],
                })
    report = {
        "schema": "eltiro.gaps.aggregate/v1", "generation": generation, "active_demands": len(active),
        "planning_note": "Possible sources are leads, not verified coverage; registered_not_allowed relevance is unknown.",
        "by_concept": [{"concept": concept, "demands": by_concept[concept]} for concept in sorted(by_concept)],
        "by_source": [{"source_id": source, "demands": by_source[source]} for source in sorted(by_source)],
    }
    # JSON fenced content preserves literal untrusted concepts without interpreting markup.
    markdown = "# Eltiro gaps — generation " + str(generation) + "\n\n" + report["planning_note"] + "\n\n"
    markdown += "## By source\n\n" + _markdown_json(report["by_source"])
    markdown += "\n## By indicator concept\n\n" + _markdown_json(report["by_concept"])
    _atomic_write(json_path, _json(report))
    _atomic_write(md_path, markdown)
    return report


def _markdown_json(value: Sequence) -> str:
    # Escaping backticks/HTML also prevents source text from closing a fence or emitting HTML.
    content = _json(value).replace("`", "\\u0060").replace("<", "\\u003c").replace(">", "\\u003e")
    return "```json\n" + content + "```\n"
