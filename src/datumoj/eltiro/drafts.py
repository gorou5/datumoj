"""Rule-filled, explicitly unregistered YAML drafts; suggestions are inert text."""

from __future__ import annotations

import json
import os
import re
import stat
from collections.abc import Mapping
from dataclasses import asdict
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

import yaml
from pydantic import TypeAdapter

from datumoj.eltiro.contracts import ModelCache, ModelProviderClient, ModelRouter
from datumoj.eltiro.intake import open_directory
from datumoj.eltiro.model_match import _invoke_result, _unique_object
from datumoj.eltiro.models import (
    MAX_FILE_BYTES,
    ConsumerModelConfig,
    Id,
    ManualQueueItem,
    ModelBudget,
    ModelInput,
    ModelInputPart,
    ModelProvider,
    RoutingPolicy,
    Version,
)
from datumoj.eltiro.page_cases import PageCase
from datumoj.rikolto.core.contracts import Registry
from datumoj.rikolto.core.models import GenerationToken, LicenseInfo

SUGGESTED_FIELDS = frozenset(("entities", "indicators", "period", "operations"))
DRAFT_NAME = re.compile(r"(?:V[0-9]{2,3}|#[0-9]{3})-[0-9a-f]{16}\.yaml\Z")


def _host(url: str) -> str | None:
    try:
        parsed = urlsplit(url)
        if parsed.scheme not in ("http", "https") or parsed.username or parsed.password or not parsed.hostname:
            return None
        # Exact official hosts only; no fuzzy suffix matching, www stripping or redirects.
        return parsed.hostname.lower().encode("idna").decode("ascii")
    except (ValueError, UnicodeError):
        return None


def _sources(case: PageCase, registry: Registry) -> tuple[list[str], list[dict]]:
    by_host = {}
    for source in registry.all():
        for host in {_host(str(source.base_url)), *(value.lower() for value in source.allowed_hosts)} - {None}:
            by_host.setdefault(host, set()).add(source.id)
    preferred, unconnected = set(), []
    for link in case.sources:
        candidates = by_host.get(_host(link.url), set())
        # Shared hosts cannot disambiguate datasets/products; leave for a person.
        if len(candidates) == 1:
            preferred.update(candidates)
        else:
            unconnected.append({"text": link.text, "url": link.url,
                                "reason": "ambiguous_official_domain" if candidates else "not_in_registry"})
    return sorted(preferred), unconnected


def _suggest(case, *, consumer, router, page_source_id, page_license, consumer_config,
             policy, budget, generation_token, run_id, providers, cache, rerun):
    if any(value is None for value in (page_source_id, consumer_config, policy, budget, generation_token, run_id, cache)):
        raise ValueError("model drafts require host routing context and page source identity")
    if consumer_config.consumer != consumer:
        raise ValueError("model consumer differs from draft consumer")
    license = LicenseInfo() if page_license is None else LicenseInfo.model_validate(page_license.model_dump())
    # Only the page is model input: linked datasets are neither fetched nor licensed by this page.
    content = json.dumps({"content_role": "untrusted_source_text", "case": asdict(case)}, ensure_ascii=False)
    request = ModelInput(id="demand_draft", step="demand_draft", difficulty="medium", prompt_version="v2",
                         parts=(ModelInputPart(source_id=page_source_id, content=content),))
    licenses = {page_source_id: license}
    plan = router.plan(run_id=run_id, budget=budget, consumer=consumer_config, licenses=licenses,
                       policy=policy, generation_token=generation_token, inputs=[request])
    suggestions, manual = {}, []
    for index, decision in enumerate(plan.items):
        if isinstance(decision, ManualQueueItem):
            manual.append(decision.reason)
            continue
        result = _invoke_result(router, run_id, index, consumer=consumer_config, licenses=licenses,
                                providers=providers or {}, cache=cache, rerun=rerun)
        if isinstance(result, ManualQueueItem):
            manual.append(result.reason)
            continue
        if result is None:
            manual.append("invalid_model_output")
            continue
        result.validate_route(decision)
        if set(result.fields) == {"answer"}:
            field = result.fields["answer"]
            try:
                values = json.loads(field.value, object_pairs_hook=_unique_object)
            except (ValueError, RecursionError):
                values = None
            if (not isinstance(values, dict) or not set(values) <= SUGGESTED_FIELDS
                    or any(not isinstance(value, str) or not value.strip() for value in values.values())):
                manual.append("invalid_model_output")
                continue
            fields = {key: (value, field.model_use) for key, value in values.items()}
        elif set(result.fields) <= SUGGESTED_FIELDS:
            # Preserve 810's structured provider interface alongside 806's text answer.
            fields = {key: (field.value, field.model_use) for key, field in result.fields.items()}
        else:
            manual.append("invalid_model_output")
            continue
        for key, (value, use) in fields.items():
            suggestions[key] = {"model_generated": True, "value": value,
                                "model_use": use.model_dump(mode="json")}
    return suggestions, manual


