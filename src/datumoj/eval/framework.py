"""离线评测题库、指标、报告与门槛。"""

from __future__ import annotations

import json
import re
import unicodedata
from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, model_validator

from datumoj.core.contracts import (
    CatalogFilter,
    CatalogStore,
    Manifest,
    ObservationStore,
    Retriever,
    citation_for_observation,
)
from datumoj.core.models import (
    Citation,
    DocKey,
    FetchOutcome,
    GapState,
    Hit,
    Lang,
    Observation,
    SourceId,
    SourceText,
    TimeBasis,
)


QuestionKind = Literal["retrieval", "numeric", "gap", "time", "injection", "cross_source"]


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ExpectedHit(_StrictModel):
    doc_key: DocKey
    locator_prefix: str | None = None

    @model_validator(mode="after")
    def _valid_prefix(self) -> ExpectedHit:
        if self.locator_prefix is not None:
            from datumoj.core.models import parse_locator

            parse_locator(self.locator_prefix)
        return self


class ExpectedValue(_StrictModel):
    sql: str = Field(min_length=1)
    equals: Decimal
    tolerance: Decimal = Field(default=Decimal(0), ge=0)

    @model_validator(mode="after")
    def _nonblank_sql(self) -> ExpectedValue:
        if not self.sql.strip():
            raise ValueError("sql 不能为空白")
        return self


class Expect(_StrictModel):
    hits: list[ExpectedHit] | None = None
    value: ExpectedValue | None = None
    gap_state: GapState | None = None


