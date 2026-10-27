"""802 audit: availability, complete identities, incremental equality and read boundary."""

import pytest

from datumoj.eltiro.profile_store import SQLiteProfileStore
from datumoj.eltiro.profiles import generate_profiles
from datumoj.rikolto.testing.fakes import FakeCatalogStore, FakeChunkStore, FakeObservationStore, FakeRegistry
from datumoj.rikolto.testing.samples import make_chunk, make_dataset, make_document, make_observation, make_spec


class RecordingReads:
    """Every call is recorded; any non-read operation fails immediately."""

    def __init__(self, delegate, allowed):
        self.delegate, self.allowed, self.calls = delegate, allowed, []

    def __getattr__(self, name):
        def call(*args, **kwargs):
            self.calls.append((name, args, kwargs))
            assert name in self.allowed, f"Rikolto write/unsupported access: {name}"
            return getattr(self.delegate, name)(*args, **kwargs)
        return call


@pytest.fixture
def system(tmp_path):
    obs, catalog, chunks = FakeObservationStore(), FakeCatalogStore(), FakeChunkStore()
    row = make_observation("same-id", "1", indicator_code="POP", unit="千人")
    obs.write_table("jp-stat", "t1", [row])
    obs.write_table("jp-stat", "t2", [make_observation("same-id", "2", table_key="t2", indicator_code="POP")])
    catalog.upsert([make_dataset("jp-stat:pop", "Population"), make_dataset("jp-stat:metadata", "Catalog title")])
    for key, kind, lang, title in (("first", "law", "ja", "法令"), ("second", "law", "en", "Source law"),
                                    ("third", "pdf", "ja", "出典PDF")):
        document = make_document(f"jp-stat:{key}", "ab" * 32, title=title, kind=kind, lang=lang)
        chunk = make_chunk(document.doc_key, document.snapshot_sha256, "row=1", "source text", lang=lang)
        chunks.put_document(document, [chunk])
    readers = {
        "observations": RecordingReads(obs, {"describe", "query_readonly"}),
        "catalog": RecordingReads(catalog, {"search", "get_dataset", "resources_for"}),
        "chunks": RecordingReads(chunks, {"iter_chunks", "get_document", "chunks_for"}),
        "registry": RecordingReads(FakeRegistry([make_spec("jp-stat")]), {"get", "all"}),
    }
    with SQLiteProfileStore(tmp_path / "incremental") as store:
        yield {"readers": readers, "store": store, "obs": obs, "catalog": catalog, "chunks": chunks, "row": row}


def build(system, generation, previous=None, store=None):
    return generate_profiles(generation, **system["readers"], store=store or system["store"],
                             previous_generation=previous)


def assert_full_equal(system, tmp_path, generation, incremental):
    with SQLiteProfileStore(tmp_path / f"full-{generation}") as store:
        assert build(system, generation, store=store) == incremental


def detail_queries(system):
    return [args[0] for name, args, _ in system["readers"]["observations"].calls
            if name == "query_readonly" and args[0].startswith("SELECT *")]


def test_three_availabilities_and_same_obs_id_across_tables(system):
    profiles = build(system, 1)
    observations = [p for p in profiles if p.availability == "observations"]
    catalogs = [p for p in profiles if p.availability == "catalog_only"]
    documents = [p for p in profiles if p.availability == "document_only"]
    assert len(observations) == 2 and {p.series.table_key for p in observations} == {"t1", "t2"}
    assert len(catalogs) == 1 and catalogs[0].names == {"und": "Catalog title"}
    assert len(documents) == 2
    law = next(p for p in documents if p.series.indicator == "law")
    assert law.names == {"ja": "法令", "en": "Source law"}
    assert all(p.model_generated_fields == [] for p in profiles)


def test_scale_is_unconfirmed_without_source_evidence(system):
    system["catalog"].upsert([make_dataset("jp-stat:pop", "Population", extras={"scale": {"multiplier": 1000.0}})])
    profile = next(p for p in build(system, 1) if p.series.table_key == "t1")
    assert profile.unit == "千人"
    assert profile.scale.multiplier is None and profile.scale.evidence is None


def test_incremental_rebuilds_only_changed_table_and_equals_full(system, tmp_path):
    original = build(system, 1)
    system["obs"].write_table("jp-stat", "t2", [make_observation(
        "same-id", "3", table_key="t2", indicator_code="POP", snapshot_sha256="ef" * 32,
        dims={"valuation": "current"},
    )])
    system["readers"]["observations"].calls.clear()
    incremental = build(system, 2, previous=1)
    queries = detail_queries(system)
    assert len(queries) == 1 and "table_key = 't2'" in queries[0]
    assert [p.model_dump(exclude={"generation"}) for p in incremental if p.series.table_key == "t1"] == [
        p.model_dump(exclude={"generation"}) for p in original if p.series.table_key == "t1"]
    assert_full_equal(system, tmp_path, 2, incremental)
    system["readers"]["observations"].calls.clear()
    assert build(system, 3, previous=2)
    assert detail_queries(system) == []


