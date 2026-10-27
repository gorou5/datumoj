"""Bounded routing configuration and durable, frozen model runs."""

from __future__ import annotations

import os
from pathlib import Path
import sqlite3
import stat

import yaml

from datumoj.eltiro.contracts import ProviderUnavailable, RerunCacheMiss, RouteInvalid
from datumoj.eltiro.models import (
    CLOUD_PROVIDERS, MAX_FILE_BYTES, MODEL_TIERS, ConsumerModelConfig, ConsumersConfig,
    ManualQueueItem, ModelBudget, ModelInput, ModelResult, RouteDecision, RoutePlan,
    RouteTarget, RoutingPolicy, content_hash,
)
from datumoj.rikolto.core.models import GenerationToken, LicenseInfo, Outflow


def load_routing(data_root: str | Path) -> RoutingPolicy:
    """Read only <data>/eltiro/routing.yaml; reject oversized/nonregular YAML."""
    try:
        fd = os.open(Path(data_root) / "eltiro" / "routing.yaml",
                     os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, "rb") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_FILE_BYTES:
                raise ValueError("invalid routing file")
            content = stream.read(MAX_FILE_BYTES + 1)
        if len(content) > MAX_FILE_BYTES:
            raise ValueError("routing file too large")
        if any(isinstance(token, yaml.tokens.AliasToken) for token in yaml.scan(content)):
            raise ValueError("routing aliases are forbidden")
        return RoutingPolicy.model_validate(yaml.safe_load(content))
    except (OSError, ValueError, yaml.YAMLError, UnicodeError, RecursionError):
        raise ValueError("invalid routing configuration") from None


def _restrictions(target, tier, budget, consumer, licenses, request):
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


