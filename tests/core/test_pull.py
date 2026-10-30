"""Offline R2 pulls: SHA reuse, verified swaps and read-only local copies."""

from __future__ import annotations

import json
import os
from pathlib import Path
from threading import Event, Thread
from types import SimpleNamespace

import boto3
import pytest
from moto import mock_aws
from typer.testing import CliRunner

from datumoj.rikolto import cli, mcp_server, wiring
from datumoj.rikolto.core import pull as pull_module
from datumoj.rikolto.core.models import StateWriter
from datumoj.rikolto.core.pull import PullError, pull
from datumoj.rikolto.core.state import StateManager
from datumoj.rikolto.core.state_s3 import S3StateObjectStore
from datumoj.rikolto.core.storage import StorageConfig
from datumoj.rikolto.testing.fakes import FakeParser, FakeRegistry
from datumoj.rikolto.testing.samples import T0, make_snapshot, make_spec

_BUCKET = "synthetic-pull-bucket"
_ENDPOINT = "https://s3.amazonaws.com"
_ACCESS = "synthetic-pull-access"
_SECRET = "synthetic-pull-secret"


@pytest.fixture
def remote(monkeypatch):
    monkeypatch.setenv("R2_ACCESS_KEY_ID", _ACCESS)
    monkeypatch.setenv("R2_SECRET_ACCESS_KEY", _SECRET)
    monkeypatch.setenv("R2_ENDPOINT", _ENDPOINT)
    monkeypatch.setenv("DATUMOJ_S3_BUCKET", _BUCKET)
    monkeypatch.setenv("DATUMOJ_S3_PREFIX", "synthetic/pull")
    monkeypatch.setenv("DATUMOJ_S3_REGION", "us-east-1")
    with mock_aws():
        client = boto3.client(
            "s3", region_name="us-east-1", aws_access_key_id="testing", aws_secret_access_key="testing",
        )
        client.create_bucket(Bucket=_BUCKET)
        config = StorageConfig(
            s3_endpoint=_ENDPOINT, s3_bucket=_BUCKET, s3_prefix="synthetic/pull", s3_region="us-east-1",
        )
        store = S3StateObjectStore(config)
        yield store
        store._client.close()
        client.close()


class ObservedStore:
    def __init__(self, store):
        self.store = store
        self.content_gets: list[str] = []
        self.fail_key: str | None = None

    def get(self, key):
        if key.startswith(("copies/sha256/", "raw/")):
            self.content_gets.append(key)
            if key == self.fail_key:
                raise OSError(f"{_ACCESS} {_SECRET} {_ENDPOINT} {_BUCKET}")
        return self.store.get(key)

    def list(self, prefix=""):
        return self.store.list(prefix)

    def put(self, key, data):
        return self.store.put(key, data)

    def put_if_absent(self, key, data):
        return self.store.put_if_absent(key, data)

    def put_if_match(self, key, data, etag):
        return self.store.put_if_match(key, data, etag)

    def delete_if_match(self, key, etag):
        return self.store.delete_if_match(key, etag)

    def exists(self, key):
        return self.store.exists(key)


def _local_deps(root):
    specs = [make_spec("jp-a", kind="fake"), make_spec("jp-b", kind="fake")]
    return wiring.build(root, {
        "registry": FakeRegistry(specs), "vectors": None,
        "parsers": {("fake", "text/plain"): FakeParser},
    })


def _add_snapshot(deps, source_id, url, body):
    data = body.encode()
    snapshot = make_snapshot(source_id, url, data, fetched_at=T0)
    deps.raw.put(data)
    deps.manifest.record_snapshot(snapshot)
    return snapshot


