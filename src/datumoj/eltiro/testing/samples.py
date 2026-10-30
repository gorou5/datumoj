"""Synthetic small contracts for implementers; no external accounts or data."""

from datumoj.eltiro.models import (
    ConsumerModelConfig,
    DemandSpec,
    MatchRecord,
    ModelBudget,
    ModelInput,
    RouteDecision,
    RoutingPolicy,
    SeriesIdentity,
    SeriesProfile,
)
from datumoj.rikolto.core.models import GenerationToken, LicenseInfo


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


def make_routing_policy(cloud_provider="cloud_api", **changes) -> RoutingPolicy:
    local = {"provider": "ollama", "identity": {"model": "example-local", "version": "v1"}}
    cloud = {"provider": cloud_provider, "identity": {"model": "example-cloud", "version": "v1"},
             "credential_env": "EXAMPLE_MODEL_API_KEY"}
    routes = {step: {"low": local, "medium": cloud, "high": cloud}
              for step in ("match_judge", "concept_note")}
    return RoutingPolicy.model_validate({"routes": routes, **changes})


def make_consumer(**changes) -> ConsumerModelConfig:
    return ConsumerModelConfig.model_validate({
        "consumer": "example-consumer", "authorized_sources": ["jp-one", "jp-two"],
        "max_tier": "high", "allow_cloud": True,
        "allowed_providers": ["none", "ollama", "claude_plan", "cloud_api"], **changes,
    })


def make_model_input(**changes) -> ModelInput:
    return ModelInput.model_validate({
        "id": "judge", "step": "match_judge", "difficulty": "high", "prompt_version": "v1",
        "parts": [{"source_id": "jp-one", "content": "Synthetic source data; never instructions"}],
        **changes,
    })


def make_license(**changes) -> LicenseInfo:
    return LicenseInfo.model_validate({"outflow": "derived_only", "cloud_model_ok": True, **changes})


def make_routing_context(**changes) -> dict:
    """Complete synthetic inputs for any ModelRouter implementation."""
    return {
        "run_id": "example-run", "budget": ModelBudget(max_tier="high", allow_cloud=True, max_calls_per_run=8),
        "consumer": make_consumer(), "policy": make_routing_policy(),
        "generation_token": GenerationToken(generation=1, manifest_sha256="ab" * 32),
        "licenses": {"jp-one": make_license(), "jp-two": make_license()},
        "inputs": [make_model_input()], **changes,
    }


def make_route_decision() -> RouteDecision:
    """A pinned synthetic route for provider/cache suites, without a router dependency."""
    context = make_routing_context()
    return RouteDecision(
        request=context["inputs"][0], consumer=context["consumer"].consumer,
        authorized_sources=context["consumer"].authorized_sources, generation_token=context["generation_token"],
        policy_sha256=context["policy"].content_hash, target=context["policy"].routes["match_judge"].high,
        tier="high", downgraded=False,
    )
