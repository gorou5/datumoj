"""Read-only, bucket-scoped capacity accounting. Output never contains object keys.

Inputs are namespace-relative inventory, validated generation descriptors and the
current manifest.sqlite and optional validated NAS receipt. Retained manifest
references are read through the caller's reader only for orphan raw candidates.
Bytes are decimal bytes; headroom is signed (negative means above the threshold).
Copy statistics count physical objects once, excluding checksum-only descriptors.
Logical stores sharing an object each reference its bytes, so store rows need not
sum to the bucket total. Raw source rows, in contrast, are mutually exclusive.
"""

import json
import math
import os
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from fractions import Fraction
from pathlib import Path

from datumoj.rikolto.core.contracts import SourceSpec, StateObject
from datumoj.rikolto.core.models import StateGeneration
from datumoj.rikolto.core.nas_receipt import NasReceipt
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

# The only publication permitted in the stop tier (besides lease writes and deletes):
# a mark-only generation. It uploads changed manifest.sqlite blocks, writes one
# generation descriptor and switches the current pointer; never raw, databases or indexes.
MARK_ONLY_WRITES = ("manifest_copy", "generation_descriptor", "current_pointer")
MARK_ONLY_RESERVE = 1_000_000  # Conservative bound for the descriptor and pointer objects.


def mark_only_estimate(manifest_bytes: int) -> int:
    """Upper bound of bytes written: every manifest block is new, plus the reserve."""
    if type(manifest_bytes) is not int or manifest_bytes < 0:
        raise ValueError("invalid_capacity_size")
    return manifest_bytes + MARK_ONLY_RESERVE


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
              other_objects: tuple[StateObject, ...] | list[StateObject] = (),
              receipt: NasReceipt | None = None,
              retained_refs: Callable[[StateGeneration], set[str]] | None = None) -> dict:
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

    plan = plan_cleanup(objects, generations, current, manifest, specs, now=now,
                        receipt=receipt, retained_refs=retained_refs)
    return {
        "scope": "this_bucket",
        "total": total,
        "headroom_bytes": {"7gb": LIMITS.report - total["bytes"],
                           "8_5gb": LIMITS.freeze - total["bytes"], "9gb": LIMITS.stop - total["bytes"]},
        "prefixes": {name: _counts(group) for name, group in groups.items()},
        "current_generation": next((row for row in per_generation if row["generation"] == current), None),
        "generations": per_generation,
        "raw_sources": raw_source_counts(groups["raw"], manifest),
        **({"unrecognized_raw_keys": len(plan["unrecognized_raw"])} if plan["unrecognized_raw"] else {}),
        "reclaimable_by_source": raw_source_counts([*plan["raw"], *plan["orphan_raw"]], manifest),
        "stores": {name: {"bytes": sum(by_key[key].size_bytes for key in keys if key in by_key)}
                   for name, keys in sorted(stores.items())},
        "reclaimable": {**{kind: _counts(plan[kind]) for kind in ("generations", "copies", "raw")},
                        "raw_enabled": raw_enabled},
    }


# ---- 容量摘要（capacity.jsonl）：每轮一行，随代次发布；周报“容量”一节只从它计算 ----
CAPACITY_LOG = "capacity.jsonl"
CAPACITY_KEEP = timedelta(days=16)  # 覆盖上一完整周
REASON_INSUFFICIENT = "capacity_rows_insufficient"
REASON_UNAVAILABLE = "capacity_log_unavailable"
TOP_SOURCES = 10
_DB_STORES = {"manifest.sqlite", "chunks.sqlite", "keyword.sqlite", "catalog.sqlite", "changes.sqlite",
              "observations.duckdb"}


def capacity_row(listing, copies, manifest: Path, now: datetime) -> dict:
    """Build one summary row from the round's single listing; no extra listing or data GET."""
    scoped, other = listing
    raw = [obj for obj in scoped if obj.key.split("/", 1)[0] == "raw" and "/" in obj.key]
    by_source = ({name: row["bytes"] for name, row in raw_source_counts(raw, manifest).items() if row["bytes"]}
                 if raw else {})
    db = {copy.key: copy.size_bytes for copy in copies
          if copy.format != "sqlite-blocks-v1" and _store_name(copy.name) in _DB_STORES}
    return {"recorded_at": now.astimezone(UTC).isoformat(),
            "total_bytes": sum(obj.size_bytes for obj in (*scoped, *other)),
            "raw_bytes_by_source": by_source, "db_store_bytes": sum(db.values())}


