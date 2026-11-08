"""Read a published generation and exercise the MCP tool layer; print safe counts only."""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
from contextlib import ExitStack, contextmanager, redirect_stderr, redirect_stdout
from pathlib import Path
from threading import Lock
from time import monotonic

import boto3

from datumoj.rikolto import cli
from datumoj.rikolto.core.pull import data_state

QUERY = "気象 警報"
SQL = "SELECT COUNT(*) AS observation_count FROM observations"


@contextmanager
def _read_meter():
    """Count successful GET response payload bytes, including repeated metadata GETs."""
    if boto3.DEFAULT_SESSION is None:
        boto3.setup_default_session()
    events = boto3.DEFAULT_SESSION.events
    meter = {"bytes": 0}
    lock = Lock()

    def read_only(model, **kwargs):
        if model.name not in {"GetObject", "HeadObject", "ListObjects", "ListObjectsV2"}:
            raise ValueError("read_only")

    def count(parsed, **kwargs):
        size = parsed.get("ContentLength", 0)
        if type(size) is int and size >= 0:
            with lock:
                meter["bytes"] += size

    events.register("before-call.s3", read_only)
    events.register("after-call.s3.GetObject", count)
    try:
        yield meter
    finally:
        events.unregister("before-call.s3", read_only)
        events.unregister("after-call.s3.GetObject", count)


def _count(value):
    if type(value) is not int or value < 0:
        raise ValueError("invalid_count")
    return value


def check(root: Path) -> dict:
    result = {
        "generation": None, "pull_bytes": 0, "pull_elapsed_s": 0,
        "source_snapshots": {}, "search_hits": 0, "search_source_ids": [],
        "cite_restored": False, "sql_rows": None, "exit_status": 1, "phase": "pull",
    }
    started = monotonic()
    disabled = logging.root.manager.disable
    try:
        # The public boundary must also hide SDK diagnostics, warnings and exception text.
        with open(os.devnull, "w") as sink, redirect_stdout(sink), redirect_stderr(sink), ExitStack() as stack:
            logging.disable(logging.CRITICAL)
            with _read_meter() as meter:
                try:
                    pulled = cli.pull_state(root)
                finally:
                    result["pull_bytes"] = meter["bytes"]
                    result["pull_elapsed_s"] = round(monotonic() - started, 3)
            result["generation"] = _count(pulled["generation"])
            if result["generation"] == 0 or pulled["verified"] is not True:
                raise ValueError("unverified_generation")

            result["phase"] = "version"
            deps = cli._build_wiring(root)
            for name in ("manifest", "chunks", "keyword", "catalog", "change_log"):
                stack.callback(getattr(deps, name).close)
            deps.require("search")
            tools = deps.tools
            version = tools.version()
            version["data"].update(data_state(root))  # Same augmentation as the MCP version wrapper.
            if version["data"]["data_generation"] != result["generation"]:
                raise ValueError("generation_mismatch")

            result["phase"] = "coverage"
            sources = {}
            for row in tools.coverage()["data"]["sources"]:
                source_id = row["source_id"]
                if not isinstance(source_id, str) or not re.fullmatch(r"[a-z]{2,4}(?:-[a-z0-9]+)+", source_id):
                    raise ValueError("invalid_source_id")
                sources[source_id] = _count(row["snapshots"])
            result["source_snapshots"] = sources

            result["phase"] = "search_docs"
            hits = tools.search_docs(QUERY, k=5)["data"]["hits"]
            result["search_hits"] = len(hits)
            hit_sources = sorted({hit["citation"]["source_id"] for hit in hits})
            if not hits or any(source_id not in sources for source_id in hit_sources):
                raise ValueError("no_supported_hits")
            result["search_source_ids"] = hit_sources

            result["phase"] = "cite"
            citation = hits[0]["citation"]
            cited = tools.cite(citation["snapshot_sha256"], citation["locator"], snapshot_id=citation["snapshot_id"])
            result["cite_restored"] = cited["data"]["status"] == "ok" and bool(cited["data"]["passages"])
            if not result["cite_restored"]:
                raise ValueError("cite_failed")

            result["phase"] = "query_sql"
            result["sql_rows"] = _count(tools.query_sql(SQL, max_rows=1)["data"]["rows"][0][0])
        result.update(exit_status=0, phase="complete")
    except Exception:  # noqa: BLE001 -- Public Actions logs must never contain arbitrary SDK or source errors.
        result["exit_status"] = 1
    finally:
        logging.disable(disabled)
    return result


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("data_dir", type=Path, help="runner temporary destination")
    args = parser.parse_args(argv)
    result = check(args.data_dir)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return result["exit_status"]


if __name__ == "__main__":
    raise SystemExit(main())
