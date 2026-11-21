"""OECD SDMX-CSV observations, plus the contentconstraint release marker.

Values and attribute columns stay as the source wrote them; UNIT_MULT is recorded, never applied.
A release identity is taken only from the source: the EDITION dimension of the revisions dataflow, or the
contentconstraint ``validFrom`` the crawler stored in the task meta. Without either it stays empty.
"""

from __future__ import annotations

import csv
import io
import json
import re
from calendar import monthrange
from collections.abc import Mapping
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from hashlib import sha256

from lxml import etree
from pydantic import JsonValue

from datumoj.rikolto.core.contracts import ParseError, Parser, ParseResult
from datumoj.rikolto.core.models import Dataset, Observation, Snapshot, TimeGranularity, format_locator

_STRUCTURE = "http://www.sdmx.org/resources/sdmxml/schemas/v2_1/structure"
_MISSING = {"", ":", "-", "…", "...", "x", "X", "秘匿"}
_FLOW = re.compile(r"([A-Za-z0-9_.]+):([A-Za-z0-9_@]+)\(([0-9]+(?:\.[0-9]+)*)\)")


def _xml(data: bytes) -> etree._Element:
    try:
        root = etree.fromstring(data, etree.XMLParser(resolve_entities=False, load_dtd=False, no_network=True))
    except (etree.XMLSyntaxError, ValueError) as exc:
        raise ParseError("OECD 响应不是有效 XML") from exc
    if root.getroottree().docinfo.doctype:
        raise ParseError("OECD XML 不允许 DTD 或实体声明")
    return root


def _utc(value: str) -> datetime:
    try:
        moment = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ParseError(f"OECD validFrom 无效：{value!r}") from exc
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


def read_valid_from(data: bytes, agency: str, dataflow: str) -> str:
    """The ``validFrom`` of ``CR_A_<dataflow>`` exactly as written (the source's last-update time)."""
    for node in _xml(data).iter(f"{{{_STRUCTURE}}}ContentConstraint"):
        if node.get("id") == f"CR_A_{dataflow}" and node.get("agencyID") == agency:
            valid_from = node.get("validFrom")
            if not valid_from:
                break
            _utc(valid_from)
            return valid_from
    raise ParseError(f"OECD contentconstraint 缺少 CR_A_{dataflow} 的 validFrom")


_COMMON = "http://www.sdmx.org/resources/sdmxml/schemas/v2_1/common"
_XML_LANG = "{http://www.w3.org/XML/1998/namespace}lang"
_INDICATOR_DIMS = ("MEASURE", "TRANSACTION")  # same precedence as the observation's indicator


def read_code_labels(data: bytes) -> dict[str, dict[str, str]]:
    """English code names of the indicator dimensions, from the codelists of a structure message."""
    root = _xml(data)
    lists = {}
    for node in root.iter(f"{{{_STRUCTURE}}}Codelist"):
        names = {}
        for item in node.findall(f"{{{_STRUCTURE}}}Code"):
            text = next((n.text for n in item.findall(f"{{{_COMMON}}}Name") if n.get(_XML_LANG) == "en" and n.text and n.text.strip()), None)
            if text:
                names[item.get("id")] = text
        lists[node.get("agencyID"), node.get("id"), node.get("version")] = names  # SDMX identifies a codelist by all three
    labels = {}
    for dim in root.iter(f"{{{_STRUCTURE}}}Dimension"):
        ref = dim.find(f"{{{_STRUCTURE}}}LocalRepresentation/{{{_STRUCTURE}}}Enumeration/Ref")
        key = None if ref is None else (ref.get("agencyID"), ref.get("id"), ref.get("version"))
        if dim.get("id") in _INDICATOR_DIMS and lists.get(key):
            labels[dim.get("id")] = lists[key]
    return labels


