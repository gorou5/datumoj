"""Immutable output-only SQLite cache. Authorization belongs to ModelRouter."""

from pathlib import Path
import sqlite3

from datumoj.eltiro.models import ModelCacheKey, ModelResult


class SQLiteModelCache:
    def __init__(self, data_root: str | Path):
        self.path = Path(data_root) / "eltiro" / "model_cache.sqlite"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(self.path)
        with self._db:
            self._db.execute("CREATE TABLE IF NOT EXISTS outputs (digest TEXT PRIMARY KEY, result TEXT NOT NULL)")

    def close(self):
        self._db.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def get(self, key: ModelCacheKey) -> ModelResult | None:
        key = ModelCacheKey.model_validate(key.model_dump())
        row = self._db.execute("SELECT result FROM outputs WHERE digest = ?", (key.digest,)).fetchone()
        return ModelResult.model_validate_json(row[0]) if row else None

    def put(self, key: ModelCacheKey, result: ModelResult) -> None:
        key = ModelCacheKey.model_validate(key.model_dump())
        result = ModelResult.model_validate(result.model_dump())
        expected = (key.provider, key.identity.model, key.identity.version)
        if any((field.model_use.provider, field.model_use.model, field.model_use.version) != expected
               for field in result.fields.values()):
            raise ValueError("cache provenance disagrees with key")
        with self._db:
            self._db.execute("BEGIN IMMEDIATE")
            old = self.get(key)
            if old is not None and old != result:
                raise ValueError("cached model output is immutable")
            self._db.execute("INSERT OR IGNORE INTO outputs VALUES (?, ?)", (key.digest, result.model_dump_json()))
