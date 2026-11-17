"""UN Comtrade v1 annual HS totals → observations, plus the getDA release marker.

Only total rows are kept: cmdCode=TOTAL, partner2Code=0, customsCode=C00, motCode=0, flow X or M. Rows with
reporter = partner (re-imports) are dropped. ``primaryValue`` is stored verbatim as the value (current USD);
``cifvalue`` and ``fobvalue`` travel in ``dims`` as the source wrote them (omitted when the source says null).
The release identity comes only from the getDA record the crawler stored in the task meta
(``<datasetCode>@<lastReleased>``); without a matching record it stays empty. No mirror gap is computed here.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from datetime import date
from decimal import Decimal
from hashlib import sha256

from pydantic import JsonValue

from datumoj.rikolto.core.contracts import ParseError, Parser, ParseResult
from datumoj.rikolto.core.models import Dataset, Observation, Snapshot, TimeGranularity, format_locator

FLOWS = {"X": "Exports", "M": "Imports"}
_RELEASE_KEYS = ("period", "classificationCode", "datasetCode", "datasetChecksum", "lastReleased")


def _load(data: bytes) -> list[dict]:
    def non_finite(_: str) -> None:
        raise ParseError("Comtrade JSON 含非有限数")

    try:
        doc = json.loads(data.decode("utf-8-sig"), parse_float=Decimal, parse_constant=non_finite)
    except (ValueError, RecursionError) as exc:  # includes UnicodeDecodeError and JSONDecodeError
        raise ParseError("Comtrade 响应不是有效 JSON") from exc
    rows = doc.get("data") if isinstance(doc, dict) else None
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise ParseError("Comtrade 响应缺少 data 数组")
    if doc.get("error"):
        raise ParseError("Comtrade 响应带错误信息")
    return rows


def _int(row: Mapping[str, object], key: str) -> int:
    value = row.get(key)
    if type(value) is int:
        return value
    if isinstance(value, str) and re.fullmatch(r"[0-9]+", value):
        return int(value)
    raise ParseError(f"Comtrade 记录的 {key} 无效")


def _text(row: Mapping[str, object], key: str) -> str:
    value = row.get(key)
    if not isinstance(value, str) or not value:
        raise ParseError(f"Comtrade 记录的 {key} 无效")
    return value


def _number(row: Mapping[str, object], key: str) -> Decimal | None:
    value = row.get(key)
    if value is None:
        return None
    if type(value) is int or isinstance(value, Decimal):
        return Decimal(value)
    raise ParseError(f"Comtrade 记录的 {key} 不是数字")


def _released_on(last_released: str) -> date:
    """The date part of ``lastReleased`` (the source writes up to 7 fractional digits, so no full ISO parse)."""
    if not re.match(r"[0-9]{4}-[0-9]{2}-[0-9]{2}(?:T|$)", last_released):
        raise ParseError(f"Comtrade lastReleased 无效：{last_released!r}")
    try:
        return date.fromisoformat(last_released[:10])
    except ValueError as exc:
        raise ParseError(f"Comtrade lastReleased 无效：{last_released!r}") from exc


def read_releases(data: bytes, reporter: int, period: int) -> list[dict[str, JsonValue]]:
    """The getDA records of one reporter and year (one per classification), with ``datasetChecksum``.

    Empty when the source lists nothing (not released yet).
    """
    found: list[dict[str, JsonValue]] = []
    for row in _load(data):
        if (_int(row, "reporterCode") != reporter or _int(row, "period") != period or row.get("typeCode") != "C"
                or row.get("freqCode") != "A" or row.get("classificationSearchCode") != "HS"):
            continue
        release: dict[str, JsonValue] = {
            "period": period, "classificationCode": _text(row, "classificationCode"),
            "datasetCode": _int(row, "datasetCode"), "datasetChecksum": _int(row, "datasetChecksum"),
            "lastReleased": _text(row, "lastReleased"),
        }
        _released_on(release["lastReleased"])
        found.append(release)
    if len({r["classificationCode"] for r in found}) != len(found):
        raise ParseError("Comtrade getDA 同一分类出现多条记录")
    return sorted(found, key=lambda r: r["classificationCode"])


def _releases(meta: Mapping[str, JsonValue]) -> dict[tuple[int, str], dict[str, JsonValue]]:
    items = meta.get("releases", [])
    if not isinstance(items, list) or any(not isinstance(i, dict) or set(i) != set(_RELEASE_KEYS) for i in items):
        raise ParseError("任务 meta 的 releases 无效")
    for item in items:
        _released_on(str(item["lastReleased"]))
    return {(int(i["period"]), str(i["classificationCode"])): i for i in items}


class UnComtradeParser(Parser):
    name = "un_comtrade"
    version = "1"
    source_kind = "un_comtrade"
    content_type = "*"

    def parse(self, snapshot: Snapshot, data: bytes, meta: Mapping[str, JsonValue] | None = None) -> ParseResult:
        releases = _releases(meta or {})
        rows = _load(data)
        if not rows:
            return ParseResult(warnings=["Comtrade 响应没有数据行"])
        if all("datasetChecksum" in row and "flowCode" not in row for row in rows):
            return ParseResult(warnings=["getDA 只提供发布标识，没有观测值"])

        observations: dict[tuple[str, str | None], Observation] = {}
        datasets: dict[str, Dataset] = {}
        reimports = other = unreleased = 0
        for number, row in enumerate(rows, 1):
            reporter, partner = _int(row, "reporterCode"), _int(row, "partnerCode")
            if reporter == partner:
                reimports += 1
                continue
            flow, cmd, customs = row.get("flowCode"), row.get("cmdCode"), row.get("customsCode")
            partner2, mot = _int(row, "partner2Code"), _int(row, "motCode")
            if (not isinstance(flow, str) or flow not in FLOWS or cmd != "TOTAL" or customs != "C00"
                    or partner2 != 0 or mot != 0):
                other += 1
                continue
            year = _int(row, "period")
            classification = _text(row, "classificationCode")
            value, cif, fob = (_number(row, k) for k in ("primaryValue", "cifvalue", "fobvalue"))
            if not 1 <= year <= 9999:
                raise ParseError(f"Comtrade period 无效：{year}")

            dims = {
                "reporter": str(reporter), "partner": str(partner), "partner2": str(partner2), "flow": flow,
                "year": str(year), "cmd": cmd, "customs": customs, "mot": str(mot), "classification": classification,
            }
            series = [dims[k] for k in ("reporter", "partner", "partner2", "flow", "year", "cmd", "customs", "mot",
                                        "classification")]
            obs_id = "comtrade:" + sha256(json.dumps(series, separators=(",", ":")).encode()).hexdigest()
            for key, amount in (("cifvalue", cif), ("fobvalue", fob)):
                if amount is not None:
                    dims[key] = str(amount)
            release = releases.get((year, classification))
            if release is None:
                release_id = released_at = None
                unreleased += 1
            else:
                release_id = f"{release['datasetCode']}@{release['lastReleased']}"
                released_at = _released_on(str(release["lastReleased"]))
                dims["datasetChecksum"] = str(release["datasetChecksum"])

            table = f"total_{reporter}_{partner}_{year}"
            dataset_key = f"{snapshot.source_id}:{table}"
            obs = Observation(
                obs_id=obs_id, source_id=snapshot.source_id, dataset_key=dataset_key, table_key=table,
                survey="UN Comtrade C/A/HS", indicator=FLOWS[flow], indicator_code=flow, area_code=str(reporter),
                unit="USD", dims=dims, time_label=str(year), time_start=date(year, 1, 1), time_end=date(year, 12, 31),
                granularity=TimeGranularity.year, value=value,
                note=None if value is not None else "primaryValue=null",
                snapshot_sha256=snapshot.sha256, fetched_at=snapshot.fetched_at,
                locator=format_locator({"row": number}), release_id=release_id, released_at=released_at,
            )
            if (obs_id, release_id) in observations:
                raise ParseError("Comtrade 响应包含同一观测键的重复行")
            observations[obs_id, release_id] = obs
            datasets[dataset_key] = Dataset(
                dataset_key=dataset_key, source_id=snapshot.source_id, kind="stat_dataset", publisher="UN Comtrade",
                title=f"UN Comtrade 货物贸易合计：报告方 {reporter}，伙伴 {partner}，{year}",
                url=snapshot.resource_url, snapshot_sha256=snapshot.sha256,
                extras={"reporter": reporter, "partner": partner, "year": year, "cmdCode": "TOTAL"},
            )

        warnings = []
        if reimports:
            warnings.append(f"排除 {reimports} 条 reporter = partner 的行（复进口）")
        if other:
            warnings.append(f"排除 {other} 条不是合计行的记录（需要 TOTAL / partner2=0 / C00 / mot=0 / X 或 M）")
        if unreleased:
            warnings.append(f"{unreleased} 条记录没有对应的 getDA 发布标识，release_id 留空")
        return ParseResult(datasets=list(datasets.values()), observations=list(observations.values()), warnings=warnings)
