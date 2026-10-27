"""Detached, revalidated in-memory implementations; never read Rikolto stores."""

from datumoj.eltiro.contracts import ProviderUnavailable, RerunCacheMiss, RouteInvalid
from datumoj.eltiro.models import (
    CLOUD_PROVIDERS,
    MODEL_TIERS,
    ConsumerModelConfig,
    ManualQueueItem,
    MatchRecord,
    ModelBudget,
    ModelInput,
    ModelResult,
    ModelUse,
    RouteDecision,
    RoutePlan,
    RouteTarget,
    RoutingPolicy,
    SeriesIdentity,
    content_hash,
    load_demand,
)
from datumoj.rikolto.core.models import GenerationToken, LicenseInfo, Outflow


def _copy(model):
    return type(model).model_validate(model.model_dump(by_alias=True))


def _series_key(series: SeriesIdentity) -> tuple:
    return (series.source_id, series.table_key, series.indicator, series.indicator_code,
            tuple(sorted(series.fixed_dimensions.items())))


def _match_key(record: MatchRecord) -> tuple:
    return (record.demand_id, record.demand_version, record.indicator_key, _series_key(record.series), record.generation)


class FakeDemandRegistry:
    def __init__(self):
        self._demands = {}
        self._active = {}

    def validate(self, path_or_bytes):
        return load_demand(path_or_bytes)

    def register(self, demand, *, active=True):
        demand = _copy(demand)
        key = (demand.id, demand.version)
        if key in self._demands and self._demands[key] != demand:
            raise ValueError("demand version is immutable")
        self._demands[key] = demand
        if active:
            self._active[demand.id] = demand.version

    def get(self, demand_id, version):
        value = self._demands.get((demand_id, version))
        return _copy(value) if value is not None else None

    def set_active(self, demand_id, version, active):
        if (demand_id, version) not in self._demands:
            raise KeyError((demand_id, version))
        if active:
            self._active[demand_id] = version
        elif self._active.get(demand_id) == version:
            del self._active[demand_id]

    def list_active(self):
        return [self.get(key, self._active[key]) for key in sorted(self._active)]


class FakeProfileStore:
    def __init__(self):
        self._generations = {}

    def write_generation(self, generation, profiles):
        # Validate the generation even for an empty replacement.
        from pydantic import TypeAdapter

        from datumoj.eltiro.models import Generation

        TypeAdapter(Generation).validate_python(generation)
        validated = [_copy(profile) for profile in profiles]
        values = {_series_key(profile.series): profile for profile in validated}
        if len(values) != len(validated) or any(profile.generation != generation for profile in validated):
            raise ValueError("duplicate series or mismatched generation")
        self._generations[generation] = values

    def get(self, generation, series):
        value = self._generations.get(generation, {}).get(_series_key(series))
        return _copy(value) if value is not None else None

    def list(self, generation):
        return [_copy(value) for value in self._generations.get(generation, {}).values()]


class FakeMatchStore:
    def __init__(self):
        self._records = {}

    def put(self, record):
        record = _copy(record)
        key = _match_key(record)
        if key in self._records:
            # A stale matcher cannot erase a person's decision.
            confirmation = self._records[key].confirmation
            record = MatchRecord.model_validate({
                **record.model_dump(), "confirmation": confirmation,
                "auto_bind": record.auto_bind and confirmation != "rejected",
            })
        self._records[key] = record

    def for_demand(self, demand_id, version, generation=None):
        return [_copy(record) for record in self._records.values()
                if (record.demand_id, record.demand_version) == (demand_id, version)
                and (generation is None or record.generation == generation)]

    def set_confirmation(self, demand_id, version, indicator_key, series, generation, confirmation):
        key = (demand_id, version, indicator_key, _series_key(series), generation)
        record = self._records[key]
        updated = {**record.model_dump(), "confirmation": confirmation}
        if confirmation == "rejected":
            updated["auto_bind"] = False
        self._records[key] = MatchRecord.model_validate(updated)


def _route_restrictions(target, tier, budget, consumer, licenses, request):
    reasons = []
    if any(part.source_id not in consumer.authorized_sources for part in request.parts):
        reasons.append("unauthorized_source")
    if any(part.source_id not in licenses for part in request.parts):
        reasons.append("missing_registry_license")
    if MODEL_TIERS.index(tier) > min(MODEL_TIERS.index(budget.max_tier),
                                    MODEL_TIERS.index(consumer.max_tier)):
        reasons.append("max_tier")
    if target.provider not in consumer.allowed_providers:
        reasons.append("consumer_provider")
    if target.provider in CLOUD_PROVIDERS:
        if not (budget.allow_cloud and consumer.allow_cloud):
            reasons.append("allow_cloud")
        for part in request.parts:
            license = licenses.get(part.source_id, LicenseInfo())
            if license.outflow == Outflow.no_outflow:
                reasons.append("no_outflow")
            if not license.cloud_model_ok:
                reasons.append("cloud_model_forbidden")
    return list(dict.fromkeys(reasons))


class FakeModelProvider:
    """Fixed synthetic output; availability and calls are observable test controls."""

    def __init__(self, value="synthetic model result"):
        self.value = value
        self.available = True
        self.calls = []

    def invoke(self, decision):
        decision = _copy(decision)
        self.calls.append(decision)
        if not self.available:
            raise ProviderUnavailable("synthetic provider unavailable")
        identity = decision.target.identity
        if identity is None:
            raise ValueError("none cannot be called")
        return ModelResult(fields={"answer": {
            "value": self.value,
            "model_use": ModelUse(provider=decision.target.provider, model=identity.model,
                                  version=identity.version, tier=decision.tier),
        }})


