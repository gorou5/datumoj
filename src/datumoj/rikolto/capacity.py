"""Read-only, bucket-scoped capacity accounting. Output never contains object keys.

Inputs are namespace-relative inventory, validated generation descriptors and the
current manifest.sqlite. Only that SQLite file is read; no storage API is called.
Bytes are decimal bytes; headroom is signed (negative means above the threshold).
Copy statistics count physical objects once, excluding checksum-only descriptors.
Logical stores sharing an object each reference its bytes, so store rows need not
sum to the bucket total. Raw source rows, in contrast, are mutually exclusive.
"""

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from datumoj.rikolto.core.contracts import SourceSpec, StateObject
from datumoj.rikolto.core.models import StateGeneration
from datumoj.rikolto.core.state import plan_cleanup, raw_source_counts


@dataclass(frozen=True)
class CapacityLimits:
    report: int = 7_000_000_000
    freeze: int = 8_500_000_000
    stop: int = 9_000_000_000
    crawl_reserve: int = 500_000_000

    def __post_init__(self):
        if (any(type(value) is not int or value <= 0 for value in
                (self.report, self.freeze, self.stop, self.crawl_reserve))
                or not self.report < self.freeze < self.stop):
            raise ValueError("invalid_capacity_limits")


WEEKLY_REPORT_RESERVE = 1_000_000  # Conservative upper bound for one Markdown object.

LIMITS = CapacityLimits()  # Validate once at import; all write gates use these limits.


def gate(current_bytes: int | None, estimated_new_bytes: int | None, category: str, *,
         reserve_bytes: int = 0) -> dict:
    """Decide using total + growth + reserve. Unknown sizes fail closed except reclamation.

    Categories and reasons are fixed codes. No storage identifiers or source text
    enter this result. The crawl caller supplies LIMITS.crawl_reserve exactly once.
    """
    if category not in {"essential", "optional", "reclaim"}:
        raise ValueError("invalid_write_category")
    sizes = (current_bytes, estimated_new_bytes, reserve_bytes)
    unknown = any(type(value) is not int or value < 0 for value in sizes)
    projected = None if unknown else sum(sizes)
    tier = ("stop" if unknown or projected >= LIMITS.stop else
            "freeze" if projected >= LIMITS.freeze else
            "report" if projected >= LIMITS.report else "normal")
    allowed = category == "reclaim" or (not unknown and tier != "stop"
                                        and (tier != "freeze" or category == "essential"))
    return {"current_bytes": current_bytes if type(current_bytes) is int and current_bytes >= 0 else None,
            "estimated_new_bytes": estimated_new_bytes if type(estimated_new_bytes) is int
            and estimated_new_bytes >= 0 else None,
            "reserve_bytes": reserve_bytes if type(reserve_bytes) is int and reserve_bytes >= 0 else None,
            "projected_bytes": projected, "tier": tier, "allowed": allowed,
            "reason": "capacity_unknown" if unknown else f"capacity_{tier}", "skipped_steps": []}


def _counts(objects):
    return {"objects": len(objects), "bytes": sum(obj.size_bytes for obj in objects)}


def _store_name(name):
    name = name.split(".blocks/", 1)[0]
    if name.startswith("vectors/"):
        return "vectors"
    if name.startswith("parquet/"):
        return "parquet"
    if name in {"manifest.sqlite", "chunks.sqlite", "keyword.sqlite", "catalog.sqlite", "changes.sqlite",
                "observations.duckdb", "runs.jsonl", "control.json", "vectors-state.json"}:
        return name
    return "other"


def inventory(objects: list[StateObject], generations: list[StateGeneration], current: int,
              manifest: Path, specs: list[SourceSpec], *, now: datetime, raw_enabled: bool = False,
              other_objects: tuple[StateObject, ...] | list[StateObject] = ()) -> dict:
    """Account only for this bucket; outside-namespace objects remain unclassified."""
    by_key = {obj.key: obj for obj in objects}
    groups = {name: [] for name in ("generations", "copies", "raw", "other")}
    for obj in objects:
        prefix = obj.key.split("/", 1)[0] if "/" in obj.key else "other"
        groups[prefix if prefix in groups else "other"].append(obj)
    groups["other"].extend(other_objects)
    total = _counts([*objects, *other_objects])

    references = defaultdict(set)
    stores = defaultdict(set)
    for generation in generations:
        for copy in generation.stores:
            if copy.format == "sqlite-blocks-v1":
                continue  # This entry checks the assembled file; it is not an object.
            references[copy.key].add(generation.generation)
            stores[_store_name(copy.name)].add(copy.key)
    per_generation = []
    for generation in sorted(generations, key=lambda g: g.generation):
        keys = {copy.key for copy in generation.stores if copy.format != "sqlite-blocks-v1"}
        per_generation.append({
            "generation": generation.generation,
            "referenced_bytes": sum(by_key[key].size_bytes for key in keys if key in by_key),
            "exclusive_bytes": sum(by_key[key].size_bytes for key in keys
                                   if key in by_key and len(references[key]) == 1),
        })

    plan = plan_cleanup(objects, generations, current, manifest, specs, now=now)
    return {
        "scope": "this_bucket",
        "total": total,
        "headroom_bytes": {"7gb": LIMITS.report - total["bytes"],
                           "8_5gb": LIMITS.freeze - total["bytes"], "9gb": LIMITS.stop - total["bytes"]},
        "prefixes": {name: _counts(group) for name, group in groups.items()},
        "current_generation": next((row for row in per_generation if row["generation"] == current), None),
        "generations": per_generation,
        "raw_sources": raw_source_counts(groups["raw"], manifest),
        "reclaimable_by_source": raw_source_counts([*plan["raw"], *plan["orphan_raw"]], manifest),
        "stores": {name: {"bytes": sum(by_key[key].size_bytes for key in keys if key in by_key)}
                   for name, keys in sorted(stores.items())},
        "reclaimable": {**{kind: _counts(plan[kind]) for kind in ("generations", "copies", "raw")},
                        "raw_enabled": raw_enabled},
    }
