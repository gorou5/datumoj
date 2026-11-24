"""Run Eltiro demands on the latest verified R2 generation (read-only); upload only a value-free summary.

Flow: validate arguments -> read-only restore (as cloud_qa) -> inject the named demands from the repository ->
profiles -> deterministic matching (no vector, no model) -> unconfirmed run -> [confirmation list: confirm/reject, run,
package behind the license gate] -> gap report. The summary never contains observation values; the output package is
copied to the output directory only when the license gate passes and every source in its lineage is ``raw_ok``.
Only phases, fixed failure codes and counts go to standard output.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import traceback
from pathlib import Path
from time import monotonic

import yaml
from cloud_qa import Failure, quiet, restore_latest

ROOT = Path(__file__).resolve().parents[1]
DEMANDS_DIR = ROOT / "eval/eltiro/demands"
BINDINGS_DIR = ROOT / "eval/eltiro/bindings"
CONSUMERS = ROOT / "eval/eltiro/consumers-cloud.yaml"
SOURCES_DIR = ROOT / "sources"
DEMAND_ID = re.compile(r"[a-z0-9][a-z0-9-]*")
BINDINGS_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*\.yaml")
BINDING_KEYS = {"demand", "indicator", "source_id", "indicator_code", "dimensions"}
MAX_DEMANDS = 10
MAX_BINDINGS = 256
MAX_BYTES = 256 * 1024
TEXT_CAP = 400
# Only these keys of a run gap leave the machine (symbol, reason code, country code).
GAP_KEYS = ("symbol", "reason", "entity")


def parse_demands(text):
    ids = text.split(",") if isinstance(text, str) else []
    if not 1 <= len(ids) <= MAX_DEMANDS or len(set(ids)) != len(ids) or not all(DEMAND_ID.fullmatch(i) for i in ids):
        raise Failure("invalid_demands")
    return ids


def read_demand(demand_id, demands_dir):
    """The demand file as written in the repository directory; no other path is ever read."""
    path = demands_dir / f"{demand_id}.yaml"
    if (path.is_symlink() or not path.is_file() or path.resolve().parent != demands_dir.resolve()
            or path.stat().st_size > MAX_BYTES):
        raise Failure("demand_not_found")
    return path.read_bytes()


def load_bindings(value, bindings_dir, demand_ids):
    """Confirmation list: a YAML file directly inside the repository bindings directory, checked entry by entry."""
    path = Path(value)
    if (not BINDINGS_NAME.fullmatch(path.name) or ".." in path.name or path.is_symlink() or not path.is_file()
            or path.resolve().parent != bindings_dir.resolve() or path.stat().st_size > MAX_BYTES):
        raise Failure("invalid_bindings_path")
    try:
        content = path.read_bytes()
        if any(isinstance(token, yaml.tokens.AliasToken) for token in yaml.scan(content)):
            raise ValueError("aliases")
        data = yaml.safe_load(content)
    except (OSError, ValueError, UnicodeError, yaml.YAMLError, RecursionError):
        raise Failure("invalid_bindings") from None

    def text(item):
        return isinstance(item, str) and 0 < len(item) <= 200

    entries = data.get("bindings") if isinstance(data, dict) and set(data) == {"bindings"} else None
    if not isinstance(entries, list) or not 0 < len(entries) <= MAX_BINDINGS:
        raise Failure("invalid_bindings")
    seen = set()
    for entry in entries:
        dimensions = entry.get("dimensions") if isinstance(entry, dict) else None
        if (not isinstance(entry, dict) or set(entry) != BINDING_KEYS
                or not all(text(entry[key]) for key in BINDING_KEYS - {"dimensions"})
                or not isinstance(dimensions, dict) or len(dimensions) > 16
                or not all(text(k) and text(v) for k, v in dimensions.items())
                or entry["demand"] not in demand_ids):
            raise Failure("invalid_bindings")
        key = json.dumps(entry, sort_keys=True)
        if key in seen:
            raise Failure("invalid_bindings")
        seen.add(key)
    return entries


def hit(record, entry):
    """A candidate is the listed one when key, source and code are equal and every listed dimension is equal."""
    series = record.series
    return ((record.indicator_key, series.source_id, series.indicator_code)
            == (entry["indicator"], entry["source_id"], entry["indicator_code"])
            and all(series.fixed_dimensions.get(name) == value for name, value in entry["dimensions"].items()))


def cap(value):
    return None if value is None else value[:TEXT_CAP]


def candidate_view(record, profile):
    series = record.series
    scale = profile.scale if profile is not None else None
    return {
        "indicator": record.indicator_key, "source_id": series.source_id, "table_key": series.table_key,
        "indicator_code": series.indicator_code, "fixed_dimensions": dict(sorted(series.fixed_dimensions.items())),
        "availability": profile.availability if profile is not None else None,
        "unit": profile.unit if profile is not None else None,
        "scale": {"multiplier": scale.multiplier if scale else None, "evidence": cap(scale.evidence) if scale else None},
        "tier": record.tier, "confidence": record.confidence, "reason": cap(record.reason),
        "confirmation": record.confirmation, "auto_bind": record.auto_bind,
    }


def run_view(root, record, demand):
    """Row counts per (symbol, source) and gap codes of one run; the values themselves are never read out."""
    import pyarrow.parquet as pq

    run = root / "eltiro/runs" / record["run_id"]
    table = pq.read_table(run / "data.parquet", columns=["symbol", "source", "value"])
    keys = {indicator.key for indicator in demand.indicators}
    counts = {}
    for symbol, source, value in zip(*(table[name].to_pylist() for name in ("symbol", "source", "value")), strict=True):
        item = counts.setdefault((symbol, source), {"symbol": symbol, "kind": "indicator" if symbol in keys else "operation",
                                                    "source": source, "rows": 0, "non_null": 0})
        item["rows"] += 1
        item["non_null"] += value is not None
    gaps = json.loads((run / "gaps.json").read_bytes())
    return {"run_id": record["run_id"], "row_count": record["row_count"],
            "series": [counts[key] for key in sorted(counts, key=lambda k: (k[0], k[1] or ""))],
            "gaps": [{key: gap[key] for key in GAP_KEYS if key in gap} for gap in gaps]}


def lineage(root, run_id, registry):
    from datumoj.eltiro.license_gate import source_level

    citations = json.loads((root / "eltiro/runs" / run_id / "citations.json").read_bytes())
    sources = {i["source_id"] for c in citations for i in c["citation"]["inputs"]}
    return {source: source_level(registry, source).value for source in sorted(sources)}


def upload_decision(levels, row_count):
    """(uploadable, reason, sources): the package leaves only when it has rows and every source is raw_ok."""
    others = sorted(source for source, level in levels.items() if level != "raw_ok")
    if row_count == 0:
        return False, "no_rows", []
    if others:
        return False, "non_raw_ok_source", others
    return True, None, []


def json_text(value):
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)


def markdown(summary):
    # Indented code blocks keep arbitrary source text inert in the report.
    def block(value):
        return "\n".join("    " + line for line in json_text(value).splitlines())

    meta = {key: value for key, value in summary.items() if key != "demands"}
    lines = ["# Eltiro cloud run (no observation values)", "", block(meta), ""]
    for demand in summary["demands"]:
        lines.extend([f"## {demand['demand_id']}", "",
                      block({key: value for key, value in demand.items() if key != "indicators"}), ""])
        for indicator in demand.get("indicators", []):
            lines.extend([f"### {indicator['key']}", "", block(indicator), ""])
    return "\n".join(lines) + "\n"


def execute(root, ids, sources, bindings, output, generation, mark, counts, models_note):
    """Everything after the restore. ``counts`` is the public counter dict; ``mark`` writes a phase line."""
    from datumoj.eltiro.executor import run_registered
    from datumoj.eltiro.gaps import gap_report
    from datumoj.eltiro.intake import IntakeService, load_consumers
    from datumoj.eltiro.license_gate import LicenseGateError
    from datumoj.eltiro.match_store import SQLiteMatchStore
    from datumoj.eltiro.matching import match_demand
    from datumoj.eltiro.package import package_run
    from datumoj.eltiro.profile_store import SQLiteProfileStore
    from datumoj.eltiro.profiles import generate_profiles
    from datumoj.eltiro.registry_store import SQLiteDemandRegistry
    from datumoj.rikolto.core.models import GenerationToken
    from datumoj.rikolto.core.registry import load_registry
    from datumoj.rikolto.wiring import open_generation_views

    registry = load_registry(SOURCES_DIR)
    mark("intake", demands=len(ids))
    (root / "eltiro").mkdir(exist_ok=True)
    shutil.copyfile(CONSUMERS, root / "eltiro/consumers.yaml")
    inbox = root / "consumer/inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    for demand_id in ids:
        (inbox / f"{demand_id}.yaml").write_bytes(sources[demand_id])
    summary = {"schema": "eltiro-cloud-summary/v1", "commit_sha": os.environ.get("GITHUB_SHA", "unknown"),
               "generation": generation, "generation_token": None,
               "mode": "bindings" if bindings else "candidates_only", "models": models_note, "demands": []}
    packages = []
    with SQLiteDemandRegistry(root / "eltiro/demands.sqlite3") as demands:
        service = IntakeService(root, demands, registry)
        service.scan()
        receipts = {}
        for path in sorted((root / "consumer/outbox").glob("*.json")):
            receipt = json.loads(path.read_text(encoding="utf-8"))
            receipts[receipt["file_sha256"]] = receipt
        accepted = {}
        for demand_id in ids:
            receipt = receipts.get(hashlib.sha256(sources[demand_id]).hexdigest())
            if receipt is None:
                raise Failure("intake_failed")
            item = {"demand_id": demand_id, "intake": {"conclusion": receipt["conclusion"],
                                                       "reason_code": receipt["reason_code"]}}
            summary["demands"].append(item)
            if receipt["conclusion"] == "accepted":
                if receipt["demand_id"] != demand_id:
                    raise Failure("demand_mismatch")
                accepted[demand_id] = item
        counts["accepted"] = len(accepted)
        active = {d.id: d for d in demands.list_active() if d.id in accepted}
        if set(active) != set(accepted) or any(entry["demand"] not in accepted for entry in bindings or []):
            raise Failure("bindings_unmatched" if bindings else "intake_failed")

        mark("matching", accepted=len(accepted))
        consumers = {c.id: c for c in load_consumers(root).consumers}
        with (open_generation_views(root) as views, SQLiteProfileStore(root) as profiles,
              SQLiteMatchStore(root) as matches):
            token = views.generation_token
            if GenerationToken.parse(token).generation != generation:
                raise Failure("generation_mismatch")
            summary["generation_token"] = token
            counts["profiles"] = len(generate_profiles(
                generation, observations=views.observations, catalog=views.catalog, chunks=views.chunks,
                registry=registry, store=profiles))
            for demand_id, demand in active.items():
                match_demand(demand, generation, profiles=profiles, consumer=consumers[demand.consumer],
                             observations=views.observations, catalog=views.catalog, chunks=views.chunks,
                             store=matches, vector=False, model=False)
            # Every list entry must name an existing candidate before anything runs or is decided.
            entries = {demand_id: [e for e in bindings or [] if e["demand"] == demand_id] for demand_id in active}
            for demand_id, demand in active.items():
                records = matches.for_demand(demand.id, demand.version, generation)
                for entry in entries[demand_id]:
                    entry["hits"] = sum(hit(record, entry) for record in records)
                    if entry["hits"] == 0:
                        raise Failure("bindings_unmatched")

        mark("run", demands=len(active))

        def execute_run(demand):
            counts["runs"] += 1
            return run_registered(demand.id, generation_token=token, data_root=root, registry=registry, demands=demands)

        for demand_id, demand in active.items():
            accepted[demand_id]["unconfirmed_run"] = run_view(root, execute_run(demand), demand)
        if bindings:
            mark("confirm", demands=sum(bool(e) for e in entries.values()))
            with SQLiteMatchStore(root) as matches:
                for demand_id, demand in active.items():
                    if entries[demand_id]:
                        for record in matches.for_demand(demand.id, demand.version, generation):
                            decision = "confirmed" if any(hit(record, e) for e in entries[demand_id]) else "rejected"
                            matches.set_confirmation(demand.id, demand.version, record.indicator_key, record.series,
                                                     generation, decision)
                            counts["confirmed"] += decision == "confirmed"
            for demand_id, demand in active.items():
                if not entries[demand_id]:
                    continue
                accepted[demand_id]["bindings"] = [{**{k: e[k] for k in sorted(BINDING_KEYS - {"dimensions"})},
                                                    "dimensions": e["dimensions"], "candidates_hit": e["hits"]}
                                                   for e in entries[demand_id]]
                record = execute_run(demand)
                accepted[demand_id]["confirmed_run"] = run_view(root, record, demand)
                mark("package", demands=len(active))
                levels = lineage(root, record["run_id"], registry)
                gate = {"lineage": levels}
                try:
                    delivery = package_run(record["run_id"], data_root=root, registry=registry,
                                           consumers=consumers, demands=demands)
                except LicenseGateError as exc:
                    counts["refused"] += 1
                    gate.update(status="refused", code=exc.code, sources=list(exc.sources))
                else:
                    ok, reason, others = upload_decision(levels, delivery["row_count"])
                    gate.update(status="passed", row_count=delivery["row_count"], file_count=delivery["file_count"],
                                uploaded=ok, not_uploaded_reason=reason, not_uploaded_sources=others)
                    if ok:
                        packages.append((demand_id, Path(delivery["path"])))
                        gate["package"] = f"packages/{demand_id}"
                accepted[demand_id]["license_gate"] = gate
        mark("gaps", demands=len(active))
        with SQLiteProfileStore(root) as profiles, SQLiteMatchStore(root) as matches:
            for demand_id, demand in active.items():
                item = accepted[demand_id]
                item["version"] = demand.version
                records = matches.for_demand(demand.id, demand.version, generation)
                report = gap_report(demand, generation, consumer=consumers[demand.consumer], matches=matches,
                                    profiles=profiles, registry=registry, data_dir=root)
                gaps = {i["key"]: i for i in report["indicators"]}
                item["indicators"] = []
                for indicator in demand.indicators:
                    found = gaps[indicator.key]
                    candidates = [candidate_view(r, profiles.get(generation, r.series)) for r in records
                                  if r.indicator_key == indicator.key]
                    counts["candidates"] += len(candidates)
                    item["indicators"].append({
                        "key": indicator.key, "min_sources": indicator.min_sources,
                        "preferred_sources": list(indicator.preferred_sources), "candidates": candidates,
                        "gap_report": {"bound_sources": found["bound_sources"],
                                       "gaps": [g["kind"] for g in found["gaps"]],
                                       "possible_sources": found["possible_sources"]}})
    mark("artifacts", packages=len(packages))
    output.mkdir(parents=True, exist_ok=True)
    for demand_id, path in packages:
        shutil.copytree(path, output / "packages" / demand_id)
    counts["packages"] = len(packages)
    summary["no_observation_values"] = True
    (output / "summary.json").write_text(json_text(summary) + "\n", encoding="utf-8")
    (output / "summary.md").write_text(markdown(summary), encoding="utf-8")


def where(exc):
    """Exception class chain and the innermost repository frame: no message, no values, no absolute paths."""
    chain, seen = [], exc
    while seen is not None and len(chain) < 4:
        chain.append(type(seen).__name__)
        seen = seen.__cause__ or seen.__context__
    frames = []
    for frame in traceback.extract_tb(exc.__traceback__):
        path = Path(frame.filename).resolve()
        if any(path.is_relative_to(ROOT / part) for part in ("src", "scripts")):
            frames.append(f"{path.relative_to(ROOT)}:{frame.lineno}")
    at = frames[-1] if frames else None
    return {"error_type": "<-".join(chain), "error_at": at}


def run(data_dir, demand_ids, bindings_path, output, *, demands_dir=DEMANDS_DIR, bindings_dir=BINDINGS_DIR):
    started = monotonic()
    counts = {"demands": 0, "accepted": 0, "profiles": 0, "candidates": 0, "confirmed": 0, "runs": 0,
              "refused": 0, "packages": 0}
    result = {"phase": "arguments", "generation": None, "error_code": None, "exit_status": 1}
    with quiet() as out:
        def mark(phase, **numbers):
            # Phase name and numbers only, written when the phase starts.
            result["phase"] = phase
            out(json.dumps({"phase": phase, "elapsed_s": round(monotonic() - started, 1), **numbers}, sort_keys=True))

        try:
            from pull_check import _read_meter

            ids = parse_demands(demand_ids)
            counts["demands"] = len(ids)
            sources = {demand_id: read_demand(demand_id, demands_dir) for demand_id in ids}
            bindings = load_bindings(bindings_path, bindings_dir, ids) if bindings_path else None
            if Path(output).exists() and any(Path(output).iterdir()):
                raise Failure("output_not_empty")  # an old packages/ would ride along in this artifact
            mark("restore")
            with _read_meter():
                result["generation"] = generation = restore_latest(data_dir)
                root = Path(data_dir).resolve()
                note = {"calls": 0, "note": "no model client is created; matching runs with vector=False, model=False"}
                execute(root, ids, sources, bindings, Path(output), generation, mark, counts, note)
            result.update(phase="complete", exit_status=0)
        except Exception as exc:  # noqa: BLE001 -- Only fixed phases, fixed codes and counts cross the public boundary.
            result["exit_status"] = 1
            result["error_code"] = str(exc) if isinstance(exc, Failure) else f"{result['phase']}_failed"
            if not isinstance(exc, Failure):
                result.update(where(exc))
    return {**result, **counts}


def main(argv=None):
    with quiet():
        parser = argparse.ArgumentParser(description=__doc__)
        parser.add_argument("data_dir", type=Path)
        parser.add_argument("output_dir", type=Path)
        parser.add_argument("--demands", required=True)
        parser.add_argument("--bindings")
        args = parser.parse_args(argv)
    summary = run(args.data_dir, args.demands, args.bindings, args.output_dir)
    print(json.dumps(summary, sort_keys=True), flush=True)
    return summary["exit_status"]


if __name__ == "__main__":
    raise SystemExit(main())
