"""Optional, single-source model judgments of already recalled candidates."""

from __future__ import annotations

import json
import re
import sqlite3
from contextlib import closing
from pathlib import Path

from pydantic import TypeAdapter, ValidationError

from datumoj.eltiro.match_store import series_key
from datumoj.eltiro.models import (
    ConsumerConfig,
    DemandSpec,
    Generation,
    ManualQueueItem,
    MatchRecord,
    ModelInput,
    ModelResult,
    ModelUse,
    RoutePlan,
    SeriesProfile,
    content_hash,
)
from datumoj.rikolto.core.models import GenerationToken


def _context(demand, consumer, generation, generation_token, router, cache, licenses, policy):
    TypeAdapter(Generation).validate_python(generation)
    demand = DemandSpec.model_validate(demand.model_dump())
    consumer = ConsumerConfig.model_validate(consumer.model_dump())
    if demand.consumer != consumer.id:
        raise ValueError("consumer mismatch")
    if any(value is None for value in (router, cache, licenses, policy, generation_token)):
        raise ValueError("model tier requires router, cache, licenses, policy and generation_token")
    token = GenerationToken.model_validate(generation_token.model_dump())
    if token.generation != generation:
        raise ValueError("model generation mismatch")
    return demand, consumer, token


def _model_config(config, enabled, policy, generation_token):
    if type(enabled) is not bool:
        raise ValueError("model must be a boolean")
    if not enabled:
        return config  # Preserve the 803/808 fingerprint byte for byte.
    if policy is None or generation_token is None:
        raise ValueError("model tier requires policy and generation_token")
    return content_hash([config, policy.model_dump(mode="json"), generation_token.model_dump(mode="json")])


def _run_id(step, demand, generation):
    return step.replace("_", "-") + "-" + content_hash([demand.consumer, demand.id, demand.version, generation])


def _invoke_result(router, run_id, index, **kwargs):
    """Discard malformed model fields, retaining routing/provenance error handling."""
    try:
        result = router.invoke(run_id, index, **kwargs)
        if not isinstance(result, ManualQueueItem):
            result = ModelResult.model_validate(result.model_dump())
    except ValidationError as exc:
        # 806 also validates provider results. Do not swallow input/context errors.
        if exc.title != "ModelResult" or any(error["loc"][:1] != ("fields",) for error in exc.errors()):
            raise
        return None
    return result


def _run_inputs(step, demand, generation, *, data_root, consumer, router, cache,
                licenses, policy, generation_token, providers, inputs=None):
    """Journal the frozen plan before I/O; every subsequent attempt is cache only.

    Persist router manual outcomes too: budget-exhausted items have no model cache.
    An interrupted invocation with no cached result fails closed on the next attempt.
    """
    demand, consumer, token = _context(demand, consumer, generation, generation_token,
                                       router, cache, licenses, policy)
    run_id = _run_id(step, demand, generation)
    path = Path(data_root) / "eltiro" / "model_tasks.sqlite"
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(path)) as db:
        db.executescript("""
            CREATE TABLE IF NOT EXISTS tasks (run_id TEXT PRIMARY KEY, plan TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS manual (run_id TEXT, item_index INTEGER, payload TEXT NOT NULL,
                                               PRIMARY KEY (run_id, item_index));
        """)
        old = db.execute("SELECT plan FROM tasks WHERE run_id=?", (run_id,)).fetchone()
        if inputs is None:
            if old is None:
                raise ValueError("original model task is missing")
            inputs = [item.request for item in RoutePlan.model_validate_json(old[0]).items]
        plan = router.plan(run_id=run_id, budget=demand.models, consumer=consumer.models,
                           licenses=licenses, policy=policy, generation_token=token, inputs=inputs)
        if old is not None and RoutePlan.model_validate_json(old[0]) != plan:
            raise ValueError("original model plan changed")
        with db:
            db.execute("INSERT OR IGNORE INTO tasks VALUES (?,?)", (run_id, plan.model_dump_json()))
        output = []
        for index, item in enumerate(plan.items):
            manual = db.execute("SELECT payload FROM manual WHERE run_id=? AND item_index=?",
                                (run_id, index)).fetchone()
            if manual is not None:
                result = ManualQueueItem.model_validate_json(manual[0])
            else:
                result = _invoke_result(router, run_id, index, consumer=consumer.models, licenses=licenses,
                                        providers=providers, cache=cache, rerun=old is not None)
                if isinstance(result, ManualQueueItem):
                    with db:
                        db.execute("INSERT OR IGNORE INTO manual VALUES (?,?,?)",
                                   (run_id, index, result.model_dump_json()))
                elif result is not None:
                    result.validate_route(item)
            output.append((item, result))
        return output


def _one_sentence(value):
    # A bounded single line; do not accept paragraphs, empty strings or long prose.
    return (isinstance(value, str) and 0 < len(value.strip()) <= 512
            and re.search(r"\w", value) is not None
            and not any(c in value for c in "\r\n\u2028\u2029")
            and len(re.split(r"[。！？!?]|\.(?:\s|$)", value.strip().rstrip("。！？!?."))) == 1)


