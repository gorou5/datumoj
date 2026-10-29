"""Safe failure reasons for persisted records and shared output.

Only exact project exception types with an exact, code-owned message may retain
that message. Arbitrary exception text (including subclasses) is never rendered.
Local CLI operation errors may deliberately show full messages to the operator.
"""

from datumoj.rikolto.core.budget import BudgetExceeded
from datumoj.rikolto.core.contracts import ParseError

MAX_REASON_LENGTH = 240
_BUDGET_MESSAGES = frozenset(
    f"budget_exceeded:{item}" for item in ("pages", "text_chars", "output_chunks", "duration_seconds")
)
_PARSE_MESSAGES = frozenset({
    "PDF is encrypted and requires a password",
    "PDF is damaged or cannot be read",
    "PDF 快照没有正文",
    "PDF 没有页面",
    "empty_content: HTML 页面正文少于 100 字",
})


def safe_reason(exc: BaseException) -> str:
    """Return a bounded, printable reason without invoking untrusted ``__str__``.

    Budget stop codes retain their historical format. Other allowed messages
    follow the exception type; unknown messages yield only the type name.
    """
    reason = type(exc).__name__
    if len(exc.args) == 1 and type(exc.args[0]) is str:
        message = exc.args[0]
        if type(exc) is BudgetExceeded and message in _BUDGET_MESSAGES:
            reason = message
        elif type(exc) is ParseError and message in _PARSE_MESSAGES:
            reason = f"{reason}: {message}"
    return "".join(char for char in reason if char.isprintable())[:MAX_REASON_LENGTH]