def _period(label: str) -> tuple[date | None, date | None, TimeGranularity]:
    try:
        if re.fullmatch(r"[0-9]{4}", label):
            year = int(label)
            return date(year, 1, 1), date(year, 12, 31), TimeGranularity.year
        if match := re.fullmatch(r"([0-9]{4})-([QS])([1-4])", label):
            year, kind, part = int(match[1]), match[2], int(match[3])
            width = 3 if kind == "Q" else 6
            if kind == "S" and part > 2:
                raise ValueError("invalid half year")
            last = part * width
            return (date(year, last - width + 1, 1), date(year, last, monthrange(year, last)[1]),
                    TimeGranularity.quarter if kind == "Q" else TimeGranularity.half_year)
        if re.fullmatch(r"[0-9]{4}-[0-9]{2}", label):
            year, month = map(int, label.split("-"))
            return date(year, month, 1), date(year, month, monthrange(year, month)[1]), TimeGranularity.month
        if re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", label):
            day = date.fromisoformat(label)
            return day, day, TimeGranularity.day
    except ValueError as exc:
        raise ParseError(f"OECD TIME_PERIOD 无效：{label}") from exc
    return None, None, TimeGranularity.other


def _edition_date(edition: str) -> date:
    """EDITION is YYYYMM; the source gives no day, so the 1st of that month orders the editions."""
    match = re.fullmatch(r"([0-9]{4})(0[1-9]|1[0-2])", edition)
    if match is None:
        raise ParseError(f"OECD EDITION 必须是 YYYYMM：{edition!r}")
    return date(int(match[1]), int(match[2]), 1)


def _unit(attrs: dict[str, str], dims: dict[str, str]) -> str | None:
    # CURRENCY is "_Z" (not applicable) on USD_EXC rows; those keep the measure code, so XDC and USD_EXC stay apart.
    currency = attrs.get("CURRENCY", "")
    unit = currency if currency not in ("", "_Z") else dims.get("UNIT_MEASURE")
    mult = attrs.get("UNIT_MULT", "")
    if mult and not re.fullmatch(r"-?[0-9]+", mult):
        raise ParseError(f"OECD UNIT_MULT 无效：{mult!r}")
    if unit and mult not in ("", "0"):
        unit = f"{unit} ×10^{mult}"
    return unit


