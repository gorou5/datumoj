"""Four WEO archive files imported through ``rikolto observations import-release`` as history, offline.

The API release stays current; the archive releases are readable per release with their own snapshots.
"""

import json
from datetime import date
from decimal import Decimal
from pathlib import Path

import httpx
import pytest
from typer.testing import CliRunner

from datumoj.rikolto import cli, wiring
from tests.crawlers.test_imf_sdmx import GRC_SPEC
from tests.integration.test_imf_weo_e2e import build, close, crawl_and_parse, routes_for

SID = "intl-imf-weo"
ARCHIVE = Path(__file__).parents[2] / "tests/fixtures/imf_weo_archive"
SPEC = GRC_SPEC.model_copy(update={"scope": {**GRC_SPEC.scope, "dataflows": ["WEO"]}})  # one request matches the file
RELEASES = [  # release_id and released_at are the ones the person gives (README suggestions)
    ("weo_2009_apr_GRC_excerpt.tsv", "WEO-2009-APR", "2009-04-22", "GGB_NGDP", "-4.509"),
    ("weo_2009_oct_GRC_excerpt.tsv", "WEO-2009-OCT", "2009-10-01", "GGB_NGDP", "-6.398"),
    ("weo_2010_apr_GRC_excerpt.tsv", "WEO-2010-APR", "2010-04-21", "GGXCNL_NGDP", "-12.866"),
    ("weo_2010_oct_GRC_excerpt.tsv", "WEO-2010-OCT", "2010-10-06", "GGXCNL_NGDP", "-13.584"),
]


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("IMF tests must never access the network")

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", forbidden)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", forbidden)
    monkeypatch.setenv("DATUMOJ_RAW_BACKEND", "local")
    monkeypatch.setenv("DATUMOJ_VECTORS", "off")


def run_import(root, deps, monkeypatch, name, release_id, released_at):
    monkeypatch.setattr(cli, "_build_wiring", lambda _root: deps)
    return CliRunner().invoke(cli.app, [
        "--data-dir", str(root), "observations", "import-release", "--source", SID, "--file", str(ARCHIVE / name),
        "--release-id", release_id, "--released-at", released_at])


def test_four_archive_releases_import_as_history_and_current_stays_api(tmp_path, monkeypatch):
    root, deps = build(tmp_path, SPEC)
    try:
        crawl_and_parse(deps, SPEC, routes_for(tmp_path))
        table = "WEO:GRC.GGXCNL_NGDP.A"
        watermark = deps.observations.table_order(SID, table)
        for name, release_id, released_at, _, _ in RELEASES:
            result = run_import(root, deps, monkeypatch, name, release_id, released_at)
            assert result.exit_code == 0, result.output
            report = json.loads(result.output)
            assert (report["release_id"], report["released_at"]) == (release_id, released_at)
        assert deps.observations.table_order(SID, table) == watermark  # history only, current not advanced
        current = deps.observations.query_readonly(
            f"SELECT release_id, value FROM observations WHERE table_key='{table}' AND time_label='2009'").rows
        assert current == [("WEO-2026-OCT", Decimal("-15.423608"))]

        manager = wiring._ReleaseStateManager(wiring_store(), root)
        manager.push()
        token = deps.observations.pin_generation()
        seen_sha = set()
        for _, release_id, released_at, code, value in RELEASES:
            sql = ("SELECT release_id, released_at, value, note, snapshot_sha256 FROM observations "
                   f"WHERE area_code='GRC' AND indicator_code='{code}' AND time_label='2009' AND unit='PT'")
            answer = deps.observations.query_readonly(sql, generation_token=token, as_of_release=release_id)
            (rid, released, got, note, sha), = answer.rows
            assert (rid, released, got) == (release_id, date.fromisoformat(released_at), Decimal(value))
            assert ("代码对应" in note) == code.startswith("GGB")
            seen_sha.add(sha)
        assert len(seen_sha) == 4
        gap = deps.observations.query_readonly(
            "SELECT value, unit FROM observations WHERE indicator_code='GGXCNL' AND time_label='2009'",
            generation_token=token, as_of_release="WEO-2010-APR")
        assert gap.rows == [(Decimal(-30557000000), "XDC")]
        legacy = deps.observations.query_readonly(
            "SELECT value FROM observations WHERE indicator_code='GGB' AND time_label='2009'",
            generation_token=token, as_of_release="WEO-2009-OCT")
        assert legacy.rows == [(Decimal(-15757000000),)]
        assert all(p.release_id == "WEO-2009-OCT" for p in legacy.provenance)
    finally:
        close(deps)


def wiring_store():
    from datumoj.rikolto.testing.fakes import FakeStateObjectStore

    return FakeStateObjectStore()


def test_unrecognised_file_is_rejected(tmp_path, monkeypatch):
    root, deps = build(tmp_path, SPEC)
    try:
        bogus = tmp_path / "other.xls"
        bogus.write_bytes(b"not\ta\tweo\tfile\r\n1\t2\t3\t4\r\n")
        monkeypatch.setattr(cli, "_build_wiring", lambda _root: deps)
        result = CliRunner().invoke(cli.app, [
            "--data-dir", str(root), "observations", "import-release", "--source", SID, "--file", str(bogus),
            "--release-id", "X", "--released-at", "2010-04-21"])
        assert result.exit_code != 0
        assert deps.observations.query_readonly("SELECT count(*) FROM observations").rows == [(0,)]
    finally:
        close(deps)
