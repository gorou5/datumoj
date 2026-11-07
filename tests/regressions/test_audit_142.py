"""Audit 142: private exception types, nonfatal maintenance and unpublished issues."""

import io
import json
import logging
import sys
from datetime import timedelta

import pytest
from typer.testing import CliRunner

from datumoj.rikolto import cli, cloud
from datumoj.rikolto.core.lease import LeaseError, LeaseManager
from datumoj.rikolto.core.state import StateManager
from tests.test_cloud_embed import invoke_embed, seed
from tests.test_cloud_run import ENV, invoke, released, remote
from tests.test_cloud_run import world as world  # noqa: PLC0414 -- Shared offline cloud fixture.
from tests.test_source_issues import health, source_issues


@pytest.mark.parametrize("embed_only", [False, True])
def test_maintenance_failure_publishes_next_generation_and_deletes_orphan(world, monkeypatch, embed_only):
    if embed_only:
        seed(world)
    else:
        assert invoke(world, "initial")[0]["generation"] == 1
        world.clock[0] += timedelta(hours=1)
    before = remote(world).get("current.json")
    cleanup_calls = []
    original_cleanup = StateManager.cleanup

    def fail(*args):
        raise OSError("synthetic-private-maintenance-message")

    def cleanup(manager, *args, **kwargs):
        assert manager.list()[0].generation == 2
        manager.store.put("copies/sha256/synthetic-audit-142-orphan", b"orphan")
        cleanup_calls.append(True)
        return original_cleanup(manager, *args, **kwargs)

    monkeypatch.setattr(cloud, "maintain_vectors", fail)
    monkeypatch.setattr(StateManager, "cleanup", cleanup)
    result = invoke_embed(world) if embed_only else invoke(world)[0]
    assert result["status"] == "completed" and result["exit_status"] == 0
    assert result["generation"] == 2 and result["maintenance_error"] == "OSError"
    assert result["error_type"] is None and result["reason"] is None
    assert remote(world).get("current.json") != before
    assert cleanup_calls == [True] and result["cleanup"]["copies_deleted"] >= 1
    assert not remote(world).exists("copies/sha256/synthetic-audit-142-orphan")
    assert "synthetic-private-maintenance-message" not in json.dumps(result)
    released(world)


@pytest.mark.parametrize("embed_only", [False, True])
def test_round_exception_outputs_only_class_name(world, monkeypatch, capsys, caplog, embed_only):
    private = (*ENV.values(), "synthetic-private-error-message", "/synthetic/private/audit-142")
    monkeypatch.setenv("AUDIT_142_PRIVATE", private[-2])
    stream = io.StringIO()
    monkeypatch.setattr(sys, "__stderr__", stream)

    def fail(*args):
        message = " ".join(private)
        print(message)
        print(message, file=sys.stderr)
        logging.getLogger(__name__).error("%s", message)
        raise RuntimeError(message)

    monkeypatch.setattr(LeaseManager, "renew", fail)
    command = "embed" if embed_only else "run"
    response = CliRunner().invoke(cli.app, [
        "cloud", command, "--work-dir", str(world.tmp / "failed"), "--max-minutes", "100",
    ])
    result = json.loads(response.output)
    assert response.exit_code == result["exit_status"] == 1
    assert result["reason"] == "lease_renew_failed" and result["generation"] is None
    assert result["error_type"] == "RuntimeError" and result["maintenance_error"] is None
    captured = capsys.readouterr()
    output = response.output + stream.getvalue() + captured.out + captured.err + caplog.text
    assert all(value not in output for value in (*private, *world.tokens, "Traceback"))
    released(world)


@pytest.mark.parametrize("maintenance_fails", [False, True])
def test_lease_loss_immediately_after_maintenance_prevents_publication(world, monkeypatch, maintenance_fails):
    assert invoke(world, "initial")[0]["generation"] == 1
    before = remote(world).get("current.json")
    world.clock[0] += timedelta(hours=1)
    maintained = []
    original_check = LeaseManager.check

    def maintain(*args):
        maintained.append(True)
        if maintenance_fails:
            raise OSError("synthetic maintenance failure before lease loss")

    def check(manager, lease):
        if maintained:
            raise LeaseError("synthetic private lease failure")
        return original_check(manager, lease)

    def forbidden(*args, **kwargs):
        pytest.fail("Lost lease must prevent publication and cleanup")

    monkeypatch.setattr(cloud, "maintain_vectors", maintain)
    monkeypatch.setattr(LeaseManager, "check", check)
    monkeypatch.setattr(StateManager, "push", forbidden)
    monkeypatch.setattr(StateManager, "cleanup", forbidden)
    result, _ = invoke(world)
    assert maintained == [True]
    assert result["exit_status"] == 1 and result["generation"] is None
    assert result["reason"] == "lease_check_failed" and result["error_type"] == "LeaseError"
    assert result["maintenance_error"] == ("OSError" if maintenance_fails else None)
    assert remote(world).get("current.json") == before
    assert len(world.managers[-1].list()) == 1
    released(world)


@pytest.mark.parametrize("embed_only", [False, True])
def test_exception_fields_default_to_none(world, embed_only):
    result = invoke_embed(world) if embed_only else invoke(world)[0]
    assert result["exit_status"] == 0
    assert result["error_type"] is None and result["maintenance_error"] is None
    released(world)


def test_no_generation_skips_gh_for_both_recovery_and_failure(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("An unpublished round must never call gh")

    monkeypatch.setattr(source_issues.subprocess, "run", forbidden)
    result = {"generation": None, "source_health": [health("ok", 0), health(), health("missing", 0)]}
    assert source_issues.sync_issues(result) == 0
    assert source_issues.sync_issues({"generation": None}) == 0
