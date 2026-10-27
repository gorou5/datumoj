"""WISTERIA-801 offline audit of persistent intake and consumer isolation."""

import hashlib
import json
from pathlib import Path

import pytest

from datumoj.eltiro import models as m
from datumoj.eltiro.intake import IntakeService, load_consumers
from datumoj.eltiro.registry_store import SQLiteDemandRegistry
from datumoj.eltiro.testing.contract_suites import DemandRegistrySuite
from datumoj.eltiro.testing.samples import make_demand
from datumoj.rikolto.testing.fakes import FakeRegistry
from datumoj.rikolto.testing.samples import make_spec

SAMPLES = Path(__file__).resolve().parents[2] / "docs/plans/0.4-wisteria-samples"
SOURCES = ["eu-eurostat", "intl-imf-weo", "intl-oecd", "intl-wdi"]


class TestPersistentRegistryAudit(DemandRegistrySuite):
    @pytest.fixture
    def make_registry(self, tmp_path):
        stores = []

        def factory():
            store = SQLiteDemandRegistry(tmp_path / f"registry-{len(stores)}.sqlite3")
            stores.append(store)
            return store

        yield factory
        for store in stores:
            store.close()


def consumer_config(consumer="example-consumer", **changes):
    return {
        "id": consumer, "inbox": f"consumers/{consumer}/inbox", "outbox": f"consumers/{consumer}/outbox",
        "allowed_sources": SOURCES, "accept_outflow": ["raw_ok", "derived_only", "no_outflow"],
        "models": {"consumer": consumer, "authorized_sources": SOURCES, "max_tier": "medium",
                   "allow_cloud": True, "allowed_providers": ["none", "ollama", "cloud_api"]},
        "max_active_demands": 2, "max_calls_per_run": 200, **changes,
    }


def write_config(root, consumers):
    (root / "eltiro").mkdir(exist_ok=True)
    (root / "eltiro/consumers.yaml").write_text(json.dumps({"consumers": consumers}), encoding="utf-8")


@pytest.fixture
def env(tmp_path):
    write_config(tmp_path, [consumer_config(), consumer_config("another-consumer")])
    sources = FakeRegistry([make_spec(source) for source in SOURCES])
    with SQLiteDemandRegistry(tmp_path / "eltiro/demands.sqlite3") as registry:
        service = IntakeService(tmp_path, registry, sources)
        inbox = tmp_path / service.consumer("example-consumer").inbox
        inbox.mkdir(parents=True)
        yield tmp_path, registry, service, inbox


def put(inbox, name="request.yaml", **changes):
    content = make_demand(**changes).model_dump_json(by_alias=True).encode()
    path = inbox / name
    path.write_bytes(content)
    return path, content


def records(root):
    receipts = [json.loads(path.read_text()) for path in (root / "consumers/example-consumer/outbox").glob("*.json")]
    audit = [json.loads(line) for line in (root / "eltiro/audit.jsonl").read_text().splitlines()]
    return receipts, audit


def assert_result(env, expected, content=None):
    root, registry, service, _inbox = env
    counts = service.scan()
    assert counts == {"accepted": int(expected == "accepted"), "rejected": int(expected != "accepted")}
    receipts, audit = records(root)
    assert len(receipts) == len(audit) == 1
    receipt = receipts[0]
    assert receipt["reason_code"] == audit[0]["reason_code"] == expected
    assert receipt["conclusion"] == audit[0]["conclusion"]
    if content is not None:
        assert receipt["file_sha256"] == audit[0]["file_sha256"] == hashlib.sha256(content).hexdigest()
    assert set(audit[0]) == {"time", "consumer", "file_sha256", "conclusion", "reason_code"}
    if expected != "accepted":
        assert registry.list_active() == []
    return receipt


def test_two_unmodified_samples_register_with_accepted_receipts(tmp_path):
    write_config(tmp_path, [consumer_config("example-analysis-site")])
    with SQLiteDemandRegistry(tmp_path / "eltiro/demands.sqlite3") as registry:
        service = IntakeService(tmp_path, registry, FakeRegistry([make_spec(source) for source in SOURCES]))
        inbox = tmp_path / service.config.consumers[0].inbox
        inbox.mkdir(parents=True)
        for path in SAMPLES.glob("*.yaml"):
            (inbox / path.name).write_bytes(path.read_bytes())
        assert service.scan() == {"accepted": 2, "rejected": 0}
        assert len(registry.list_active()) == 2
        for path in SAMPLES.glob("*.yaml"):
            demand = m.load_demand(path)
            assert registry.get(demand.id, 1) == demand
            assert len(list((inbox / "processed").glob(f"*/{path.name}"))) == 1
        receipts = list((tmp_path / service.config.consumers[0].outbox).glob("*.json"))
        assert len(receipts) == 2
        for path in receipts:
            assert json.loads(path.read_text())["conclusion"] == "accepted"
            assert "purpose" not in path.read_text()
        assert len((tmp_path / "eltiro/audit.jsonl").read_text().splitlines()) == 2


def test_consumer_mismatch_does_not_expose_foreign_identity(env):
    _, content = put(env[3], consumer="another-consumer", id="private-demand", purpose="PRIVATE CONTENT")
    receipt = assert_result(env, "consumer_mismatch", content)
    assert receipt["demand_id"] is None and receipt["version"] is None
    serialized = json.dumps(records(env[0]))
    for secret in ("another-consumer", "private-demand", "PRIVATE CONTENT"):
        assert secret not in serialized
    assert list((env[0] / "consumers/another-consumer/outbox").glob("*.json")) == []


