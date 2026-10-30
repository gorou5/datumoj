"""SDMX-ML 2.1 Generic observations; values and flags stay in source units.

Only source release records can establish a release_id/released_at. Header
Prepared/Extracted timestamps describe an API response, not a source release.
"""

from __future__ import annotations

import json
import re
from calendar import monthrange
from collections.abc import Mapping
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from hashlib import sha256

from lxml import etree
from pydantic import JsonValue

from datumoj.rikolto.core.contracts import ParseError, Parser, ParseResult
from datumoj.rikolto.core.models import Dataset, Observation, Snapshot, TimeGranularity, format_locator

_BASE = "http://www.sdmx.org/resources/sdmxml/schemas/v2_1/"
_NS = {"m": _BASE + "message", "s": _BASE + "structure", "c": _BASE + "common", "g": _BASE + "data/generic"}
_MISSING = {"", ":", "-", "…", "...", "x", "X", "秘匿"}


def _xml(data: bytes) -> etree._Element:
    try:
        root = etree.fromstring(data, etree.XMLParser(resolve_entities=False, load_dtd=False, no_network=True))
    except (etree.XMLSyntaxError, ValueError) as exc:
        raise ParseError("Eurostat 响应不是有效 XML") from exc
    if root.getroottree().docinfo.doctype:
        raise ParseError("Eurostat XML 不允许 DTD 或实体声明")
    return root


def _text(element: etree._Element, name: str) -> str | None:
    nodes = element.findall(f"c:{name}", _NS)
    english = [node for node in nodes if node.get("{http://www.w3.org/XML/1998/namespace}lang") == "en"]
    return next((node.text for node in [*english, *nodes] if node.text and node.text.strip()), None)


def read_structure(data: bytes, code: str) -> dict:
    """JSON-compatible DSD metadata; dimension labels come only from codelists."""
    root = _xml(data)
    structures = root.findall(".//s:DataStructure", _NS)
    structure = next((node for node in structures if node.get("id", "").lower() == code.lower()), None)
    if root.tag != f"{{{_NS['m']}}}Structure" or structure is None:
        raise ParseError(f"Eurostat DSD 缺少数据集 {code}")
    dimensions = structure.findall(".//s:DimensionList/s:Dimension", _NS)
    ids = [node.get("id") for node in dimensions]
    if not ids or any(not item for item in ids) or len(ids) != len(set(ids)):
        raise ParseError("Eurostat DSD 维度列表无效")
    lists = {
        node.get("id"): {item.get("id"): _text(item, "Name") for item in node.findall("s:Code", _NS)}
        for node in root.findall(".//s:Codelist", _NS)
    }
    labels = {}
    for node in dimensions:
        ref = node.find("s:LocalRepresentation/s:Enumeration/Ref", _NS)
        labels[node.get("id")] = lists.get(ref.get("id"), {}) if ref is not None else {}
    return {
        "id": code.lower(), "title": _text(structure, "Name") or code,
        "description": _text(structure, "Description"), "dimensions": ids, "labels": labels,
    }


def _period(label: str) -> tuple[date | None, date | None, TimeGranularity]:
    try:
        if re.fullmatch(r"[0-9]{4}", label):
            year = int(label)
            return date(year, 1, 1), date(year, 12, 31), TimeGranularity.year
        if match := re.fullmatch(r"([0-9]{4})-([QS])([1-4])", label):
            year, kind, part = match.groups()
            width = 3 if kind == "Q" else 6
            if kind == "S" and int(part) > 2:
                raise ValueError("invalid half year")
            first, last = (int(part) - 1) * width + 1, int(part) * width
            return (date(int(year), first, 1), date(int(year), last, monthrange(int(year), last)[1]),
                    TimeGranularity.quarter if kind == "Q" else TimeGranularity.half_year)
        if re.fullmatch(r"[0-9]{4}-[0-9]{2}", label):
            year, month = map(int, label.split("-"))
            return date(year, month, 1), date(year, month, monthrange(year, month)[1]), TimeGranularity.month
        if match := re.fullmatch(r"([0-9]{4})-W([0-9]{2})", label):
            start = date.fromisocalendar(*map(int, match.groups()), 1)
            return start, start + timedelta(days=6), TimeGranularity.week
        if re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", label):
            day = date.fromisoformat(label)
            return day, day, TimeGranularity.day
    except ValueError as exc:
        raise ParseError(f"Eurostat TIME_PERIOD 无效：{label}") from exc
    return None, None, TimeGranularity.other


def _values(node: etree._Element, path: str) -> dict[str, str]:
    values = {}
    for item in node.findall(path + "/g:Value", _NS):
        key, value = item.get("id"), item.get("value")
        if not key or value is None or key in values:
            raise ParseError("Eurostat 维度/属性缺少 id、value 或含重复 id")
        values[key] = value
    return values


