"""IMF WEO via SDMX-CSV 1.0: one observation per row, values kept exactly as published.

Hard rules: OBS_VALUE is already the full value (SCALE is a display magnitude and is never
applied); a missing value stays empty with its annotation; the release comes only from the
row's PUBLICATION_DATE (never the fetch time); a 200 response that lacks a requested series
(IMF answers a wrong key with one dataset-attribute-only row) is a parse failure.

table_key is the logical dataflow plus the request key (``WEO:<key>``) and dataset_key is
``<source_id>:<table_key>``: the store (and the keyword projection) keeps only the latest written
snapshot of a table/dataset as "current", so each requested series needs its own. Vintage dataflows
(``WEO_<YYYY>_<APR|OCT>_VINTAGE``) are earlier releases of the same series and share its table;
the crawler requests them oldest first so the newest release is written last.
"""

from __future__ import annotations

import csv
import io
import json
import re
from collections.abc import Mapping
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from hashlib import sha256
from itertools import product

from pydantic import JsonValue

from datumoj.rikolto.core.contracts import ParseError, Parser, ParseResult
from datumoj.rikolto.core.models import Dataset, Observation, Snapshot, TimeGranularity, format_locator
from datumoj.rikolto.parsers.imf_weo_archive import is_archive, parse_archive

AGENCY = "IMF.RES"
DATAFLOW = re.compile(r"WEO(?:_([0-9]{4})_(APR|OCT)_VINTAGE)?")
_ROW_DATAFLOW = re.compile(rf"{re.escape(AGENCY)}:(WEO(?:_[0-9]{{4}}_(?:APR|OCT)_VINTAGE)?)\(([^()]+)\)")
_PART = re.compile(r"[A-Za-z0-9_]+(?:\+[A-Za-z0-9_]+)*")
_YEAR = re.compile(r"[0-9]{4}")
_RELEASE_MONTHS = {4: "APR", 10: "OCT"}
_REQUIRED = ("DATAFLOW", "COUNTRY", "INDICATOR", "FREQUENCY", "TIME_PERIOD", "OBS_VALUE", "UNIT",
             "PUBLICATION_DATE", "COUNTRY_UPDATE_DATE", "LATEST_ACTUAL_ANNUAL_DATA", "SERIES_NAME",
             "PRIMARY_DOMESTIC_CURRENCY")
_DIMENSIONS = ("COUNTRY", "INDICATOR", "FREQUENCY")


def parse_key(key: object) -> tuple[list[str], list[str], str]:
    """``COUNTRY.INDICATOR.FREQUENCY`` with ``+`` for alternatives; anything else is ValueError."""
    parts = key.split(".") if isinstance(key, str) else []
    if len(parts) != 3 or not all(_PART.fullmatch(p) for p in parts) or "+" in parts[2]:
        raise ValueError("SDMX key 必须是 COUNTRY.INDICATOR.FREQUENCY，只允许维度代码字符、. 和 +")
    return parts[0].split("+"), parts[1].split("+"), parts[2]


def release_of(published: str) -> tuple[str, date] | None:
    """``WEO-<YYYY>-<APR|OCT>`` and its date from PUBLICATION_DATE; None when it cannot be told."""
    try:
        moment = datetime.fromisoformat(published.strip())
    except ValueError:
        return None
    day = (moment.replace(tzinfo=UTC) if moment.tzinfo is None else moment.astimezone(UTC)).date()
    month = _RELEASE_MONTHS.get(day.month)
    return (f"WEO-{day.year}-{month}", day) if month else None


def _text(data: bytes) -> str:
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ParseError("IMF 响应不是 UTF-8 文本") from exc


