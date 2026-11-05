"""Offline, stdlib-only control-flow checks against saved fixed-SHA source.

No project imports, network calls, production state, or repository writes.
The original AST bodies are executed with synthetic in-memory dependencies.
This proves code paths, not the actual runtime location of Cloud #28.
"""

from __future__ import annotations

import ast
import argparse
from collections import Counter
from contextlib import closing, contextmanager
from datetime import UTC, datetime, timedelta
import hashlib
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace

SHA = "f7e665e868df14b5ee17444d0b095f6ad508374e"


def verify_source(path, expected_blob):
    data = path.read_bytes()
    # Connector extraction may append one blank line; no code is normalized.
    data = data.rstrip(b"\n") + b"\n"
    actual = hashlib.sha1(f"blob {len(data)}\0".encode() + data).hexdigest()
    if actual != expected_blob:
        raise ValueError(f"Source differs from fixed SHA: {path}")


def compile_nodes(nodes, namespace, filename):
    future = ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0)
    module = ast.Module(body=[future, *nodes], type_ignores=[])
    exec(compile(ast.fix_missing_locations(module), str(filename), "exec"), namespace)


def check_cleanup_paths():
    tree = ast.parse(STATE.read_text())
    cls = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "StateManager")
    methods = [node for node in cls.body if isinstance(node, ast.FunctionDef)
               and node.name in {"cleanup", "_generation_raw", "_get"}]
    namespace = {
        "closing": closing, "timedelta": timedelta, "hashlib": hashlib,
        "StateError": RuntimeError, "_digest": lambda data: hashlib.sha256(data).hexdigest(),
        "_raw_refs": lambda path: set(),
        "_raw_key": lambda prefix, sha: prefix + "/" + sha,
    }

    class Manifest:
        def __init__(self, path):
            pass

        def raw_markers(self):
            return {}

        def close(self):
            pass

    namespace["SqliteManifest"] = Manifest
    # Each synthetic generation shares both immutable blocks. No SQL is loaded.
    payloads = {"copies/sha256/block-a": b"block-a", "copies/sha256/block-b": b"block-b"}
    blocks = [SimpleNamespace(name=f"manifest.sqlite.blocks/{i}", key=key,
                              size_bytes=len(data), sha256=hashlib.sha256(data).hexdigest())
              for i, (key, data) in enumerate(payloads.items())]
    combined = b"".join(payloads.values())
    descriptor = SimpleNamespace(name="manifest.sqlite", key="copies/sha256/logical-manifest",
                                 size_bytes=len(combined), sha256=hashlib.sha256(combined).hexdigest())
    namespace["_block_groups"] = lambda stores: {"manifest.sqlite": blocks}
    compile_nodes(methods, namespace, STATE)

    class FakeManager:
        cleanup = namespace["cleanup"]
        _generation_raw = namespace["_generation_raw"]
        _get = namespace["_get"]

        def __init__(self, generations):
            self.events = []
            self.last_timings = {"total": 1118.5, "post_publish": 974.5}
            self.original_timings = self.last_timings
            self.transfer_workers = 8
            self.generations = generations
            self.store = SimpleNamespace(get=self.get, list_objects=self.list_objects)

        def get(self, key):
            self.events.append(("get", key))
            return payloads[key], "synthetic-etag"

        def list_objects(self, prefix):
            self.events.append(("list_objects", prefix))
            return [SimpleNamespace(key=key, size_bytes=len(data), etag="synthetic-etag")
                    for key, data in payloads.items()]

        def _current(self):
            self.events.append(("current", None))
            return self.generations[-1].generation, "synthetic-etag"

        def _generation(self, number):
            return next(g for g in self.generations if g.generation == number)

        @contextmanager
        def _temporary_stage(self, **kwargs):
            with tempfile.TemporaryDirectory(**kwargs) as directory:
                yield Path(directory)

        def _materialize(self, generation, stage, *, raw):
            self.events.append(("materialize", raw))
            self.last_timings["download_copies"] = 123.0

        def list(self):
            self.events.append(("history", None))
            return self.generations

        def raw_retention(self, specs, now):
            self.events.append(("raw_retention", None))
            return {}

        def _transfer(self, *args, **kwargs):
            raise AssertionError("Generation manifest reads must be sequential in this code path")

    now = datetime(2026, 11, 5, tzinfo=UTC)
    history = [SimpleNamespace(generation=i, created_at=now - timedelta(hours=i),
                               stores=[descriptor, *blocks], raw_prefix="raw") for i in range(1, 25)]
    outputs = []
    for enabled, raw_enabled in ((False, False), (True, False), (True, True)):
        manager = FakeManager(history)
        checks = []
        namespace["delete_retired_object"] = lambda *args: (_ for _ in ()).throw(
            AssertionError("No deletions are expected for this synthetic inventory"))
        report = manager.cleanup([], now=now, check=lambda: checks.append(True),
                                 enabled=enabled, raw_enabled=raw_enabled)
        gets = Counter(value for event, value in manager.events if event == "get")
        assert report["failures"] == 0, report
        assert sum(gets.values()) == 48 and set(gets.values()) == {24}, gets
        assert ("materialize", "remote") in manager.events
        assert ("raw_retention", None) in manager.events
        assert manager.last_timings is manager.original_timings
        assert "download_copies" not in manager.last_timings
        assert "timings" not in report
        assert len(checks) == 1
        outputs.append({"enabled": enabled, "raw_enabled": raw_enabled,
                        "retained_generations": 24, "unique_manifest_blocks": 2,
                        "manifest_get_calls": sum(gets.values()), "lease_checks": len(checks),
                        "materialize_invoked": True, "cleanup_timings_preserved": False})
    return outputs


def check_publish_end_is_not_success():
    tree = ast.parse(CLOUD.read_text())
    node = next(node for node in ast.walk(tree) if isinstance(node, ast.Try)
                and node.body and isinstance(node.body[0], ast.Assign)
                and isinstance(node.body[0].value, ast.Call)
                and isinstance(node.body[0].value.func, ast.Attribute)
                and node.body[0].value.func.attr == "push")
    events = []

    def failed_push():
        raise RuntimeError("synthetic push failure")

    namespace = {
        "manager": SimpleNamespace(push=failed_push, last_push_timings={}, last_timings={}),
        "time": SimpleNamespace(monotonic=lambda: 10.0), "publish_started": 5.0,
        "result": {}, "started": 0.0,
        "_progress": lambda event, *args: events.append(event),
    }
    try:
        compile_nodes([node], namespace, CLOUD)
    except RuntimeError as exc:
        assert str(exc) == "synthetic push failure"
    else:
        raise AssertionError("Original push exception must propagate")
    assert events == ["publish_end"] and "generation" not in namespace
    return {"push_failed": True, "event_emitted": events, "generation_returned": False}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True, help="Independent checkout of the fixed SHA")
    args = parser.parse_args()
    STATE = args.repo / "src/datumoj/rikolto/core/state.py"
    CLOUD = args.repo / "src/datumoj/rikolto/cloud.py"
    verify_source(STATE, "6370f87a58a6e09cdb745c13f227095df698f618")
    verify_source(CLOUD, "ca67c7a45832e4674b2a5dbbe47de2b5c51da2d8")
    print(json.dumps({"fixed_sha": SHA, "source_blobs_verified": True, "network_calls": 0,
                      "cleanup_cases": check_cleanup_paths(),
                      "publish_exception_case": check_publish_end_is_not_success()}, indent=2))
