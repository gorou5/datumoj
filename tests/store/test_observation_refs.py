from datumoj.core.models import ObservationRef
from datumoj.store.observations import DuckDBObservationStore
from datumoj.testing.samples import make_observation


def _store_with_collision(tmp_path):
    store = DuckDBObservationStore(tmp_path / "observations")
    store.write_table("jp-stat", "table-a", [make_observation("shared", "1", table_key="table-a")])
    store.write_table("jp-stat", "table-b", [
        make_observation("shared", "2", table_key="table-b"),
        make_observation("unique", "3", table_key="table-b"),
    ])
    return store


def test_three_reference_columns_resolve_each_table(tmp_path):
    store = _store_with_collision(tmp_path)

    result = store.query_readonly(
        "SELECT source_id, table_key, obs_id, value FROM observations ORDER BY table_key, obs_id"
    )

    assert [(row[1], row[2]) for row in result.rows] == [
        ("table-a", "shared"), ("table-b", "shared"), ("table-b", "unique"),
    ]
    assert [(obs.table_key, obs.obs_id) for obs in result.provenance] == [
        ("table-a", "shared"), ("table-b", "shared"), ("table-b", "unique"),
    ]
    assert result.warnings == []


def test_obs_id_only_omits_ambiguous_provenance_and_warns(tmp_path):
    store = _store_with_collision(tmp_path)

    result = store.query_readonly("SELECT obs_id FROM observations ORDER BY obs_id, table_key")

    assert result.provenance == [make_observation("unique", "3", table_key="table-b")]
    assert len(result.warnings) == 1
    assert "shared" in result.warnings[0]
    assert all(field in result.warnings[0] for field in ("source_id", "table_key", "obs_id"))


def test_unique_obs_id_only_still_resolves(tmp_path):
    store = _store_with_collision(tmp_path)

    result = store.query_readonly("SELECT obs_id FROM observations WHERE obs_id = 'unique'")

    assert [(obs.table_key, obs.obs_id) for obs in result.provenance] == [("table-b", "unique")]
    assert result.warnings == []


def test_observation_ref_roundtrips_special_characters():
    ref = ObservationRef(source_id="jp-stat", table_key='日本語|;=\\\n', obs_id='same:"|;=\\\n')

    assert ObservationRef.parse(str(ref)) == ref
    assert str(ObservationRef.parse(str(ref))) == str(ref)