def _build_local(root, *, add_second_a=False):
    deps = _local_deps(root)
    snapshots = []
    for source_id, body in (("jp-a", "A 的公开数据"), ("jp-b", "B 的公开数据")):
        snapshots.append(_add_snapshot(deps, source_id, f"https://example.go.jp/{source_id}.txt", body))
    if add_second_a:
        snapshots.append(_add_snapshot(deps, "jp-a", "https://example.go.jp/jp-a-new.txt", "A 的更新数据"))
    assert set(deps.parse_pending(deps.pipeline_deps).parsed) == {snap.snapshot_id for snap in snapshots}
    return deps, snapshots


def _extend_local(root, url, body):
    deps = _local_deps(root)
    snapshot = _add_snapshot(deps, "jp-a", url, body)
    assert deps.parse_pending(deps.pipeline_deps).parsed == [snapshot.snapshot_id]
    return deps, snapshot


def _close(deps):
    for name in ("manifest", "chunks", "keyword", "catalog", "change_log"):
        value = getattr(deps, name, None)
        if value is not None:
            value.close()


def _push(root, store):
    return StateManager(store, root, writer=StateWriter(kind="actions", run_id="synthetic")).push()


def _content_keys(generation):
    return {copy.key for copy in generation.stores}


def _tree_bytes(root):
    return {path.relative_to(root).as_posix(): path.read_bytes()
            for path in root.rglob("*") if path.is_file()}


def test_first_pull_and_unchanged_pull_reuse_all_objects(remote, tmp_path, monkeypatch, caplog):
    source = tmp_path / "source"
    deps, snapshots = _build_local(source, add_second_a=True)
    generation = _push(source, remote)
    _close(deps)
    observed = ObservedStore(remote)
    target = tmp_path / "local-copy"

    first = pull(target, store=observed)
    assert first["generation"] == generation.generation
    assert first["verified"] is True
    assert first["downloaded_objects"] > 0
    marker = json.loads((target / "state-pull.json").read_text())
    assert marker["generation"] == generation.generation
    assert marker["writer"]["kind"] == "actions"
    assert marker["last_successful_runs"] == {
        source_id: run.model_dump(mode="json")
        for source_id, run in generation.last_successful_runs.items()
    }
    assert marker["pulled_at"] == first["pulled_at"]
    assert first["pulled_at"].endswith("+00:00") or first["pulled_at"].endswith("Z")
    raw = snapshots[0]
    assert (target / "raw" / raw.sha256[:2] / raw.sha256).exists()
    observed.content_gets.clear()

    second = pull(target, store=observed)
    assert observed.content_gets == [], observed.content_gets
    assert second["downloaded_objects"] == 0
    assert (target.with_name("local-copy.previous") / "state-pull.json").exists()
    for _ in range(3):
        observed.content_gets.clear()
        assert pull(target, store=observed)["downloaded_objects"] == 0
        assert observed.content_gets == []

    result = CliRunner().invoke(cli.app, ["pull", "--data-dir", str(target)])
    assert result.exit_code == 0, result.output
    selected = CliRunner().invoke(
        cli.app, ["pull", "--data-dir", str(target), "--generation", str(generation.generation)],
    )
    assert selected.exit_code == 0, selected.output
    assert json.loads(selected.output)["generation"] == generation.generation
    output = result.output + selected.output + caplog.text
    assert all(secret not in output for secret in (_ACCESS, _SECRET, _ENDPOINT, _BUCKET))


def test_pull_downloads_only_objects_missing_after_one_source_changes(remote, tmp_path):
    source = tmp_path / "source"
    deps, _ = _build_local(source)
    first_generation = _push(source, remote)
    _close(deps)
    target = tmp_path / "local-copy"
    observed = ObservedStore(remote)
    pull(target, store=observed)

    deps, changed = _extend_local(source, "https://example.go.jp/jp-a-new.txt", "A 的更新数据")
    second_generation = _push(source, remote)
    _close(deps)
    observed.content_gets.clear()
    second = pull(target, store=observed)

    expected = _content_keys(second_generation) - _content_keys(first_generation)
    expected.add(f"raw/{changed.sha256[:2]}/{changed.sha256}")
    assert set(observed.content_gets) == expected, set(observed.content_gets) - expected
    assert second["downloaded_objects"] == len(expected)
    assert second["generation"] == first_generation.generation + 1
    assert (target / "state-pull.json").exists()

    observed.content_gets.clear()
    older = pull(target, first_generation.generation, store=observed)
    assert older["generation"] == first_generation.generation
    assert older["downloaded_objects"] == 0
    assert observed.content_gets == []