def _unique_object(pairs):
    value = dict(pairs)
    if len(value) != len(pairs):
        raise ValueError("duplicate output keys")
    return value


def _answer(result, keys):
    if result is None or set(result.fields) != {"answer"}:
        return None
    try:
        value = json.loads(result.fields["answer"].value, object_pairs_hook=_unique_object)
    except (ValueError, RecursionError):
        return None
    return value if isinstance(value, dict) and set(value) == set(keys) else None


def judge_candidates(demand, generation, records, *, profiles, consumer, data_root,
                     router, cache, licenses, policy, generation_token, providers,
                     concept_notes=()) -> list[MatchRecord]:
    """Return existing records with optional model ranking; never create or bind a series.

    ``profiles`` is the candidate-generation profile sequence. Model provenance is a
    ModelUse-shaped object in reason, since MatchRecord intentionally has no such field.
    """
    demand, consumer, _ = _context(demand, consumer, generation, generation_token,
                                   router, cache, licenses, policy)
    records = [MatchRecord.model_validate(r.model_dump()) for r in records]
    by_series = {}
    for raw in profiles:
        profile = SeriesProfile.model_validate(raw.model_dump())
        if profile.generation != generation or series_key(profile.series) in by_series:
            raise ValueError("invalid profile generation or duplicate identity")
        by_series[series_key(profile.series)] = profile
    indicators = {i.key: i for i in demand.indicators}
    inputs, selected = [], []
    for index, record in enumerate(records):
        if (record.demand_id, record.demand_version, record.generation) != (demand.id, demand.version, generation):
            raise ValueError("candidate demand or generation mismatch")
        if record.series.source_id not in consumer.allowed_sources:
            raise ValueError("candidate outside consumer scope")
        if record.confirmation != "pending":
            continue
        indicator = indicators[record.indicator_key]
        profile = by_series[series_key(record.series)]
        close = any(other is not record and other.indicator_key == record.indicator_key
                    and other.confirmation == "pending" and abs(other.confidence - record.confidence) <= 0.05
                    for other in records)
        difficulty = "high" if close or not profile.methodology or not profile.unit or not profile.survey else "medium"
        if difficulty == "medium" and all(v == "same" for v in record.compatibility.model_dump().values()):
            difficulty = "low"
        notes = [n.text for n in concept_notes if n.generation == generation and n.series == profile.series
                 and n.profile_sha256 == content_hash(profile.model_dump(mode="json", exclude={"generation"}))
                 and n.cache_key.generation_token == generation_token
                 and n.cache_key.consumer == consumer.id
                 and n.cache_key.authorized_sources == consumer.models.authorized_sources]
        payload = {"task": "Judge only this existing candidate; never bind or invent series.",
                   "output_contract": {"verdict": "relevant|irrelevant|uncertain", "reason": "one sentence"},
                   "indicator": {"concept": indicator.concept, "unit": indicator.unit,
                                 "examples": indicator.examples.model_dump()},
                   "candidate": profile.model_dump(mode="json"),
                   "model_generated_concept_notes": notes}
        inputs.append(ModelInput(id=f"candidate_{len(inputs)}", step="match_judge", difficulty=difficulty,
                                 prompt_version="v2", parts=[{"source_id": record.series.source_id,
                                 "content": json.dumps(payload, ensure_ascii=False, sort_keys=True)}]))
        selected.append(index)
    outcomes = _run_inputs("match_judge", demand, generation, data_root=data_root, consumer=consumer,
                           router=router, cache=cache, licenses=licenses, policy=policy,
                           generation_token=generation_token, providers=providers, inputs=inputs)
    for index, (decision, result) in zip(selected, outcomes, strict=True):
        record = records[index]
        if isinstance(result, ManualQueueItem):
            # Keep the candidate's original score/tier/reason; it now needs a human.
            records[index] = MatchRecord.model_validate({**record.model_dump(), "auto_bind": False})
            continue
        answer = _answer(result, ("verdict", "reason"))
        if (answer is None or answer["verdict"] not in ("relevant", "irrelevant", "uncertain")
                or not _one_sentence(answer["reason"])):
            answer = {"verdict": "uncertain", "reason": "Invalid structured model output"}
        # The frozen route identifies the invocation even when no fields survived.
        identity = decision.target.identity
        use = ModelUse(provider=decision.target.provider, model=identity.model,
                       version=identity.version, tier=decision.tier)
        audit = {"label": "模型生成", **answer, "model_use": use.model_dump(mode="json"),
                 "matcher_reason": record.reason}
        records[index] = MatchRecord.model_validate({
            **record.model_dump(), "tier": "model", "auto_bind": False,
            "confidence": {"relevant": 0.9, "irrelevant": 0.1, "uncertain": 0.5}[answer["verdict"]],
            "reason": json.dumps(audit, ensure_ascii=False, sort_keys=True),
        })
    return sorted(records, key=lambda r: ({"confirmed": 0, "pending": 1, "rejected": 2}[r.confirmation],
                                          r.indicator_key, -r.confidence, series_key(r.series)))
