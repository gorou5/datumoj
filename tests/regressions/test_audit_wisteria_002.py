"""WISTERIA-002：发布历史与固定代次的离线审核回归。"""

import json

import pytest

from datumoj.rikolto.core.contracts import (
    OBSERVATION_COLUMNS,
    OBSERVATION_RELEASE_COLUMNS,
    ParseResult,
    QueryResult,
)
from datumoj.rikolto.core.models import (
    EvidenceState,
    GenerationToken,
    Observation,
    ObservationIdentity,
    ObservationRowEvidence,
)
from datumoj.rikolto.testing.contract_suites import ObservationReleaseSuite
from datumoj.rikolto.testing.fakes import FakeObservationStore
from datumoj.rikolto.testing.samples import make_observation


class TestObservationReleases(ObservationReleaseSuite):
    @pytest.fixture
    def store(self):
        return FakeObservationStore()

    @pytest.fixture
    def publish(self):
        return lambda store, manifest: store.publish_generation(manifest)

    @pytest.fixture
    def discard(self):
        return lambda store, generation: store.discard_generation(generation)


def test_optional_release_fields_and_full_identity_do_not_collapse_versions():
    old = make_observation('same:|;="行', "1")
    assert old.release_id is None and old.released_at is None
    first = make_observation(old.obs_id, "2", release_id="first")
    second = make_observation(old.obs_id, "3", release_id="second")
    other_table = make_observation(old.obs_id, "4", table_key="t2")
    other_snapshot = make_observation(old.obs_id, "5", snapshot_sha256="ef" * 32)
    observations = [old, first, second, other_table, other_snapshot]
    assert len({o.identity for o in observations}) == len(observations)
    assert ParseResult(observations=observations).observations == observations
    for observation in observations:
        identity = observation.identity
        assert ObservationIdentity.parse(str(identity)) == identity
        assert json.loads(str(identity)) == [
            observation.source_id, observation.table_key, observation.obs_id,
            observation.snapshot_sha256, observation.release_id,
        ]
    with pytest.raises(ValueError):
        ParseResult(observations=[first, first])


def test_query_row_metadata_cannot_silently_change_generation():
    token = str(GenerationToken(generation=1, manifest_sha256="ab" * 32))
    kwargs = {"columns": ["count"], "rows": [(1,)], "generation_token": token,
              "evidence_state": EvidenceState.unavailable, "evidence_inputs": {"as_of_release": "first"}}
    with pytest.raises(ValueError):
        QueryResult(**kwargs)
    with pytest.raises(ValueError):
        QueryResult(**kwargs, row_evidence=[ObservationRowEvidence(generation=2)])
    result = QueryResult(**kwargs, row_evidence=[ObservationRowEvidence(generation=1)])
    assert QueryResult.model_validate_json(result.model_dump_json()) == result


def test_legacy_column_contract_does_not_require_release_columns_in_old_parquet():
    assert "release_id" not in OBSERVATION_COLUMNS and "released_at" not in OBSERVATION_COLUMNS
    assert set(OBSERVATION_RELEASE_COLUMNS) == set(Observation.model_fields)
    assert {name: OBSERVATION_RELEASE_COLUMNS[name] for name in OBSERVATION_COLUMNS} == OBSERVATION_COLUMNS
