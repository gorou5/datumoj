"""ABR town text: one document per municipality, one lossless JSON row per chunk."""

import csv
import io
import json
import re
from collections.abc import Mapping

from pydantic import JsonValue

from datumoj.rikolto.core.contracts import ParseError, Parser, ParseResult
from datumoj.rikolto.core.models import Chunk, Document, Snapshot, format_locator
from datumoj.rikolto.parsers._zip import read_zip

MUNICIPALITY_CODE = "134023"
MEMBER = f"mt_town_city{MUNICIPALITY_CODE}.csv"


class AbrParser(Parser):
    name = "abr-town"
    version = "1"
    source_kind = "abr_town"
    content_type = "*"

    def parse(self, snapshot: Snapshot, data: bytes, meta: Mapping[str, JsonValue] | None = None) -> ParseResult:
        if snapshot.sha256 is None:
            raise ParseError("ABR snapshot has no body")
        if meta and meta.get("municipality_code") != MUNICIPALITY_CODE:
            raise ParseError(f"ABR requires municipality {MUNICIPALITY_CODE}")
        members = read_zip(data)
        if MEMBER not in members:
            raise ParseError(f"Missing ABR ZIP member: {MEMBER}")
        try:
            payload = members[MEMBER]
            try:
                text = payload.decode("utf-8-sig")
            except UnicodeDecodeError:
                text = payload.decode("cp932")
            reader = csv.DictReader(io.StringIO(text, newline=""), strict=True)
            fields = reader.fieldnames
            if (not fields or len(set(fields)) != len(fields) or "" in fields
                    or not {"lg_code", "machiaza_id", "pref", "city", "oaza_cho", "oaza_cho_kana",
                            "efct_date", "ablt_date"}.issubset(fields)):
                raise ValueError("missing or duplicate ABR columns")
            key = f"{snapshot.source_id}:mt_town:{MUNICIPALITY_CODE}"
            chunks = []
            seen = set()
            municipality = None
            for row_number, row in enumerate(reader, 2):
                if None in row or any(value is None for value in row.values()):
                    raise ValueError(f"ABR CSV row {row_number} has the wrong number of fields")
                town_id = row["machiaza_id"]
                if (row["lg_code"] != MUNICIPALITY_CODE or not re.fullmatch(r"[0-9]{7}", town_id)
                        or town_id in seen or not row["pref"] or not row["city"]):
                    raise ValueError(f"ABR CSV row {row_number} has an invalid municipality or town ID")
                place = (row["pref"], row["city"])
                if municipality is not None and municipality != place:
                    raise ValueError("ABR municipality names differ between rows")
                municipality = place
                seen.add(town_id)
                chunks.append(Chunk(
                    chunk_id=f"{key}:{town_id}", doc_key=key,
                    locator=format_locator({"member": MEMBER, "row": str(row_number)}),
                    text=f"{''.join(place)} {row['oaza_cho']}（{row['oaza_cho_kana']}）\n"
                         + json.dumps(row, ensure_ascii=False), title_path=[*place, town_id],
                    snapshot_sha256=snapshot.sha256, lang="ja",
                ))
            if not chunks:
                raise ValueError("ABR archive contains no towns")
            title = f"アドレス・ベース・レジストリ 町字マスター {''.join(municipality)}"
            return ParseResult(documents=[Document(
                doc_key=key, source_id=snapshot.source_id, snapshot_sha256=snapshot.sha256,
                title=title, lang="ja", kind="dataset", content_type="application/zip",
                metadata={"municipality_code": MUNICIPALITY_CODE, "publisher": "デジタル庁"},
            )], chunks=chunks)
        except (UnicodeError, csv.Error, TypeError, ValueError) as exc:
            raise ParseError(f"Invalid ABR town CSV: {exc}") from exc
