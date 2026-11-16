"""Run an unscored question list against a verified generation; log counts only."""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
from contextlib import ExitStack, contextmanager, redirect_stderr, redirect_stdout
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Lock
from time import monotonic

ALLOWED_TOOLS = {"search_docs", "search_catalog", "query_sql", "cite", "list_changes", "coverage", "version"}
START = datetime(2026, 11, 10, tzinfo=UTC)
END = datetime(2026, 11, 15, tzinfo=UTC)
PAUSE_START = datetime(2026, 11, 11, 14, tzinfo=UTC)
PAUSE_END = datetime(2026, 11, 13, 19, 4, tzinfo=UTC)
RAW_KEY = re.compile(r"(?:^|/)raw/[0-9a-f]{2}/[0-9a-f]{64}\Z")


class Failure(Exception):
    """A failure whose message is a fixed code that may appear in the public log."""


@contextmanager
def quiet():
    """Hide Python and native SDK diagnostics, including import/close failures.

    Yields a writer for the real stdout. It is unbuffered, so a cancelled run still shows its last phase.
    """
    disabled = logging.root.manager.disable
    with open(os.devnull, "w") as sink, ExitStack() as stack:
        logging.disable(logging.CRITICAL)
        try:
            # LanceDB can emit native diagnostics directly to these descriptors.
            for fd in (1, 2):
                saved = os.dup(fd)
                if fd == 1:
                    out_fd = saved
                stack.callback(os.close, saved)
                stack.callback(os.dup2, saved, fd)
                os.dup2(sink.fileno(), fd)
            with redirect_stdout(sink), redirect_stderr(sink):
                yield lambda line: os.write(out_fd, line.encode() + b"\n")
        finally:
            logging.disable(disabled)


@contextmanager
def raw_meter():
    """Count raw objects (and bytes) read by GetObject. Enter before any S3 client is built."""
    import boto3

    events = boto3.DEFAULT_SESSION.events
    meter = {"objects": 0, "bytes": 0}
    lock = Lock()

    def tag(params, context, **kwargs):
        if RAW_KEY.search(params.get("Key", "")):
            context["datumoj_raw"] = True

    def count(http_response, parsed, context, **kwargs):
        # after-call also fires for error responses; count only successful reads.
        if not context.get("datumoj_raw") or not 200 <= getattr(http_response, "status_code", 0) < 300:
            return
        size = parsed.get("ContentLength")
        if type(size) is int and size >= 0:
            with lock:
                meter["objects"] += 1
                meter["bytes"] += size

    events.register("before-parameter-build.s3.GetObject", tag)
    events.register("after-call.s3.GetObject", count)
    try:
        yield meter
    finally:
        events.unregister("before-parameter-build.s3.GetObject", tag)
        events.unregister("after-call.s3.GetObject", count)


def restore_latest(root):
    """Restore the newest valid generation. Raw objects are only checked for existence, never downloaded."""
    from pull_check import _count

    from datumoj.rikolto import wiring
    from datumoj.rikolto.core.pull import PULL_RECORD
    from datumoj.rikolto.store.observations import retain_generation

    manager = wiring.build_state(root)
    try:
        report = manager.restore(root, raw="remote")
        generation = _count(report["generation"])
        if generation == 0 or report["verified"] is not True:
            raise Failure("unverified_generation")
        # Same pointer recheck as pull(): the restored generation must still be the latest valid one.
        history = manager.list()
        if not history or history[0].generation != generation:
            raise Failure("generation_changed")
        selected = history[0]
        record = {
            "generation": generation, "created_at": selected.created_at.isoformat(),
            "pulled_at": datetime.now(UTC).isoformat(), "writer": selected.writer.model_dump(mode="json"),
            "last_successful_runs": {key: run.model_dump(mode="json")
                                     for key, run in selected.last_successful_runs.items()},
            "vector_coverage": (selected.vector_coverage.model_dump(mode="json")
                                if selected.vector_coverage is not None else None),
            "stores": [{"name": copy.name, "sha256": copy.sha256} for copy in selected.stores],
        }
        (Path(root) / PULL_RECORD).write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
        found = manager.store.get(f"generations/{generation}.json")
        if found is None:
            raise Failure("manifest_unavailable")
        # Observation queries need the retained generation; restore already checked the remote raw inventory.
        retain_generation(Path(root), found[0], Path(root), verified=True, remote_raw=True)
        return generation
    finally:
        manager.store._client.close()


def load_questions(path):
    import yaml

    questions = yaml.safe_load(path.read_text(encoding="utf-8"))["questions"]
    if not isinstance(questions, list) or not questions:
        raise ValueError("invalid_questions")
    ids = set()
    for question in questions:
        if any(not isinstance(question.get(key), str) or not question[key] for key in ("id", "lang", "question")):
            raise ValueError("invalid_question")
        if question["id"] in ids or not isinstance(question.get("calls"), list) or not question["calls"]:
            raise ValueError("invalid_calls")
        ids.add(question["id"])
        for call in question["calls"]:
            if not isinstance(call, dict) or not isinstance(call.get("tool"), str) or not isinstance(call.get("args"), dict):
                raise ValueError("invalid_call")
    return questions


