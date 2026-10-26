"""Synthetic small contracts for implementers; no external accounts or data."""

from datumoj.eltiro.models import DemandSpec, MatchRecord, SeriesIdentity, SeriesProfile


def make_demand(**changes) -> DemandSpec:
    return DemandSpec.model_validate({
        "schema": "eltiro.request/v0", "id": "example-demand", "version": 1, "consumer": "example-consumer",
        "purpose": "Compare annual GDP", "source_case": "synthetic contract example",
        "entities": {"countries": ["JPN"]},
        "period": {"from": "2000", "to": "2024", "frequency": "annual"}, "revisions": "latest",
        "indicators": [{"key": "gdp", "concept": "Nominal GDP", "unit": "USD"}],
        "align": {"keys": ["country", "year"], "require_same_unit": True},
        "output": {"fields": ["country", "year", "gdp", "citation_id"], "format": ["parquet"]},
        **changes,
    })


def make_series(**changes) -> SeriesIdentity:
    return SeriesIdentity.model_validate({
        "source_id": "intl-wdi", "table_key": "gdp", "indicator": "Nominal GDP",
        "indicator_code": "NY.GDP.MKTP.CD", "fixed_dimensions": {"valuation": "current"}, **changes,
    })


def make_profile(**changes) -> SeriesProfile:
    return SeriesProfile.model_validate({
        "series": make_series(), "names": {"en": "Nominal GDP", "ja": "名目GDP"},
        "frequency": "annual", "availability": "observations", "generation": 1, **changes,
    })


def make_match(**changes) -> MatchRecord:
    return MatchRecord.model_validate({
        "demand_id": "example-demand", "demand_version": 1, "indicator_key": "gdp", "series": make_series(),
        "tier": "deterministic", "confidence": 1, "reason": "Source concept identifier agrees",
        "evidence": [{"profile_field": "concept_ids", "source_id": "intl-wdi",
                      "snapshot_sha256": "ab" * 32, "locator": "row=1"}], "generation": 1, **changes,
    })
