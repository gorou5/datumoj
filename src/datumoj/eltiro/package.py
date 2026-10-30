"""Offline output bundles: checked lineage, exact decimals, atomic publication.

Host supplies Registry, ConsumersConfig (or id mapping) and DemandRegistry.
No network, model calls or Rikolto implementation imports. Invalid local state
raises ValueError/OSError; license denials raise LicenseGateError with safe codes.
"""

from __future__ import annotations

import csv
import fcntl
import io
import json
import os
import re
import shutil
import stat
from datetime import date, datetime
from decimal import Decimal
from hashlib import sha256
from pathlib import Path
from uuid import uuid4

import pyarrow as pa
import pyarrow.parquet as pq

from datumoj.eltiro.license_gate import (
    IDENTIFIERS,
    NUMBERS,
    RAW_TEXT,
    UNITS,
    LicenseGateError,
    check_rows,
    source_level,
)
from datumoj.eltiro.models import ConsumerConfig, DemandSpec, LicenseSummary, OutputManifest
from datumoj.eltiro.runs import RunStore
from datumoj.rikolto.core.models import Outflow

_RUN = re.compile(r"run-[0-9a-f]{32}\Z")
# Gap text is not safe to copy: emit only known structural reason codes.
_RUN_REASONS = frozenset({
    "no_observations", "frequency_mismatch", "table_unavailable", "unknown_area_scheme",
    "area_unmapped", "calendar_mismatch", "missing_input", "no_matching_observations",
    "ambiguous_source_alignment", "alignment_missing", "pending_confirmation", "unbound_indicator",
    "entity_unavailable", "insufficient_sources", "no_aligned_inputs", "unit_mismatch",
    "scale_unconfirmed", "zero_denominator", "window_incomplete", "missing_baseline",
    "incomplete_window", "scale_mismatch", "unconfirmed_scale", "zero_variance",
})
_REPORT_REASONS = frozenset({"missing", "catalog_only", "document_only", "pending_confirmation",
                             "below_min_sources", "rejected_only"})
_LEGACY_KEY = ("symbol", "country", "region_scheme", "region", "period", "source", "release_id")
_KEY = (*_LEGACY_KEY, "series_sha256")


def _json(value):
    def encode(item):
        if isinstance(item, Decimal):
            return format(item, "f")
        if isinstance(item, (date, datetime)):
            return item.isoformat()
        raise TypeError("unsupported output type")
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, default=encode) + "\n").encode()