def hits(data):
    if not isinstance(data, dict):
        return []
    found = data.get("hits", data.get("datasets", []))
    return found if isinstance(found, list) else []


def call_tool(tools, tool, args, root):
    record = {"tool": tool, "args": args, "data": None, "gap_state": None, "error_code": None}
    started = monotonic()
    try:
        if tool not in ALLOWED_TOOLS:
            raise ValueError("tool_not_allowed")
        response = getattr(tools, tool)(**args)
        if tool in {"search_docs", "search_catalog"}:
            field = "hits" if tool == "search_docs" else "datasets"
            if not isinstance(response["data"][field], list):
                raise ValueError("invalid_hits")
        if tool == "version":
            from datumoj.rikolto.core.pull import data_state

            response["data"].update(data_state(root))
        record.update(response)
    except Exception:  # noqa: BLE001 -- Never retain arbitrary exception text, even in public artifacts.
        record["error_code"] = "tool_failed" if tool in ALLOWED_TOOLS else "tool_not_allowed"
    record["elapsed_s"] = round(monotonic() - started, 3)
    return record


def run_question(question, deps, root):
    from datumoj.rikolto.core.models import SourceText

    answer = {**question, "calls": [], "error_code": None}
    for request in question["calls"]:
        call = call_tool(deps.tools, request["tool"], request["args"], root)
        answer["calls"].append(call)
        if request["tool"] in {"search_docs", "search_catalog"} and call["error_code"] is None:
            try:
                found = hits(call["data"])
                call["top_hits"] = []
                for hit in found[:5]:
                    view = dict(hit)
                    citation = hit.get("citation") or {}
                    view.setdefault("source_id", citation.get("source_id"))
                    if "doc_key" in hit:
                        doc = deps.chunks.get_document(hit["doc_key"], citation.get("snapshot_sha256"))
                        view["title"] = SourceText(content=doc.title).model_dump(mode="json") if doc else None
                    call["top_hits"].append(view)
                if found:
                    citation = found[0].get("citation")
                    if citation:
                        args = {key: citation[key] for key in ("snapshot_sha256", "locator", "snapshot_id")}
                        restored = call_tool(deps.tools, "cite", args, root)
                        restored["restored"] = (restored["error_code"] is None and restored["data"].get("status") == "ok"
                                                and bool(restored["data"].get("passages")))
                    else:
                        restored = {"restored": False, "error_code": "citation_unavailable"}
                    call["first_hit_cite"] = restored
            except Exception:  # noqa: BLE001 -- Isolate malformed results to this question.
                call["error_code"] = "result_failed"
        if call["error_code"] or call.get("first_hit_cite", {}).get("error_code"):
            answer["error_code"] = "question_failed"
    return answer