@pytest.mark.parametrize("source", ["intl-unknown", "intl-denied"])
def test_unknown_or_unauthorized_source_rejected(env, source):
    env[2].sources = FakeRegistry([make_spec("intl-denied")])
    _, content = put(env[3], indicators=[{"key": "gdp", "concept": "GDP", "preferred_sources": [source]}])
    assert_result(env, "source_forbidden", content)


def test_license_acceptance_cannot_be_widened(env):
    root, registry, service, inbox = env
    write_config(root, [consumer_config(accept_outflow=["derived_only", "no_outflow"])])
    env = root, registry, IntakeService(root, registry, service.sources), inbox
    _, content = put(inbox, accept_outflow=["raw_ok"])
    assert_result(env, "outflow_forbidden", content)


@pytest.mark.parametrize("models", [
    {"max_tier": "high"}, {"max_calls_per_run": 201},
    {"allow_cloud": True},
])
def test_model_settings_cannot_be_widened(env, models):
    root, registry, service, inbox = env
    config = consumer_config()
    config["models"]["allow_cloud"] = False
    write_config(root, [config])
    env = root, registry, IntakeService(root, registry, service.sources), inbox
    _, content = put(inbox, models=models)
    assert_result(env, "model_forbidden", content)


def test_active_limit_and_new_version_replacement(env):
    root, registry, service, inbox = env
    write_config(root, [consumer_config(max_active_demands=1)])
    service = IntakeService(root, registry, service.sources)
    put(inbox)
    assert service.scan()["accepted"] == 1
    put(inbox, version=2)
    assert service.scan()["accepted"] == 1
    assert [d.version for d in registry.list_active()] == [2]
    put(inbox, id="over-limit")
    assert service.scan()["rejected"] == 1
    receipts, audit = records(root)
    assert any(r["reason_code"] == "active_limit" for r in receipts)
    assert audit[-1]["reason_code"] == "active_limit"
    assert registry.get("over-limit", 1) is None


def test_size_is_checked_before_parsing_and_hash_is_of_entire_file(env, monkeypatch):
    content = b"unparsed synthetic input\n" * (m.MAX_FILE_BYTES // 10)
    (env[3] / "large.yaml").write_bytes(content)

    def no_parse(_):
        pytest.fail("oversized input reached parser")

    monkeypatch.setattr("datumoj.eltiro.intake.load_demand", no_parse)
    assert_result(env, "file_too_large", content)


def test_symlink_is_rejected_without_reading_or_changing_target(env, monkeypatch):
    target = env[0] / "target.yaml"
    content = b"PRIVATE TARGET CONTENT"
    target.write_bytes(content)
    link = env[3] / "link.yaml"
    link.symlink_to(target)
    original_open = __import__("os").open

    def guarded_open(path, flags, *args, **kwargs):
        assert path != "link.yaml", "symlink must never be opened"
        return original_open(path, flags, *args, **kwargs)

    monkeypatch.setattr("datumoj.eltiro.intake.os.open", guarded_open)
    assert_result(env, "symlink", str(target).encode())
    assert target.read_bytes() == content
    assert not link.is_symlink()
    assert len(list((env[3] / "rejected").glob("*/link.yaml"))) == 1


def test_unknown_fields_have_sanitized_receipt_and_audit(env):
    data = make_demand(purpose="PRIVATE REQUEST CONTENT").model_dump(mode="json", by_alias=True)
    data["execute"] = "PRIVATE UNKNOWN FIELD"
    content = json.dumps(data).encode()
    (env[3] / "unknown.json").write_bytes(content)
    receipt = assert_result(env, "invalid_demand", content)
    assert (receipt["demand_id"], receipt["version"]) == ("example-demand", 1)
    assert "PRIVATE" not in json.dumps(records(env[0]))
    assert "execute" not in json.dumps(records(env[0]))


def test_version_is_immutable_and_audit_is_append_only(env):
    root, registry, service, inbox = env
    put(inbox, purpose="ORIGINAL REQUEST CONTENT")
    assert service.scan()["accepted"] == 1
    prefix = (root / "eltiro/audit.jsonl").read_bytes()
    put(inbox, purpose="CHANGED REQUEST CONTENT")
    assert service.scan()["rejected"] == 1
    assert registry.get("example-demand", 1).purpose == "ORIGINAL REQUEST CONTENT"
    assert (root / "eltiro/audit.jsonl").read_bytes().startswith(prefix)
    receipts, audit = records(root)
    assert audit[-1]["reason_code"] == "version_conflict"
    assert any(r["reason_code"] == "version_conflict" for r in receipts)
    assert "REQUEST CONTENT" not in json.dumps((receipts, audit))
    assert len(list((inbox / "processed").glob("*/request.yaml"))) == 1
    assert len(list((inbox / "rejected").glob("*/request.yaml"))) == 1


@pytest.mark.parametrize("kind", ["escape", "absolute", "overlap", "same", "symlink", "internal", "unknown"])
def test_invalid_consumer_directories_or_configuration_rejected(tmp_path, kind):
    root = tmp_path / "data"
    root.mkdir()
    one, two = consumer_config(), consumer_config("another-consumer")
    if kind == "escape":
        one["inbox"] = "../outside"
    elif kind == "absolute":
        one["inbox"] = str(tmp_path / "outside")
    elif kind == "overlap":
        two["outbox"] = one["inbox"] + "/nested"
    elif kind == "same":
        one["outbox"] = one["inbox"]
    elif kind == "symlink":
        (root / "alias").symlink_to(tmp_path, target_is_directory=True)
        one["inbox"] = "alias/outside"
    elif kind == "internal":
        one["inbox"] = "eltiro"
    else:
        one["password"] = "synthetic-forbidden-field"
    write_config(root, [one, two])
    with pytest.raises(ValueError, match="invalid consumer configuration"):
        load_consumers(root)