def test_mid_pull_and_hash_validation_failures_preserve_current_directory(remote, tmp_path):
    source = tmp_path / "source"
    deps, _ = _build_local(source)
    first_generation = _push(source, remote)
    _close(deps)
    target = tmp_path / "local-copy"
    pull(target, store=remote)

    deps, _ = _extend_local(source, "https://example.go.jp/jp-a-new.txt", "A 的更新数据")
    second_generation = _push(source, remote)
    _close(deps)
    new_copy_key = next(key for key in _content_keys(second_generation) if key not in _content_keys(first_generation))
    before = _tree_bytes(target)
    observed = ObservedStore(remote)
    observed.fail_key = new_copy_key
    with pytest.raises(PullError):
        pull(target, store=observed)
    assert _tree_bytes(target) == before

    observed.fail_key = None
    # A different source body creates a new immutable copy key for the next generation.
    third_deps, _snap = _extend_local(source, "https://example.go.jp/jp-a-third.txt", "A 的第三次更新")
    third_generation = _push(source, remote)
    _close(third_deps)
    corrupt_key = next(key for key in _content_keys(third_generation) if key not in _content_keys(second_generation))
    remote._client.put_object(Bucket=remote._bucket, Key=remote._key(corrupt_key), Body=b"corrupt")
    before = _tree_bytes(target)
    with pytest.raises(PullError):
        pull(target, store=remote)
    assert _tree_bytes(target) == before


def test_install_rolls_back_if_previous_directory_cannot_be_installed(tmp_path, monkeypatch):
    target = tmp_path / "active"
    previous = tmp_path / "active.previous"
    temporary = tmp_path / "pull-stage"
    stage = temporary / "data"
    target.mkdir()
    previous.mkdir()
    stage.mkdir(parents=True)
    (target / "value").write_text("active", encoding="utf-8")
    (previous / "value").write_text("previous", encoding="utf-8")
    (stage / "value").write_text("new", encoding="utf-8")

    original_replace = pull_module.os.replace

    def fail_stage_install(source, destination):
        if Path(source) == stage and Path(destination) == previous:
            raise OSError("synthetic rename failure")
        return original_replace(source, destination)

    monkeypatch.setattr(pull_module.os, "replace", fail_stage_install)
    with pytest.raises(OSError, match="synthetic rename failure"):
        pull_module._install(stage, target, temporary)

    assert (target / "value").read_text(encoding="utf-8") == "active"
    assert (previous / "value").read_text(encoding="utf-8") == "previous"
    assert (stage / "value").read_text(encoding="utf-8") == "new"


def test_pulled_replica_rejects_schedule_writes_and_state_push(remote, tmp_path, monkeypatch):
    source = tmp_path / "source"
    deps, _ = _build_local(source)
    _push(source, remote)
    _close(deps)
    target = tmp_path / "local-copy"
    pull(target, store=remote)

    monkeypatch.setattr(cli.wiring, "build", lambda *_: pytest.fail("write built against pulled replica"))
    runner = CliRunner()
    for args in (
        ["schedule", "tick"],
        ["schedule", "run-now", "jp-a"],
        ["schedule", "enable", "jp-a"],
        ["schedule", "disable", "jp-a"],
        ["schedule", "master", "on"],
    ):
        result = runner.invoke(cli.app, ["--data-dir", str(target), *args])
        assert result.exit_code == 2
        assert "只读副本" in result.output

    result = runner.invoke(cli.app, ["state", "push", "--data-dir", str(target)])
    assert result.exit_code == 1
    assert "只读副本" in result.output