def _open_dir(root, path):
    """Read-only walk: never creates directories or follows directory symlinks."""
    descriptor = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in path.relative_to(root).parts:
            if part in (".", ".."):
                raise ValueError("invalid path")
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def _read(directory, name):
    descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
    with os.fdopen(descriptor, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise ValueError("invalid package input")
        return stream.read()


def _write(directory, name, content):
    descriptor = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(content)
        stream.flush()
        os.fsync(stream.fileno())


def _audit(root, run_id, content):
    directory = _open_dir(root, root / "eltiro/runs")
    temporary = ".audit-" + uuid4().hex
    try:
        _write(directory, temporary, _json(content))
        os.replace(temporary, run_id + ".package-audit.json", src_dir_fd=directory, dst_dir_fd=directory)
        os.fsync(directory)
    finally:
        try:
            os.unlink(temporary, dir_fd=directory)
        except FileNotFoundError:
            pass
        os.close(directory)


def _configs(root, consumers):
    if not hasattr(consumers, "consumers") and any(key != c.id for key, c in consumers.items()):
        raise ValueError("consumer configuration key mismatch")
    items = consumers.consumers if hasattr(consumers, "consumers") else consumers.values()
    configs = [ConsumerConfig.model_validate(c.model_dump()) for c in items]
    if len({c.id for c in configs}) != len(configs):
        raise ValueError("duplicate consumers")
    paths = []
    for config in configs:
        for value in (config.inbox, config.outbox):
            path = root / value
            if (not path.is_relative_to(root) or path == root or path != path.resolve()
                    or path.is_relative_to(root / "eltiro") or (root / "eltiro").is_relative_to(path)):
                raise ValueError("unsafe consumer directory")
            if any(path.is_relative_to(other) or other.is_relative_to(path) for other in paths):
                raise ValueError("overlapping consumer directories")
            paths.append(path)
    return {c.id: c for c in configs}


def _gaps(root, run_gaps, demand, record, consumer, registry):
    symbols = {i.key for i in demand.indicators} | {o.id for o in demand.operations}
    run = [{"symbol": g["symbol"], "reason": g["reason"]} for g in run_gaps
           if g.get("symbol") in symbols and g.get("reason") in _RUN_REASONS]
    report = []
    try:
        directory = _open_dir(root, root / "eltiro/gaps")
    except FileNotFoundError:
        directory = None
    if directory is not None:
        try:
            name = f'{demand.id}-v{demand.version}-g{record["generation"]}.json'
            try:
                content = json.loads(_read(directory, name))
            except FileNotFoundError:
                content = None
        finally:
            os.close(directory)
        if content is not None:
            if (content.get("schema"), content.get("demand_id"), content.get("demand_version"), content.get("generation")) != (
                    "eltiro.gaps/v1", demand.id, demand.version, record["generation"]):
                raise ValueError("gap report identity mismatch")
            allowed = {s for s in consumer.allowed_sources if source_level(registry, s) != Outflow.no_outflow}
            for indicator in content["indicators"]:
                if indicator.get("key") not in {i.key for i in demand.indicators}:
                    continue
                report.append({
                    "key": indicator["key"],
                    "gaps": sorted({g["kind"] for g in indicator["gaps"] if g["kind"] in _REPORT_REASONS}),
                    "bound_sources": sorted(set(indicator["bound_sources"]) & allowed),
                    "possible_sources": sorted({s["source_id"] for s in indicator["possible_sources"]
                                                if s["source_id"] in allowed}),
                })
    return {"demand_id": demand.id, "demand_version": demand.version, "generation": record["generation"],
            "run": run, "report": report}


def _previous(root, consumer, demand):
    base = root / consumer.outbox / demand.id
    if not base.exists():
        return None
    descriptor = _open_dir(root, base)
    candidates = []
    try:
        for version in os.listdir(descriptor):
            if not re.fullmatch(r"v[1-9][0-9]*", version):
                continue
            folder = _open_dir(root, base / version)
            try:
                names = os.listdir(folder)
            finally:
                os.close(folder)
            for name in names:
                if not _RUN.fullmatch(name):
                    continue
                child = _open_dir(root, base / version / name)
                try:
                    delivery = json.loads(_read(child, "delivery.json"))
                    manifest = OutputManifest.model_validate_json(_read(child, "manifest.json"))
                    if (delivery["consumer"], manifest.demand_id, delivery["run_id"]) != (consumer.id, demand.id, name):
                        raise ValueError("previous delivery identity mismatch")
                    rows = json.loads(_read(child, "provenance.json"))
                    rows = [{**row, **{n: None if row.get(n) is None else Decimal(row[n]) for n in NUMBERS}}
                            for row in rows]
                    candidates.append((delivery["sequence"], name, rows, manifest))
                finally:
                    os.close(child)
    finally:
        os.close(descriptor)
    return max(candidates, key=lambda item: item[0]) if candidates else None


def _difference(rows, previous):
    def grouped(items):
        result = {}
        for row in items:
            key = tuple(str(row.get(k) or "") for k in _LEGACY_KEY)
            result.setdefault(key, []).append(row)
        return result
    old = grouped(previous[2]) if previous else {}
    new = grouped(rows)
    def entry(row):
        return {key: row.get(key) for key in ("citation_id", *_KEY, *NUMBERS)}
    def keyed(items):
        result = {}
        for row in items:
            digest = row["series_sha256"]
            if digest in result:
                raise ValueError("ambiguous difference identity")
            result[digest] = row
        return result
    added, deleted, changes, incomparable = [], [], [], []
    for key in sorted(old.keys() | new.keys()):
        before_rows, after_rows = old.get(key, []), new.get(key, [])
        after = keyed(after_rows)
        if any(row.get("series_sha256") is None for row in before_rows):
            # 805 had no series identity: only a one-to-one legacy key is safe.
            if len(before_rows) > 1 or len(after_rows) > 1:
                incomparable.append({**{k: before_rows[0].get(k) for k in _LEGACY_KEY},
                                     "reason": "previous_delivery_not_comparable_by_series",
                                     "message": "上次交付无法逐序列比较"})
                continue
            before = {"legacy": before_rows[0]}
            after = {"legacy": after_rows[0]} if after_rows else {}
        else:
            before = keyed(before_rows)
        added.extend(entry(after[k]) for k in sorted(after.keys() - before.keys()))
        deleted.extend(entry(before[k]) for k in sorted(before.keys() - after.keys()))
        for digest in sorted(before.keys() & after.keys()):
            first, last = before[digest], after[digest]
            if any((None if first.get(n) is None else Decimal(str(first[n]))) != last.get(n) for n in NUMBERS):
                changes.append({"before": entry(first), "after": entry(last)})
    return {"previous_run_id": previous[1] if previous else None,
            "comparable": previous is not None and not incomparable,
            "added": added, "deleted": deleted, "changed": changes, "incomparable": incomparable}


def _data_table(rows, source_table, demand):
    symbols = {i.key for i in demand.indicators} | {op.id for op in demand.operations}
    fields = []
    for name in dict.fromkeys(demand.output.fields):
        if name in RAW_TEXT and not any(name in row for row in rows):
            continue
        if name in symbols:
            fields.append(pa.field(name, source_table.schema.field("value").type))
        elif name == "year":
            fields.append(pa.field(name, pa.int64()))
        elif name in IDENTIFIERS + NUMBERS + UNITS + RAW_TEXT:
            fields.append(source_table.schema.field(name))
        else:
            raise ValueError("unsupported output field")
    projected = []
    for row in rows:
        projected.append({field.name: (row["value"] if field.name == row["symbol"] else
                                      None if field.name in symbols else
                                      row["period"].year if field.name == "year" else row.get(field.name))
                          for field in fields})
    return pa.Table.from_pylist(projected, schema=pa.schema(fields))


def _publish(root, parent, run_id, contents):
    # Stage privately beside the run directories, outside every consumer outbox.
    runs = root / "eltiro/runs"
    staging = ".package-" + uuid4().hex
    descriptor = _open_dir(root, runs)
    stage_fd = None
    destination = None
    created = []
    published = False
    try:
        os.mkdir(staging, mode=0o700, dir_fd=descriptor)
        stage_fd = os.open(staging, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
        for name, content in sorted(contents.items()):
            _write(stage_fd, name, content)
        os.fsync(stage_fd)
        # Record exactly which empty ancestors this call creates, for rollback.
        destination = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        current = root
        for part in parent.relative_to(root).parts:
            current = current / part
            try:
                os.mkdir(part, mode=0o700, dir_fd=destination)
                created.append(current)
            except FileExistsError:
                pass
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=destination)
            os.fsync(destination)
            os.close(destination)
            destination = child
        if run_id in os.listdir(destination):
            raise ValueError("delivery already exists")
        os.rename(staging, run_id, src_dir_fd=descriptor, dst_dir_fd=destination)
        published = True
        os.fsync(destination)
    except BaseException:
        if published:
            shutil.rmtree(run_id, dir_fd=destination)
        raise
    finally:
        if destination is not None:
            os.close(destination)
        if stage_fd is not None:
            os.close(stage_fd)
        shutil.rmtree(runs / staging, ignore_errors=True)
        for directory in reversed(created):
            try:
                directory.rmdir()  # Never remove populated/pre-existing directories.
            except OSError:
                pass
        os.close(descriptor)


def package_run(run_id, *, data_root, registry, consumers, demands):
    """Return {path: Path, row_count: int, file_count: int, gap_count: int}.

    Idempotent delivery is checked only AFTER current authorization is revalidated.
    A host-local lock serializes predecessor selection and publication. Run and
    report files are immutable host state; inbox publishers must not control them.
    """
    root = Path(data_root).absolute()
    if root != root.resolve() or not _RUN.fullmatch(run_id):
        raise ValueError("unsafe package context")
    configs = _configs(root, consumers)
    record = RunStore(root).show(run_id)
    if record.get("run_id") != run_id:
        raise ValueError("run identity mismatch")
    demand = demands.get(record["demand_id"], record["demand_version"])
    if demand is None:
        raise ValueError("unknown demand")
    demand = DemandSpec.model_validate(demand.model_dump(by_alias=True))
    if (demand.id, demand.version) != (record["demand_id"], record["demand_version"]):
        raise ValueError("demand identity mismatch")
    consumer = configs[demand.consumer]
    directory = _open_dir(root, root / "eltiro/runs" / run_id)
    try:
        table = pq.read_table(pa.BufferReader(_read(directory, "data.parquet")))
        rows = [r for r in table.to_pylist() if r["symbol"] in demand.output.fields]
        citations = json.loads(_read(directory, "citations.json"))
        gaps = json.loads(_read(directory, "gaps.json"))
    finally:
        os.close(directory)
    # Hash the exact UTF-8 series cell before restricted source text is removed.
    digests = [sha256((row.get("series") or "").encode("utf-8")).hexdigest()[:16] for row in rows]
    sanitizations = []
    try:
        rows, index, summary = check_rows(rows, citations, registry=registry, demand=demand, consumer=consumer,
                                          audit=sanitizations)
    except LicenseGateError as exc:
        _audit(root, run_id, {"status": "blocked", "code": exc.code, "sources": exc.sources})
        raise
    # Executor ids contain hyphens; OutputManifest keys are Symbol identifiers.
    # Assign stable bundle-local references without changing the input run format.
    index = {f"row_{n}": index[row["citation_id"]] for n, row in enumerate(rows)}
    rows = [{**row, "citation_id": f"row_{n}", "series_sha256": digests[n]} for n, row in enumerate(rows)]
    path = root / consumer.outbox / demand.id / f"v{demand.version}" / run_id
    lock_dir = _open_dir(root, root / "eltiro/runs")
    lock = None
    try:
        lock = os.open(".package.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600, dir_fd=lock_dir)
        lock_info = os.fstat(lock)
        if not stat.S_ISREG(lock_info.st_mode) or lock_info.st_nlink != 1:
            raise ValueError("invalid package lock")
        fcntl.flock(lock, fcntl.LOCK_EX)
        if path.exists() or path.is_symlink():
            child = _open_dir(root, path)
            try:
                delivery = json.loads(_read(child, "delivery.json"))
                if (delivery["consumer"], delivery["run_id"]) != (consumer.id, run_id):
                    raise ValueError("existing delivery identity mismatch")
                manifest = OutputManifest.model_validate_json(_read(child, "manifest.json"))
                if (manifest.demand_id, manifest.demand_version) != (demand.id, demand.version):
                    raise ValueError("existing manifest identity mismatch")
                files = {name: _read(child, name) for name in os.listdir(child)}
                if any(sha256(files[item.name]).hexdigest() != item.sha256 for item in manifest.files):
                    raise ValueError("existing delivery hash mismatch")
                existing = [{"citation_id": k, "citation": v.model_dump(mode="json")}
                            for k, v in manifest.citation_index.items()]
                _, _, current = check_rows([{"citation_id": c["citation_id"]} for c in existing], existing,
                                            registry=registry, demand=demand, consumer=consumer)
                changed = {source for source, level in current.by_source.items()
                           if manifest.license_summary.by_source.get(source) != level}
                if changed:
                    raise LicenseGateError("license_changed", changed)
                _audit(root, run_id, {"status": "allowed", "license_summary": current.model_dump(mode="json"),
                                     "sanitizations": sanitizations})
                return {"path": path, **delivery["counts"]}
            finally:
                os.close(child)
        previous = _previous(root, consumer, demand)
        difference = _difference(rows, previous)
        if previous:
            # Deleted/changed values in diff also have lineage and current licenses.
            previous_citations = [{"citation_id": k, "citation": v.model_dump(mode="json")}
                                  for k, v in previous[3].citation_index.items()]
            _, _, old_summary = check_rows(previous[2], previous_citations, registry=registry, demand=demand, consumer=consumer)
            old_entries = difference["deleted"] + [item["before"] for item in difference["changed"]]
            old_ids = {row["citation_id"] for row in old_entries}
            old_sources = {obs.source_id for key in old_ids for obs in previous[3].citation_index[key].inputs}
            levels = {**summary.by_source, **{s: old_summary.by_source[s] for s in old_sources}}
            rank = {Outflow.raw_ok: 0, Outflow.derived_only: 1, Outflow.no_outflow: 2}
            summary = LicenseSummary(outflow=max(levels.values(), key=rank.__getitem__) if levels else Outflow.no_outflow,
                                     by_source=levels)
            index.update({"previous_" + key: previous[3].citation_index[key] for key in sorted(old_ids)})
            for entry in old_entries:
                entry["citation_id"] = "previous_" + entry["citation_id"]
        merged_gaps = _gaps(root, gaps, demand, record, consumer, registry)
        contents = {}
        output = _data_table(rows, table, demand)
        if "parquet" in demand.output.format:
            sink = pa.BufferOutputStream()
            pq.write_table(output, sink)
            contents["data.parquet"] = sink.getvalue().to_pybytes()
        if "csv" in demand.output.format:
            stream = io.StringIO(newline="")
            writer = csv.DictWriter(stream, output.column_names, lineterminator="\n")
            writer.writeheader()
            for row in output.to_pylist():
                writer.writerow({k: format(v, "f") if isinstance(v, Decimal) else v for k, v in row.items()})
            contents["data.csv"] = stream.getvalue().encode()
        manifest = OutputManifest(
            demand_id=demand.id, demand_version=demand.version, generation=record["generation"],
            generation_token=record["generation_token"], run_at=record["started_at"], models=record.get("models", []),
            license_summary=summary, citation_index=index,
            files=[{"name": name, "sha256": sha256(content).hexdigest()} for name, content in sorted(contents.items())],
        )
        # The provenance sidecar provides row alignment even for CSV-only bundles,
        # and stores only identifiers/numbers; source text never enters diffs.
        contents["provenance.json"] = _json([{k: v for k, v in row.items()
                                             if k in IDENTIFIERS + NUMBERS + ("series_sha256",)} for row in rows])
        contents["manifest.json"] = _json(manifest.model_dump(mode="json"))
        contents["gaps.json"] = _json(merged_gaps)
        contents["diff.json"] = _json(difference)
        counts = {"row_count": len(rows), "file_count": len(contents) + 1,
                  "gap_count": len(merged_gaps["run"]) + sum(bool(i["gaps"]) for i in merged_gaps["report"])}
        contents["delivery.json"] = _json({"consumer": consumer.id, "run_id": run_id,
                                            "sequence": previous[0] + 1 if previous else 1, "counts": counts})
        _audit(root, run_id, {"status": "allowed", "license_summary": summary.model_dump(mode="json"),
                             "sanitizations": sanitizations})
        _publish(root, path.parent, run_id, contents)
        return {"path": path, **counts}
    except LicenseGateError as exc:
        _audit(root, run_id, {"status": "blocked", "code": exc.code, "sources": exc.sources})
        raise
    finally:
        if lock is not None:
            os.close(lock)
        os.close(lock_dir)