class ImfSdmxParser(Parser):
    name = "imf_sdmx"
    version = "2"
    source_kind = "imf_sdmx"
    content_type = "*"

    def parse(self, snapshot: Snapshot, data: bytes, meta: Mapping[str, JsonValue] | None = None) -> ParseResult:
        if is_archive(data):  # old WEO database file: recognised by its header, not by name
            return parse_archive(snapshot, data, meta)
        meta = dict(meta or {})
        flow, key, start, end = (meta.get(n) for n in ("dataflow", "key", "startPeriod", "endPeriod"))
        try:
            countries, indicators, frequency = parse_key(key)
        except ValueError as exc:
            raise ParseError(f"IMF 请求上下文无效：{exc}") from exc
        if (not isinstance(flow, str) or not DATAFLOW.fullmatch(flow)
                or not all(isinstance(v, str) and _YEAR.fullmatch(v) for v in (start, end)) or start > end):
            raise ParseError("IMF 请求上下文缺少 dataflow 或起止年份")
        expected = set(product(countries, indicators))
        table_key = f"WEO:{key}"
        dataset_key = f"{snapshot.source_id}:{table_key}"  # one dataset per table, as citations and projections expect
        try:
            reader = csv.DictReader(io.StringIO(_text(data), newline=""))
            if not set(_REQUIRED) <= set(reader.fieldnames or ()):
                raise ParseError("IMF 响应不是带全部属性的 SDMX-CSV")
            rows = list(reader)
        except csv.Error as exc:
            raise ParseError(f"IMF CSV 无效：{exc}") from exc
        observations: dict[str, Observation] = {}
        seen: set[tuple[str, str]] = set()
        releases: set[tuple[str, date] | None] = set()
        first: dict[str, str] = {}
        scales: set[str] = set()
        warnings: list[str] = []
        for number, row in enumerate(rows, 1):
            if None in row or None in row.values():
                raise ParseError(f"IMF CSV 第 {number} 行列数与表头不符")
            if not any(row[name] for name in (*_DIMENSIONS, "TIME_PERIOD", "OBS_VALUE")):
                continue  # only dataset attributes: what IMF returns for a key that matches nothing
            country, indicator, period = row["COUNTRY"], row["INDICATOR"], row["TIME_PERIOD"]
            matched = _ROW_DATAFLOW.fullmatch(row["DATAFLOW"])
            if matched is None or matched[1] != flow:
                raise ParseError(f"IMF 第 {number} 行的 DATAFLOW 与请求不符：{row['DATAFLOW']!r}")
            if (country, indicator) not in expected or row["FREQUENCY"] != frequency:
                raise ParseError(f"IMF 第 {number} 行的序列不在请求范围内：{country}.{indicator}.{row['FREQUENCY']}")
            if not _YEAR.fullmatch(period) or not start <= period <= end:
                raise ParseError(f"IMF 第 {number} 行的 TIME_PERIOD 无效或超出请求范围：{period!r}")
            year = int(period)
            seen.add((country, indicator))
            first = first or row
            if row.get("SCALE"):
                scales.add(row["SCALE"])
            release = release_of(row["PUBLICATION_DATE"])
            vintage = DATAFLOW.fullmatch(flow)
            if vintage[1] and release is not None and release[0] != f"WEO-{vintage[1]}-{vintage[2]}":
                raise ParseError(f"IMF 第 {number} 行的发布日期与 {flow} 不符：{row['PUBLICATION_DATE']}")
            releases.add(release)
            if len(releases) > 1:
                raise ParseError("IMF 一份响应里出现了多个发布")
            latest = row["LATEST_ACTUAL_ANNUAL_DATA"]
            if _YEAR.fullmatch(latest):
                status = "预测" if year > int(latest) else "实绩"
            else:
                status = "实绩/预测未知（LATEST_ACTUAL_ANNUAL_DATA 缺失）"
            notes = [status, f"dataflow={row['DATAFLOW']}"]
            if row["COUNTRY_UPDATE_DATE"]:
                notes.append(f"COUNTRY_UPDATE_DATE={row['COUNTRY_UPDATE_DATE']}")
            if row["UNIT"] == "XDC" and row["PRIMARY_DOMESTIC_CURRENCY"]:
                notes.append(f"PRIMARY_DOMESTIC_CURRENCY={row['PRIMARY_DOMESTIC_CURRENCY']}")
            raw = row["OBS_VALUE"].strip()
            try:
                value = Decimal(raw)
                if not value.is_finite():
                    raise InvalidOperation
            except InvalidOperation:
                value = None
                notes.append(f"OBS_VALUE={raw}" if raw else "OBS_VALUE=missing")
            identity = json.dumps([country, indicator, frequency, period], separators=(",", ":"))
            obs_id = "imf:" + sha256(identity.encode()).hexdigest()
            if obs_id in observations:
                raise ParseError(f"IMF 响应包含重复观测值：{country}.{indicator} {period}")
            try:
                observations[obs_id] = Observation(
                    obs_id=obs_id, source_id=snapshot.source_id, dataset_key=dataset_key,
                    table_key=table_key, survey="World Economic Outlook (WEO)",
                    indicator=row["SERIES_NAME"] or indicator, indicator_code=indicator,
                    area_code=country, time_label=period, time_start=date(year, 1, 1), time_end=date(year, 12, 31),
                    granularity=TimeGranularity.year,
                    dims={"COUNTRY": country, "INDICATOR": indicator, "FREQUENCY": frequency, "TIME_PERIOD": period},
                    unit=row["UNIT"] or None, value=value, note="; ".join(notes),
                    snapshot_sha256=snapshot.sha256, locator=format_locator({"row": number}),
                    fetched_at=snapshot.fetched_at,
                    release_id=release[0] if release else None, released_at=release[1] if release else None,
                )
            except ValueError as exc:  # e.g. year 0000, or a field the model rejects
                raise ParseError(f"IMF 第 {number} 行无法转成观测值：{exc}") from exc
        if missing := sorted(expected - seen):
            raise ParseError("IMF 返回 200 但缺少请求的序列（key 可能写错）：" + ", ".join(f"{c}.{i}" for c, i in missing))
        if releases == {None}:
            warnings.append("PUBLICATION_DATE 缺失或不是 4、10 月，release_id 留空")
        dataset = Dataset(
            dataset_key=dataset_key, source_id=snapshot.source_id, title=f"World Economic Outlook (WEO): {key}",
            kind="stat_dataset", description=first.get("FULL_DESCRIPTION") or None, publisher="International Monetary Fund",
            url=snapshot.resource_url, license=first.get("LICENSE") or None,
            extras={"attribution": first.get("SUGGESTED_CITATION") or None, "dataflow": flow,
                    # OBS_VALUE is kept as published and is a full value; SCALE is display-only (see module doc).
                    "scale": {"multiplier": 1.0, "evidence": "IMF SDMX-CSV 列 OBS_VALUE 为完整值，列 SCALE 只是显示量级、未应用"
                              + (f"（SCALE={','.join(sorted(scales))}）" if scales else "")}},
            snapshot_sha256=snapshot.sha256,
        )
        return ParseResult(datasets=[dataset], observations=list(observations.values()), warnings=warnings)
