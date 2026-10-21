"""SQLite FTS5 trigram 日文关键词索引（KeywordIndex）。不分词；两个字以下退回 LIKE 扫描。"""

from __future__ import annotations

import sqlite3
import time
from collections.abc import Sequence
from pathlib import Path

from datumoj.core.contracts import IndexResult, ScoredChunk
from datumoj.core.models import Chunk
from datumoj.text import normalize

_TITLE_WEIGHT = 5.0  # bm25 列权重：标题路径命中比正文命中更靠前
_MIN_FTS_CHARS = 3

_SCHEMA = """
CREATE TABLE IF NOT EXISTS entries (
    id INTEGER PRIMARY KEY,
    chunk_id TEXT NOT NULL UNIQUE,
    doc_key TEXT NOT NULL,
    source_id TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS entries_doc ON entries(doc_key);
CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(title_path, text, tokenize='trigram');
"""


def _like_pattern(q: str) -> str:
    return "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


class SqliteKeywordIndex:
    """``path`` 是 SQLite 文件路径，或 ``:memory:``。

    ``like_limit``：短查询（LIKE 扫描）最多收集的命中条数；``like_timeout``：LIKE 扫描的秒数上限。
    """

    def __init__(
        self, path: str | Path, timeout: float = 30.0, like_limit: int = 1000, like_timeout: float = 2.0
    ) -> None:
        if timeout <= 0 or like_timeout <= 0:
            raise ValueError("timeout 必须 > 0")
        if like_limit < 1:
            raise ValueError("like_limit 必须 ≥ 1")
        self.like_limit = like_limit
        self.like_timeout = like_timeout
        db_path = str(path)
        if db_path != ":memory:":
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(db_path, timeout=timeout, isolation_level=None)
        self._require_trigram()
        self._db.executescript(_SCHEMA)

    def close(self) -> None:
        self._db.close()

    def _require_trigram(self) -> None:
        try:
            self._db.execute("CREATE VIRTUAL TABLE temp._trigram_probe USING fts5(x, tokenize='trigram')")
            self._db.execute("DROP TABLE temp._trigram_probe")
        except sqlite3.OperationalError as e:
            raise RuntimeError(f"当前 SQLite（{sqlite3.sqlite_version}）不支持 FTS5 trigram 分词：{e}") from e

    def _delete_ids(self, ids: Sequence[int]) -> None:
        for i in ids:
            self._db.execute("DELETE FROM chunks_fts WHERE rowid=?", (i,))
            self._db.execute("DELETE FROM entries WHERE id=?", (i,))

    def add(self, chunks: Sequence[Chunk]) -> None:
        self._db.execute("BEGIN IMMEDIATE")
        try:
            for c in chunks:
                old = self._db.execute("SELECT id FROM entries WHERE chunk_id=?", (c.chunk_id,)).fetchone()
                if old:
                    self._delete_ids([old[0]])
                rowid = self._db.execute(
                    "INSERT INTO entries(chunk_id, doc_key, source_id) VALUES(?,?,?)",
                    (c.chunk_id, c.doc_key, c.source_id),
                ).lastrowid
                self._db.execute(
                    "INSERT INTO chunks_fts(rowid, title_path, text) VALUES(?,?,?)",
                    (rowid, normalize(" ".join(c.title_path)), normalize(c.text)),
                )
        except BaseException:
            self._db.execute("ROLLBACK")
            raise
        self._db.execute("COMMIT")

    def delete(self, doc_key: str) -> None:
        self._db.execute("BEGIN IMMEDIATE")
        try:
            self._delete_ids([r[0] for r in self._db.execute("SELECT id FROM entries WHERE doc_key=?", (doc_key,))])
        except BaseException:
            self._db.execute("ROLLBACK")
            raise
        self._db.execute("COMMIT")

    def clear(self) -> None:
        self._db.execute("BEGIN IMMEDIATE")
        try:
            self._db.execute("DELETE FROM chunks_fts")
            self._db.execute("DELETE FROM entries")
        except BaseException:
            self._db.execute("ROLLBACK")
            raise
        self._db.execute("COMMIT")

    def search(self, query: str, k: int = 10, source_ids: Sequence[str] | None = None) -> IndexResult:
        q = normalize(query) if isinstance(query, str) else ""
        if not q:
            raise ValueError("查询不能为空白")
        if k < 1:
            raise ValueError("k 必须 ≥ 1")
        if source_ids is not None and not source_ids:
            return IndexResult()
        where, params = "", []
        if source_ids is not None:
            where = f" AND e.source_id IN ({','.join('?' * len(source_ids))})"
            params = list(source_ids)
        terms = list(dict.fromkeys(q.split()))
        if len(terms) == 1:
            term = terms[0]
            if len(term) < _MIN_FTS_CHARS:
                found, _, warnings = self._search_like([term], where, params, mixed=False)
                items = sorted(found.values(), key=lambda h: (-h.score, h.chunk_id))
                return IndexResult(items=items[:k], warnings=warnings)
            phrase = '"' + term.replace('"', '""') + '"'
            rows = self._db.execute(
                "SELECT e.chunk_id, e.doc_key, -bm25(chunks_fts, ?, 1.0) AS score FROM chunks_fts "
                "JOIN entries e ON e.id = chunks_fts.rowid "
                f"WHERE chunks_fts MATCH ?{where} ORDER BY score DESC, e.chunk_id LIMIT ?",
                [_TITLE_WEIGHT, phrase, *params, k],
            ).fetchall()
            return IndexResult(items=[ScoredChunk(chunk_id=r[0], doc_key=r[1], score=r[2]) for r in rows])

        fts_terms = [term for term in terms if len(term) >= _MIN_FTS_CHARS]
        like_terms = [term for term in terms if len(term) < _MIN_FTS_CHARS]
        matches: dict[str, set[str]] = {}
        found: dict[str, ScoredChunk] = {}

        if fts_terms:
            phrases = ['"' + term.replace('"', '""') + '"' for term in fts_terms]
            rows = self._db.execute(
                "SELECT e.chunk_id, e.doc_key, -bm25(chunks_fts, ?, 1.0) AS score FROM chunks_fts "
                "JOIN entries e ON e.id = chunks_fts.rowid "
                f"WHERE chunks_fts MATCH ?{where} ORDER BY score DESC, e.chunk_id",
                [_TITLE_WEIGHT, " OR ".join(phrases), *params],
            ).fetchall()
            found.update({r[0]: ScoredChunk(chunk_id=r[0], doc_key=r[1], score=r[2]) for r in rows})
            if len(fts_terms) == 1:
                for r in rows:
                    matches.setdefault(r[0], set()).add(fts_terms[0])
            else:
                for term, phrase in zip(fts_terms, phrases):
                    hits = self._db.execute(
                        "SELECT e.chunk_id FROM chunks_fts JOIN entries e ON e.id = chunks_fts.rowid "
                        f"WHERE chunks_fts MATCH ?{where}",
                        [phrase, *params],
                    ).fetchall()
                    for (chunk_id,) in hits:
                        matches.setdefault(chunk_id, set()).add(term)

        warnings: list[str] = []
        if like_terms:
            like_found, like_matches, warnings = self._search_like(
                like_terms, where, params, mixed=bool(fts_terms)
            )
            for chunk_id, hit in like_found.items():
                found.setdefault(chunk_id, hit)
            for chunk_id, hit_terms in like_matches.items():
                matches.setdefault(chunk_id, set()).update(hit_terms)

        if not found:
            return IndexResult(warnings=warnings)

        # Expose the same primary ranking in ScoredChunk.score so callers see
        # scores in result order; the original FTS/LIKE score breaks ties.
        score_span = max(hit.score for hit in found.values()) - min(hit.score for hit in found.values()) + 1.0
        items = [
            ScoredChunk(
                chunk_id=hit.chunk_id,
                doc_key=hit.doc_key,
                score=len(matches.get(chunk_id, ())) * score_span + hit.score,
            )
            for chunk_id, hit in found.items()
        ]
        items.sort(key=lambda hit: (-hit.score, hit.chunk_id))
        return IndexResult(items=items[:k], warnings=warnings)

    def _search_like(
        self, queries: Sequence[str], where: str, params: list[str], *, mixed: bool
    ) -> tuple[dict[str, ScoredChunk], dict[str, set[str]], list[str]]:
        warning = (
            f"查询含少于 {_MIN_FTS_CHARS} 个字的词，已退回 LIKE 扫描"
            if mixed else f"查询少于 {_MIN_FTS_CHARS} 个字，已退回 LIKE 扫描"
        )
        warnings = [warning]
        patterns = [_like_pattern(term) for term in queries]
        conditions = " OR ".join(
            "(f.title_path LIKE ? ESCAPE '\\' OR f.text LIKE ? ESCAPE '\\')" for _ in queries
        )
        deadline = time.monotonic() + self.like_timeout
        found: dict[str, ScoredChunk] = {}
        matches: dict[str, set[str]] = {}
        self._db.set_progress_handler(lambda: int(time.monotonic() > deadline), 1000)
        try:
            cur = self._db.execute(
                "SELECT e.chunk_id, e.doc_key, f.title_path, f.text FROM chunks_fts f JOIN entries e ON e.id = f.rowid "
                f"WHERE ({conditions}){where}",
                [value for pattern in patterns for value in (pattern, pattern)] + params,
            )
            for chunk_id, doc_key, title, text in cur:
                folded_title, folded_text = title.casefold(), text.casefold()
                hit_terms = {
                    term for term in queries
                    if term.casefold() in folded_title or term.casefold() in folded_text
                }
                score = sum(
                    folded_text.count(term.casefold()) + _TITLE_WEIGHT * folded_title.count(term.casefold())
                    for term in hit_terms
                )
                found[chunk_id] = ScoredChunk(chunk_id=chunk_id, doc_key=doc_key, score=float(score))
                matches[chunk_id] = hit_terms
                if len(found) >= self.like_limit:
                    warnings.append(f"查询过短，只扫描了前 {self.like_limit} 条命中，结果不完整")
                    break
        except sqlite3.OperationalError as e:
            if "interrupted" not in str(e):
                raise
            warnings.append("查询过短且超时，结果不完整")
        finally:
            self._db.set_progress_handler(None, 0)
        return found, matches, warnings