class Question(_StrictModel):
    id: str = Field(min_length=1, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
    question: str = Field(min_length=1)
    lang: Lang
    kind: QuestionKind
    queries: list[str] = Field(min_length=1)
    source_ids: list[SourceId] | None = None
    expect: Expect
    must_not: list[str] = Field(default_factory=list)
    notes: str | None = None
    _source_file: str | None = PrivateAttr(default=None)

    @model_validator(mode="after")
    def _kind_and_queries(self) -> Question:
        if not self.question.strip():
            raise ValueError("question 不能为空白")
        if any(not query.strip() for query in self.queries):
            raise ValueError("queries 不能包含空白查询")
        if self.kind in {"retrieval", "numeric", "time", "injection", "cross_source"} and not self.expect.hits:
            raise ValueError(f"{self.kind} 题必须填写非空 expect.hits")
        if self.kind == "numeric" and self.expect.value is None:
            raise ValueError("numeric 题必须填写 expect.value")
        if self.kind == "gap" and self.expect.gap_state is None:
            raise ValueError("gap 题必须填写 expect.gap_state")
        if self.kind == "injection" and not self.must_not:
            raise ValueError("injection 题必须在 must_not 中列出注入文本")
        if self.kind == "cross_source" and len(
            {hit.doc_key.split(":", 1)[0] for hit in self.expect.hits or []}
        ) < 2:
            raise ValueError("cross_source 题的 expect.hits 必须覆盖至少两个来源")
        if any(not marker.strip() for marker in self.must_not):
            raise ValueError("must_not 不能包含空文本")
        return self


DEFAULT_THRESHOLDS: dict[str, float] = {
    "recall_at_10": 0.85,
    "numeric_precision": 1.0,
    "numeric_occurrence": 1.0,
    "gap_accuracy": 1.0,
    "citation_recoverability": 0.95,
}

METRIC_NAMES = (
    "recall_at_10",
    "mrr",
    "numeric_precision",
    "numeric_occurrence",
    "gap_accuracy",
    "time_semantics_accuracy",
    "injection_containment_accuracy",
    "citation_recoverability",
)


@dataclass(frozen=True)
class EvaluationReport:
    overall: dict[str, float | None]
    by_source: dict[str, dict[str, float | None]]
    by_language: dict[str, dict[str, float | None]]
    questions: list[dict[str, Any]]
    failures: list[dict[str, Any]]

    def to_dict(self) -> dict[str, Any]:
        return {
            "overall": self.overall,
            "by_source": self.by_source,
            "by_language": self.by_language,
            "questions": self.questions,
            "failures": self.failures,
        }


@dataclass(frozen=True)
class GateResult:
    passed: bool
    thresholds: dict[str, float]
    failures: list[str]

    def __bool__(self) -> bool:
        return self.passed


class _Totals:
    def __init__(self) -> None:
        self.values: dict[str, list[tuple[float, float]]] = defaultdict(list)

    def add(self, metrics: Mapping[str, float | None], weights: Mapping[str, float] | None = None) -> None:
        for name, value in metrics.items():
            weight = (weights or {}).get(name, 1.0)
            if value is not None and weight > 0:
                self.values[name].append((value, weight))

    def result(self) -> dict[str, float | None]:
        return {
            name: (
                sum(value * weight for value, weight in values) / sum(weight for _, weight in values)
                if values
                else None
            )
            for name in METRIC_NAMES
            for values in (self.values[name],)
        }


def load_questions(directory: str | Path) -> list[Question]:
    """递归加载 YAML/JSON 题库；每个文件可放一个题目、题目列表或 ``questions`` 列表。"""
    root = Path(directory)
    if not root.is_dir():
        raise ValueError(f"题库目录不存在或不是目录：{root}")
    files = sorted((*root.rglob("*.yaml"), *root.rglob("*.yml"), *root.rglob("*.json")))
    questions: list[Question] = []
    ids: set[str] = set()
    for path in files:
        try:
            raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise ValueError(f"{path}: question #1: 无法读取题库：{exc}") from exc
        if isinstance(raw, Mapping) and "questions" in raw:
            entries = raw["questions"]
        elif isinstance(raw, list):
            entries = raw
        elif isinstance(raw, Mapping):
            entries = [raw]
        else:
            raise ValueError(f"{path}: question #1: 文件顶层必须是题目、题目列表或 questions 对象")
        if not isinstance(entries, list):
            raise ValueError(f"{path}: question #1: questions 必须是列表")
        for index, entry in enumerate(entries, 1):
            label = entry.get("id", f"#{index}") if isinstance(entry, Mapping) else f"#{index}"
            try:
                question = Question.model_validate(entry)
            except Exception as exc:
                raise ValueError(f"{path}: question {label}: 格式错误：{exc}") from exc
            if question.id in ids:
                raise ValueError(f"{path}: question {question.id}: id 重复")
            ids.add(question.id)
            question._source_file = str(path)
            questions.append(question)
    return questions


def evaluate(
    questions: Sequence[Question],
    *,
    retriever: Retriever,
    observation_store: ObservationStore,
    cite: Callable[[Citation], Any] | None,
    manifest: Manifest | None = None,
    catalog: CatalogStore | None = None,
    k: int = 10,
) -> EvaluationReport:
    """通过契约注入检索器、只读 SQL 库、可选快照清单及出处还原函数，离线计算题库指标。"""
    if k < 1:
        raise ValueError("k 必须 ≥ 1")
    all_metrics: list[tuple[dict[str, float | None], int]] = []
    question_rows: list[dict[str, Any]] = []
    failure_rows: list[dict[str, Any]] = []
    languages: dict[str, _Totals] = defaultdict(_Totals)
    sources: dict[str, _Totals] = defaultdict(_Totals)

    for question in questions:
        reasons: list[str] = []
        result = retriever.search(question.queries, k=max(10, k, 3), source_ids=question.source_ids)
        hits = result.data
        catalog_results = []
        if catalog is not None:
            filters = CatalogFilter(source_ids=question.source_ids) if question.source_ids is not None else None
            catalog_results = [
                [entry.dataset.dataset_key for entry in catalog.search(query, filters=filters, k=max(10, k, 3))]
                for query in question.queries
            ]
        catalog_ranks = _catalog_ranks(catalog_results, k)
        metrics: dict[str, float | None] = {name: None for name in METRIC_NAMES}
        resolved: list[tuple[Hit, Any]] = []
        attempts = hits[:3]
        for hit in attempts:
            try:
                evidence = cite(hit.citation) if cite is not None else None
            except Exception:
                evidence = None
            resolved.append((hit, evidence))
        metrics["citation_recoverability"] = (
            sum(_has_evidence(evidence) for _, evidence in resolved) / len(resolved) if resolved else None
        )
        if metrics["citation_recoverability"] is not None and metrics["citation_recoverability"] < 1.0:
            reasons.append("前 3 条命中中有出处无法还原")

        expected_hits = question.expect.hits or []
        if expected_hits:
            metrics["recall_at_10"], metrics["mrr"] = _retrieval_metrics(
                expected_hits, hits[:k], catalog_ranks
            )
            if metrics["recall_at_10"] < 1.0:
                reasons.append("预期命中未全部出现在前 k 条结果中")

        if question.expect.value is not None:
            try:
                query_result = observation_store.query_readonly(question.expect.value.sql)
                actual = _scalar(query_result.rows, query_result.columns)
                expected = question.expect.value.equals
                tolerance = question.expect.value.tolerance
                numeric_ok = actual is not None and abs(actual - expected) <= tolerance
                metrics["numeric_precision"] = float(numeric_ok)
                if not numeric_ok:
                    reasons.append("SQL 数值与 expect.value.equals 不符")
                occurrence = any(_has_number(evidence, expected) for _, evidence in resolved)
                if not occurrence:
                    occurrence = _has_sql_number(query_result.provenance, expected, manifest, cite)
                metrics["numeric_occurrence"] = float(occurrence)
                if not occurrence:
                    reasons.append("文档检索前 3 条引用与 SQL 出处行中均未找到可还原的期望数值")
            except Exception as exc:
                metrics["numeric_precision"] = 0.0
                metrics["numeric_occurrence"] = 0.0
                reasons.append(f"只读 SQL 评测失败：{type(exc).__name__}: {exc}")

        if question.expect.gap_state is not None:
            gap_ok = result.gap_state is question.expect.gap_state
            metrics["gap_accuracy"] = float(gap_ok)
            if not gap_ok:
                reasons.append(f"缺口状态错误：期望 {question.expect.gap_state.value}，实际 {result.gap_state.value}")

        if question.kind == "time":
            relevant = [hit for hit in hits if _is_expected(hit, expected_hits)] if expected_hits else hits
            time_ok = any(
                hit.citation.period is not None
                or hit.citation.published_at is not None
                or (hit.citation.time_basis is TimeBasis.effective and hit.citation.effective_at is not None)
                for hit in relevant
            )
            metrics["time_semantics_accuracy"] = float(time_ok)
            if not time_ok:
                reasons.append("相关出处没有统计期间或发布日")

        if question.kind == "injection":
            injection_ok = all(_marker_only_in_source_text(result, marker) for marker in question.must_not)
            metrics["injection_containment_accuracy"] = float(injection_ok)
            if not injection_ok:
                reasons.append("注入文本缺失或出现在 SourceText.content 之外")

        all_metrics.append((metrics, len(resolved)))
        languages[question.lang].add(metrics, {"citation_recoverability": float(len(resolved))})
        expected_sources = sorted({hit.doc_key.split(":", 1)[0] for hit in expected_hits})
        question_sources = (
            expected_sources or question.source_ids or sorted({hit.citation.source_id for hit in hits}) or ["unknown"]
        )
        for source_id in question_sources:
            scoped = dict(metrics)
            source_expected = [h for h in expected_hits if h.doc_key.startswith(f"{source_id}:")]
            if source_expected:
                source_catalog_ranks = _catalog_ranks(catalog_results, k, source_id)
                scoped["recall_at_10"], scoped["mrr"] = _retrieval_metrics(
                    source_expected,
                    [hit for hit in hits[:k] if hit.citation.source_id == source_id],
                    source_catalog_ranks,
                )
            source_resolved = [(hit, evidence) for hit, evidence in resolved if hit.citation.source_id == source_id]
            scoped["citation_recoverability"] = (
                sum(_has_evidence(evidence) for _, evidence in source_resolved) / len(source_resolved)
                if source_resolved
                else None
            )
            sources[source_id].add(scoped, {"citation_recoverability": float(len(source_resolved))})

        row = {
            "id": question.id,
            "file": question._source_file,
            "kind": question.kind,
            "lang": question.lang,
            "metrics": metrics,
            "failures": reasons,
        }
        question_rows.append(row)
        if reasons:
            failure_rows.append({"id": question.id, "file": question._source_file, "kind": question.kind, "reasons": reasons})

    overall = _Totals()
    for metrics, citation_attempts in all_metrics:
        overall.add(metrics, {"citation_recoverability": float(citation_attempts)})
    return EvaluationReport(
        overall=overall.result(),
        by_source={key: value.result() for key, value in sorted(sources.items())},
        by_language={key: value.result() for key, value in sorted(languages.items())},
        questions=question_rows,
        failures=failure_rows,
    )


def _catalog_ranks(
    results: Sequence[Sequence[str]], k: int, source_id: str | None = None
) -> dict[str, int]:
    ranks: dict[str, int] = {}
    for result in results:
        candidates = list(result[:k])
        if source_id is not None:
            candidates = [key for key in candidates if key.startswith(f"{source_id}:")]
        for rank, dataset_key in enumerate(candidates, 1):
            ranks[dataset_key] = min(rank, ranks.get(dataset_key, rank))
    return ranks


def _retrieval_metrics(
    expected: Sequence[Any], hits: Sequence[Hit], catalog_ranks: Mapping[str, int] | None = None
) -> tuple[float, float]:
    catalog_ranks = catalog_ranks or {}
    doc_ranks = [next((rank for rank, hit in enumerate(hits, 1) if _matches(hit, item)), None) for item in expected]
    expected_ranks = []
    for item, doc_rank in zip(expected, doc_ranks, strict=True):
        catalog_rank = catalog_ranks.get(item.doc_key) if item.locator_prefix is None else None
        ranks = [rank for rank in (doc_rank, catalog_rank) if rank is not None]
        expected_ranks.append(min(ranks) if ranks else None)
    matched = [rank is not None for rank in expected_ranks]
    recall = sum(matched) / len(expected)
    rank = min((rank for rank in expected_ranks if rank is not None), default=None)
    return recall, 0.0 if rank is None else 1.0 / rank


def _matches(hit: Hit, expected: Any) -> bool:
    if hit.doc_key != expected.doc_key:
        return False
    return expected.locator_prefix is None or hit.locator == expected.locator_prefix or hit.locator.startswith(
        expected.locator_prefix + ";"
    )


def _is_expected(hit: Hit, expected: Sequence[Any]) -> bool:
    return any(_matches(hit, item) for item in expected)


def _scalar(rows: Sequence[Sequence[Any]], columns: Sequence[str] = ()) -> Decimal | None:
    if len(rows) != 1 or not rows[0]:
        return None
    value_index = columns.index("value") if "value" in columns else 0
    if value_index >= len(rows[0]):
        return None
    value = rows[0][value_index]
    if value is None or isinstance(value, bool):
        return None
    try:
        return Decimal(str(value).replace(",", ""))
    except (InvalidOperation, ValueError):
        return None


def _has_evidence(value: Any) -> bool:
    if value is None or value is False:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, SourceText):
        return bool(value.content.strip())
    if isinstance(value, Observation):
        return True
    if isinstance(value, Mapping):
        return any(_has_evidence(item) for item in value.values())
    if isinstance(value, Sequence) and not isinstance(value, str | bytes):
        return any(_has_evidence(item) for item in value)
    return False