def test_pulled_replica_blocks_other_local_writers_before_build(tmp_path, monkeypatch):
    target = tmp_path / "local-copy"
    target.mkdir()
    (target / "state-pull.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(cli.wiring, "build", lambda *_: pytest.fail("wiring ran before read-only gate"))

    result = CliRunner().invoke(cli.app, ["--data-dir", str(target), "crawl", "jp-a"])
    assert result.exit_code == 1
    assert "只读副本" in result.output


def test_writer_waits_for_pull_switch_then_rechecks_read_only_marker(tmp_path, monkeypatch):
    target = tmp_path / "local-copy"
    target.mkdir()
    locked, release, finished = Event(), Event(), Event()
    results = []

    def pull_switch():
        with cli.scheduling.workspace_lock(target):
            locked.set()
            assert release.wait(2)
            (target / "state-pull.json").write_text("{}", encoding="utf-8")

    pull_thread = Thread(target=pull_switch)
    pull_thread.start()
    assert locked.wait(2)
    monkeypatch.setattr(cli.wiring, "build", lambda *_: pytest.fail("write built against pulled replica"))

    def run_writer():
        results.append(CliRunner().invoke(cli.app, ["--data-dir", str(target), "crawl", "jp-a"]))
        finished.set()

    writer_thread = Thread(target=run_writer)
    writer_thread.start()
    assert not finished.wait(0.05)
    release.set()
    pull_thread.join(2)
    writer_thread.join(2)

    assert not pull_thread.is_alive() and not writer_thread.is_alive()
    assert results[0].exit_code == 1
    assert "只读副本" in results[0].output


def test_workspace_lock_allows_nested_local_calls(tmp_path):
    with cli.scheduling.workspace_lock(tmp_path), cli.scheduling.workspace_lock(tmp_path):
        assert not cli.scheduling.is_pulled_replica(tmp_path)


def test_failed_pull_command_and_logs_hide_remote_configuration(remote, tmp_path, monkeypatch, caplog):
    source = tmp_path / "source"
    deps, _ = _build_local(source)
    generation = _push(source, remote)
    _close(deps)
    observed = ObservedStore(remote)
    observed.fail_key = generation.stores[0].key

    monkeypatch.setattr(cli, "pull_state", lambda data_dir, gen: pull(data_dir, gen, store=observed))
    result = CliRunner().invoke(cli.app, ["pull", "--data-dir", str(tmp_path / "local-copy")])
    assert result.exit_code == 1
    output = result.output + caplog.text
    assert all(secret not in output for secret in (_ACCESS, _SECRET, _ENDPOINT, _BUCKET))


def test_search_json_includes_local_generation_state(tmp_path, monkeypatch):
    tools = SimpleNamespace(search_docs=lambda *args, **kwargs: {"data": {"hits": []}})
    deps = SimpleNamespace(data_dir=tmp_path, tools=tools, require=lambda _: None)
    monkeypatch.setattr(cli.wiring, "build", lambda _: deps)
    result = CliRunner().invoke(cli.app, ["search", "人口統計", "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["data_generation"] is None
    assert payload["data_generated_at"] is None
    assert payload["data_origin"] == "本机自建数据"


def test_mcp_command_passes_cli_data_dir_to_server_factory(tmp_path, monkeypatch):
    seen = []
    monkeypatch.setenv("DATUMOJ_DATA_DIR", "prior-data-dir")
    monkeypatch.setattr(mcp_server, "main", lambda: seen.append(os.environ["DATUMOJ_DATA_DIR"]))

    result = CliRunner().invoke(cli.app, ["--data-dir", str(tmp_path), "mcp"])

    assert result.exit_code == 0, result.output
    assert seen == [str(tmp_path)]
    assert os.environ["DATUMOJ_DATA_DIR"] == "prior-data-dir"
