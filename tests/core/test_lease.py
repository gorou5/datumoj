"""Offline lease races, expiry, publication fencing and CLI redaction."""

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier

import boto3
import pytest
from moto import mock_aws
from typer.testing import CliRunner

from datumoj.rikolto import cli
from datumoj.rikolto.core.lease import LEASE_KEY, LeaseError, LeaseManager
from datumoj.rikolto.core.manifest import SqliteManifest
from datumoj.rikolto.core.state import StateError, StateManager
from datumoj.rikolto.core.state_s3 import S3StateObjectStore
from datumoj.rikolto.core.storage import StorageConfig
from datumoj.rikolto.testing.contract_suites import StateObjectStoreSuite
from datumoj.rikolto.testing.fakes import FakeStateObjectStore
from datumoj.rikolto.testing.samples import T0

SECRETS = ["synthetic-lease-id", "synthetic-lease-secret", "https://s3.amazonaws.com", "synthetic-lease-bucket"]


@pytest.fixture(params=["memory", "s3"])
def store(request, monkeypatch):
    if request.param == "memory":
        yield FakeStateObjectStore()
        return
    monkeypatch.setenv("R2_ACCESS_KEY_ID", SECRETS[0])
    monkeypatch.setenv("R2_SECRET_ACCESS_KEY", SECRETS[1])
    with mock_aws():
        client = boto3.client("s3", region_name="us-east-1", aws_access_key_id="testing", aws_secret_access_key="testing")
        client.create_bucket(Bucket=SECRETS[3])
        adapter = S3StateObjectStore(StorageConfig(s3_endpoint=SECRETS[2], s3_bucket=SECRETS[3],
                                                  s3_region="us-east-1", s3_prefix="state"))
        yield adapter
        adapter._client.close()
        client.close()


class TestLeaseStoreContract(StateObjectStoreSuite):
    @pytest.fixture
    def state_store(self, store):
        return store


@pytest.fixture
def leases(store):
    clock = [T0]
    return LeaseManager(store, now=lambda: clock[0], ttl=timedelta(minutes=3)), clock


def test_competing_acquisition(leases):
    manager, _ = leases
    barrier = Barrier(2)

    def acquire(holder):
        barrier.wait()
        try:
            return manager.acquire(holder)
        except LeaseError:
            return None

    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(acquire, ["local/one", "actions/two"]))
    winners = [lease for lease in results if lease]
    assert len(winners) == 1
    manager.check(winners[0])


def test_nas_holder_and_existing_holder_validation(leases):
    manager, _ = leases
    lease = manager.acquire("nas/run-117")
    assert lease.holder == "nas/run-117"
    manager.release(lease.holder, lease.token)
    for holder in ("nas/", "unknown/run", "nas/run\x01bad"):
        with pytest.raises(LeaseError):
            manager.acquire(holder)


def test_renew_release_require_owner_and_preserve_identity(leases, store):
    manager, clock = leases
    lease = manager.acquire("local/one")
    original = store.get(LEASE_KEY)
    clock[0] += timedelta(minutes=1)
    for holder, token in [("actions/two", lease.token), (lease.holder, "wrong")]:
        with pytest.raises(LeaseError):
            manager.renew(holder, token)
        with pytest.raises(LeaseError):
            manager.release(holder, token)
        assert store.get(LEASE_KEY) == original
    renewed = manager.renew(lease.holder, lease.token)
    assert renewed.holder == lease.holder and renewed.token == lease.token
    assert renewed.acquired_at == lease.acquired_at and renewed.expires_at > lease.expires_at
    manager.release(renewed.holder, renewed.token)
    assert manager.status() == {"held": False}
    with pytest.raises(LeaseError):
        manager.release(lease.holder, lease.token)


def test_force_only_after_expiry_and_old_owner_cannot_revive_or_release(leases, store):
    manager, clock = leases
    lease = manager.acquire("local/one")
    original = store.get(LEASE_KEY)
    for force in (False, True):
        with pytest.raises(LeaseError):
            manager.acquire("actions/two", force=force)
        assert store.get(LEASE_KEY) == original
    clock[0] = lease.expires_at
    assert manager.status()["active"] is False
    with pytest.raises(LeaseError):
        manager.renew(lease.holder, lease.token)
    with pytest.raises(LeaseError):
        manager.acquire("actions/two")
    successor = manager.acquire("actions/two", force=True)
    assert successor.token != lease.token and successor.acquired_at == clock[0]
    for action in (manager.renew, manager.release):
        with pytest.raises(LeaseError):
            action(lease.holder, lease.token)
    manager.check(successor)


