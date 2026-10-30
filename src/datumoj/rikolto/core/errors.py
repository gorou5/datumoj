"""Safe failure reasons for persisted records and shared output.

Only exact exception types with a full code-owned message template may retain
that message. Arbitrary exception text (including subclasses) is never rendered.
Local CLI operation errors may deliberately show full messages to the operator.
"""

import re

from datumoj.rikolto.core.budget import BudgetExceeded
from datumoj.rikolto.core.contracts import (
    AmbiguousRelease,
    GenerationUnavailable,
    InvalidGenerationToken,
    ParseError,
    QueryError,
    QueryRejected,
    QueryTimeout,
    ReleaseDateUnavailable,
    ReleaseNotFound,
    TooManyRows,
)

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

_NAME = r"[A-Za-z_][A-Za-z0-9_]{0,63}"
_INTEGER = r"[0-9]+"
_NUMBER = r"[0-9]+(?:\.[0-9]+)?"
# Match the raw message before cleaning it. Never admit arbitrary exception
# suffixes, SQL diagnostics, paths, source text, or whitespace via placeholders.
_MESSAGE_TEMPLATES = tuple((kind, re.compile(pattern)) for kind, pattern in (
    (ValueError, rf"{_NAME} 必须是 {_INTEGER}–{_INTEGER} 的整数"),
    (ValueError, rf"{_NAME} 必须是非空的字符串列表"),
    (ValueError, rf"{_NAME} 里不能有空白或非字符串"),
    (ValueError, rf"{_NAME} 必须是日期或 YYYY-MM-DD"),
    (ValueError, rf"{_NAME} 必须是带时区的 ISO-8601 日期时间或 YYYY-MM-DD"),
    (ValueError, rf"{_NAME} 必须带时区"),
    (ValueError, rf"{_NAME} 必须是非空字符串"),
    (ValueError, r"invalid JSON object"),
    (ValueError, r"invalid JSON array"),
    (ValueError, r"since 不能晚于 until"),
    (ValueError, r"since/until datetime 必须带时区"),
    (ValueError, r"kinds 只能包含 law, html_page, pdf, dataset, bulletin"),
    (ValueError, r"kinds 只能包含 added, modified, removed, fetch_failed, parse_failed"),
    (ValueError, r"query 必须是字符串（可以为空，表示只按条件列出）"),
    (ValueError, r"query 不能为空白"),
    (ValueError, r"sql 不能为空白"),
    (ValueError, r"timeout_s 必须是有限正数"),
    (ValueError, r"chunk_id 不能为空白"),
    (ValueError, r"snapshot_sha256 必须是 64 位小写十六进制"),
    (ValueError, r"locator 必须是字符串"),
    (ValueError, r"max_rows 必须 ≥ 1，timeout_s 必须 > 0"),
    (ValueError, r"timeout_s 必须 > 0"),
    (ValueError, r"as_of_release 不能是空发布标识"),
    (ValueError, r"as_of_release 日期对象须只有 date 字段"),
    (QueryTimeout, rf"query_sql 整个请求超过 {_NUMBER} 秒"),
    (QueryTimeout, rf"查询超过 {_NUMBER} 秒"),
    (QueryTimeout, rf"describe 超过 {_NUMBER} 秒"),
    (QueryTimeout, rf"查询（含连接、查询和出处回查）超过 {_NUMBER} 秒"),
    (TooManyRows, rf"结果超过 {_INTEGER} 行，请加条件或聚合"),
    (QueryRejected, r"只允许单条 SELECT / WITH 查询"),
    (QueryRejected, r"不允许多条语句"),
    (QueryRejected, rf"不允许使用 '{_NAME}'（读文件或执行 SQL 串）"),
    (QueryError, r"发布感知读取需要持久化 SnapshotOrder"),
    (QueryError, r"历史段引用无效"),
    (QueryError, r"历史文件引用无效"),
    (QueryError, r"发布历史段路径无效"),
    (QueryError, r"发布历史段冲突"),
    (QueryError, r"历史迁移核对不一致"),
    (QueryError, r"历史迁移路径无效"),
    (InvalidGenerationToken, r"generation_token 格式无效"),
    (InvalidGenerationToken, r"generation_token 清单哈希不匹配"),
    (InvalidGenerationToken, r"generation_token 必须显式提供"),
    (GenerationUnavailable, r"截至查询需要已发布代次"),
    (GenerationUnavailable, r"没有已发布代次"),
    (GenerationUnavailable, r"已发布代次的保留记录不可用"),
    (GenerationUnavailable, r"代次校验记录不匹配"),
    (GenerationUnavailable, r"代次依赖已改变"),
    (GenerationUnavailable, r"代次查询文件没有校验记录"),
    (GenerationUnavailable, r"代次或其必需依赖不可用"),
    (GenerationUnavailable, r"代次依赖路径无效"),
    (GenerationUnavailable, r"代次依赖不能是符号链接"),
    (GenerationUnavailable, r"保留代次编号发生冲突"),
    (GenerationUnavailable, r"代次校验记录无效"),
    (GenerationUnavailable, r"保留代次依赖已改变"),
    (GenerationUnavailable, r"前代依赖已改变"),
    (GenerationUnavailable, r"发布历史段路径无效"),
    (GenerationUnavailable, r"DuckDB 重建描述不匹配"),
    (GenerationUnavailable, r"DuckDB 重建副本非空"),
    (GenerationUnavailable, r"代次存储副本校验失败"),
    (GenerationUnavailable, r"代次包含未完成发布"),
    (GenerationUnavailable, r"raw 哈希无效"),
    (GenerationUnavailable, r"代次 raw 校验失败"),
    (ReleaseNotFound, r"该代次没有指定发布标识"),
    (ReleaseNotFound, r"该代次没有指定来源发布项"),
    (AmbiguousRelease, r"发布标识跨来源或对应多个日期"),
    (AmbiguousRelease, r"同一引用的不同身份具有相同发布排序键"),
    (ReleaseDateUnavailable, r"显式来源发布缺少 released_at"),
))


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
        elif (type(exc) is ParseError and message in _PARSE_MESSAGES) or any(
            type(exc) is kind and pattern.fullmatch(message)
            for kind, pattern in _MESSAGE_TEMPLATES
        ):
            reason = f"{reason}: {message}"
    return "".join(char for char in reason if char.isprintable())[:MAX_REASON_LENGTH]