def _count(value) -> bool:
    return type(value) is int and value >= 0


def _at(row: dict) -> datetime:
    return datetime.fromisoformat(row["recorded_at"])


def _valid_row(row) -> bool:
    try:
        raw = row["raw_bytes_by_source"]
        return (_at(row).utcoffset() is not None
                and _count(row["total_bytes"]) and _count(row["db_store_bytes"])
                and isinstance(raw, dict) and all(isinstance(k, str) and _count(v) for k, v in raw.items()))
    except (KeyError, TypeError, ValueError):
        return False


def parse_capacity_rows(data: bytes) -> list[dict]:
    """Skip blank, undecodable, malformed or invalid lines instead of failing."""
    rows = []
    for line in data.split(b"\n"):
        try:
            row = json.loads(line.decode("utf-8"))
        except ValueError:  # includes UnicodeDecodeError and JSONDecodeError
            continue
        if _valid_row(row):
            rows.append(row)
    return rows


def read_capacity_rows(root: Path) -> list[dict]:
    """Raises OSError when the file is missing or unreadable."""
    return parse_capacity_rows((Path(root) / CAPACITY_LOG).read_bytes())


def append_capacity_row(root: Path, row: dict) -> None:
    """Append one row, keep the last 16 days, and replace the file atomically."""
    path = Path(root) / CAPACITY_LOG
    try:
        old = read_capacity_rows(root)
    except FileNotFoundError:
        old = []
    cutoff = _at(row) - CAPACITY_KEEP
    kept = [r for r in old if _at(r) >= cutoff] + [row]
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text("".join(json.dumps(r, sort_keys=True, separators=(",", ":")) + "\n" for r in kept),
                   encoding="utf-8")
    os.replace(tmp, path)


def summarize_capacity(rows: list[dict], start: datetime, end: datetime) -> dict:
    """Pure: rows inside [start, end] -> exact numbers (Fractions, no floats)."""
    window = sorted((r for r in rows if start <= _at(r) <= end), key=_at)
    if len(window) < 2 or _at(window[-1]) == _at(window[0]):
        return {"status": "insufficient", "rows": len(window), "reason": REASON_INSUFFICIENT}
    first, last = window[0], window[-1]
    days = Fraction((_at(last) - _at(first)) // timedelta(microseconds=1), 86_400_000_000)
    old, new = first["raw_bytes_by_source"], last["raw_bytes_by_source"]
    sources = sorted(({"source": n, "bytes": new.get(n, 0), "growth": new.get(n, 0) - old.get(n, 0)}
                      for n in set(old) | set(new)), key=lambda s: (-s["growth"], s["source"]))
    rest = sources[TOP_SOURCES:]
    total = last["total_bytes"]
    growth = total - first["total_bytes"]
    if total >= LIMITS.freeze:
        prediction = {"kind": "already_over"}
    elif growth <= 0:
        prediction = {"kind": "never"}
    else:
        prediction = {"kind": "days", "days": math.ceil((LIMITS.freeze - total) / (Fraction(growth) / days))}
    return {"status": "ok", "rows": len(window), "covered_days": days, "total_bytes": total,
            "headroom_bytes": {"7": LIMITS.report - total, "8.5": LIMITS.freeze - total,
                               "9": LIMITS.stop - total},
            "sources": sources[:TOP_SOURCES],
            "other": {"count": len(rest), "bytes": sum(s["bytes"] for s in rest),
                      "growth": sum(s["growth"] for s in rest)} if rest else None,
            "db_bytes": last["db_store_bytes"], "db_growth": last["db_store_bytes"] - first["db_store_bytes"],
            "growth_bytes": growth, "prediction": prediction}