@pytest.mark.parametrize("operation", ["takeover", "renew", "release"])
def test_cas_race_never_changes_successor(leases, store, monkeypatch, operation):
    manager, clock = leases
    lease = manager.acquire("local/one")
    if operation == "takeover":
        clock[0] = lease.expires_at
    else:
        clock[0] += timedelta(seconds=1)
    original_put = store.put_if_match
    original_delete = store.delete_if_match
    successor = lease.model_copy(update={"holder": "actions/new", "token": "new-random-token"})

    def race(key, *args):
        current = store.get(key)
        original_put(key, successor.model_dump_json().encode(), current[1])
        if operation == "release":
            return original_delete(key, *args)
        return original_put(key, *args)

    monkeypatch.setattr(store, "delete_if_match" if operation == "release" else "put_if_match", race)
    with pytest.raises(LeaseError):
        if operation == "takeover":
            manager.acquire("actions/two", force=True)
        else:
            getattr(manager, operation)(lease.holder, lease.token)
    assert json.loads(store.get(LEASE_KEY)[0])["token"] == successor.token


@pytest.mark.parametrize("bad", [b"{", b"{}", b'{"holder":"local/a","token":"x","acquired_at":"bad"}'])
def test_corrupt_lease_force_fails_closed(leases, store, bad):
    manager, _ = leases
    store.put_if_absent(LEASE_KEY, bad)
    with pytest.raises(LeaseError):
        manager.acquire("local/new", force=True)
    assert store.get(LEASE_KEY)[0] == bad


def test_clock_and_extension_validation(leases, store):
    manager, clock = leases
    lease = manager.acquire("local/one")
    with pytest.raises(LeaseError):
        manager.renew(lease.holder, lease.token)
    clock[0] = T0.replace(tzinfo=None)
    with pytest.raises(LeaseError):
        manager.acquire("local/new", force=True)
    with pytest.raises(LeaseError):
        LeaseManager(store, ttl=timedelta(0))


@pytest.mark.parametrize("initial", [False, True])
@pytest.mark.parametrize("takeover", [False, True])
def test_publish_loses_lease_pointer_unchanged(leases, store, tmp_path, initial, takeover):
    manager, clock = leases
    SqliteManifest(tmp_path / "manifest.sqlite").close()
    state = StateManager(store, tmp_path)
    if initial:
        state.push()
    pointer = store.get("current.json")
    lease = manager.acquire("local/one")

    def lost():
        clock[0] = lease.expires_at
        if takeover:
            manager.acquire("actions/two", force=True)
        manager.check(lease)

    state.before_publish = lost
    with pytest.raises(StateError):
        state.push()
    assert store.get("current.json") == pointer


def test_no_subsequent_upload_after_expiry(leases, store, tmp_path, monkeypatch):
    manager, clock = leases
    SqliteManifest(tmp_path / "manifest.sqlite").close()
    state = StateManager(store, tmp_path)
    lease = manager.acquire("local/one")
    state.before_write = lambda: manager.check(lease)
    put = store.put_if_absent
    writes = []

    def expire(key, data):
        writes.append(key)
        result = put(key, data)
        clock[0] = lease.expires_at
        return result

    monkeypatch.setattr(store, "put_if_absent", expire)
    with pytest.raises(StateError):
        state.push()
    assert len(writes) == 1 and writes[0].startswith("copies/")
    assert store.get("current.json") is None


