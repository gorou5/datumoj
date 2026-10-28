"""Private, immutable run directories; a rename publishes the complete run.

No consumer output, Rikolto storage, network access, or caller-selected filenames.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shutil
from uuid import uuid4

import pyarrow as pa
import pyarrow.parquet as pq

from datumoj.eltiro.intake import open_directory
from datumoj.eltiro.models import CitationEntry

_RUN_ID = re.compile(r"run-[0-9a-f]{32}\Z")


class RunStore:
    def __init__(self, data_root: str | Path):
        self.root = Path(data_root).absolute()
        self.path = self.root / "eltiro" / "runs"

    def _open(self):
        return open_directory(self.root, self.path)

    def write(self, record: dict, rows: list[dict], citations: list[dict], gaps: list[dict], *, validate) -> dict:
        """validate rechecks the generation immediately before publication.

        Decimal columns use decimal256 with an exact common scale (never float).
        Values outside Arrow's 76-digit capacity fail atomically, never round.
        """
        if len(rows) != len(citations):
            raise ValueError("incomplete citations")
        for citation in citations:
            CitationEntry.model_validate(citation["citation"])
        descriptor = self._open()
        run_id = "run-" + uuid4().hex
        staging = "." + run_id
        stage = self.path / staging
        try:
            os.mkdir(staging, mode=0o700, dir_fd=descriptor)
            # Fixed filenames inside a new, private directory.
            schema = pa.schema([
                ("citation_id", pa.string()), ("symbol", pa.string()), ("country", pa.string()),
                ("region_scheme", pa.string()), ("region", pa.string()), ("period", pa.date32()),
                ("source", pa.string()), ("series", pa.string()), ("release_id", pa.string()),
                ("released_at", pa.date32()), ("release_position", pa.int64()), ("unit", pa.string()),
                ("value", _decimal_type([r["value"] for r in rows])),
                ("spread_range", _decimal_type([r.get("spread_range") for r in rows])),
                ("note", pa.string()),
            ])
            pq.write_table(pa.Table.from_pylist(rows, schema=schema), stage / "data.parquet")
            complete = {**record, "run_id": run_id, "row_count": len(rows), "gap_count": len(gaps)}
            for filename, content in (("record.json", complete), ("citations.json", citations), ("gaps.json", gaps)):
                with (stage / filename).open("x", encoding="utf-8") as stream:
                    json.dump(content, stream, ensure_ascii=False, indent=2)
                    stream.write("\n")
                    stream.flush()
                    os.fsync(stream.fileno())
            with (stage / "data.parquet").open("rb") as stream:
                os.fsync(stream.fileno())
            validate()
            os.rename(staging, run_id, src_dir_fd=descriptor, dst_dir_fd=descriptor)
            return complete
        except BaseException:
            if stage.exists():
                shutil.rmtree(stage)
            raise
        finally:
            os.close(descriptor)

    def show(self, run_id: str) -> dict:
        if not _RUN_ID.fullmatch(run_id):
            raise ValueError("invalid run id")
        root = self._open()
        child = None
        try:
            child = os.open(run_id, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=root)
            file = os.open("record.json", os.O_RDONLY | os.O_NOFOLLOW, dir_fd=child)
            with os.fdopen(file, encoding="utf-8") as stream:
                return json.load(stream)
        finally:
            if child is not None:
                os.close(child)
            os.close(root)

    def list(self) -> list[dict]:
        descriptor = self._open()
        try:
            names = sorted(n for n in os.listdir(descriptor) if _RUN_ID.fullmatch(n))
        finally:
            os.close(descriptor)
        return [self.show(name) for name in names]


def _decimal_type(values):
    values = [v for v in values if v is not None]
    scale = max([0, *(-v.as_tuple().exponent for v in values)])
    integer_digits = max([1, *(max(0, v.adjusted() + 1) for v in values)])
    precision = integer_digits + scale
    if precision > 76:
        raise ValueError("result exceeds exact Parquet decimal capacity")
    return pa.decimal256(precision, scale)