class FakeModelCache:
    def __init__(self):
        self._values = {}

    def get(self, key):
        key = _copy(key)
        value = self._values.get(key.digest)
        return _copy(value) if value is not None else None

    def put(self, key, result):
        key, result = _copy(key), _copy(result)
        expected = (key.provider, key.identity.model, key.identity.version)
        if any((field.model_use.provider, field.model_use.model, field.model_use.version) !=
               expected for field in result.fields.values()):
            raise ValueError("cache provenance disagrees with key")
        if key.digest in self._values and self._values[key.digest] != result:
            raise ValueError("cached model output is immutable")
        self._values[key.digest] = result


class FakeModelRouter:
    """Offline reference semantics only; not a production adapter or durable run store."""

    def __init__(self):
        self._plans = {}
        self._contexts = {}
        self._calls = {}

    def plan(self, *, run_id, budget, consumer, licenses, policy, generation_token, inputs):
        budget = ModelBudget.model_validate(budget.model_dump())
        consumer = ConsumerModelConfig.model_validate(consumer.model_dump())
        policy = RoutingPolicy.model_validate(policy.model_dump())
        generation_token = GenerationToken.model_validate(generation_token.model_dump())
        inputs = [ModelInput.model_validate(item.model_dump()) for item in inputs]
        licenses = {key: LicenseInfo.model_validate(value.model_dump()) for key, value in licenses.items()}
        if len({item.id for item in inputs}) != len(inputs):
            raise ValueError("duplicate request ids")
        context = content_hash({
            "budget": budget.model_dump(), "consumer": consumer.model_dump(),
            "licenses": {key: value.model_dump(mode="json") for key, value in licenses.items()},
            "policy": policy.model_dump(), "generation": generation_token.model_dump(),
            "inputs": [item.model_dump() for item in inputs],
        })
        if run_id in self._plans:
            if context != self._contexts[run_id]:
                raise RouteInvalid("run context is immutable; rerun the original plan")
            return _copy(self._plans[run_id])
        items = []
        for request in inputs:
            if len(request.parts) > 1 and not request.splittable:
                items.append(ManualQueueItem(request=request, reason="inseparable_sources"))
                continue
            # Each source is its own permission boundary; never forward a mixed payload.
            for part in request.parts:
                single = ModelInput.model_validate({**request.model_dump(), "parts": [part.model_dump()]})
                reasons = []
                selected, tier = RouteTarget(provider="none"), "none"
                if budget.max_calls_per_run == 0:
                    reasons.append("budget_exhausted")
                elif request.step not in policy.routes:
                    reasons.append("unconfigured_step")
                else:
                    for candidate in reversed(MODEL_TIERS[1:MODEL_TIERS.index(request.difficulty) + 1]):
                        target = getattr(policy.routes[request.step], candidate)
                        blocked = _route_restrictions(target, candidate, budget, consumer, licenses, single)
                        if target.provider == "none":
                            blocked.append("configured_none")
                        if not blocked:
                            selected, tier = target, candidate
                            break
                        reasons.extend(blocked)
                if tier == "none":
                    reasons.append("no_eligible_route")
                decision = RouteDecision(
                    request=single, consumer=consumer.consumer, authorized_sources=consumer.authorized_sources,
                    generation_token=generation_token, policy_sha256=policy.content_hash, target=selected,
                    tier=tier, downgraded=tier != request.difficulty, reasons=tuple(dict.fromkeys(reasons)),
                )
                items.append(ManualQueueItem(request=single, reason=reasons[-1], decision=decision)
                             if tier == "none" else decision)
        plan = RoutePlan(run_id=run_id, budget=budget, items=tuple(items))
        self._plans[run_id], self._contexts[run_id], self._calls[run_id] = _copy(plan), context, 0
        return _copy(plan)

    def invoke(self, run_id, item_index, *, consumer, licenses, providers, cache, rerun=False):
        plan = self._plans[run_id]
        if type(item_index) is not int or not 0 <= item_index < len(plan.items):
            raise ValueError("invalid item index")
        decision = plan.items[item_index]
        if isinstance(decision, ManualQueueItem):
            return _copy(decision)
        consumer = ConsumerModelConfig.model_validate(consumer.model_dump())
        licenses = {key: LicenseInfo.model_validate(value.model_dump()) for key, value in licenses.items()}
        if (consumer.consumer, consumer.authorized_sources) != (decision.consumer, decision.authorized_sources):
            raise RouteInvalid("consumer or authorization scope changed")
        if _route_restrictions(decision.target, decision.tier, plan.budget, consumer, licenses, decision.request):
            raise RouteInvalid("current authorization or registry license forbids the frozen route")
        key = decision.cache_key()
        result = cache.get(key)
        if result is not None:
            result = ModelResult.model_validate(result.model_dump())
            result.validate_route(decision)
            return result
        if rerun:
            raise RerunCacheMiss("original model result is missing")
        if self._calls[run_id] >= plan.budget.max_calls_per_run:
            return ManualQueueItem(request=decision.request, decision=decision, reason="budget_exhausted")
        self._calls[run_id] += 1
        provider = providers.get(decision.target.provider)
        if provider is None:
            raise ProviderUnavailable("selected provider unavailable")
        result = ModelResult.model_validate(provider.invoke(_copy(decision)).model_dump())
        result.validate_route(decision)
        cache.put(key, result)
        return _copy(result)