def _flatten(value: Any) -> list[str]:
    if isinstance(value, SourceText):
        return [value.content]
    if isinstance(value, Observation):
        return [str(value.value)] if value.value is not None else []
    if isinstance(value, str):
        return [value]
    if isinstance(value, Mapping):
        return [part for item in value.values() for part in _flatten(item)]
    if isinstance(value, Sequence) and not isinstance(value, str | bytes):
        return [part for item in value for part in _flatten(item)]
    if isinstance(value, int | float | Decimal) and not isinstance(value, bool):
        return [str(value)]
    return []


def _canonical_number(value: Decimal) -> str:
    text = format(value.normalize(), "f")
    return "0" if text in {"-0", ""} else text


def _has_sql_number(
    provenance: Sequence[Observation],
    expected: Decimal,
    manifest: Manifest | None,
    cite: Callable[[Citation], Any] | None,
) -> bool:
    if manifest is None or cite is None:
        return False
    target = _canonical_number(expected)
    for obs in provenance:
        if obs.value is None or _canonical_number(obs.value) != target:
            continue
        try:
            snapshot = next(
                snapshot
                for snapshot in manifest.snapshots_by_sha256(obs.snapshot_sha256)
                if snapshot.source_id == obs.source_id
                and snapshot.fetched_at == obs.fetched_at
                and snapshot.outcome is FetchOutcome.ok
            )
            evidence = cite(citation_for_observation(obs, snapshot))
        except Exception:
            continue
        if _has_evidence(evidence):
            return True
    return False