def _dataset(snapshot: Snapshot, structure: dict) -> Dataset:
    code = structure["id"]
    return Dataset(
        dataset_key=f"{snapshot.source_id}:{code}", source_id=snapshot.source_id,
        title=structure["title"], description=structure.get("description"), kind="stat_dataset",
        publisher="Eurostat", url=snapshot.resource_url,
        extras={"dimensions": structure["dimensions"], "codelists": structure["labels"]},
        snapshot_sha256=snapshot.sha256,
    )


class EurostatParser(Parser):
    name = "eurostat"
    version = "1"
    source_kind = "eurostat_sdmx"
    content_type = "*"

    def parse(self, snapshot: Snapshot, data: bytes, meta: Mapping[str, JsonValue] | None = None) -> ParseResult:
        root = _xml(data)
        meta = dict(meta or {})
        if root.tag == f"{{{_NS['m']}}}Structure":
            node = root.find(".//s:DataStructure", _NS)
            if node is None or not node.get("id"):
                raise ParseError("Eurostat DSD 缺少 DataStructure")
            structure = read_structure(data, str(meta.get("id", node.get("id"))))
            return ParseResult(datasets=[_dataset(snapshot, structure)])
        if root.tag != f"{{{_NS['m']}}}GenericData":
            raise ParseError("Eurostat 响应不是 SDMX-ML 2.1 GenericData / Structure")
        code = root.findtext("m:Header/m:DataSetID", namespaces=_NS)
        structure = meta.get("structure")
        if not code or not isinstance(structure, dict) or structure.get("id") != code.lower():
            raise ParseError("Eurostat 数据缺少匹配的 DSD 上下文")
        dataset = root.find("m:DataSet", _NS)
        if dataset is None:
            raise ParseError("Eurostat 响应缺少 DataSet")
        code = code.lower()
        observations = {}
        labels = structure["labels"]
        dataset_dims = _values(dataset, "g:DataSetKey")
        dataset_attrs = _values(dataset, "g:Attributes")
        row = 0
        for series in dataset.findall("g:Series", _NS):
            series_dims = {**dataset_dims, **_values(series, "g:SeriesKey")}
            series_attrs = {**dataset_attrs, **_values(series, "g:Attributes")}
            for node in series.findall("g:Obs", _NS):
                row += 1
                dims = {**series_dims, **_values(node, "g:ObsKey")}
                time = node.find("g:ObsDimension", _NS)
                label = time.get("value") if time is not None else dims.pop("TIME_PERIOD", None)
                if not label or set(dims) != set(structure["dimensions"]):
                    raise ParseError("Eurostat 观测值缺少时间或维度与 DSD 不符")
                attrs = {**series_attrs, **_values(node, "g:Attributes")}
                notes = [f"{key}={value}" for key, value in sorted(attrs.items())]
                value_node = node.find("g:ObsValue", _NS)
                raw = value_node.get("value") if value_node is not None else None
                if raw is None or raw.strip() in _MISSING:
                    value = None
                    notes.append(f"OBS_VALUE={raw}" if raw else "OBS_VALUE=missing")
                else:
                    try:
                        value = Decimal(raw)
                    except InvalidOperation as exc:
                        raise ParseError(f"Eurostat OBS_VALUE 无效：{raw!r}") from exc
                    if not value.is_finite():
                        raise ParseError("Eurostat OBS_VALUE 必须是有限数")
                start, end, granularity = _period(label)
                identity = json.dumps([code, sorted(dims.items()), label], ensure_ascii=False, separators=(",", ":"))
                obs_id = "eurostat:" + sha256(identity.encode()).hexdigest()
                obs = Observation(
                    obs_id=obs_id, source_id=snapshot.source_id, dataset_key=f"{snapshot.source_id}:{code}",
                    table_key=code, survey=structure["title"], indicator=dims.get("na_item", code),
                    indicator_code=dims.get("na_item", code), unit=dims.get("unit"),
                    area_code=dims.get("geo"), area_label=labels.get("geo", {}).get(dims.get("geo")),
                    dims={**dims, "TIME_PERIOD": label}, time_label=label, time_start=start, time_end=end,
                    granularity=granularity, value=value, note="; ".join(notes) or None,
                    snapshot_sha256=snapshot.sha256, fetched_at=snapshot.fetched_at,
                    locator=format_locator({"row": row}),
                )
                previous = observations.get(obs_id)
                if previous is not None:
                    if previous.model_copy(update={"locator": obs.locator}) != obs:
                        raise ParseError("Eurostat 响应包含同一观测键的冲突值")
                    continue
                observations[obs_id] = obs
        warnings = []
        if any(not labels.get(dim) for dim in ("geo", "unit")):
            warnings.append("DSD 未含 geo / unit codelist 标签；代码保留，名称未知")
        return ParseResult(datasets=[_dataset(snapshot, structure)], observations=list(observations.values()), warnings=warnings)
