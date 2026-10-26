"""Statistics Dashboard JSON responses to Dataset and Observation records."""

from __future__ import annotations

import json
import re
from calendar import monthrange
from collections.abc import Mapping
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from hashlib import sha256
from typing import Any

from pydantic import JsonValue

from datumoj.rikolto.core.contracts import ParseError, ParseResult, Parser
from datumoj.rikolto.core.models import Dataset, Observation, Snapshot, TimeGranularity, format_locator
from datumoj.rikolto.reference.areas import lookup

_MISSING_MARKS = {"-", "…", "...", "x", "X", "秘匿"}


def _object_list(value: object, where: str) -> list[dict[str, Any]]:
    if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
        raise ParseError(f"{where} 必须是对象列表")
    return value


def _check_result(section: Mapping[str, Any], where: str) -> None:
    result = section.get("RESULT")
    if not isinstance(result, dict):
        raise ParseError(f"{where} 缺少 RESULT")
    status = result.get("status")
    if status not in ("0", 0, None):
        raise ParseError(f"{where} API 返回错误：{result.get('errorMsg', status)}")


def _catalog(payload: Mapping[str, Any], snapshot: Snapshot) -> ParseResult:
    section = payload.get("GET_META_INDICATOR_INF")
    if not isinstance(section, dict):
        raise ParseError("响应不是 getIndicatorInfo 指标目录")
    _check_result(section, "getIndicatorInfo")
    metadata = section.get("METADATA_INF")
    class_inf = metadata.get("CLASS_INF") if isinstance(metadata, dict) else None
    records = class_inf.get("CLASS_OBJ") if isinstance(class_inf, dict) else None
    indicators = _object_list(records, "指标目录 CLASS_OBJ")

    datasets = []
    for record in indicators:
        code, title = record.get("@code"), record.get("@name")
        if not isinstance(code, str) or not code or not isinstance(title, str) or not title.strip():
            raise ParseError("指标目录条目缺少 @code 或 @name")
        classes = record.get("CLASS", [])
        if not isinstance(classes, list) or any(not isinstance(item, dict) for item in classes):
            raise ParseError(f"指标 {code} 的 CLASS 必须是对象列表")
        surveys = list(dict.fromkeys(
            item["@statName"] for item in classes
            if isinstance(item.get("@statName"), str) and item["@statName"].strip()
        ))
        datasets.append(
            Dataset(
                dataset_key=f"{snapshot.source_id}:{code}",
                source_id=snapshot.source_id,
                title=title,
                kind="stat_indicator",
                publisher="、".join(surveys) or None,
                extras={
                    "indicator_code": code,
                    "details": record.get("details"),
                    "classes": classes,
                    "annotations": record.get("annotations", []),
                },
            )
        )
    return ParseResult(datasets=datasets)


def _class_maps(statistical_data: Mapping[str, Any]) -> dict[str, dict[str, str]]:
    class_inf = statistical_data.get("CLASS_INF")
    if not isinstance(class_inf, dict):
        raise ParseError("getData 缺少 CLASS_INF")
    groups = class_inf.get("CLASS_OBJ")
    if not isinstance(groups, list):
        raise ParseError("getData CLASS_INF.CLASS_OBJ 必须是列表")
    maps: dict[str, dict[str, str]] = {}
    for group in groups:
        if not isinstance(group, dict):
            raise ParseError("getData CLASS_INF 含非对象分组")
        group_id, classes = group.get("@id"), group.get("CLASS")
        if not isinstance(group_id, str) or not isinstance(classes, list):
            continue
        values: dict[str, str] = {}
        for item in classes:
            if not isinstance(item, dict) or not isinstance(item.get("@code"), str):
                continue
            label = item.get("@name", item.get("$"))
            if isinstance(label, str) and label:
                values[item["@code"]] = label
        maps[group_id] = values
    return maps


def _period(code: str, label: str) -> tuple[date | None, date | None, TimeGranularity]:
    try:
        if match := re.fullmatch(r"(\d{4})CY00", code):
            year = int(match.group(1))
            return date(year, 1, 1), date(year, 12, 31), TimeGranularity.year
        if match := re.fullmatch(r"(\d{4})FY00", code):
            year = int(match.group(1))
            return date(year, 4, 1), date(year + 1, 3, 31), TimeGranularity.fiscal_year
        if match := re.fullmatch(r"(\d{4})H([12])00", code):
            year, half = int(match.group(1)), int(match.group(2))
            start_month = 1 if half == 1 else 7
            end_month = start_month + 5
            return date(year, start_month, 1), date(year, end_month, monthrange(year, end_month)[1]), TimeGranularity.half_year
        if match := re.fullmatch(r"(\d{4})Q([1-4])00", code):
            year, quarter = int(match.group(1)), int(match.group(2))
            start_month = 1 + (quarter - 1) * 3
            end_month = start_month + 2
            return date(year, start_month, 1), date(year, end_month, monthrange(year, end_month)[1]), TimeGranularity.quarter
        if match := re.fullmatch(r"(\d{4})(\d{2})00", code):
            year, month = int(match.group(1)), int(match.group(2))
            return date(year, month, 1), date(year, month, monthrange(year, month)[1]), TimeGranularity.month
        if match := re.fullmatch(r"(\d{4})(\d{2})(\d{2})", code):
            day = date(*(int(part) for part in match.groups()))
            return day, day, TimeGranularity.day
        if match := re.fullmatch(r"(\d{4})W(\d{2})", code):
            start = date.fromisocalendar(int(match.group(1)), int(match.group(2)), 1)
            return start, start + timedelta(days=6), TimeGranularity.week
    except ValueError as exc:
        raise ParseError(f"getData 时间码无效：{code!r} ({label})") from exc
    return None, None, TimeGranularity.other