def _has_number(evidence: Any, expected: Decimal) -> bool:
    number_re = re.compile(r"(?<![\d.])[-+]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?(?![\d.])")
    target = _canonical_number(expected)
    for text in _flatten(evidence):
        normalized = unicodedata.normalize("NFKC", text).replace("−", "-")
        for candidate in number_re.findall(normalized):
            try:
                if _canonical_number(Decimal(candidate.replace(",", ""))) == target:
                    return True
            except InvalidOperation:
                continue
    return False


def _marker_only_in_source_text(value: Any, marker: str) -> bool:
    found_source = False
    found_other = False

    def visit(item: Any, allowed: bool = False) -> None:
        nonlocal found_source, found_other
        if isinstance(item, SourceText):
            visit(item.content, True)
        elif isinstance(item, BaseModel):
            for name in type(item).model_fields:
                visit(getattr(item, name), False)
        elif isinstance(item, Mapping):
            for child in item.values():
                visit(child, False)
        elif isinstance(item, Sequence) and not isinstance(item, str | bytes):
            for child in item:
                visit(child, False)
        elif isinstance(item, str) and marker in item:
            if allowed:
                found_source = True
            else:
                found_other = True

    visit(value)
    return found_source and not found_other


def check_gate(
    report: EvaluationReport | Mapping[str, Any], thresholds: Mapping[str, float] | None = None
) -> GateResult:
    """按默认门槛或传入覆盖值判定；未评测到的门槛指标视为失败。"""
    selected = dict(DEFAULT_THRESHOLDS)
    if thresholds is not None:
        unknown = set(thresholds) - set(DEFAULT_THRESHOLDS)
        if unknown:
            raise ValueError(f"未知门槛指标：{', '.join(sorted(unknown))}")
        selected.update(thresholds)
    for name, threshold in selected.items():
        if not 0 <= threshold <= 1:
            raise ValueError(f"门槛 {name} 必须在 0 到 1 之间")
    overall = report.overall if isinstance(report, EvaluationReport) else report.get("overall", {})
    failures = []
    for name, threshold in selected.items():
        value = overall.get(name)
        if value is None:
            failures.append(f"{name}: 未评测（门槛 ≥ {threshold:.3f}）")
        elif value < threshold:
            failures.append(f"{name}: {value:.3f} < {threshold:.3f}")
    return GateResult(passed=not failures, thresholds=selected, failures=failures)


