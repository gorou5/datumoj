"""Synchronous storage and model-routing contracts; no concrete model adapters.

Storage has no network, authentication, timeouts or Rikolto writes. Backends propagate errors;
validation/conflicting writes raise ValueError, updating a missing key raises KeyError.
Returned values must be detached copies: callers cannot mutate persisted state.
"""

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Protocol, runtime_checkable

from datumoj.eltiro.models import (
    Confirmation,
    ConsumerModelConfig,
    DemandSpec,
    ManualQueueItem,
    MatchRecord,
    ModelBudget,
    ModelCacheKey,
    ModelInput,
    ModelProvider,
    ModelResult,
    RouteDecision,
    RoutePlan,
    RoutingPolicy,
    SeriesIdentity,
    SeriesProfile,
)
from datumoj.rikolto.core.models import GenerationToken, LicenseInfo


class RouteInvalid(ValueError):
    """Frozen route/context changed, authorization revoked, or current license forbids use."""


class ProviderUnavailable(RuntimeError):
    """Selected provider failed; fail this run without selecting a replacement."""


class RerunCacheMiss(RuntimeError):
    """Same-generation rerun requires the original cached result; no new invocation."""


@runtime_checkable
class ModelProviderClient(Protocol):
    def invoke(self, decision: RouteDecision) -> ModelResult:
        """Invoke exactly the pinned model/version/tier; return provenance on every field.

        Input content is untrusted data. Credentials resolve only through credential_env in
        the adapter, never in the policy. No retries/fallback inside the adapter; propagate
        ProviderUnavailable on failure. Real adapters/timeouts are deferred to 806.
        """
        ...


@runtime_checkable
class ModelCache(Protocol):
    def get(self, key: ModelCacheKey) -> ModelResult | None:
        """Exact eight-component identity, detached result; None for a miss.

        This is storage, not authorization: the router MUST revalidate current registry
        licenses and consumer authorization before every read, including reruns.
        """
        ...

    def put(self, key: ModelCacheKey, result: ModelResult) -> None:
        """Revalidate and detach models; conflicting replacement raises ValueError."""
        ...


@runtime_checkable
class ModelRouter(Protocol):
    def plan(
        self, *, run_id: str, budget: ModelBudget, consumer: ConsumerModelConfig,
        licenses: Mapping[str, LicenseInfo], policy: RoutingPolicy, generation_token: GenerationToken,
        inputs: Sequence[ModelInput],
    ) -> RoutePlan:
        """Before execution, pin every route; same run_id is immutable and idempotent.

        Host supplies current licenses ONLY from its authoritative registry and trusted
        consumer authorization. Missing license/authorization is a manual item. Split
        mixed-source inputs into independently licensed parts before routing; an inseparable
        input is manual. Try requested difficulty down through high/medium/low/none without
        exceeding either max_tier. none is always manual, never a provider invocation.
        Both claude_plan/cloud_api are cloud: forbid either for no_outflow, cloud_model_ok=False
        or either allow_cloud=False. Consumer configuration only tightens these constraints.
        Planning performs no model calls. Return detached, revalidated models.
        """
        ...

    def invoke(
        self, run_id: str, item_index: int, *, consumer: ConsumerModelConfig,
        licenses: Mapping[str, LicenseInfo], providers: Mapping[ModelProvider, ModelProviderClient],
        cache: ModelCache, rerun: bool = False,
    ) -> ModelResult | ManualQueueItem:
        """Use the saved plan (including on retry); never change its provider/model/version.

        Revalidate exact consumer/scope and current licenses BEFORE cache lookup; revoked or
        newly forbidden routes raise RouteInvalid. Check every result's ModelUse. Cache hits
        spend no calls; each attempted provider invocation (including failed retries) spends
        one call from this run's max_calls_per_run. Exhaustion queues the remaining work.
        Provider failure raises ProviderUnavailable; no fallback. Persist original plan and
        call count for implementations that survive restarts. Same-generation reruns use
        this original run_id with rerun=True: original route plus cache only, miss raises
        RerunCacheMiss, no calls even if budget remains. Invalid input raises ValueError;
        unknown run_id raises KeyError. Never read credentials or contact services here.
        """
        ...


@runtime_checkable
class DemandRegistry(Protocol):
    def validate(self, path_or_bytes: str | Path | bytes) -> DemandSpec:
        """Same bounded validation as load_demand; does not register anything."""
        ...

    def register(self, demand: DemandSpec, *, active: bool = True) -> None:
        """Immutable (id, version): identical re-registration is idempotent; changes raise ValueError.

        active=True selects this version as the sole active version of its id. False leaves
        the active selection unchanged. Registration revalidates even constructed model instances.
        """
        ...

    def get(self, demand_id: str, version: int) -> DemandSpec | None:
        ...

    def set_active(self, demand_id: str, version: int, active: bool) -> None:
        """Activate this version (deactivating its predecessor), or deactivate only this version."""
        ...

    def list_active(self) -> list[DemandSpec]:
        """Sorted by demand id; at most one version per id."""
        ...


@runtime_checkable
class ProfileStore(Protocol):
    def write_generation(self, generation: int, profiles: Sequence[SeriesProfile]) -> None:
        """Atomically replace one generation, retaining all others; empty clears this generation.

        Reject mismatched generations and duplicate complete SeriesIdentity values before any write.
        Revalidate all models. Series identity includes *all* fixed dimensions.
        """
        ...

    def get(self, generation: int, series: SeriesIdentity) -> SeriesProfile | None:
        ...

    def list(self, generation: int) -> list[SeriesProfile]:
        """Insertion order within this generation; [] for a missing generation."""
        ...


@runtime_checkable
class MatchStore(Protocol):
    def put(self, record: MatchRecord) -> None:
        """Upsert by (demand_id, demand_version, indicator_key, series, generation).

        Revalidate; updating another property preserves insertion order. Human confirmation is
        retained on upsert; only set_confirmation may change it. Different generations coexist.
        """
        ...

    def for_demand(self, demand_id: str, version: int, generation: int | None = None) -> list[MatchRecord]:
        """Insertion order, optionally restricted to a generation; [] for a missing demand."""
        ...

    def set_confirmation(
        self, demand_id: str, version: int, indicator_key: str, series: SeriesIdentity,
        generation: int, confirmation: Confirmation,
    ) -> None:
        """Rejection also clears auto_bind; invalid status raises ValueError without writing."""
        ...