def test_cli_lease_lifecycle_and_push_require_token(leases, store, tmp_path, monkeypatch, caplog):
    manager, clock = leases
    SqliteManifest(tmp_path / "manifest.sqlite").close()
    state = StateManager(store, tmp_path)
    monkeypatch.setattr(cli.wiring, "build_state", lambda _: state)
    monkeypatch.setattr(cli, "LeaseManager", lambda _: manager)
    for name, value in zip(("R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY", "R2_ENDPOINT", "DATUMOJ_S3_BUCKET"), SECRETS):
        monkeypatch.setenv(name, value)
    monkeypatch.delenv("DATUMOJ_LEASE_TOKEN", raising=False)
    runner = CliRunner()
    outputs = []

    def invoke(*args, code=0):
        result = runner.invoke(cli.app, list(args))
        assert result.exit_code == code, result.output
        outputs.append(result.output)
        return json.loads(result.output)

    invoke("state", "push", code=1)
    assert store.list("") == []
    acquired = invoke("state", "lease", "acquire", "--holder", "local/one")
    token = acquired["token"]
    invoke("state", "lease", "acquire", "--force", code=1)
    assert invoke("state", "lease", "status")["active"]
    monkeypatch.setenv("DATUMOJ_LEASE_TOKEN", "wrong")
    invoke("state", "push", code=1)
    monkeypatch.setenv("DATUMOJ_LEASE_TOKEN", token)
    clock[0] += timedelta(seconds=1)
    assert invoke("state", "lease", "renew")["renewed"]
    assert invoke("state", "push")["generation"] == 1
    assert invoke("state", "lease", "release")["released"]
    assert invoke("state", "lease", "status") == {"held": False}
    assert token not in "".join(outputs[2:]) + caplog.text
    old = invoke("state", "lease", "acquire", "--holder", "local/old")
    clock[0] += manager.ttl
    replacement = invoke("state", "lease", "acquire", "--force", "--holder", "actions/new")
    assert replacement["token"] != old["token"]
    monkeypatch.setenv("DATUMOJ_LEASE_TOKEN", old["token"])
    invoke("state", "lease", "renew", code=1)
    invoke("state", "lease", "release", code=1)
    invoke("state", "push", code=1)
    assert all(secret not in "".join(outputs) + caplog.text for secret in SECRETS)


def test_cli_callback_detects_takeover_without_pointer_update(leases, store, tmp_path, monkeypatch):
    manager, clock = leases
    SqliteManifest(tmp_path / "manifest.sqlite").close()
    state = StateManager(store, tmp_path)
    state.push()
    pointer = store.get("current.json")
    owner = manager.acquire("local/one")
    monkeypatch.setenv("DATUMOJ_S3_BUCKET", SECRETS[3])
    monkeypatch.setenv("DATUMOJ_LEASE_TOKEN", owner.token)
    monkeypatch.setattr(cli.wiring, "build_state", lambda _: state)
    monkeypatch.setattr(cli, "LeaseManager", lambda _: manager)

    def takeover():
        clock[0] = owner.expires_at
        manager.acquire("actions/new", force=True)

    state.before_publish = takeover
    result = CliRunner().invoke(cli.app, ["state", "push"])
    assert result.exit_code == 1
    assert store.get("current.json") == pointer
    assert state.before_publish is takeover and state.before_write is None


def test_cli_errors_hide_untrusted_details(monkeypatch, caplog):
    def fail(_):
        raise RuntimeError(" ".join(SECRETS))

    monkeypatch.setattr(cli.wiring, "build_state", fail)
    runner = CliRunner()
    for args in [("lease", "acquire"), ("lease", "status"), ("lease", "renew"), ("lease", "release"), ("push",)]:
        result = runner.invoke(cli.app, ["state", *args])
        assert result.exit_code == 1 and "error" in json.loads(result.output)
        assert all(secret not in result.output + caplog.text for secret in SECRETS)


@pytest.mark.parametrize("lose", [False, True])
def test_heartbeat_renews_and_stops_without_sleep(leases, monkeypatch, lose):
    import datumoj.rikolto.core.lease as module

    manager, clock = leases
    lease = manager.acquire("local/one")
    clock[0] += timedelta(seconds=1)
    events = []

    class ImmediateThread:
        def __init__(self, *, target, **kwargs):
            self.target = target

        def start(self):
            self.target()

        def join(self):
            events.append("joined")

    class ControlledEvent:
        def __init__(self):
            self.set_value = False
            self.waits = 0

        def wait(self, timeout):
            self.waits += 1
            return self.waits > 1

        def set(self):
            self.set_value = True

        def is_set(self):
            return self.set_value

    monkeypatch.setattr(module, "Thread", ImmediateThread)
    monkeypatch.setattr(module, "Event", ControlledEvent)
    if lose:
        def fail(*args):
            raise OSError("offline failure")

        monkeypatch.setattr(manager, "renew", fail)
    with manager.maintain(lease) as check:
        if lose:
            with pytest.raises(LeaseError):
                check()
        else:
            check()
            assert manager._read()[0].expires_at > lease.expires_at
    assert events == ["joined"]


def test_io_errors_not_reported_as_competition(leases, store, monkeypatch):
    manager, _ = leases

    def fail(*args):
        raise OSError("offline transport failure")

    monkeypatch.setattr(store, "put_if_absent", fail)
    with pytest.raises(OSError):
        manager.acquire("local/one")