def write_reports(report: EvaluationReport, directory: str | Path) -> tuple[Path, Path]:
    """写入 evaluation.json 与 evaluation.md。"""
    target = Path(directory)
    target.mkdir(parents=True, exist_ok=True)
    json_path = target / "evaluation.json"
    markdown_path = target / "evaluation.md"
    json_path.write_text(json.dumps(report.to_dict(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    markdown_path.write_text(_markdown(report), encoding="utf-8")
    return markdown_path, json_path


def _markdown(report: EvaluationReport) -> str:
    lines = ["# Offline evaluation", "", "## Overall", "", "| Metric | Score |", "| --- | ---: |"]
    lines.extend(f"| {name} | {_score(value)} |" for name, value in report.overall.items())
    for title, groups in (("By source", report.by_source), ("By language", report.by_language)):
        lines.extend(["", f"## {title}", ""])
        if not groups:
            lines.append("No results.")
            continue
        lines.extend(["| Group | " + " | ".join(METRIC_NAMES) + " |", "| --- | " + " | ".join("---:" for _ in METRIC_NAMES) + " |"])
        for group, metrics in groups.items():
            lines.append("| " + group + " | " + " | ".join(_score(metrics[name]) for name in METRIC_NAMES) + " |")
    lines.extend(["", "## Failed questions", ""])
    if report.failures:
        for failure in report.failures:
            lines.append(f"- `{failure['id']}` ({failure['kind']}): {'; '.join(failure['reasons'])}")
    else:
        lines.append("None.")
    return "\n".join(lines) + "\n"


def _score(value: float | None) -> str:
    return "N/A" if value is None else f"{value:.3f}"
