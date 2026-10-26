"""Operation parameter boundaries, profile evidence and output provenance."""

import pytest
from pydantic import TypeAdapter, ValidationError

from datumoj.eltiro import models as m
from datumoj.eltiro.testing.samples import make_demand, make_match, make_profile

OPERATION = TypeAdapter(m.Operation)


@pytest.mark.parametrize("op,params", [
    ("rescale", {"input": "gdp", "target_scale": 1}),
    ("diff", {"left": "gdp", "right": "other", "sign": "plus"}),
    ("diff_lag", {"input": "gdp", "lag": 1}),
    ("ratio", {"left": "gdp", "right": "other"}),
    ("growth_rate", {"input": "gdp", "lag": 1}),
    ("source_spread", {"input": "gdp"}),
    ("revision_diff", {"input": "gdp"}),
    ("mirror_diff", {"left": "gdp", "right": "other"}),
    ("residual", {"left": "gdp", "right": "other"}),
    ("moving_average", {"input": "gdp", "window": 12}),
    ("lead_lag_corr", {"left": "gdp", "right": "other", "lag": -1, "window": 12}),
])
def test_fixed_operation_catalog(op, params):
    operation = OPERATION.validate_python({"id": "result", "op": op, **params})
    assert operation.op == op
    with pytest.raises(ValidationError):
        OPERATION.validate_python({**operation.model_dump(), "script": "run()"})


@pytest.mark.parametrize("params", [
    {"op": "rescale", "input": "gdp", "target_scale": 0},
    {"op": "rescale", "input": "gdp", "target_scale": -1},
    {"op": "rescale", "input": "gdp", "target_scale": float("inf")},
    {"op": "rescale", "input": "gdp", "target_scale": True},
    {"op": "diff_lag", "input": "gdp", "lag": 0},
    {"op": "diff_lag", "input": "gdp", "lag": m.MAX_LAG + 1},
    {"op": "diff_lag", "input": "gdp", "lag": True},
    {"op": "growth_rate", "input": "gdp", "lag": -1},
    {"op": "diff", "left": "gdp", "right": "gdp", "sign": "eval"},
    {"op": "moving_average", "input": "gdp", "window": 1},
    {"op": "moving_average", "input": "gdp", "window": m.MAX_WINDOW + 1},
    {"op": "lead_lag_corr", "left": "gdp", "right": "gdp", "lag": -m.MAX_LAG - 1, "window": 2},
    {"op": "ratio", "left": "gdp"},
    {"op": "residual", "left": "gdp", "right": "gdp", "formula": "execute()"},
])
def test_operation_specific_parameters(params):
    with pytest.raises(ValidationError):
        OPERATION.validate_python({"id": "result", **params})


def test_profiles_preserve_unknown_scale_and_have_no_license_copy():
    profile = make_profile()
    assert profile.scale.multiplier is None
    assert profile.model_generated_fields == []
    for availability in ("observations", "catalog_only", "document_only"):
        assert make_profile(availability=availability).availability == availability
    assert make_profile(scale={"multiplier": 1e9, "evidence": "Source metadata: billions"}).scale.multiplier == 1e9
    with pytest.raises(ValidationError):
        make_profile(scale={"multiplier": 1})
    with pytest.raises(ValidationError):
        make_profile(license={"outflow": "raw_ok"})
    with pytest.raises(ValidationError):
        make_profile(time_start="2024-01-01", time_end="2000-01-01")
    with pytest.raises(ValidationError):
        make_profile(model_generated_fields=["series.source_id"])
    with pytest.raises(ValidationError):
        make_profile(names={"en": "x" * (m.MAX_LABEL_LENGTH + 1)})


def manifest_data():
    return {
        "demand_id": "example-demand", "demand_version": 1, "generation": 1,
        "generation_token": "opaque token, format is not specified here", "run_at": "2026-10-27T00:00:00Z",
        "files": [{"name": "data.parquet", "sha256": "cd" * 32}],
        "citation_index": {"c1": {"inputs": [
            {"source_id": "intl-wdi", "table_key": "first", "obs_id": "same-id", "snapshot_sha256": "ab" * 32,
             "release_id": "opaque first release"},
            {"source_id": "intl-oecd", "table_key": "second", "obs_id": "same-id", "snapshot_sha256": "ef" * 32,
             "release_id": "opaque second release"},
        ], "operation_chain": [{"id": "difference", "op": "diff", "left": "wdi", "right": "oecd"}]}},
    }


