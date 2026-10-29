"""Persistent router qualification and local configuration boundary tests."""

import json

import pytest

from datumoj.eltiro.models import MAX_FILE_BYTES, ConsumersConfig
from datumoj.eltiro.routing import SQLiteModelRouter, load_routing
from datumoj.eltiro.testing.contract_suites import ModelRouterSuite
from datumoj.eltiro.testing.samples import make_consumer, make_routing_policy


def consumers_config(cap=1000):
    return ConsumersConfig.model_validate({"consumers": [
        {"id": name, "inbox": f"{name}/inbox", "outbox": f"{name}/outbox",
         "allowed_sources": ["jp-one", "jp-two"], "models": make_consumer(consumer=name),
         "max_active_demands": 1, "max_calls_per_run": cap}
        for name in ("example-consumer", "another-consumer")
    ]})


class TestSQLiteRouter(ModelRouterSuite):
    @pytest.fixture
    def make_router(self, tmp_path):
        opened = []

        def make():
            router = SQLiteModelRouter(tmp_path / str(len(opened)), consumers=consumers_config())
            opened.append(router)
            return router

        yield make
        for router in opened:
            router.close()


def test_load_routing(tmp_path):
    state = tmp_path / "eltiro"
    state.mkdir()
    policy = make_routing_policy()
    (state / "routing.yaml").write_text(policy.model_dump_json())
    assert load_routing(tmp_path) == policy


@pytest.mark.parametrize("content", [b"{broken", b"\xff", b"[]", b"x: &x [1]\ny: *x",
                                     b"!!python/object/apply:os.system ['ignored']"])
def test_invalid_yaml_is_sanitized(tmp_path, content):
    state = tmp_path / "eltiro"
    state.mkdir()
    (state / "routing.yaml").write_bytes(content)
    with pytest.raises(ValueError, match="^invalid routing configuration$"):
        load_routing(tmp_path)


@pytest.mark.parametrize("where", ["root", "target"])
def test_unknown_config_fields_rejected(tmp_path, where):
    data = make_routing_policy().model_dump()
    if where == "root":
        data["endpoint"] = "https://example.invalid"
    else:
        data["routes"]["match_judge"]["high"]["endpoint"] = "https://example.invalid"
    state = tmp_path / "eltiro"
    state.mkdir()
    (state / "routing.yaml").write_text(json.dumps(data))
    with pytest.raises(ValueError, match="^invalid routing configuration$"):
        load_routing(tmp_path)


def test_size_checked_before_yaml_parse(tmp_path, monkeypatch):
    state = tmp_path / "eltiro"
    state.mkdir()
    (state / "routing.yaml").write_bytes(b" " * (MAX_FILE_BYTES + 1))
    monkeypatch.setattr("datumoj.eltiro.routing.yaml.safe_load", lambda *_: pytest.fail("must not parse"))
    with pytest.raises(ValueError):
        load_routing(tmp_path)


def test_missing_and_symlink_routing_rejected(tmp_path):
    with pytest.raises(ValueError):
        load_routing(tmp_path)
    state = tmp_path / "eltiro"
    state.mkdir()
    target = tmp_path / "config"
    target.write_text(make_routing_policy().model_dump_json())
    (state / "routing.yaml").symlink_to(target)
    with pytest.raises(ValueError):
        load_routing(tmp_path)
