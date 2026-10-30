"""Configurable state: unchanged S3 default and offline directory publication/pull."""

import hashlib
import json
import sqlite3

import pytest

from datumoj.rikolto import cloud, wiring
from datumoj.rikolto.core.contracts import PreconditionFailed
from datumoj.rikolto.core.lease import LeaseError, remote_configured
from datumoj.rikolto.core.pull import pull
from datumoj.rikolto.core.schedule import Scheduler
from datumoj.rikolto.core.state_fs import DirStateObjectStore
from datumoj.rikolto.core.state_s3 import S3StateObjectStore
from datumoj.rikolto.core.storage import build_state_store, load_storage_config
from datumoj.rikolto.testing.fakes import (
    FakeCrawler,
    FakeHttpFetcher,
    FakeParser,
    FakeRegistry,
    FakeResponse,
)
from datumoj.rikolto.testing.samples import make_spec


@pytest.fixture(autouse=True)
def isolated_environment(monkeypatch):
    import os

    for name in tuple(os.environ):
        if name.startswith(("DATUMOJ_", "R2_", "GITHUB_RUN_ID")):
            monkeypatch.delenv(name)


def test_default_factory_is_s3(tmp_path, monkeypatch):
    monkeypatch.setenv("DATUMOJ_S3_ENDPOINT", "https://s3.example.invalid")
    monkeypatch.setenv("DATUMOJ_S3_BUCKET", "synthetic-bucket")
    monkeypatch.setenv("DATUMOJ_S3_ACCESS_KEY_ID", "synthetic-access")
    monkeypatch.setenv("DATUMOJ_S3_SECRET_ACCESS_KEY", "synthetic-secret")
    config = load_storage_config(data_dir=tmp_path / "data")
    assert config.state_backend == "s3" and config.state_dir is None
    assert isinstance(build_state_store(config), S3StateObjectStore)
    assert isinstance(wiring.build_state(tmp_path / "data").store, S3StateObjectStore)


def test_dir_cloud_publish_then_pull(tmp_path, monkeypatch):
    state, work, target = (tmp_path / name for name in ("state", "work", "replica"))
    monkeypatch.setenv("DATUMOJ_STATE_BACKEND", "dir")
    monkeypatch.setenv("DATUMOJ_STATE_DIR", str(state))
    monkeypatch.setenv("DATUMOJ_WRITER_KIND", "local")
    monkeypatch.setenv("DATUMOJ_VECTORS", "off")
    spec = make_spec("jp-dir", schedule="hourly", scope={"urls": ["https://example.test/source"]})
    body = b"Synthetic directory-backed source\n\nSecond paragraph"
    fetcher = FakeHttpFetcher({"https://example.test/source": FakeResponse(
        body=body, headers={"content-type": "text/plain"},
    )})
    original_build = wiring.build
    components = []

    def build(root):
        deps = original_build(root, {
            "registry": FakeRegistry([spec]), "crawlers": {"fake": FakeCrawler},
            "fetcher": fetcher, "vectors": None, "parsers": {("fake", "text/plain"): FakeParser},
        })
        components.append(deps)
        return deps

    monkeypatch.setattr(wiring, "build", build)
    assert cloud.check()["exit_status"] == 0
    assert remote_configured()
    result = cloud.run(work_dir=work)
    assert result["status"] == "completed", result
    assert result["generation"] == 1 and result["new"] == result["parsed"] == 1
    assert fetcher.requests == ["https://example.test/source"]
    remote = wiring.build_state(tmp_path / "unused").store
    assert isinstance(remote, DirStateObjectStore)
    assert remote.get("write-lease.json") is None
    manifest = json.loads(remote.get("generations/1.json")[0])
    assert manifest["writer"]["kind"] == "local"
    assert remote.get(result["weekly_report_key"]) is not None
    report = pull(target)
    assert report["generation"] == 1 and report["verified"]
    assert (target / "runs.jsonl").read_bytes() == (work / "runs.jsonl").read_bytes()
    sha = hashlib.sha256(body).hexdigest()
    assert (target / "raw" / sha[:2] / sha).read_bytes() == body
    for filename, table in (("manifest.sqlite", "snapshots"), ("chunks.sqlite", "chunks")):
        with sqlite3.connect(work / filename) as original, sqlite3.connect(target / filename) as replica:
            assert original.execute(f"SELECT * FROM {table} ORDER BY 1").fetchall() == replica.execute(
                f"SELECT * FROM {table} ORDER BY 1").fetchall()
    # Every restored file still verifies against the published generation.
    verified = wiring.build_state(tmp_path / "verify-source").restore(tmp_path / "verified")
    assert verified["generation"] == 1
    assert json.loads((target / "state-pull.json").read_bytes())["generation"] == 1
    scheduler = Scheduler(tmp_path / "scheduled", [spec], lambda source: None)
    leases = scheduler._remote_lease()
    assert isinstance(leases.store, DirStateObjectStore)
    held = leases.acquire("local/scheduled")
    with pytest.raises(LeaseError):
        leases.acquire("local/competing")
    leases.release(held.holder, held.token)
    etag = remote.put_if_absent("synthetic-cas", b"same")
    replacement = remote.put_if_match("synthetic-cas", b"same", etag)
    assert etag != replacement
    with pytest.raises(PreconditionFailed):
        remote.delete_if_match("synthetic-cas", etag)
