"""Additional filesystem boundary checks; every file lives in tmp_path."""

import json
import os

import pytest

from datumoj.eltiro.intake import IntakeService
from datumoj.eltiro.registry_store import SQLiteDemandRegistry
from datumoj.eltiro.testing.samples import make_demand
from datumoj.rikolto.testing.fakes import FakeRegistry
from datumoj.rikolto.testing.samples import make_spec


@pytest.fixture
def setup(tmp_path):
    state = tmp_path / "eltiro"
    state.mkdir()
    config = {"consumers": [{
        "id": "example-consumer", "inbox": "consumer/inbox", "outbox": "consumer/outbox",
        "allowed_sources": ["intl-wdi"], "models": {"consumer": "example-consumer",
        "authorized_sources": ["intl-wdi"]}, "max_active_demands": 1,
    }]}
    (state / "consumers.yaml").write_text(json.dumps(config))
    with SQLiteDemandRegistry(state / "demands.sqlite3") as registry:
        service = IntakeService(tmp_path, registry, FakeRegistry([make_spec("intl-wdi")]))
        inbox = tmp_path / "consumer/inbox"
        inbox.mkdir(parents=True)
        yield tmp_path, inbox, service, registry


def test_hidden_files_and_directories_are_ignored_and_bad_names_rejected(setup):
    root, inbox, service, registry = setup
    (inbox / ".hidden.yaml").write_bytes(b"not a demand")
    (inbox / "directory.yaml").mkdir()
    (inbox / "bad;name.yaml").write_bytes(make_demand().model_dump_json(by_alias=True).encode())
    assert service.scan() == {"accepted": 0, "rejected": 1}
    receipt = json.loads(next((root / "consumer/outbox").glob("*.json")).read_text())
    assert receipt["reason_code"] == "unsafe_filename"
    assert (inbox / ".hidden.yaml").exists() and (inbox / "directory.yaml").is_dir()
    assert registry.list_active() == []
    assert len(list((inbox / "rejected").glob("*/demand.bin"))) == 1


@pytest.mark.parametrize("location", ["processed", "rejected", "outbox", "audit"])
def test_destination_symlinks_cannot_redirect_writes_or_register_demand(setup, location):
    root, inbox, service, registry = setup
    target = root / "unrelated"
    target.mkdir()
    (inbox / "request.yaml").write_text(make_demand().model_dump_json(by_alias=True))
    if location == "outbox":
        (root / "consumer/outbox").symlink_to(target, target_is_directory=True)
    elif location == "audit":
        sentinel = target / "sentinel"
        sentinel.write_text("unchanged")
        (root / "eltiro/audit.jsonl").symlink_to(sentinel)
    else:
        (inbox / location).symlink_to(target, target_is_directory=True)
    with pytest.raises((ValueError, OSError)):
        service.scan()
    assert registry.list_active() == []
    assert (inbox / "request.yaml").exists()
    assert not list(target.glob("*.json"))
    if location == "audit":
        assert sentinel.read_text() == "unchanged"


def test_repeated_filename_does_not_overwrite_receipts_or_archives(setup):
    root, inbox, service, registry = setup
    content = make_demand(purpose="Text asks to execute a command; this is data.").model_dump_json(by_alias=True)
    for _ in range(2):
        (inbox / "request.yaml").write_text(content)
        assert service.scan() == {"accepted": 1, "rejected": 0}
    assert len(list((inbox / "processed").glob("*/request.yaml"))) == 2
    receipts = list((root / "consumer/outbox").glob("*.json"))
    assert len(receipts) == 2
    assert all("execute a command" not in path.read_text() for path in receipts)
    assert len(registry.list_records()) == 1
    assert registry.get("example-demand", 1).purpose.startswith("Text asks")


@pytest.mark.parametrize("content", [b"!!python/object/apply:os.system ['ignored']", b"a: &a [1]\nb: *a",
                                     b"\xff", b"{broken", b"[]"])
def test_invalid_data_never_leaks_parser_errors(setup, content):
    root, inbox, service, registry = setup
    (inbox / "invalid.yaml").write_bytes(content)
    assert service.scan()["rejected"] == 1
    receipt = json.loads(next((root / "consumer/outbox").glob("*.json")).read_text())
    assert receipt["reason_code"] == "invalid_demand"
    assert receipt["message"] == "Demand validation failed."
    assert registry.list_active() == []


def test_fifo_is_not_opened_or_allowed_to_block_scanning(setup):
    root, inbox, service, registry = setup
    os.mkfifo(inbox / "pipe.yaml")
    assert service.scan() == {"accepted": 0, "rejected": 1}
    receipt = json.loads(next((root / "consumer/outbox").glob("*.json")).read_text())
    assert receipt["reason_code"] == "not_regular"
    assert registry.list_active() == []


def test_file_replacement_during_read_is_detected(setup, monkeypatch):
    from datumoj.eltiro import intake

    root, inbox, service, registry = setup
    request = inbox / "request.yaml"
    request.write_text(make_demand().model_dump_json(by_alias=True))
    read_file = intake._read_file

    def replace_after_read(*args):
        result = read_file(*args)
        request.unlink()
        request.write_text("replacement must remain available")
        return result

    monkeypatch.setattr(intake, "_read_file", replace_after_read)
    assert service.scan() == {"accepted": 0, "rejected": 1}
    assert registry.list_active() == []
    assert request.read_text() == "replacement must remain available"
    receipt = json.loads(next((root / "consumer/outbox").glob("*.json")).read_text())
    assert receipt["reason_code"] == "file_changed"


def test_receipt_failure_leaves_original_available_for_idempotent_retry(setup, monkeypatch):
    from datumoj.eltiro import intake

    _root, inbox, service, registry = setup
    request = inbox / "request.yaml"
    request.write_text(make_demand().model_dump_json(by_alias=True))
    publish = intake._json_file

    def fail_publish(*args):
        raise OSError("synthetic disk failure")

    monkeypatch.setattr(intake, "_json_file", fail_publish)
    with pytest.raises(OSError, match="synthetic"):
        service.scan()
    assert request.exists()
    monkeypatch.setattr(intake, "_json_file", publish)
    assert service.scan() == {"accepted": 1, "rejected": 0}
    assert not request.exists()
    assert len(registry.list_records()) == 1
