"""Cooperative limits for parsing one snapshot."""

from __future__ import annotations

import math
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass

from datumoj.core.contracts import ParseError

DEFAULT_MAX_PAGES = 1_000
DEFAULT_MAX_TEXT_CHARS = 5_000_000
DEFAULT_MAX_OUTPUT_CHUNKS = 10_000
DEFAULT_MAX_PARSE_SECONDS = 60.0
PARSE_BUDGET_META_KEY = "_datumoj_parse_budget"


class BudgetExceeded(ParseError):
    """A parser crossed a configured per-snapshot limit."""

    def __init__(self, item: str):
        self.item = item
        super().__init__(f"budget_exceeded:{item}")


@dataclass(frozen=True)
class ParseBudget:
    max_pages: int = DEFAULT_MAX_PAGES
    max_text_chars: int = DEFAULT_MAX_TEXT_CHARS
    max_output_chunks: int = DEFAULT_MAX_OUTPUT_CHUNKS
    max_seconds: float = DEFAULT_MAX_PARSE_SECONDS

    def __post_init__(self) -> None:
        for name in ("max_pages", "max_text_chars", "max_output_chunks"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValueError(f"{name} 必须是正整数")
        if (
            isinstance(self.max_seconds, bool)
            or not isinstance(self.max_seconds, int | float)
            or not math.isfinite(self.max_seconds)
            or self.max_seconds <= 0
        ):
            raise ValueError("max_seconds 必须是有限正数")

    @classmethod
    def from_scope(cls, scope: Mapping[str, object]) -> ParseBudget:
        """Read optional ``scope.parse_budget`` overrides."""
        if "parse_budget" not in scope:
            return cls()
        overrides = scope["parse_budget"]
        if not isinstance(overrides, Mapping):
            raise ValueError("scope.parse_budget 必须是映射")
        return cls.from_mapping(overrides)

    @classmethod
    def from_mapping(cls, overrides: Mapping[str, object]) -> ParseBudget:
        allowed = {"max_pages", "max_text_chars", "max_output_chunks", "max_seconds"}
        unknown = set(overrides) - allowed
        if unknown:
            raise ValueError(f"parse_budget 包含未知字段：{sorted(unknown)}")
        try:
            return cls(**dict(overrides))
        except TypeError as exc:
            raise ValueError(f"parse_budget 字段无效：{exc}") from exc

    def as_dict(self) -> dict[str, int | float]:
        return {
            "max_pages": self.max_pages,
            "max_text_chars": self.max_text_chars,
            "max_output_chunks": self.max_output_chunks,
            "max_seconds": self.max_seconds,
        }


def budget_from_meta(meta: Mapping[str, object] | None) -> ParseBudget:
    values = (meta or {}).get(PARSE_BUDGET_META_KEY)
    if values is None:
        return ParseBudget()
    if not isinstance(values, Mapping):
        raise ValueError(f"{PARSE_BUDGET_META_KEY} 必须是映射")
    return ParseBudget.from_mapping(values)


class BudgetChecker:
    """Counters and a monotonic timer that parsers can check cooperatively."""

    def __init__(self, budget: ParseBudget | None = None, clock: Callable[[], float] = time.monotonic):
        self.budget = budget or ParseBudget()
        self.clock = clock
        self.started = clock()
        self.pages = 0
        self.text_chars = 0
        self.output_chunks = 0

    def check_time(self) -> None:
        if self.clock() - self.started > self.budget.max_seconds:
            raise BudgetExceeded("duration_seconds")

    def check_pages(self, count: int) -> None:
        self.check_time()
        if count > self.budget.max_pages:
            raise BudgetExceeded("pages")

    def add_page(self) -> None:
        self.check_time()
        self.pages += 1
        self.check_pages(self.pages)

    def check_text_chars(self, count: int) -> None:
        self.check_time()
        if count > self.budget.max_text_chars:
            raise BudgetExceeded("text_chars")

    def add_text(self, count: int) -> None:
        self.check_time()
        self.text_chars += count
        self.check_text_chars(self.text_chars)

    def add_output_chunk(self) -> None:
        self.check_time()
        self.output_chunks += 1
        if self.output_chunks > self.budget.max_output_chunks:
            raise BudgetExceeded("output_chunks")

