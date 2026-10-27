"""Bounded declarative contracts; text is data, never code or routing instructions.

Export with ``python -m datumoj.eltiro.models schemas/eltiro``.
Cross-field rules (references, cycles, compatibility) additionally require Pydantic validation.
"""

from __future__ import annotations

import json
import re
import sys
from calendar import monthrange
from datetime import date
from hashlib import sha256
from pathlib import Path
from typing import Annotated, Any, Literal

import yaml
from pydantic import (
    AfterValidator,
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    TypeAdapter,
    model_validator,
)

from datumoj.rikolto.core.models import GenerationToken, Lang, Outflow, Sha256, SourceId

MAX_FILE_BYTES = 256 * 1024
MAX_INDICATORS = 64
MAX_ENTITIES = 256
MAX_OPERATIONS = 128
MAX_TEXT_LENGTH = 4096
MAX_LABEL_LENGTH = 256
MAX_ID_LENGTH = 128
MAX_ITEMS = 256
MAX_LANGUAGES = 32
MAX_LAG = 120
MAX_WINDOW = 1200
MAX_CALLS_PER_RUN = 1000

# ISO 3166-1 alpha-3 assigned codes; a shape-only check would accept e.g. ZZZ.
ISO_ALPHA3 = frozenset({
    'AFG', 'ALA', 'ALB', 'DZA', 'ASM', 'AND', 'AGO', 'AIA', 'ATA', 'ATG', 'ARG', 'ARM',
    'ABW', 'AUS', 'AUT', 'AZE', 'BHS', 'BHR', 'BGD', 'BRB', 'BLR', 'BEL', 'BLZ', 'BEN',
    'BMU', 'BTN', 'BOL', 'BES', 'BIH', 'BWA', 'BVT', 'BRA', 'IOT', 'BRN', 'BGR', 'BFA',
    'BDI', 'CPV', 'KHM', 'CMR', 'CAN', 'CYM', 'CAF', 'TCD', 'CHL', 'CHN', 'CXR', 'CCK',
    'COL', 'COM', 'COG', 'COD', 'COK', 'CRI', 'CIV', 'HRV', 'CUB', 'CUW', 'CYP', 'CZE',
    'DNK', 'DJI', 'DMA', 'DOM', 'ECU', 'EGY', 'SLV', 'GNQ', 'ERI', 'EST', 'SWZ', 'ETH',
    'FLK', 'FRO', 'FJI', 'FIN', 'FRA', 'GUF', 'PYF', 'ATF', 'GAB', 'GMB', 'GEO', 'DEU',
    'GHA', 'GIB', 'GRC', 'GRL', 'GRD', 'GLP', 'GUM', 'GTM', 'GGY', 'GIN', 'GNB', 'GUY',
    'HTI', 'HMD', 'VAT', 'HND', 'HKG', 'HUN', 'ISL', 'IND', 'IDN', 'IRN', 'IRQ', 'IRL',
    'IMN', 'ISR', 'ITA', 'JAM', 'JPN', 'JEY', 'JOR', 'KAZ', 'KEN', 'KIR', 'PRK', 'KOR',
    'KWT', 'KGZ', 'LAO', 'LVA', 'LBN', 'LSO', 'LBR', 'LBY', 'LIE', 'LTU', 'LUX', 'MAC',
    'MDG', 'MWI', 'MYS', 'MDV', 'MLI', 'MLT', 'MHL', 'MTQ', 'MRT', 'MUS', 'MYT', 'MEX',
    'FSM', 'MDA', 'MCO', 'MNG', 'MNE', 'MSR', 'MAR', 'MOZ', 'MMR', 'NAM', 'NRU', 'NPL',
    'NLD', 'NCL', 'NZL', 'NIC', 'NER', 'NGA', 'NIU', 'NFK', 'MKD', 'MNP', 'NOR', 'OMN',
    'PAK', 'PLW', 'PSE', 'PAN', 'PNG', 'PRY', 'PER', 'PHL', 'PCN', 'POL', 'PRT', 'PRI',
    'QAT', 'REU', 'ROU', 'RUS', 'RWA', 'BLM', 'SHN', 'KNA', 'LCA', 'MAF', 'SPM', 'VCT',
    'WSM', 'SMR', 'STP', 'SAU', 'SEN', 'SRB', 'SYC', 'SLE', 'SGP', 'SXM', 'SVK', 'SVN',
    'SLB', 'SOM', 'ZAF', 'SGS', 'SSD', 'ESP', 'LKA', 'SDN', 'SUR', 'SJM', 'SWE', 'CHE',
    'SYR', 'TWN', 'TJK', 'TZA', 'THA', 'TLS', 'TGO', 'TKL', 'TON', 'TTO', 'TUN', 'TUR',
    'TKM', 'TCA', 'TUV', 'UGA', 'UKR', 'ARE', 'GBR', 'USA', 'UMI', 'URY', 'UZB', 'VUT',
    'VEN', 'VNM', 'VGB', 'VIR', 'WLF', 'ESH', 'YEM', 'ZMB', 'ZWE',
})