def test_manifest_preserves_full_input_identities_and_chain():
    data = manifest_data()
    manifest = m.OutputManifest.model_validate(data)
    assert manifest.license_summary.outflow == "no_outflow"
    assert len(manifest.citation_index["c1"].inputs) == 2
    assert manifest.citation_index["c1"].operation_chain[0].op == "diff"
    assert m.OutputManifest.model_validate_json(manifest.model_dump_json()) == manifest
    data["models"] = [{"provider": "example-provider", "model": "example-model", "version": "v1", "tier": "low"}]
    data["license_summary"] = {"outflow": "derived_only", "by_source": {
        "intl-wdi": "raw_ok", "intl-oecd": "derived_only",
    }}
    assert m.OutputManifest.model_validate(data).models[0].tier == "low"
    data["license_summary"]["by_source"].pop("intl-oecd")
    with pytest.raises(ValidationError):
        m.OutputManifest.model_validate(data)


def test_manifest_can_record_a_run_with_no_observations():
    data = manifest_data()
    data["files"] = []
    data["citation_index"] = {}
    manifest = m.OutputManifest.model_validate(data)
    assert manifest.files == [] and manifest.license_summary.outflow == "no_outflow"


def test_period_includes_the_entire_end_month_or_year():
    for end in ("2024", "2024-02", "2024-02-29"):
        assert make_demand(period={"from": "2024-02-29", "to": end, "frequency": "daily"}).period.end == end
    with pytest.raises(ValidationError):
        make_demand(period={"from": "2024-03-01", "to": "2024-02", "frequency": "daily"})


@pytest.mark.parametrize("filename", ["../data.csv", "/data.csv", "raw.txt", "x;run.csv", "a.csv/other.csv"])
def test_manifest_file_names_cannot_escape_output_or_attach_raw(filename):
    data = manifest_data()
    data["files"][0]["name"] = filename
    with pytest.raises(ValidationError):
        m.OutputManifest.model_validate(data)


@pytest.mark.parametrize("field", ["source_id", "table_key", "obs_id", "snapshot_sha256", "release_id"])
def test_manifest_requires_complete_provenance(field):
    data = manifest_data()
    del data["citation_index"]["c1"]["inputs"][0][field]
    with pytest.raises(ValidationError):
        m.OutputManifest.model_validate(data)


def test_manifest_rejects_raw_text_bad_digest_duplicate_files_and_naive_time():
    data = manifest_data()
    data["citation_index"]["c1"]["inputs"][0]["text"] = "raw source content"
    with pytest.raises(ValidationError):
        m.OutputManifest.model_validate(data)
    data = manifest_data()
    data["files"][0]["sha256"] = "bad"
    with pytest.raises(ValidationError):
        m.OutputManifest.model_validate(data)
    data = manifest_data()
    data["files"] *= 2
    with pytest.raises(ValidationError):
        m.OutputManifest.model_validate(data)
    data = manifest_data()
    data["run_at"] = "2026-10-27T00:00:00"
    with pytest.raises(ValidationError):
        m.OutputManifest.model_validate(data)


def test_budget_period_and_source_validation():
    for changes in ({"max_calls_per_run": m.MAX_CALLS_PER_RUN + 1}, {"max_tier": "unlimited"}):
        with pytest.raises(ValidationError):
            make_demand(models=changes)
    for start, end in (("2024", "2000"), ("2024-13", "2025"), ("yesterday", "today")):
        with pytest.raises(ValidationError):
            make_demand(period={"from": start, "to": end, "frequency": "annual"})
    with pytest.raises(ValidationError):
        make_demand(indicators=[{"key": "gdp", "concept": "GDP", "preferred_sources": ["World Bank"]}])
    with pytest.raises(ValidationError):
        make_match(auto_bind=True)
    with pytest.raises(ValidationError):
        make_match(confidence=float("nan"))