def jma_hourly(chunks):
    """Count current JMA documents once, using the preserved publication timestamp."""
    counts = [0] * int((END - START).total_seconds() / 3600)
    seen = set()
    missing = 0
    outside = 0
    pause_count = 0
    for chunk in chunks.iter_chunks(source_ids=["jp-jma"]):
        if chunk.doc_key in seen:
            continue
        seen.add(chunk.doc_key)
        doc = chunks.get_document(chunk.doc_key, chunk.snapshot_sha256)
        value = doc.metadata.get("published_at") if doc else None
        try:
            if not isinstance(value, str):
                raise ValueError("missing_timestamp")
            stamp = datetime.fromisoformat(value)
            if stamp.utcoffset() is None:
                raise ValueError("missing_timezone")
            stamp = stamp.astimezone(UTC)
        except ValueError:
            missing += 1
            continue
        if START <= stamp < END:
            counts[int((stamp - START).total_seconds() // 3600)] += 1
            pause_count += int(PAUSE_START <= stamp < PAUSE_END)
        else:
            outside += 1
    rows = []
    pause_hours = (PAUSE_END - PAUSE_START).total_seconds() / 3600
    for index, count in enumerate(counts):
        hour = START + timedelta(hours=index)
        # A partial hour belongs to the pause if it overlaps the pause at all.
        in_pause = hour < PAUSE_END and hour + timedelta(hours=1) > PAUSE_START
        rows.append({"hour_utc": hour.isoformat(), "count": count, "overlaps_pause": in_pause})
    return {
        "source_id": "jp-jma", "field": "Document.metadata.published_at",
        "time_basis": "JMA Head.ReportDateTime (parser fallback: Control.DateTime); never fetched_at",
        "unit": "one current parsed document per doc_key; multiple chunks count once; no raw/feed re-fetch",
        "window": {"start": START.isoformat(), "end_exclusive": END.isoformat()},
        "pause": {"start": PAUSE_START.isoformat(), "end_exclusive": PAUSE_END.isoformat(),
                  "mean_rule": "exact half-open pause interval; fractional hours in mean denominator",
                  "hour_rule": "overlaps_pause labels any overlap; hourly counts always cover full UTC hours"},
        "missing_or_invalid_timestamp": missing, "outside_window": outside,
        "current_documents": len(seen), "hours": rows,
        "pause_mean": {"hours": pause_hours, "entries": pause_count, "entries_per_hour": pause_count / pause_hours},
        "outside_pause_mean": {"hours": len(rows) - pause_hours, "entries": sum(counts) - pause_count,
                               "entries_per_hour": (sum(counts) - pause_count) / (len(rows) - pause_hours)},
        "limitation": "Counts describe retained current parsed entries, not historical feed completeness or crawl continuity.",
    }


def json_text(value):
    return json.dumps(value, ensure_ascii=False, indent=2)


def markdown(report):
    # Indented code blocks keep arbitrary source Markdown/HTML inert in the report.
    def block(value):
        return "\n".join("    " + line for line in json_text(value).splitlines())

    lines = ["# Cloud QA (unscored)", "", block(report["metadata"]), ""]
    for answer in report["questions"]:
        lines.extend(["## Question", "", block({key: answer[key] for key in ("id", "lang", "question", "error_code")}), ""])
        for call in answer["calls"]:
            lines.extend(["### Call", "", block({key: value for key, value in call.items() if key != "data"}), ""])
            if call["tool"] not in {"search_docs", "search_catalog"}:
                lines.extend([block(call["data"]), ""])
    jma = report["jma_hourly"]
    lines.extend(["## JMA hourly counts", "", block({key: value for key, value in jma.items() if key != "hours"}), "",
                  "| Hour (UTC) | Entries | Overlaps pause |", "| --- | ---: | --- |"])
    lines.extend(f'| {row["hour_utc"]} | {row["count"]} | {row["overlaps_pause"]} |' for row in jma["hours"])
    return "\n".join(lines) + "\n"


def run(root, question_path, output):
    started = monotonic()
    summary = {"generation": None, "questions": 0, "question_hits": [], "error_questions": 0,
               "jma_hours": 0, "phase": "questions", "error_code": None, "exit_status": 1}
    with quiet() as out:
        def mark(phase, **counts):
            # Phase name and numbers only, written when the phase starts: an interrupted run shows where it stalled.
            summary["phase"] = phase
            out(json.dumps({"phase": phase, "elapsed_s": round(monotonic() - started, 1), **counts}, sort_keys=True))

        try:
            from pull_check import _read_meter

            from datumoj.rikolto import wiring
            from datumoj.rikolto.core.pull import data_state

            questions = load_questions(question_path)
            summary["questions"] = len(questions)
            mark("restore")
            with _read_meter(), raw_meter() as raw_read, ExitStack() as stack:
                summary["generation"] = generation = restore_latest(root)
                state = data_state(root)
                if state["data_generation"] != generation or not state["data_generated_at"]:
                    raise Failure("generation_mismatch")
                mark("wiring")
                # cli._build_wiring intentionally disables vectors on pulled replicas.
                # Build directly to use the downloaded index and the cloud-run model.
                deps = wiring.build(root)
                for name in ("manifest", "chunks", "keyword", "catalog", "change_log"):
                    stack.callback(getattr(deps, name).close)
                deps.require("search")
                if deps.vectors is None:
                    raise Failure("vectors_unavailable")
                report = {"metadata": {"generation": generation, "generated_at": state["data_generated_at"],
                                       "commit_sha": os.environ.get("GITHUB_SHA", "unknown"),
                                       "argument_adjustments": [], "scoring": "human review only"}, "questions": []}
                for question in questions:
                    mark("questions", done=len(report["questions"]))
                    answer = run_question(question, deps, root)
                    report["questions"].append(answer)
                    summary["question_hits"].append(sum(len(hits(call["data"])) for call in answer["calls"]))
                    summary["error_questions"] += int(answer["error_code"] is not None)
                mark("jma")
                report["jma_hourly"] = jma_hourly(deps.chunks)
                summary["jma_hours"] = len(report["jma_hourly"]["hours"])
                mark("artifacts")
                report["metadata"]["raw_read"] = {"objects": raw_read["objects"], "bytes": raw_read["bytes"]}
                output.mkdir(parents=True, exist_ok=True)
                (output / "answers.json").write_text(json_text(report) + "\n", encoding="utf-8")
                (output / "answers.md").write_text(markdown(report), encoding="utf-8")
            summary.update(phase="complete", exit_status=0)
        except Exception as exc:  # noqa: BLE001 -- Only fixed phases, fixed codes and counts cross the public boundary.
            summary["exit_status"] = 1
            summary["error_code"] = str(exc) if isinstance(exc, Failure) else f"{summary['phase']}_failed"
    return summary


def main(argv=None):
    with quiet():
        parser = argparse.ArgumentParser(description=__doc__)
        parser.add_argument("data_dir", type=Path)
        parser.add_argument("questions", type=Path)
        parser.add_argument("output_dir", type=Path)
        args = parser.parse_args(argv)
    summary = run(args.data_dir, args.questions, args.output_dir)
    print(json.dumps(summary, sort_keys=True), flush=True)
    return summary["exit_status"]


if __name__ == "__main__":
    raise SystemExit(main())
