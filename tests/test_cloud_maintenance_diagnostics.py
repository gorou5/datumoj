"""Maintenance diagnostics expose only fixed codes, preserving fail-closed publication."""

import io
import json
import logging
import sys
from contextlib import contextmanager
from datetime import timedelta

import pytest

from datumoj.rikolto import cloud
from datumoj.rikolto.core.lease import LeaseError, LeaseManager
from datumoj.rikolto.core.state import StateManager
from datumoj.rikolto.index import vector
from datumoj.rikolto.index.embed import HashEmbedder
from tests.test_cloud_embed import invoke_embed
from tests.test_cloud_run import BODY, ENV, invoke, released, remote
from tests.test_cloud_run import world as world  # noqa: PLC0414 -- Shared offline fixture.

_PRIVATE = "synthetic-private-error /private/source.txt https://private.invalid/?token=secret"


@pytest.fixture
def maintained(world, monkeypatch):
    world.overrides.pop("vectors")
    world.overrides["embedder"] = HashEmbedder(model="fake-hash-v1")
    first, _ = invoke(world, "initial", "--max-embed", "0")
    assert first["generation"] == 1 and first["vector_pending"] == 6
    world.clock[0] += timedelta(hours=1)
    stream = io.StringIO()
    monkeypatch.setattr(sys, "__stderr__", stream)
    return world, remote(world).get("current.json"), stream


def _assert_failure(maintained, result, operation, category):
    world, before, stream = maintained
    details = {"phase": "maintenance", "operation": operation, "error_category": category}
    assert result["status"] == "failed" and result["exit_status"] == 1
    assert result["reason"] == "maintenance_failed" and result["generation"] is None
    assert result["publish_timings"] == {}
    assert remote(world).get("current.json") == before
    assert len(world.managers[-1].list()) == 1
    released(world)
    assert result.get("maintenance_failure") == details
    events = [json.loads(line) for line in stream.getvalue().splitlines()]
    assert [row for row in events if row["event"] == "maintenance_failed"] == [
        {"event": "maintenance_failed", **details},
    ]
    assert not any(row["event"] == "publish_start" for row in events)
    public = json.dumps(result) + stream.getvalue()
    assert all(value not in public for value in (_PRIVATE, BODY.decode(), *ENV.values(), *world.tokens))
    assert "Traceback" not in public


@pytest.mark.parametrize("mode", ["run", "embed"])
@pytest.mark.parametrize("boundary", ["local_write_enter", "maintain_vectors", "local_write_exit", "lease_check"])
def test_maintenance_failure_identifies_boundary_without_publishing(maintained, monkeypatch, mode, boundary):
    world, _, _ = maintained
    armed = False
    backfill = cloud._backfill
    local_write = cloud.local_write
    maintain = cloud.maintain_vectors
    check = LeaseManager.check

    def finish_backfill(*args, **kwargs):
        nonlocal armed
        result = backfill(*args, **kwargs)
        armed = True
        return result

    @contextmanager
    def write(root):
        if armed and boundary == "local_write_enter":
            raise OSError(_PRIVATE)
        with local_write(root):
            yield
        if armed and boundary == "local_write_exit":
            raise OSError(_PRIVATE)

    def vectors(path):
        if boundary == "maintain_vectors":
            print(_PRIVATE)
            logging.getLogger(__name__).error(_PRIVATE)
            raise RuntimeError(_PRIVATE)
        return maintain(path)

    def lease_check(self, lease):
        if armed and boundary == "lease_check":
            raise LeaseError(_PRIVATE)
        return check(self, lease)

    monkeypatch.setattr(cloud, "_backfill", finish_backfill)
    monkeypatch.setattr(cloud, "local_write", write)
    monkeypatch.setattr(cloud, "maintain_vectors", vectors)
    monkeypatch.setattr(LeaseManager, "check", lease_check)
    result = invoke(world)[0] if mode == "run" else invoke_embed(world)
    category = {"maintain_vectors": "runtime_error", "lease_check": "lease_error"}.get(boundary, "os_error")
    _assert_failure(maintained, result, boundary, category)


@pytest.mark.parametrize("failure, category", [
    (ValueError(_PRIVATE), "value_error"),
    (KeyError(_PRIVATE), "lookup_error"),
    (MemoryError(_PRIVATE), "memory_error"),
    (type("UntrustedPrivateClassName", (Exception,), {})(_PRIVATE), "unexpected_error"),
])
def test_exception_details_are_replaced_by_fixed_categories(maintained, monkeypatch, failure, category):
    def fail(path):
        raise failure

    monkeypatch.setattr(cloud, "maintain_vectors", fail)
    result, _ = invoke(maintained[0])
    _assert_failure(maintained, result, "maintain_vectors", category)
    assert "UntrustedPrivateClassName" not in maintained[2].getvalue() + json.dumps(result)


def test_exception_rendering_is_never_invoked(maintained, monkeypatch):
    class PrivateError(Exception):
        def __str__(self):
            raise AssertionError("Exception rendering must not run")

        def __repr__(self):
            raise AssertionError("Exception rendering must not run")

    def fail(path):
        raise PrivateError(_PRIVATE)

    monkeypatch.setattr(cloud, "maintain_vectors", fail)
    result, _ = invoke(maintained[0])
    _assert_failure(maintained, result, "maintain_vectors", "unexpected_error")


@pytest.mark.parametrize("method, category", [("stats", "lookup_error"), ("optimize", "os_error")])
def test_real_vector_maintenance_reports_inner_failures(maintained, monkeypatch, method, category):
    world, _, _ = maintained
    table_type = type(world.components[0].vectors._table)
    backfill = cloud._backfill

    def fail(*args, **kwargs):
        raise (KeyError(_PRIVATE) if method == "stats" else OSError(_PRIVATE))

    def finish_backfill(*args, **kwargs):
        result = backfill(*args, **kwargs)
        monkeypatch.setattr(table_type, method, fail)
        return result

    monkeypatch.setattr(vector, "_COMPACT_FRAGMENTS", 0)
    monkeypatch.setattr(cloud, "_backfill", finish_backfill)
    result, _ = invoke(world)
    _assert_failure(maintained, result, "maintain_vectors", category)


def test_optional_raw_marking_failure_still_publishes_without_fatal_diagnostic(maintained, monkeypatch):
    world, _, stream = maintained

    def fail(self, specs, now):
        raise OSError(_PRIVATE)

    monkeypatch.setattr(StateManager, "mark_raw", fail)
    result, _ = invoke(world)
    assert result["status"] == "completed" and result["generation"] == 2
    assert result["cleanup"]["failures"] == 1
    assert "maintenance_failure" not in result
    assert not any(json.loads(line)["event"] == "maintenance_failed" for line in stream.getvalue().splitlines())
    assert _PRIVATE not in stream.getvalue() + json.dumps(result)
    released(world)
