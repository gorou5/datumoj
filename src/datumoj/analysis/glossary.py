"""中英政务常用词 → 日文的对照（PLAN 4.3 第 6 条）。只查 ``glossary.yaml``，不调用 LLM。"""

from __future__ import annotations

import re
import unicodedata
from functools import cache
from pathlib import Path
from typing import Any

import yaml

GLOSSARY_PATH = Path(__file__).with_name("glossary.yaml")
_LANGS = ("ja", "zh", "en")


def normalize(text: str) -> str:
    """NFKC + 小写，查询和对照表都先这样处理再比较。"""
    return unicodedata.normalize("NFKC", text).casefold()


@cache
def load_glossary() -> tuple[dict[str, tuple[str, ...]], ...]:
    """读取对照表；格式不对抛 ValueError，写明第几条。"""
    entries = yaml.safe_load(GLOSSARY_PATH.read_text(encoding="utf-8"))
    if not isinstance(entries, list):
        raise ValueError(f"{GLOSSARY_PATH.name} 必须是条目列表")
    out = []
    for i, entry in enumerate(entries, 1):
        if not isinstance(entry, dict) or set(entry) - set(_LANGS):
            raise ValueError(f"{GLOSSARY_PATH.name} 第 {i} 条只能有 ja、zh、en 三个键")
        terms = {lang: tuple(entry.get(lang) or ()) for lang in _LANGS}
        if not all(isinstance(t, str) and t.strip() == t and t for ts in terms.values() for t in ts):
            raise ValueError(f"{GLOSSARY_PATH.name} 第 {i} 条有空白或不是字符串的词")
        if not terms["ja"] or not (terms["zh"] or terms["en"]):
            raise ValueError(f"{GLOSSARY_PATH.name} 第 {i} 条要有 ja，以及 zh 或 en")
        out.append(terms)
    return tuple(out)


@cache
def _patterns() -> list[tuple[re.Pattern[str], str, str, int]]:
    """(正则, 规范化后的词, 语言, 条目序号)，长词在前：长词命中的部分不再让短词重复命中。

    拉丁字母和数字开头或结尾的词按整词匹配（``ai`` 不命中 ``said``）。
    """
    found = []
    for i, entry in enumerate(load_glossary()):
        for lang in ("zh", "en"):
            for term in entry[lang]:
                norm = normalize(term)
                pattern = re.escape(norm)
                if re.match(r"[a-z0-9]", norm):
                    pattern = r"(?<![a-z0-9])" + pattern
                if re.search(r"[a-z0-9]$", norm):
                    pattern += r"(?![a-z0-9])"
                found.append((re.compile(pattern), norm, lang, i))
    return sorted(found, key=lambda p: -len(p[1]))


def expand_terms(query: str) -> dict[str, Any]:
    """查询里出现的中文、英文政务词 → 对应的日文词。

    返回 ``{"query", "ja_terms": [去重后的日文词], "matches": [{"matched", "lang", "ja"}]}``；没有命中时两个列表为空。
    query 不是字符串或为空白抛 ValueError。
    """
    if not isinstance(query, str) or not query.strip():
        raise ValueError("query 不能为空白")
    entries = load_glossary()
    text = normalize(query)
    matches: list[dict[str, Any]] = []
    ja_terms: list[str] = []
    for pattern, norm, lang, i in _patterns():
        text, n = pattern.subn(lambda mt: "\x00" * len(mt.group(0)), text)
        if not n:
            continue
        matches.append({"matched": norm, "lang": lang, "ja": list(entries[i]["ja"])})
        ja_terms.extend(t for t in entries[i]["ja"] if t not in ja_terms)
    return {"query": query, "ja_terms": ja_terms, "matches": matches}
