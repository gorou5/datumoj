"""KATABAMI-108: public retention reports hold only fixed names, counts, bytes and source IDs."""

import json
import re

from typer.testing import CliRunner

from datumoj.rikolto import cli
from tests.test_cloud_db_retention import expiring
from tests.test_cloud_run import BODY, ENV, invoke, remote
from tests.test_cloud_run import world as world  # noqa: PLC0414 -- Shared offline fixture.

SOURCE_ID = re.compile(r"^[a-z]{2,4}(?:-[a-z0-9]+)+$")
FIXED = {
    "mode", "superseded", "index_expired", "manifest", "vectors", "vacuum", "sources", "versions", "documents",
    "chunks", "bytes", "waiting_superseded", "unknown_superseded", "held", "unknown_index_age",
    "snapshots_dropped", "snapshots_kept", "hashes_released", "parse_records_trimmed", "parsed_deleted",
    "parse_writers_deleted", "raw_markers_deleted", "publish_states_deleted", "deleted", "files", "incremental",
    "one_off", "pending", "estimated_bytes", "done", "skipped_reason", "before_bytes", "free_bytes", "after_bytes",
    "pages",
}
REASONS = {None, "capacity_normal", "capacity_report", "capacity_freeze", "capacity_stop", "capacity_unknown"}


def walk(value, under_sources=False):
    """Yield every key (source IDs under ``sources``) and every scalar of a nested report."""
    if isinstance(value, dict):
        for key, item in value.items():
            if under_sources:
                assert SOURCE_ID.match(key), key
            else:
                assert key in FIXED, key
            yield from walk(item, key == "sources" and not under_sources)
    elif isinstance(value, list):
        for item in value:
            yield from walk(item)
    else:
        assert value is None or isinstance(value, (bool, int, str)), value
        yield value


def assert_public(report, world):
    text = json.dumps(report)
    for private in (BODY.decode(), *ENV.values(), "http", "raw/", "copies/", "generations/", "state-storage"):
        assert private not in text, private
    scalars = list(walk(report))
    assert all(isinstance(v, (bool, int)) or v in REASONS or v in {"dry_run", "applied", "manifest", "chunks"}
               for v in scalars), scalars


def assert_configured_sources(report, world):
    """Every source key in the report is a source the registry actually configures."""
    configured = {spec.id for spec in world.specs}
    for section in ("superseded", "index_expired"):
        assert set(report[section]["sources"]) <= configured, section


def test_cloud_report_has_no_url_text_bucket_or_object_key(world, monkeypatch):
    expiring(world)
    invoke(world, "first")
    monkeypatch.setenv("R2_DB_RETENTION", "on")
    result, root = invoke(world, "second")
    assert result["db_retention"]["index_expired"]["sources"]
    assert_public(result["db_retention"], world)
    assert_configured_sources(result["db_retention"], world)
    # Local dry-run over the same directory prints the same kind of report.
    response = CliRunner().invoke(cli.app, ["--data-dir", str(root), "db-retention", "--dry-run"])
    assert response.exit_code == 0, response.output
    dry = json.loads(response.output)
    assert dry["mode"] == "dry_run"
    assert_public(dry, world)
    assert_configured_sources(dry, world)
    assert remote(world).get("current.json") is not None