def draft_for_case(
    case: PageCase, *, consumer: str, registry: Registry, data_dir: str | Path,
    router: ModelRouter | None = None, page_source_id: str | None = None,
    page_license: LicenseInfo | None = None, consumer_config: ConsumerModelConfig | None = None,
    policy: RoutingPolicy | None = None, budget: ModelBudget | None = None,
    generation_token: GenerationToken | None = None, run_id: str | None = None,
    providers: Mapping[ModelProvider, ModelProviderClient] | None = None,
    cache: ModelCache | None = None, rerun: bool = False, version: int = 1,
) -> Path:
    """Publish only <data>/eltiro/drafts/<number>-<hash16>.yaml, return its path.

    A repeated identical draft does not touch the file. Changed host/model context
    or human edits at the same name cause ValueError, never overwrite data.
    Model suggestions remain separately annotated strings, awaiting human review.
    No DemandRegistry is accepted or called. Host owns all router context/licenses.
    """
    consumer = TypeAdapter(Id).validate_python(consumer)
    version = TypeAdapter(Version).validate_python(version)
    case = PageCase(**{**asdict(case), "sources": case.sources})
    preferred, unconnected = _sources(case, registry)
    purpose = "\n\n".join((case.hypothesis, case.method) if case.kind == "verify" else (case.question, case.fusion))
    suggestions, manual = {}, []
    if router is not None:
        suggestions, manual = _suggest(
            case, consumer=consumer, router=router, page_source_id=page_source_id, page_license=page_license,
            consumer_config=consumer_config, policy=policy, budget=budget, generation_token=generation_token,
            run_id=run_id, providers=providers, cache=cache, rerun=rerun)
    document = {
        "draft": True, "schema": "eltiro.request/v0",
        "id": f"{case.kind}-{case.number.lstrip('#').lower()}", "consumer": consumer, "version": version,
        "purpose": purpose, "source_case": f"{case.kind} {case.number} {case.title}",
        "entities": None, "period": {"from": None, "to": None, "frequency": None}, "revisions": None,
        "indicators": [{"key": None, "concept": None, "unit": None, "preferred_sources": preferred}],
        "align": None, "operations": None, "output": None, "accept_outflow": None, "models": None, "run": None,
        "page_case": {"content_hash": case.content_hash, "content_role": "untrusted_source_text", **asdict(case)},
        "unconnected_sources": unconnected, "model_suggestions": suggestions, "model_manual_reasons": manual,
        "pending_human": ["entities", "period.from", "period.to", "period.frequency", "revisions",
                          "indicators.key", "indicators.concept", "indicators.unit", "indicators.min_sources",
                          "indicators.keep_separate_by", "indicators.examples", "align", "operations", "output",
                          "accept_outflow", "models", "run"] + (["unconnected_sources"] if unconnected else []),
    }
    payload = yaml.safe_dump(document, allow_unicode=True, sort_keys=False).encode("utf-8")
    if len(payload) > MAX_FILE_BYTES:
        raise ValueError("draft exceeds demand file size limit")
    root = Path(data_dir).resolve()
    destination = root / "eltiro" / "drafts"
    directory = open_directory(root, destination)
    name = f"{case.number}-{case.content_hash[:16]}.yaml"
    temporary = "." + uuid4().hex
    created = False
    try:
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory)
        created = True
        with os.fdopen(fd, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temporary, name, src_dir_fd=directory, dst_dir_fd=directory, follow_symlinks=False)
            os.fsync(directory)
        except FileExistsError:
            fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
            with os.fdopen(fd, "rb") as stream:
                info = os.fstat(stream.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or stream.read(MAX_FILE_BYTES + 1) != payload:
                    raise ValueError("existing draft differs; preserve it for human review")
    finally:
        if created:
            os.unlink(temporary, dir_fd=directory)
        os.close(directory)
    return destination / name


def list_drafts(data_dir: str | Path) -> list[Path]:
    """Sorted regular draft paths only; do not read YAML or follow symlinks."""
    root = Path(data_dir).resolve()
    destination = root / "eltiro" / "drafts"
    try:
        parent = os.open(root / "eltiro", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    except FileNotFoundError:
        return []
    try:
        try:
            directory = os.open("drafts", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
        except FileNotFoundError:
            return []
    finally:
        os.close(parent)
    try:
        return [destination / name for name in sorted(os.listdir(directory))
                if DRAFT_NAME.fullmatch(name) and stat.S_ISREG(
                    os.stat(name, dir_fd=directory, follow_symlinks=False).st_mode)]
    finally:
        os.close(directory)