def _nonblank(value: str) -> str:
    if not value.strip():
        raise ValueError("text must not be blank")
    return value


Text = Annotated[str, Field(min_length=1, max_length=MAX_TEXT_LENGTH), AfterValidator(_nonblank)]
Label = Annotated[str, Field(min_length=1, max_length=MAX_LABEL_LENGTH), AfterValidator(_nonblank)]
Id = Annotated[str, Field(min_length=1, max_length=MAX_ID_LENGTH, pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")]
# Sample indicator keys / operation ids are column symbols, not external object ids.
Symbol = Annotated[str, Field(min_length=1, max_length=MAX_ID_LENGTH, pattern=r"^[a-z][a-z0-9_]*$")]
Source = Annotated[SourceId, Field(max_length=MAX_ID_LENGTH)]
Version = Annotated[int, Field(strict=True, ge=1)]
Generation = Annotated[int, Field(strict=True, ge=1)]
Positive = Annotated[float, Field(strict=True, gt=0, allow_inf_nan=False)]
Lag = Annotated[int, Field(strict=True, ge=1, le=MAX_LAG)]
Window = Annotated[int, Field(strict=True, ge=2, le=MAX_WINDOW)]
Frequency = Literal["annual", "quarterly", "monthly", "weekly", "daily", "irregular"]
ModelTier = Literal["none", "low", "medium", "high"]
Confirmation = Literal["pending", "confirmed", "rejected"]


def _country(value: str) -> str:
    if value not in ISO_ALPHA3:
        raise ValueError("country must be an assigned ISO 3166-1 alpha-3 code")
    return value


Country = Annotated[
    str, Field(pattern=r"^[A-Z]{3}$", json_schema_extra={"enum": sorted(ISO_ALPHA3)}), AfterValidator(_country),
]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, validate_default=True, serialize_by_alias=True)


class Region(_Model):
    scheme: Label
    code: Label


class Entities(_Model):
    countries: Annotated[list[Country], Field(max_length=MAX_ENTITIES)] = Field(default_factory=list)
    regions: Annotated[list[Region], Field(max_length=MAX_ENTITIES)] = Field(default_factory=list)

    @model_validator(mode="after")
    def _count(self) -> Entities:
        if not 1 <= len(self.countries) + len(self.regions) <= MAX_ENTITIES:
            raise ValueError("entity count outside limits")
        if len(set(self.countries)) != len(self.countries):
            raise ValueError("duplicate countries")
        return self


def _period(value: str) -> str:
    date.fromisoformat(value + {4: "-01-01", 7: "-01"}.get(len(value), ""))
    return value


PeriodLabel = Annotated[
    str, Field(max_length=10, pattern=r"^[0-9]{4}(?:-[0-9]{2}(?:-[0-9]{2})?)?$"), AfterValidator(_period),
]


class DemandPeriod(_Model):
    start: PeriodLabel = Field(alias="from")
    end: PeriodLabel = Field(alias="to")
    frequency: Frequency

    @model_validator(mode="after")
    def _order(self) -> DemandPeriod:
        start = self.start + {4: "-01-01", 7: "-01"}.get(len(self.start), "")
        end = self.end
        if len(end) == 4:
            end += "-12-31"
        elif len(end) == 7:
            end += f"-{monthrange(int(end[:4]), int(end[5:]))[1]}"
        if start > end:
            raise ValueError("period is reversed")
        return self


class Examples(_Model):
    positive: Annotated[list[Text], Field(max_length=MAX_ITEMS)] = Field(default_factory=list)
    negative: Annotated[list[Text], Field(max_length=MAX_ITEMS)] = Field(default_factory=list)


class Indicator(_Model):
    key: Symbol
    concept: Text
    unit: Label | None = None
    preferred_sources: Annotated[list[Source], Field(max_length=MAX_ITEMS)] = Field(default_factory=list)
    min_sources: Annotated[int, Field(strict=True, ge=1, le=MAX_ITEMS)] = 1
    keep_separate_by: Annotated[
        list[Literal["source", "release", "survey", "unit", "methodology"]], Field(max_length=5),
    ] = Field(default_factory=list)
    examples: Examples = Field(default_factory=Examples)


class Alignment(_Model):
    keys: Annotated[list[Symbol], Field(min_length=1, max_length=MAX_ITEMS)]
    require_same_unit: Annotated[bool, Field(strict=True)] = True


class _Operation(_Model):
    id: Symbol


class _Unary(_Operation):
    input: Symbol


class _Binary(_Operation):
    left: Symbol
    right: Symbol


class Rescale(_Unary):
    op: Literal["rescale"]
    target_scale: Positive


class Diff(_Binary):
    op: Literal["diff"]
    sign: Literal["minus", "plus"] = "minus"


class DiffLag(_Unary):
    op: Literal["diff_lag"]
    lag: Lag


class Ratio(_Binary):
    op: Literal["ratio"]


class GrowthRate(_Unary):
    op: Literal["growth_rate"]
    lag: Lag = 1


class SourceSpread(_Unary):
    op: Literal["source_spread"]


class RevisionDiff(_Unary):
    op: Literal["revision_diff"]


class MirrorDiff(_Binary):
    op: Literal["mirror_diff"]


class Residual(_Binary):
    op: Literal["residual"]


class MovingAverage(_Unary):
    op: Literal["moving_average"]
    window: Window


class LeadLagCorr(_Binary):
    op: Literal["lead_lag_corr"]
    lag: Annotated[int, Field(strict=True, ge=-MAX_LAG, le=MAX_LAG)]
    window: Window


Operation = Annotated[
    Rescale | Diff | DiffLag | Ratio | GrowthRate | SourceSpread | RevisionDiff | MirrorDiff | Residual
    | MovingAverage | LeadLagCorr,
    Field(discriminator="op"),
]


def operation_inputs(operation: Operation) -> tuple[str, ...]:
    return (operation.input,) if isinstance(operation, _Unary) else (operation.left, operation.right)


class OutputSpec(_Model):
    fields: Annotated[list[Symbol], Field(min_length=1, max_length=MAX_ITEMS)]
    format: Annotated[list[Literal["parquet", "csv"]], Field(min_length=1, max_length=2)]


class ModelBudget(_Model):
    max_tier: ModelTier = "none"
    allow_cloud: Annotated[bool, Field(strict=True)] = False
    max_calls_per_run: Annotated[int, Field(strict=True, ge=0, le=MAX_CALLS_PER_RUN)] = 0


class RunSpec(_Model):
    trigger: Literal["manual", "on_new_generation"] = "manual"
    notify_on: Annotated[
        list[Literal["binding_stale", "definition_changed", "new_candidate", "gap_filled"]], Field(max_length=4),
    ] = Field(default_factory=list)


class DemandSpec(_Model):
    schema_version: Literal["eltiro.request/v0"] = Field(alias="schema")
    id: Id
    version: Version
    consumer: Id
    purpose: Text
    source_case: Text
    entities: Entities
    period: DemandPeriod
    revisions: Literal["latest", "all"]
    indicators: Annotated[list[Indicator], Field(min_length=1, max_length=MAX_INDICATORS)]
    align: Alignment
    operations: Annotated[list[Operation], Field(max_length=MAX_OPERATIONS)] = Field(default_factory=list)
    output: OutputSpec
    accept_outflow: Annotated[list[Outflow], Field(min_length=1, max_length=3)] = Field(
        default_factory=lambda: [Outflow.no_outflow],
    )
    models: ModelBudget = Field(default_factory=ModelBudget)
    run: RunSpec = Field(default_factory=RunSpec)

    @model_validator(mode="after")
    def _references(self) -> DemandSpec:
        indicators = {item.key for item in self.indicators}
        operations = {item.id: item for item in self.operations}
        if len(indicators) != len(self.indicators) or len(operations) != len(self.operations):
            raise ValueError("duplicate indicator or operation symbol")
        if indicators & operations.keys():
            raise ValueError("indicator and operation symbols overlap")
        known = indicators | operations.keys()
        edges = {key: set(operation_inputs(op)) for key, op in operations.items()}
        if any(inputs - known for inputs in edges.values()):
            raise ValueError("dangling operation reference")
        resolved = set(indicators)
        while edges:
            ready = {key for key, inputs in edges.items() if inputs <= resolved}
            if not ready:
                raise ValueError("operation cycle")
            resolved.update(ready)
            for key in ready:
                del edges[key]
        return self


def load_demand(path_or_bytes: str | Path | bytes) -> DemandSpec:
    """Bound bytes *before* YAML/JSON parsing. Safe YAML rejects executable tags.

    Paths are local caller-supplied paths, never demand-controlled include directives.
    Even if a file grows after stat, read at most limit + 1 bytes. UTF-8 only.
    ValueError covers size, encoding and YAML syntax; model errors are ValidationError.
    """
    if isinstance(path_or_bytes, bytes):
        content = path_or_bytes
    else:
        path = Path(path_or_bytes)
        if path.stat().st_size > MAX_FILE_BYTES:
            raise ValueError("demand exceeds MAX_FILE_BYTES")
        with path.open("rb") as stream:
            content = stream.read(MAX_FILE_BYTES + 1)
    if len(content) > MAX_FILE_BYTES:
        raise ValueError("demand exceeds MAX_FILE_BYTES")
    try:
        text = content.decode("utf-8")
        # Shared/recursive YAML graphs and merge amplification have no demand semantics.
        if any(isinstance(token, yaml.tokens.AliasToken) for token in yaml.scan(text)):
            raise ValueError("YAML aliases are not allowed in demands")
        data = yaml.safe_load(text)
    except (UnicodeError, yaml.YAMLError, RecursionError) as exc:
        raise ValueError("invalid demand encoding or YAML/JSON") from exc
    return DemandSpec.model_validate(data)


class SeriesIdentity(_Model):
    source_id: Source
    table_key: Label
    indicator: Label
    indicator_code: Label | None = None
    fixed_dimensions: Annotated[dict[Label, Label], Field(max_length=MAX_ITEMS)] = Field(default_factory=dict)


class ConceptId(_Model):
    scheme: Label
    code: Label


class Scale(_Model):
    """None means unconfirmed, never an implicit multiplier of one."""

    multiplier: Positive | None = None
    evidence: Text | None = None

    @model_validator(mode="after")
    def _evidence(self) -> Scale:
        if self.multiplier is not None and self.evidence is None:
            raise ValueError("confirmed scale needs source metadata evidence")
        return self


class SeriesProfile(_Model):
    series: SeriesIdentity
    concept_ids: Annotated[list[ConceptId], Field(max_length=MAX_ITEMS)] = Field(default_factory=list)
    names: Annotated[dict[Annotated[Lang, Field(max_length=35)], Label], Field(min_length=1, max_length=MAX_LANGUAGES)]
    descriptions: Annotated[
        dict[Annotated[Lang, Field(max_length=35)], Text], Field(max_length=MAX_LANGUAGES),
    ] = Field(default_factory=dict)
    methodology: Text | None = None
    survey: Label | None = None
    unit: Label | None = None
    scale: Scale = Field(default_factory=Scale)
    areas: Annotated[list[Label], Field(max_length=MAX_ENTITIES)] = Field(default_factory=list)
    time_start: date | None = None
    time_end: date | None = None
    frequency: Frequency
    availability: Literal["observations", "catalog_only", "document_only"]
    generation: Generation
    model_generated_fields: Annotated[
        list[Literal["concept_ids", "names", "descriptions", "methodology", "survey", "unit", "scale", "areas",
                     "time_start", "time_end", "frequency"]], Field(max_length=11),
    ] = Field(default_factory=list)

    @model_validator(mode="after")
    def _coverage(self) -> SeriesProfile:
        if self.time_start and self.time_end and self.time_start > self.time_end:
            raise ValueError("time coverage is reversed")
        return self


class MatchEvidence(_Model):
    profile_field: Literal[
        "series", "concept_ids", "names", "descriptions", "methodology", "survey", "unit", "scale", "areas",
        "time_start", "time_end", "frequency", "availability",
    ]
    source_id: Source
    snapshot_sha256: Sha256
    locator: Label


class Compatibility(_Model):
    """Unknown is unsafe for automatic binding; no positive assumptions."""

    methodology: Literal["same", "different", "unknown"] = "unknown"
    unit: Literal["same", "different", "unknown"] = "unknown"
    survey: Literal["same", "different", "unknown"] = "unknown"


class MatchRecord(_Model):
    demand_id: Id
    demand_version: Version
    indicator_key: Symbol
    series: SeriesIdentity
    tier: Literal["deterministic", "vector", "model"]
    confidence: Annotated[float, Field(strict=True, ge=0, le=1, allow_inf_nan=False)]
    reason: Text
    evidence: Annotated[list[MatchEvidence], Field(min_length=1, max_length=MAX_ITEMS)]
    generation: Generation
    confirmation: Confirmation = "pending"
    compatibility: Compatibility = Field(default_factory=Compatibility)
    auto_bind: Annotated[bool, Field(strict=True)] = False

    @model_validator(mode="after")
    def _binding(self) -> MatchRecord:
        if any(item.source_id != self.series.source_id for item in self.evidence):
            raise ValueError("evidence must belong to the candidate source")
        if self.auto_bind and (
            self.confirmation == "rejected" or any(value != "same" for value in self.compatibility.model_dump().values())
        ):
            raise ValueError("incompatible, unknown or rejected candidate cannot auto-bind")
        return self


class ModelUse(_Model):
    # Existing provider ids remain valid; routing provider names also contain underscores.
    provider: Annotated[str, Field(max_length=MAX_ID_LENGTH, pattern=r"^[a-z][a-z0-9_-]*$")]
    model: Label
    version: Label
    tier: ModelTier


ModelProvider = Literal["none", "claude_plan", "ollama", "cloud_api"]
Difficulty = Literal["low", "medium", "high"]
CLOUD_PROVIDERS = frozenset({"claude_plan", "cloud_api"})
MODEL_TIERS = ("none", "low", "medium", "high")


def content_hash(value: Any) -> str:
    """Canonical JSON SHA256; never uses Python's process-dependent hash()."""
    return sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()


def _config_text(value: str) -> str:
    # Configuration carries identifiers only, never bearer tokens or credential assignments.
    if re.fullmatch(r"sha256:[0-9a-f]{64}", value):
        return value  # Explicitly named content digests can pin local model versions.
    if re.search(r"(?i)(?:\bsk[-_]|bearer\s|(?:api[_-]?key|token|password|secret)\s*[:=]|"
                 r"AKIA[A-Z0-9]{12}|-----BEGIN)", value) or re.search(r"[A-Za-z0-9+/=_-]{40,}", value):
        raise ValueError("routing configuration must not contain credential values")
    return value


ConfigLabel = Annotated[Label, AfterValidator(_config_text)]
EnvName = Annotated[
    str, Field(min_length=1, max_length=128, pattern=r"^[A-Z][A-Z0-9_]*$"), AfterValidator(_config_text),
]


class ModelIdentity(_Model):
    model: ConfigLabel
    version: ConfigLabel


class RouteTarget(_Model):
    provider: ModelProvider
    identity: ModelIdentity | None = None
    credential_env: EnvName | None = None

    @model_validator(mode="after")
    def _target(self) -> RouteTarget:
        if (self.provider == "none") != (self.identity is None):
            raise ValueError("none has no model; every other provider requires model and version")
        if self.provider in ("none", "ollama") and self.credential_env is not None:
            raise ValueError("local/none targets do not use cloud credentials")
        return self


class DifficultyRoutes(_Model):
    low: RouteTarget
    medium: RouteTarget
    high: RouteTarget


class RoutingPolicy(_Model):
    routes: Annotated[dict[Symbol, DifficultyRoutes], Field(min_length=2, max_length=MAX_ITEMS)]

    @model_validator(mode="after")
    def _steps(self) -> RoutingPolicy:
        if not {"match_judge", "concept_note"} <= self.routes.keys():
            raise ValueError("match_judge and concept_note routes are required")
        for step in self.routes:
            _config_text(step)
        return self

    @property
    def content_hash(self) -> str:
        return content_hash(self.model_dump(mode="json"))


class ConsumerModelConfig(_Model):
    """Trusted authorization supplied by the host; only tightens registry permissions."""

    consumer: Id
    authorized_sources: Annotated[tuple[Source, ...], Field(min_length=1, max_length=MAX_ITEMS)]
    max_tier: ModelTier = "none"
    allow_cloud: Annotated[bool, Field(strict=True)] = False
    allowed_providers: Annotated[tuple[ModelProvider, ...], Field(min_length=1, max_length=4)] = ("none", "ollama")

    @model_validator(mode="after")
    def _scope(self) -> ConsumerModelConfig:
        if tuple(sorted(set(self.authorized_sources))) != self.authorized_sources:
            raise ValueError("authorized_sources must be sorted and unique")
        if len(set(self.allowed_providers)) != len(self.allowed_providers):
            raise ValueError("duplicate providers")
        return self


class ConsumerConfig(_Model):
    """Host-owned intake configuration; directory containment is checked against data_dir."""

    id: Id
    inbox: ConfigLabel
    outbox: ConfigLabel
    allowed_sources: Annotated[tuple[Source, ...], Field(min_length=1, max_length=MAX_ITEMS)]
    accept_outflow: Annotated[tuple[Outflow, ...], Field(min_length=1, max_length=3)] = (Outflow.no_outflow,)
    models: ConsumerModelConfig
    max_active_demands: Annotated[int, Field(strict=True, ge=0)]
    max_calls_per_run: Annotated[int, Field(strict=True, ge=0, le=MAX_CALLS_PER_RUN)] = 0

    @model_validator(mode="after")
    def _authorization(self) -> ConsumerConfig:
        if self.models.consumer != self.id:
            raise ValueError("model consumer must match configuration id")
        if len(set(self.allowed_sources)) != len(self.allowed_sources):
            raise ValueError("duplicate allowed source")
        if len(set(self.accept_outflow)) != len(self.accept_outflow):
            raise ValueError("duplicate outflow level")
        if not set(self.models.authorized_sources) <= set(self.allowed_sources):
            raise ValueError("model sources must be within allowed sources")
        return self


class ConsumersConfig(_Model):
    consumers: Annotated[tuple[ConsumerConfig, ...], Field(max_length=MAX_ITEMS)]

    @model_validator(mode="after")
    def _unique(self) -> ConsumersConfig:
        if len({item.id for item in self.consumers}) != len(self.consumers):
            raise ValueError("duplicate consumer id")
        return self


class ModelInputPart(_Model):
    source_id: Source
    content: Text  # Untrusted source data, never instructions to the router.


class ModelInput(_Model):
    id: Symbol
    step: Symbol
    difficulty: Difficulty
    parts: Annotated[tuple[ModelInputPart, ...], Field(min_length=1, max_length=MAX_ITEMS)]
    splittable: Annotated[bool, Field(strict=True)] = False
    prompt_version: Label

    @model_validator(mode="after")
    def _sources(self) -> ModelInput:
        if len({part.source_id for part in self.parts}) != len(self.parts):
            raise ValueError("one part per source is required")
        return self


class ModelCacheKey(_Model):
    """Exactly eight components; model identity includes its pinned version."""

    consumer: Id
    authorized_sources: Annotated[tuple[Source, ...], Field(min_length=1, max_length=MAX_ITEMS)]
    generation_token: GenerationToken
    input_sha256: Sha256
    provider: Literal["claude_plan", "ollama", "cloud_api"]
    identity: ModelIdentity
    policy_sha256: Sha256
    prompt_version: Label

    @model_validator(mode="after")
    def _scope(self) -> ModelCacheKey:
        if tuple(sorted(set(self.authorized_sources))) != self.authorized_sources:
            raise ValueError("cache authorization scope must be sorted and unique")
        return self

    @property
    def digest(self) -> str:
        return content_hash(self.model_dump(mode="json"))


class RouteDecision(_Model):
    request: ModelInput
    consumer: Id
    authorized_sources: Annotated[tuple[Source, ...], Field(min_length=1, max_length=MAX_ITEMS)]
    generation_token: GenerationToken
    policy_sha256: Sha256
    target: RouteTarget
    tier: ModelTier
    downgraded: Annotated[bool, Field(strict=True)]
    reasons: Annotated[tuple[Label, ...], Field(max_length=16)] = ()

    @model_validator(mode="after")
    def _decision(self) -> RouteDecision:
        if (self.tier == "none") != (self.target.provider == "none"):
            raise ValueError("none tier and provider must agree")
        if MODEL_TIERS.index(self.tier) > MODEL_TIERS.index(self.request.difficulty):
            raise ValueError("cannot upgrade above requested difficulty")
        if self.downgraded != (self.tier != self.request.difficulty):
            raise ValueError("downgrade flag disagrees with selected tier")
        if self.downgraded and not self.reasons:
            raise ValueError("downgrades require reasons")
        if self.target.provider != "none":
            self.cache_key()  # Also validate canonical scope and complete cache identity.
        return self

    def cache_key(self) -> ModelCacheKey:
        return ModelCacheKey(
            consumer=self.consumer, authorized_sources=self.authorized_sources,
            generation_token=self.generation_token,
            input_sha256=content_hash(self.request.model_dump(mode="json")),
            provider=self.target.provider, identity=self.target.identity,
            policy_sha256=self.policy_sha256, prompt_version=self.request.prompt_version,
        )


class ManualQueueItem(_Model):
    request: ModelInput
    reason: Label
    decision: RouteDecision | None = None


class RoutePlan(_Model):
    run_id: Id
    budget: ModelBudget
    items: Annotated[tuple[RouteDecision | ManualQueueItem, ...], Field(max_length=MAX_ITEMS)]


class ModelField(_Model):
    value: Text
    model_use: ModelUse


class ModelResult(_Model):
    fields: Annotated[dict[Symbol, ModelField], Field(min_length=1, max_length=MAX_ITEMS)]

    def validate_route(self, decision: RouteDecision) -> None:
        identity = decision.target.identity
        if identity is None:
            raise ValueError("none cannot produce model fields")
        expected = ModelUse(provider=decision.target.provider, model=identity.model,
                            version=identity.version, tier=decision.tier)
        if any(field.model_use != expected for field in self.fields.values()):
            raise ValueError("every model field must identify the selected route")


class LicenseSummary(_Model):
    """Audit snapshot only; the registry remains authoritative at every gate."""

    outflow: Outflow = Outflow.no_outflow
    by_source: Annotated[dict[Source, Outflow], Field(max_length=MAX_ITEMS)] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _strictest(self) -> LicenseSummary:
        rank = {Outflow.raw_ok: 0, Outflow.derived_only: 1, Outflow.no_outflow: 2}
        if any(rank[level] > rank[self.outflow] for level in self.by_source.values()):
            raise ValueError("license summary cannot be less restrictive than an input")
        return self


class DataFile(_Model):
    # A basename only: no traversal, shell commands, absolute paths or raw attachments.
    name: Annotated[str, Field(max_length=MAX_LABEL_LENGTH, pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_-]*\.(?:parquet|csv)$")]
    sha256: Sha256


class InputObservation(_Model):
    source_id: Source
    table_key: Label
    obs_id: Label
    snapshot_sha256: Sha256
    release_id: Label  # opaque until 002; no release identity format is prescribed here


class CitationEntry(_Model):
    inputs: Annotated[list[InputObservation], Field(min_length=1, max_length=MAX_ITEMS)]
    operation_chain: Annotated[list[Operation], Field(max_length=MAX_OPERATIONS)] = Field(default_factory=list)


class OutputManifest(_Model):
    demand_id: Id
    demand_version: Version
    generation: Generation
    generation_token: Label  # opaque until 002
    run_at: AwareDatetime
    models: Annotated[list[ModelUse], Field(max_length=MAX_ITEMS)] = Field(default_factory=list)
    license_summary: LicenseSummary = Field(default_factory=LicenseSummary)
    files: Annotated[list[DataFile], Field(max_length=MAX_ITEMS)] = Field(default_factory=list)
    citation_index: Annotated[dict[Symbol, CitationEntry], Field(max_length=MAX_ITEMS)] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _files(self) -> OutputManifest:
        if len({item.name for item in self.files}) != len(self.files):
            raise ValueError("duplicate data file name")
        sources = {obs.source_id for entry in self.citation_index.values() for obs in entry.inputs}
        # Missing permissions are *represented* as no_outflow, never inferred permissive.
        if sources - self.license_summary.by_source.keys() and self.license_summary.outflow != Outflow.no_outflow:
            raise ValueError("missing input license requires no_outflow")
        return self


SCHEMA_TYPES: dict[str, Any] = {
    cls.__name__: cls for cls in (
        Region, Entities, DemandPeriod, Examples, Indicator, Alignment, Rescale, Diff, DiffLag, Ratio, GrowthRate,
        SourceSpread, RevisionDiff, MirrorDiff, Residual, MovingAverage, LeadLagCorr, OutputSpec, ModelBudget,
        RunSpec, DemandSpec, SeriesIdentity, ConceptId, Scale, SeriesProfile, MatchEvidence, Compatibility,
        MatchRecord, ModelUse, LicenseSummary, DataFile, InputObservation, CitationEntry, OutputManifest,
        ModelIdentity, RouteTarget, DifficultyRoutes, RoutingPolicy, ConsumerModelConfig, ModelInputPart,
        ModelInput, ModelCacheKey, RouteDecision, ManualQueueItem, RoutePlan, ModelField, ModelResult,
        ConsumerConfig, ConsumersConfig,
    )
}
SCHEMA_TYPES["Operation"] = Operation


def export_schemas(directory: str | Path) -> list[Path]:
    out = Path(directory)
    out.mkdir(parents=True, exist_ok=True)
    written = []
    for name, tp in SCHEMA_TYPES.items():
        path = out / f"{name}.schema.json"
        path.write_text(json.dumps(TypeAdapter(tp).json_schema(by_alias=True), ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8")
        written.append(path)
    for stale in set(out.glob("*.schema.json")) - set(written):
        stale.unlink()
    return written


if __name__ == "__main__":
    export_schemas(sys.argv[1] if len(sys.argv) > 1 else "schemas/eltiro")