def _region(code: str) -> tuple[str, str]:
    areas = lookup(code)
    if len(areas) != 1:
        raise ParseError(f"getData 地区码无法由 DATUMOJ-IVY-302 唯一归一化：{code!r}")
    return areas[0].code5, areas[0].label


def _value(raw: object, locator: str) -> tuple[Decimal | None, str | None]:
    if not isinstance(raw, str):
        raise ParseError(f"{locator} 缺少字符串值 $")
    if raw.strip() in _MISSING_MARKS:
        return None, raw
    try:
        value = Decimal(raw.strip())
    except InvalidOperation as exc:
        raise ParseError(f"{locator} 的值无法解析：{raw!r}") from exc
    if not value.is_finite():
        raise ParseError(f"{locator} 的值不是有限数：{raw!r}")
    return value, None


def _observations(payload: Mapping[str, Any], snapshot: Snapshot) -> ParseResult:
    section = payload.get("GET_STATS")
    if not isinstance(section, dict):
        raise ParseError("响应不是 getData 数据")
    _check_result(section, "getData")
    statistical_data = section.get("STATISTICAL_DATA")
    if not isinstance(statistical_data, dict):
        raise ParseError("getData 缺少 STATISTICAL_DATA")
    maps = _class_maps(statistical_data)
    indicators, times, units = maps.get("indicator", {}), maps.get("time", {}), maps.get("unit", {})

    table_info = statistical_data.get("TABLE_INF")
    stat_names: dict[str, str] = {}
    if isinstance(table_info, dict):
        names = table_info.get("STAT_NAME", [])
        if isinstance(names, list):
            stat_names = {
                item["@code"]: item["$"] for item in names
                if isinstance(item, dict) and isinstance(item.get("@code"), str)
                and isinstance(item.get("$"), str) and item["$"].strip()
            }

    data_inf = statistical_data.get("DATA_INF")
    data_objs = data_inf.get("DATA_OBJ") if isinstance(data_inf, dict) else None
    rows = _object_list(data_objs, "getData DATA_INF.DATA_OBJ")
    observations: dict[str, Observation] = {}
    identities: dict[str, Observation] = {}
    for index, item in enumerate(rows, 1):
        value_obj = item.get("VALUE")
        if not isinstance(value_obj, dict):
            raise ParseError(f"getData 第 {index} 条缺少 VALUE 对象")
        code = value_obj.get("@indicator")
        region_raw = value_obj.get("@regionCode")
        time_code = value_obj.get("@time")
        stat_code = value_obj.get("@stat")
        unit_code = value_obj.get("@unit")
        if not all(isinstance(value, str) and value for value in (code, region_raw, time_code, stat_code)):
            raise ParseError(f"getData 第 {index} 条缺少指标、地区、时间或统计名码")
        indicator = indicators.get(code)
        survey = stat_names.get(stat_code)
        if not indicator or not survey:
            raise ParseError(f"getData 第 {index} 条无法解析指标名或调查名")
        area_code, area_label = _region(region_raw)
        time_label = times.get(time_code, time_code)
        time_start, time_end, granularity = _period(time_code, time_label)
        locator = format_locator({"row": index})
        number, note = _value(value_obj.get("$"), locator)
        dimensions = {
            key.removeprefix("@"): str(value)
            for key, value in value_obj.items()
            if key.startswith("@") and key not in {"@indicator", "@regionCode", "@time"}
        }
        identity = json.dumps(
            {
                "source_id": snapshot.source_id,
                "indicator_code": code,
                "area_code": area_code,
                "time": time_code,
                "dimensions": dimensions,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        content = json.dumps(value_obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        obs_id = "stat-dashboard:" + sha256(content.encode("utf-8")).hexdigest()
        unit = units.get(unit_code, unit_code if isinstance(unit_code, str) and not unit_code.isdecimal() else None)
        observation = Observation(
            obs_id=obs_id,
            source_id=snapshot.source_id,
            dataset_key=f"{snapshot.source_id}:{code}",
            table_key=code,
            survey=survey,
            indicator=indicator,
            indicator_code=code,
            area_code=area_code,
            area_label=area_label,
            time_start=time_start,
            time_end=time_end,
            time_label=time_label,
            granularity=granularity,
            dims=dimensions,
            unit=unit,
            value=number,
            note=note,
            snapshot_sha256=snapshot.sha256,
            locator=locator,
            fetched_at=snapshot.fetched_at,
        )
        previous = identities.get(identity)
        if previous is not None:
            if previous.obs_id != obs_id:
                raise ParseError(f"getData 响应含同一观测键的冲突记录：{identity}")
            continue
        identities[identity] = observation
        observations[obs_id] = observation
    return ParseResult(observations=list(observations.values()))


class StatDashboardParser(Parser):
    name = "stat_dashboard"
    version = "1"
    source_kind = "stat_dashboard"
    content_type = "*"

    def parse(
        self, snapshot: Snapshot, data: bytes, meta: Mapping[str, JsonValue] | None = None,
    ) -> ParseResult:
        del meta  # Each response contains its own directory or observation metadata.
        try:
            payload = json.loads(data)
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
            raise ParseError("统计 Dashboard 响应不是有效 JSON") from exc
        if not isinstance(payload, dict):
            raise ParseError("统计 Dashboard JSON 顶层必须是对象")
        if "GET_META_INDICATOR_INF" in payload:
            return _catalog(payload, snapshot)
        if "GET_STATS" in payload:
            return _observations(payload, snapshot)
        raise ParseError("未知的统计 Dashboard 响应类型")
