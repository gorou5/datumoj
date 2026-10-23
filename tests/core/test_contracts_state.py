"""MAPLE-001：状态代次模型、Schema 和状态对象存储合格测试（全部离线）。"""

import json
from datetime import timedelta
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, FormatChecker
from pydantic import ValidationError

from datumoj.core.contracts import ContractError, PreconditionFailed
from datumoj.core.models import StateGeneration, StoreCopy, WriteLease
from datumoj.testing.contract_suites import StateObjectStoreSuite
from datumoj.testing.fakes import FakeStateObjectStore
from datumoj.testing.samples import T0, sha


class TestFakeStateObjectStore(StateObjectStoreSuite):
    @pytest.fixture
    def state_store(self):
        return FakeStateObjectStore()


@pytest.fixture
def generation_data():
    return {
        "generation": 1,
        "previous_generation": None,
        "created_at": T0,
        "writer": {"kind": "actions", "run_id": "run-1"},
        "stores": [{"name": "manifest", "key": f"copies/sha256/{sha(b'copy')}",
                    "sha256": sha(b"copy"), "size_bytes": 4, "format": "sqlite"}],
        "raw_prefix": "raw/",
        "vector_coverage": {"model": "test-model", "version": "1", "dim": 3,
                            "embedded_chunks": 0, "pending_chunks": 2},
        "last_successful_runs": {"jp-test": {"run_id": "run-1", "finished_at": T0 - timedelta(seconds=1)}},
    }


@pytest.fixture
def lease_data():
    return {"holder": "actions/run-1", "token": "unique-lease-token", "acquired_at": T0,
            "expires_at": T0 + timedelta(minutes=5)}


def test_json_roundtrip_and_committed_schemas(generation_data, lease_data):
    models = [StateGeneration(**generation_data), StoreCopy(**generation_data["stores"][0]), WriteLease(**lease_data)]
    for model in models:
        encoded = model.model_dump_json()
        assert type(model).model_validate_json(encoded) == model
        path = Path(__file__).resolve().parents[2] / "schemas" / f"{type(model).__name__}.schema.json"
        schema = json.loads(path.read_text(encoding="utf-8"))
        assert schema == type(model).model_json_schema()
        Draft202012Validator.check_schema(schema)
        Draft202012Validator(schema, format_checker=FormatChecker()).validate(json.loads(encoded))
        with pytest.raises(ValidationError):
            type(model).model_validate(model.model_dump() | {"unknown": 1})
        with pytest.raises(ValidationError):
            model.unknown = 1
    assert issubclass(PreconditionFailed, ContractError)


@pytest.mark.parametrize(("generation", "previous"), [(1, None), (2, 1), (999, 998)])
def test_valid_generation_chain(generation_data, generation, previous):
    model = StateGeneration(**(generation_data | {"generation": generation, "previous_generation": previous}))
    assert model.generation == generation


@pytest.mark.parametrize(("generation", "previous"), [
    (0, None), (-1, None), (True, None), (1.5, None), ("1", None),
    (1, 0), (1, 1), (2, None), (2, 0), (2, 2), (3, 1), (2, True),
])
def test_invalid_generation_chain(generation_data, generation, previous):
    with pytest.raises(ValidationError):
        StateGeneration(**(generation_data | {"generation": generation, "previous_generation": previous}))


def test_optional_vectors_and_no_successful_runs(generation_data):
    model = StateGeneration(**(generation_data | {"writer": {"kind": "local", "run_id": "local-1"},
                                                "vector_coverage": None, "last_successful_runs": {}}))
    assert StateGeneration.model_validate_json(model.model_dump_json()) == model


@pytest.mark.parametrize("field", ["dim", "embedded_chunks", "pending_chunks"])
@pytest.mark.parametrize("value", [-1, True, 1.5, "2"])
def test_vector_coverage_strict_nonnegative(generation_data, field, value):
    generation_data["vector_coverage"][field] = value
    with pytest.raises(ValidationError):
        StateGeneration(**generation_data)


def test_vector_dimension_cannot_be_zero(generation_data):
    generation_data["vector_coverage"]["dim"] = 0
    with pytest.raises(ValidationError):
        StateGeneration(**generation_data)


@pytest.mark.parametrize("field", ["created_at", "finished_at"])
def test_generation_times_must_be_aware(generation_data, field):
    if field == "created_at":
        generation_data[field] = T0.replace(tzinfo=None)
    else:
        generation_data["last_successful_runs"]["jp-test"][field] = T0.replace(tzinfo=None)
    with pytest.raises(ValidationError):
        StateGeneration(**generation_data)


def test_run_cannot_finish_after_generation(generation_data):
    generation_data["last_successful_runs"]["jp-test"]["finished_at"] = T0 + timedelta(seconds=1)
    with pytest.raises(ValidationError):
        StateGeneration(**generation_data)
    generation_data["last_successful_runs"]["jp-test"]["finished_at"] = T0
    assert StateGeneration(**generation_data).created_at == T0


@pytest.mark.parametrize("field", ["acquired_at", "expires_at"])
def test_lease_times_must_be_aware(lease_data, field):
    lease_data[field] = T0.replace(tzinfo=None)
    with pytest.raises(ValidationError):
        WriteLease(**lease_data)


@pytest.mark.parametrize("offset", [-1, 0, 1])
def test_lease_time_order(lease_data, offset):
    lease_data["expires_at"] = T0 + timedelta(seconds=offset)
    if offset <= 0:
        with pytest.raises(ValidationError):
            WriteLease(**lease_data)
    else:
        assert WriteLease(**lease_data).expires_at > T0


@pytest.mark.parametrize("field", ["holder", "token"])
def test_lease_identity_cannot_be_blank(lease_data, field):
    lease_data[field] = " "
    with pytest.raises(ValidationError):
        WriteLease(**lease_data)


@pytest.mark.parametrize("size", [-1, True, 1.5, "4"])
def test_copy_size_strict_nonnegative(generation_data, size):
    with pytest.raises(ValidationError):
        StoreCopy(**(generation_data["stores"][0] | {"size_bytes": size}))


@pytest.mark.parametrize("update", [{"sha256": "bad"}, {"name": " "}, {"key": ""}, {"format": ""}])
def test_copy_identity_validation(generation_data, update):
    with pytest.raises(ValidationError):
        StoreCopy(**(generation_data["stores"][0] | update))


def test_zero_byte_copy_is_valid(generation_data):
    assert StoreCopy(**(generation_data["stores"][0] | {"size_bytes": 0})).size_bytes == 0


@pytest.mark.parametrize("stores", [[], "duplicate"])
def test_generation_requires_distinct_copies(generation_data, stores):
    generation_data["stores"] = generation_data["stores"] * 2 if stores == "duplicate" else stores
    with pytest.raises(ValidationError):
        StateGeneration(**generation_data)


@pytest.mark.parametrize("writer", [{"kind": "other", "run_id": "run-1"}, {"kind": "local", "run_id": ""},
                                   {"kind": "local", "run_id": "run-1", "unknown": True}])
def test_writer_validation(generation_data, writer):
    with pytest.raises(ValidationError):
        StateGeneration(**(generation_data | {"writer": writer}))


def test_source_id_validation(generation_data):
    generation_data["last_successful_runs"] = {"invalid": {"run_id": "run-1", "finished_at": T0}}
    with pytest.raises(ValidationError):
        StateGeneration(**generation_data)