class SQLiteModelRouter:
    """ModelRouter with host-owned consumer caps; never resolves credentials.

    Connections are owned by the instance: close() when finished. Concurrent processes
    reserve attempts atomically before any provider I/O, including failed attempts.
    """

    def __init__(self, data_root: str | Path, *, consumers: ConsumersConfig):
        checked = ConsumersConfig.model_validate(consumers.model_dump())
        self._caps = {item.id: item.max_calls_per_run for item in checked.consumers}
        self.path = Path(data_root) / "eltiro" / "model_runs.sqlite"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(self.path)
        with self._db:
            self._db.execute("""CREATE TABLE IF NOT EXISTS runs (
                run_id TEXT PRIMARY KEY, consumer TEXT NOT NULL, context TEXT NOT NULL,
                plan TEXT NOT NULL, calls INTEGER NOT NULL DEFAULT 0)""")
            self._db.execute("""CREATE TABLE IF NOT EXISTS manual_queue (
                run_id TEXT NOT NULL, item_index INTEGER NOT NULL, consumer TEXT NOT NULL,
                item TEXT NOT NULL, PRIMARY KEY (run_id, item_index))""")

    def close(self):
        self._db.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def get_plan(self, run_id: str) -> RoutePlan:
        row = self._db.execute("SELECT plan FROM runs WHERE run_id = ?", (run_id,)).fetchone()
        if row is None:
            raise KeyError(run_id)
        return RoutePlan.model_validate_json(row[0])

    def calls_used(self, run_id: str) -> int:
        row = self._db.execute("SELECT calls FROM runs WHERE run_id = ?", (run_id,)).fetchone()
        if row is None:
            raise KeyError(run_id)
        return row[0]

    def list_manual(self, *, run_id: str | None = None, consumer: str | None = None) -> list[ManualQueueItem]:
        return [ManualQueueItem.model_validate_json(row[0]) for row in self._db.execute(
            """SELECT item FROM manual_queue WHERE (? IS NULL OR run_id = ?)
               AND (? IS NULL OR consumer = ?) ORDER BY run_id, item_index""",
            (run_id, run_id, consumer, consumer))]

    def _queue(self, run_id, index, consumer, item):
        self._db.execute("INSERT OR IGNORE INTO manual_queue VALUES (?, ?, ?, ?)",
                         (run_id, index, consumer, item.model_dump_json()))

    def plan(self, *, run_id, budget, consumer, licenses, policy, generation_token, inputs):
        budget = ModelBudget.model_validate(budget.model_dump())
        consumer = ConsumerModelConfig.model_validate(consumer.model_dump())
        policy = RoutingPolicy.model_validate(policy.model_dump())
        generation_token = GenerationToken.model_validate(generation_token.model_dump())
        inputs = [ModelInput.model_validate(item.model_dump()) for item in inputs]
        licenses = {key: LicenseInfo.model_validate(value.model_dump()) for key, value in licenses.items()}
        if consumer.consumer not in self._caps:
            raise RouteInvalid("consumer configuration is missing")
        if len({item.id for item in inputs}) != len(inputs):
            raise ValueError("duplicate request ids")
        cap = self._caps[consumer.consumer]
        context = content_hash({
            "budget": budget.model_dump(), "consumer": consumer.model_dump(), "consumer_cap": cap,
            "licenses": {key: value.model_dump(mode="json") for key, value in licenses.items()},
            "policy": policy.model_dump(), "generation": generation_token.model_dump(),
            "inputs": [item.model_dump() for item in inputs],
        })
        budget = ModelBudget.model_validate({**budget.model_dump(),
                                             "max_calls_per_run": min(budget.max_calls_per_run, cap)})
        items = []
        for request in inputs:
            if len(request.parts) > 1 and not request.splittable:
                items.append(ManualQueueItem(request=request, reason="inseparable_sources"))
                continue
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
                        blocked = _restrictions(target, candidate, budget, consumer, licenses, single)
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
        with self._db:
            self._db.execute("BEGIN IMMEDIATE")
            old = self._db.execute("SELECT context, plan FROM runs WHERE run_id = ?", (run_id,)).fetchone()
            if old is not None:
                if old[0] != context:
                    raise RouteInvalid("run context is immutable; rerun the original plan")
                return RoutePlan.model_validate_json(old[1])
            self._db.execute("INSERT INTO runs (run_id, consumer, context, plan) VALUES (?, ?, ?, ?)",
                             (run_id, consumer.consumer, context, plan.model_dump_json()))
            for index, item in enumerate(plan.items):
                if isinstance(item, ManualQueueItem):
                    self._queue(run_id, index, consumer.consumer, item)
        return RoutePlan.model_validate_json(plan.model_dump_json())

    def invoke(self, run_id, item_index, *, consumer, licenses, providers, cache, rerun=False):
        plan = self.get_plan(run_id)
        if type(item_index) is not int or not 0 <= item_index < len(plan.items):
            raise ValueError("invalid item index")
        decision = plan.items[item_index]
        if isinstance(decision, ManualQueueItem):
            return decision
        consumer = ConsumerModelConfig.model_validate(consumer.model_dump())
        licenses = {key: LicenseInfo.model_validate(value.model_dump()) for key, value in licenses.items()}
        if (consumer.consumer, consumer.authorized_sources) != (decision.consumer, decision.authorized_sources):
            raise RouteInvalid("consumer or authorization scope changed")
        if _restrictions(decision.target, decision.tier, plan.budget, consumer, licenses, decision.request):
            raise RouteInvalid("current authorization or registry license forbids the frozen route")
        key = decision.cache_key()
        result = cache.get(key)
        if result is not None:
            result = ModelResult.model_validate(result.model_dump())
            result.validate_route(decision)
            return result
        if rerun:
            raise RerunCacheMiss("original model result is missing")
        with self._db:
            self._db.execute("BEGIN IMMEDIATE")
            reserved = self._db.execute("UPDATE runs SET calls = calls + 1 WHERE run_id = ? AND calls < ?",
                                        (run_id, plan.budget.max_calls_per_run)).rowcount
            if not reserved:
                item = ManualQueueItem(request=decision.request, decision=decision, reason="budget_exhausted")
                self._queue(run_id, item_index, consumer.consumer, item)
                return item
        provider = providers.get(decision.target.provider)
        if provider is None:
            raise ProviderUnavailable("selected provider unavailable")
        result = ModelResult.model_validate(provider.invoke(decision).model_dump())
        result.validate_route(decision)
        cache.put(key, result)
        return ModelResult.model_validate(result.model_dump())