class OecdSdmxParser(Parser):
    name = "oecd_sdmx"
    version = "1"
    source_kind = "oecd_sdmx"
    content_type = "*"

    def parse(self, snapshot: Snapshot, data: bytes, meta: Mapping[str, JsonValue] | None = None) -> ParseResult:
        meta = dict(meta or {})
        if data.lstrip()[:1] == b"<":
            root = _xml(data)
            if next(root.iter(f"{{{_STRUCTURE}}}DataStructure"), None) is not None:
                return ParseResult(warnings=["结构响应只提供代码名称，没有观测值"])
            if next(root.iter(f"{{{_STRUCTURE}}}ContentConstraint"), None) is None:
                raise ParseError("OECD XML 不是 contentconstraint")
            return ParseResult(warnings=["contentconstraint 只提供发布标识，没有观测值"])
        try:
            text = data.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise ParseError("OECD CSV 不是 UTF-8") from exc
        rows = list(csv.reader(io.StringIO(text, newline="")))
        header = rows[0] if rows else []
        if (header[:1] != ["DATAFLOW"] or len(set(header)) != len(header)
                or "OBS_VALUE" not in header or "TIME_PERIOD" not in header):
            raise ParseError("OECD CSV 缺少 DATAFLOW / TIME_PERIOD / OBS_VALUE 列")
        value_at = header.index("OBS_VALUE")
        if header.index("TIME_PERIOD") > value_at:
            raise ParseError("OECD CSV 的 TIME_PERIOD 必须在 OBS_VALUE 之前")
        # SDMX-CSV order: DATAFLOW, dimensions (with TIME_PERIOD), OBS_VALUE, then attributes.
        dim_names = [name for name in header[1:value_at] if name != "TIME_PERIOD"]
        attr_names = header[value_at + 1:]

        flows = {row[0] for row in rows[1:] if row}
        if len(flows) != 1 or (matched := _FLOW.fullmatch(next(iter(flows)))) is None:
            raise ParseError("OECD CSV 必须恰有一个 DATAFLOW 值（agency:id(version)）")
        flow, (agency, flow_id, version) = next(iter(flows)), matched.groups()
        for key, found in (("agency", agency), ("id", flow_id), ("version", version)):
            if key in meta and meta[key] != found:
                raise ParseError(f"OECD 数据流与计划不符：{key} 应为 {meta[key]}，实际 {found}")
        table = flow_id.rsplit("@", 1)[-1].lower()
        valid_from = meta.get("valid_from")
        valid_date = _utc(valid_from).astimezone(UTC).date() if isinstance(valid_from, str) else None

        observations: dict[tuple[str, str | None], Observation] = {}
        used: dict[str, set[str]] = {}
        for number, row in enumerate(rows[1:], 1):
            if not row:
                continue
            if len(row) != len(header):
                raise ParseError(f"OECD CSV 第 {number} 行列数与表头不符")
            cells = dict(zip(header, row, strict=True))
            dims = {name: cells[name] for name in dim_names}
            attrs = {name: cells[name] for name in attr_names}
            label = cells["TIME_PERIOD"]
            if not label:
                raise ParseError(f"OECD CSV 第 {number} 行缺少 TIME_PERIOD")
            raw = cells["OBS_VALUE"]
            notes = [f"{name}={value}" for name, value in sorted(attrs.items()) if value]
            if raw.strip() in _MISSING:
                value = None
                notes.append(f"OBS_VALUE={raw}" if raw else "OBS_VALUE=missing")
            else:
                try:
                    value = Decimal(raw)
                except InvalidOperation as exc:
                    raise ParseError(f"OECD OBS_VALUE 无效：{raw!r}") from exc
                if not value.is_finite():
                    raise ParseError("OECD OBS_VALUE 必须是有限数")
            start, end, granularity = _period(label)
            if edition := dims.get("EDITION"):
                release_id, released_at = edition, _edition_date(edition)
            elif isinstance(valid_from, str):
                release_id, released_at = f"{flow_id}@{valid_from}", valid_date
            else:
                release_id = released_at = None
            # The logical series ignores EDITION, so editions are releases of one observation, not new ones.
            series = [agency, flow_id, sorted((k, v) for k, v in dims.items() if k != "EDITION"), label]
            obs_id = "oecd:" + sha256(json.dumps(series, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()
            indicator = dims.get("MEASURE") or dims.get("TRANSACTION") or table
            for dim in _INDICATOR_DIMS:
                if dims.get(dim) == indicator:
                    used.setdefault(dim, set()).add(indicator)
                    break
            obs = Observation(
                obs_id=obs_id, source_id=snapshot.source_id, dataset_key=f"{snapshot.source_id}:{table}",
                table_key=table, survey=flow, indicator=indicator, indicator_code=indicator,
                area_code=dims.get("REF_AREA"), unit=_unit(attrs, dims), dims={**dims, "TIME_PERIOD": label},
                time_label=label, time_start=start, time_end=end, granularity=granularity, value=value,
                note="; ".join(notes) or None, snapshot_sha256=snapshot.sha256, fetched_at=snapshot.fetched_at,
                locator=format_locator({"row": number}), release_id=release_id, released_at=released_at,
            )
            previous = observations.get((obs_id, release_id))
            if previous is not None:
                if previous.model_copy(update={"locator": obs.locator}) != obs:
                    raise ParseError("OECD 响应包含同一观测键的冲突值")
                continue
            observations[obs_id, release_id] = obs
        given = meta.get("code_labels")
        given = given if isinstance(given, dict) else {}
        code_labels = {}
        for dim, codes in sorted(used.items()):
            names = given.get(dim)
            found = {c: names[c] for c in sorted(codes) if isinstance(names, dict) and isinstance(names.get(c), str) and names[c]}
            if found:
                code_labels[dim] = found
        dataset = Dataset(
            dataset_key=f"{snapshot.source_id}:{table}", source_id=snapshot.source_id, title=flow,
            kind="stat_dataset", publisher="OECD", url=snapshot.resource_url,
            extras={"agency": agency, "dataflow": flow_id, "version": version,
                    "dimensions": dim_names, "attributes": attr_names, "code_labels": code_labels},
            snapshot_sha256=snapshot.sha256,
        )
        return ParseResult(datasets=[dataset], observations=list(observations.values()))