def test_deleted_table_does_not_survive_and_empty_units_disappear(system, tmp_path):
    build(system, 1)
    system["obs"].write_table("jp-stat", "t2", [])
    incremental = build(system, 2, previous=1)
    assert all(p.series.table_key != "t2" for p in incremental)
    assert_full_equal(system, tmp_path, 2, incremental)
    # Also remove catalog and document units by supplying empty public read interfaces.
    system["readers"]["catalog"].delegate = FakeCatalogStore()
    system["readers"]["chunks"].delegate = FakeChunkStore()
    incremental = build(system, 3, previous=2)
    assert all(p.availability == "observations" for p in incremental)
    assert_full_equal(system, tmp_path, 3, incremental)


def test_reparsing_same_snapshot_and_catalog_metadata_changes_invalidate(system, tmp_path):
    build(system, 1)
    system["obs"].write_table("jp-stat", "t1", [make_observation(
        "same-id", "1", indicator_code="POP", unit="人", dims={"sex": "female"},
    )])
    system["catalog"].upsert([make_dataset("jp-stat:pop", "Population", description="New source description")])
    incremental = build(system, 2, previous=1)
    profile = next(p for p in incremental if p.series.table_key == "t1")
    assert profile.series.fixed_dimensions == {"sex": "female"} and profile.unit == "人"
    assert profile.descriptions == {"und": "New source description"}
    assert_full_equal(system, tmp_path, 2, incremental)


def test_changed_document_content_and_new_catalog_match_full(system, tmp_path):
    build(system, 1)
    document = make_document("jp-stat:first", "cd" * 32, title="変更後法令", kind="law")
    system["chunks"].put_document(document, [make_chunk(document.doc_key, "cd" * 32, "row=1", "changed body")])
    system["catalog"].upsert([make_dataset("jp-stat:new", "New dataset")])
    incremental = build(system, 2, previous=1)
    law = next(p for p in incremental if p.availability == "document_only" and p.series.indicator == "law")
    assert law.names["ja"] == "変更後法令"
    assert_full_equal(system, tmp_path, 2, incremental)


def test_generation_uses_only_rikolto_reads_and_never_changelog(system):
    build(system, 1)
    build(system, 2, previous=1)
    for reader in system["readers"].values():
        assert reader.calls
        assert all(name in reader.allowed for name, _, _ in reader.calls)
    # No ChangeLog or state-store object is even supplied to the generator.


def test_missing_previous_cache_and_sql_literals(system, tmp_path):
    row = make_observation("same-id", "1", table_key="t'quoted; --")
    system["obs"].write_table("jp-stat", row.table_key, [row])
    incremental = build(system, 2, previous=99)
    assert any(p.series.table_key == row.table_key for p in incremental)
    assert_full_equal(system, tmp_path, 2, incremental)


def test_dataset_moves_between_catalog_and_observations(system, tmp_path):
    build(system, 1)
    system["obs"].write_table("jp-stat", "t1", [])
    system["obs"].write_table("jp-stat", "t2", [])
    incremental = build(system, 2, previous=1)
    assert not any(p.availability == "observations" for p in incremental)
    assert sum(p.availability == "catalog_only" for p in incremental) == 2
    assert_full_equal(system, tmp_path, 2, incremental)
    system["obs"].write_table("jp-stat", "new-table", [make_observation("new", "1", table_key="new-table")])
    incremental = build(system, 3, previous=2)
    assert sum(p.availability == "catalog_only" for p in incremental) == 1
    assert sum(p.availability == "observations" for p in incremental) == 1
    assert_full_equal(system, tmp_path, 3, incremental)


def test_unchanged_catalog_and_document_units_are_copied(system, monkeypatch):
    from datumoj.eltiro import profiles

    original = build(system, 1)

    def unexpected_rebuild(*args):
        pytest.fail("unchanged unit was rebuilt")

    monkeypatch.setattr(profiles, "_catalog_profile", unexpected_rebuild)
    monkeypatch.setattr(profiles, "_document_profile", unexpected_rebuild)
    incremental = build(system, 2, previous=1)
    assert [p.model_dump(exclude={"generation"}) for p in incremental] == [
        p.model_dump(exclude={"generation"}) for p in original]
