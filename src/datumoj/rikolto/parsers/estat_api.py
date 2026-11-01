"""Parse complete e-Stat API tables and catalog pages."""
import calendar
import json
import re
from datetime import date
from decimal import Decimal, InvalidOperation
from hashlib import sha256

from datumoj.rikolto.core.contracts import ParseError, Parser, ParseResult
from datumoj.rikolto.core.models import Dataset, Observation, TimeGranularity, format_locator

_LICENSE = "政府統計の総合窓口（e-Stat）利用規約"
_CREDIT = "このサービスは、政府統計総合窓口(e-Stat)のAPI機能を使用していますが、サービスの内容は国によって保証されたものではありません。"
_ATTRIBUTION = "出典：政府統計の総合窓口(e-Stat)（https://www.e-stat.go.jp/）"


def _list(value):
    return value if isinstance(value, list) else [value] if value is not None else []


def _dataset(snapshot, table, index):
    sid = str(table["@id"])
    stat = table["STAT_NAME"]
    return Dataset(dataset_key=f"{snapshot.source_id}:{sid}", source_id=snapshot.source_id,
                   title=table["TITLE"]["$"], kind="estat_table", publisher=table["GOV_ORG"]["$"],
                   description=table.get("DESCRIPTION") or None, url=f"https://www.e-stat.go.jp/dbview?sid={sid}",
                   license=_LICENSE, published_at=date.fromisoformat(table["OPEN_DATE"]),
                   extras={"stat_name": stat["$"], "stats_code": str(stat["@code"]),
                           "open_date": table["OPEN_DATE"], "updated_date": table.get("UPDATED_DATE"),
                           "attribution": _ATTRIBUTION, "service_credit": _CREDIT},
                   snapshot_sha256=snapshot.sha256, locator=format_locator({"row": index}))


class EstatAPIParser(Parser):
    name = "estat_api"
    version = "1"
    source_kind = "estat_api"
    content_type = "*"

    def parse(self, snapshot, data, meta=None):
        try:
            payload = json.loads(data)
            root = payload.get("GET_STATS_DATA") or payload["GET_STATS_LIST"]
            result = root["RESULT"]
            status = str(result["STATUS"])
            if status == "1":
                return ParseResult(warnings=[f"STATUS 1: {result.get('ERROR_MSG', '')}"])
            if status != "0":
                raise ParseError(f"STATUS {status}: {result.get('ERROR_MSG', '')}")
            if "DATALIST_INF" in root:
                tables = _list(root["DATALIST_INF"].get("TABLE_INF"))
                return ParseResult(datasets=[_dataset(snapshot, t, i + 1) for i, t in enumerate(tables)])
            body = root["STATISTICAL_DATA"]
            pagination = body["RESULT_INF"]
            if pagination.get("NEXT_KEY") is not None or int(pagination["TO_NUMBER"]) < int(pagination["TOTAL_NUMBER"]):
                raise ParseError("incomplete table response")
            table = body["TABLE_INF"]
            sid = str(table["@id"])
            if not meta or sid != meta.get("stats_data_id") or not isinstance(meta.get("unit"), str):
                raise ValueError("table context mismatch")
            rows = _list(body["DATA_INF"].get("VALUE"))
            if int(pagination["TOTAL_NUMBER"]) != len(rows) or int(pagination["FROM_NUMBER"]) != 1:
                raise ValueError("incomplete table response")
            classes = {c["@id"]: {str(v["@code"]): v["@name"] for v in _list(c["CLASS"])}
                       for c in _list(body["CLASS_INF"]["CLASS_OBJ"])}
            notes = {n["@char"]: n["$"] for n in _list(body["DATA_INF"].get("NOTE"))}
            observations = []
            skipped = 0
            for i, row in enumerate(rows):
                code = str(row["@time"])
                if not re.fullmatch(r"[0-9]{10}", code):
                    raise ValueError("invalid time code")
                if code[4:] in ("000000", "100000"):
                    if sid != "0003427113":
                        raise ValueError("nonmonthly time")
                    skipped += 1
                    continue
                if code[4:6] != "00" or code[6:8] != code[8:10] or not 1 <= int(code[6:8]) <= 12:
                    raise ValueError("invalid monthly time")
                year, month = int(code[:4]), int(code[6:8])
                dims = {}
                for dimension, choices in classes.items():
                    if "@" + dimension in row:
                        value = str(row["@" + dimension])
                        dims[dimension] = value
                        dims[dimension + "_name"] = choices[value]
                if "@unit" in row and row["@unit"] != meta["unit"]:
                    raise ValueError("unit mismatch")
                raw = str(row["$"])
                note = None
                if raw in notes:
                    value = None
                    note = f"{raw}: {notes[raw]}"
                else:
                    value = Decimal(raw)
                    if not value.is_finite():
                        raise ValueError("nonfinite value")
                identity = json.dumps([sid, sorted((k, v) for k, v in row.items() if k.startswith("@") and k != "@unit")], ensure_ascii=False)
                observations.append(Observation(obs_id="estat:" + sha256(identity.encode()).hexdigest(),
                    source_id=snapshot.source_id, dataset_key=f"{snapshot.source_id}:{sid}", table_key=sid,
                    survey=table["STAT_NAME"]["$"], indicator=dims.get("tab_name", table["TITLE"]["$"]),
                    indicator_code=dims.get("tab"), area_code=row.get("@area"), area_label=dims.get("area_name"),
                    time_start=date(year, month, 1), time_end=date(year, month, calendar.monthrange(year, month)[1]),
                    time_label=classes["time"][code], granularity=TimeGranularity.month, dims=dims,
                    unit=meta["unit"], value=value, note=note, snapshot_sha256=snapshot.sha256,
                    locator=format_locator({"row": i + 1}), fetched_at=snapshot.fetched_at))
            return ParseResult(datasets=[_dataset(snapshot, table, 1)], observations=observations,
                               warnings=[f"skipped {skipped} annual/fiscal values"] if skipped else [])
        except ParseError:
            raise
        except (ValueError, TypeError, KeyError, IndexError, InvalidOperation) as exc:
            raise ParseError(f"invalid e-Stat response: {exc}") from exc
