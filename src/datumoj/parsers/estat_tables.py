"""e-Stat 统计表文件 → Observation，两种版式（DATUMOJ-IVY-305）。

只认清单（随快照保存的 meta，即 ``sources/jp-estat-files.yaml`` 的一项）指定的版式。文件格式、结构、表题、
调查名、地区任何一项和清单对不上都抛 ParseError，不猜：文件名和响应头不可信（东京和大阪的下载名都是
``02000.csv``，CSV 响应头写 UTF-8 实际是 cp932），一律按内容判断。

- ``wide_area_by_year``：人口推計 XLSX。行是全国 + 47 都道府県，列是年份；同一指标分在两组工作表里，
  2015 年两组都有，按 (指标, 地区, 年) 去重，保留先出现的工作表（locator 记着来源工作表），两边值不同就报错。
- ``census_area_by_measure``：経済センサス CSV。第 1 列是行号，表头里有列维度编码（经营组织 × 测度），
  数据行带地区码 ``ti.<5 位>`` 和产业码。
"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from datetime import date
from decimal import Decimal
from typing import Annotated, Any, Literal
from urllib.parse import parse_qs, urlsplit

from pydantic import BaseModel, ConfigDict, Field, JsonValue, ValidationError, model_validator

from datumoj.core.contracts import ParseError, Parser, ParseResult
from datumoj.core.models import Chunk, Dataset, Document, Observation, Resource, Snapshot, Text, TimeGranularity
from datumoj.reference.areas import lookup, normalize_area_label
from datumoj.tabular import RawCell, RawTable, read_csv, read_xlsx, sniff

LAYOUT_FORMATS: dict[str, tuple[str, int]] = {
    "wide_area_by_year": ("xlsx", 4),
    "census_area_by_measure": ("csv", 1),
}
"""版式 → (文件内容应有的格式, e-Stat 下载用的 fileKind)。"""
_MEDIA_TYPES = {
    "csv": "text/csv",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}

# 有的表文件里不写调查名（人口推計的表只写“補間補正人口”）。这里列出调查本身特有的用语，
# 文件里调查名和这些用语都找不到时核对失败。新增条目要先在真实文件里核实。
_SURVEY_MARKERS: dict[str, tuple[str, ...]] = {
    "人口推計": ("補間補正人口",),
}


class TableEntry(BaseModel):
    """清单 ``scope.tables`` 的一项；采集器原样放进 FetchTask.meta，解析器据此核对文件。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    stat_inf_id: Annotated[str, Field(pattern=r"^[0-9]{12}$")]
    survey: Text
    title: Text
    """文件里的表题须包含它（比较前做 NFKC、去空白、统一顿号和破折号）。"""
    layout: Literal["wide_area_by_year", "census_area_by_measure"]
    expected_area_level: Literal["prefecture"]
    publisher: Text | None = None
    area_code: Annotated[str, Field(pattern=r"^[0-9]{5}$")] | None = None
    """census_area_by_measure 一个文件只有一个地区，必须写明；wide_area_by_year 含全国和全部都道府県，不写。"""

    @model_validator(mode="after")
    def _area_code_per_layout(self) -> TableEntry:
        if (self.layout == "census_area_by_measure") != (self.area_code is not None):
            raise ValueError("census_area_by_measure 必须写 area_code，wide_area_by_year 不能写")
        return self


_DASHES = str.maketrans({**{c: "-" for c in "‐‑‒–—―−"}, "、": ","})
_NUMBER = re.compile(r"-?[0-9]+(?:\.[0-9]+)?")
_MARKS = frozenset({"-", "…", "...", "x", "X", "秘匿", "*", "***", "・"})
_ERA = re.compile(r"(昭和|平成|令和)(元|[0-9]{1,2})年")
_ERA_BASE = {"昭和": 1925, "平成": 1988, "令和": 2018}
_YEAR = re.compile(r"([0-9]{4})年?")
_UNIT = re.compile(r"\(単位(.+)\)")
_POINT = re.compile(r"各年([0-9]{1,2})月([0-9]{1,2})日現在")
_FOOTNOTE = re.compile(r"[0-9]+\)")
_AREA = re.compile(r"ti\.([0-9]{5})")
_CODE = re.compile(r"[A-Za-z0-9]+\.[0-9]{4}")


def _norm(text: str) -> str:
    """比较用：NFKC、去掉全部空白，顿号当逗号，各种破折号当 ``-``。"""
    return "".join(unicodedata.normalize("NFKC", text).translate(_DASHES).split())


def _raw(row: Sequence[RawCell], col: int) -> str:
    return row[col - 1].raw if col <= len(row) else ""


def _text(rows: Sequence[Sequence[RawCell]]) -> str:
    return "|".join(_norm(c.raw) for row in rows for c in row if c.raw.strip())


def _value(cell: RawCell) -> tuple[Decimal | None, str | None]:
    raw = unicodedata.normalize("NFKC", cell.raw).strip()
    if _NUMBER.fullmatch(raw):
        return Decimal(raw), None
    if raw in _MARKS:
        return None, cell.raw.strip()
    raise ParseError(f"{cell.locator} 的值 {cell.raw!r} 认不出")


def _check_title(text: str, entry: TableEntry, where: str) -> None:
    if _norm(entry.title) not in text:
        raise ParseError(f"{where} 里找不到清单的表题 {entry.title!r}")


def _title_cell(tables: Sequence[RawTable], entry: TableEntry) -> RawCell:
    for table in tables:
        for row in table.rows:
            for cell in row:
                if _norm(entry.title) in _norm(cell.raw):
                    return cell
    raise ParseError(f"文件里找不到包含清单表题 {entry.title!r} 的单元格")


def _check_survey(header: str, whole: str, entry: TableEntry, where: str) -> None:
    """调查名要出现在表头（注释里提到的别的调查不算，如“国勢調査による人口”）；否则全文须含该调查的特有用语。"""
    survey = _norm(entry.survey)
    markers = _SURVEY_MARKERS.get(survey)
    if survey not in header and not (markers and all(_norm(m) in whole for m in markers)):
        raise ParseError(f"{where} 不能确认属于清单的调查 {entry.survey!r}")


def _check_prefecture(code: str, label: str, where: str) -> tuple[str, str]:
    """code 是 2 位或 5 位都道府県码，须和名称一致；返回 (5 位码, 名称)。"""
    found = lookup(code)
    if len(found) != 1 or found[0].municipality is not None or found[0].prefecture != normalize_area_label(label):
        raise ParseError(f"{where}: 地区码 {code} 和名称 {label!r} 对不上，或不是都道府県")
    return found[0].code5, found[0].prefecture


class _Obs:
    """同一快照、同一清单项的观测值共用的字段。"""

    def __init__(self, snapshot: Snapshot, entry: TableEntry):
        self.base: dict[str, Any] = dict(
            source_id=snapshot.source_id, dataset_key=f"{snapshot.source_id}:{entry.stat_inf_id}",
            table_key=entry.stat_inf_id, survey=entry.survey, snapshot_sha256=snapshot.sha256,
            fetched_at=snapshot.fetched_at,
        )
        self.stat_inf_id = entry.stat_inf_id

    def __call__(self, key: str, cell: RawCell, **kw: Any) -> Observation:
        value, note = _value(cell)
        notes = [n for n in (note, kw.pop("note", None)) if n]
        return Observation(
            **self.base, obs_id=f"{self.stat_inf_id}:{key}", locator=cell.locator, value=value,
            note="; ".join(notes) or None, granularity=TimeGranularity.year, **kw,
        )


# ---------- wide_area_by_year ----------


def _years(row: Sequence[RawCell]) -> list[tuple[int, int]]:
    """E 列起连续的年份格 → [(列号, 年)]；不是年份行返回空列表。"""
    years: list[tuple[int, int]] = []
    for col in range(5, len(row) + 1):
        text = _norm(_raw(row, col))
        if not text:
            break
        mt = _YEAR.fullmatch(text)
        if mt is None:
            return []
        years.append((col, int(mt.group(1))))
    if any(a[1] >= b[1] for a, b in zip(years, years[1:])):
        return []
    return years


def _wide_area(row: Sequence[RawCell], where: str) -> tuple[str, str]:
    a, b = _norm(_raw(row, 1)), _norm(_raw(row, 2))
    if a == "全国" and not b:
        return "00000", "全国"
    if not a and re.fullmatch(r"[0-9]{2}", b):
        return _check_prefecture(b, _raw(row, 3), where)
    raise ParseError(f"{where}: 认不出地区行")


def _wide_area_by_year(tables: list[RawTable], snapshot: Snapshot, entry: TableEntry) -> tuple[list[Observation], list[str]]:
    make = _Obs(snapshot, entry)
    kept: dict[tuple[str, str, int], Observation] = {}
    dups: Counter[tuple[str, int, str, str]] = Counter()
    for table in tables:
        sheet, rows = table.sheet, table.rows
        year_at = next((i for i, row in enumerate(rows) if _years(row)), None)
        if year_at is None:
            raise ParseError(f"工作表 {sheet!r} 找不到年份行，不是 wide_area_by_year 版式")
        header, where = rows[:year_at], f"工作表 {sheet!r}"
        head_text = _text(header)
        _check_title(head_text, entry, where)
        _check_survey(head_text, _text(rows), entry, where)
        unit = next((mt.group(1) for row in header for c in row if (mt := _UNIT.fullmatch(_norm(c.raw)))), None)
        indicator = next(
            (_norm(c.raw) for row in header[1:] for c in row[4:] if c.raw.strip() and not c.raw.isascii()), None
        )
        if unit is None or indicator is None:
            raise ParseError(f"{where} 的表头里找不到单位或指标名")
        point = _POINT.search(head_text)
        years = _years(rows[year_at])
        marks = rows[year_at + 1] if year_at + 1 < len(rows) else ()
        seen: set[str] = set()
        for row in rows[year_at + 2 :]:
            if not any(c.raw.strip() for c in row):
                break  # 空行之后是注释
            area_code, area_label = _wide_area(row, f"{where} 第 {row[0].row} 行")
            if area_code in seen:
                raise ParseError(f"{where} 地区 {area_label} 出现两次")
            seen.add(area_code)
            for col, year in years:
                if col > len(row):
                    raise ParseError(f"{where} 第 {row[0].row} 行缺 {year} 年的值")
                day = date(year, int(point.group(1)), int(point.group(2))) if point else None
                footnote = _norm(_raw(marks, col))
                obs = make(
                    f"{indicator}:{area_code}:{year}", row[col - 1], indicator=indicator, area_code=area_code,
                    area_label=area_label, time_start=day or date(year, 1, 1), time_end=day or date(year, 12, 31),
                    time_label=f"{year}年", unit=unit, note=f"注{footnote}" if _FOOTNOTE.fullmatch(footnote) else None,
                )
                key = (indicator, area_code, year)
                first = kept.setdefault(key, obs)
                if first is not obs:
                    if first.value != obs.value or first.note != obs.note:
                        raise ParseError(f"{indicator} {area_label} {year} 年在 {first.locator} 和 {obs.locator} 的值不一致")
                    first_sheet = first.locator.split(";", 1)[0].removeprefix("sheet=")
                    dups[(indicator, year, first_sheet, sheet)] += 1
        expected = {"00000"} | {f"{n:02d}000" for n in range(1, 48)}
        if seen != expected:
            raise ParseError(f"{where} 的地区不是全国 + 47 都道府県（缺 {sorted(expected - seen)[:3]}）")
    warnings = [
        f"{indicator} {year} 年在工作表 {a!r} 和 {b!r} 重复（{n} 个地区，值一致），保留 {a!r} 的"
        for (indicator, year, a, b), n in dups.items()
    ]
    return list(kept.values()), warnings


# ---------- census_area_by_measure ----------


def _survey_period(text: str, where: str) -> tuple[date, date, str]:
    if mt := _ERA.search(text):
        year = _ERA_BASE[mt.group(1)] + (1 if mt.group(2) == "元" else int(mt.group(2)))
        label = mt.group(0)
    elif mt := re.search(r"([0-9]{4})年", text):
        year, label = int(mt.group(1)), mt.group(0)
    else:
        raise ParseError(f"{where} 的调查名里没有年份")
    return date(year, 1, 1), date(year, 12, 31), label


def _measure(label: str) -> tuple[str, str | None]:
    """``従業者数(人) (注2)`` → (``従業者数(人)``, ``人``)。"""
    name = re.sub(r"\s*[(（]注[0-9０-９]+[)）]", "", label).strip()
    unit = re.search(r"[(（]([^()（）]+)[)）]$", name)
    return name, unit.group(1) if unit else None


def _census_area_by_measure(
    tables: list[RawTable], snapshot: Snapshot, entry: TableEntry
) -> tuple[list[Observation], list[str]]:
    (table,) = tables
    rows, where = table.rows, "CSV"
    for i, row in enumerate(rows, 1):
        if _raw(row, 1) != str(i):
            raise ParseError(f"CSV 第 {i} 行第 1 列不是行号，不是 census_area_by_measure 版式")
    start = next((i for i, row in enumerate(rows) if _AREA.fullmatch(_raw(row, 2))), None)
    if start is None or start < 6:
        raise ParseError("CSV 里找不到带地区码（ti.xxxxx）的数据行，不是 census_area_by_measure 版式")
    # 表头最后 6 行：经营组织码、层级、测度码、层级、经营组织名、测度名（第 4 列是“地域”）
    org_codes, measure_codes, org_names, labels = rows[start - 6], rows[start - 4], rows[start - 2], rows[start - 1]
    cols = [j for j in range(8, len(org_codes) + 1) if _raw(org_codes, j)]
    if (
        _norm(_raw(labels, 4)) != "地域" or not cols or cols != list(range(8, cols[-1] + 1))
        or not all(_CODE.fullmatch(_raw(org_codes, j)) for j in cols)
    ):
        raise ParseError("CSV 表头的列维度编码认不出，不是 census_area_by_measure 版式")
    header = rows[:start]
    _check_title(_text(header), entry, where)
    _check_survey(_text(header), _text(header), entry, where)
    survey_cell = next((_norm(c.raw) for row in header for c in row if _norm(entry.survey) in _norm(c.raw)), "")
    time_start, time_end, time_label = _survey_period(survey_cell, where)

    columns: list[tuple[int, str, str, str | None, dict[str, str]]] = []
    org_label: dict[str, str] = {}
    for j in cols:
        top, code = _raw(org_codes, j), _raw(measure_codes, j)
        if _raw(org_names, j).strip():
            org_label[top] = _raw(org_names, j).strip()
        if code:
            if not _CODE.fullmatch(code) or top not in org_label or not _raw(labels, j).strip():
                raise ParseError(f"CSV 第 {j} 列的表头认不出")
            name, dims = _raw(labels, j), {"legal_org": org_label[top], "legal_org_code": top}
        else:
            # 不分经营组织的测度（１事業所当たり従業者数）：码在经营组织那一行，名称也在那一行
            if not _raw(org_names, j).strip():
                raise ParseError(f"CSV 第 {j} 列的表头认不出")
            name, code, dims = _raw(org_names, j), top, {}
        columns.append((j, code, *_measure(name), dims))

    make = _Obs(snapshot, entry)
    checked: set[tuple[str, str, str]] = set()
    out: list[Observation] = []
    for row in rows[start:]:
        line = f"CSV 第 {row[0].row} 行"
        mt = _AREA.fullmatch(_raw(row, 2))
        if mt is None:
            raise ParseError(f"{line} 不是带地区码的数据行")
        area_code, area_name, level = mt.group(1), _raw(row, 4), _raw(row, 3)
        if area_code != entry.area_code:
            raise ParseError(
                f"{line}: 文件内容的地区是 {area_code} {area_name}，清单要求 {entry.area_code}（以内容为准，不看文件名）"
            )
        if (area_code, area_name, level) not in checked:
            if level != "1":
                raise ParseError(f"{line}: 地区层级是 {level!r}，清单要求都道府県（1）")
            _check_prefecture(area_code, area_name, line)
            checked.add((area_code, area_name, level))
        ind_code, ind_level, ind_name = _raw(row, 5), _raw(row, 6), _raw(row, 7).strip()
        if not _CODE.fullmatch(ind_code) or not ind_name:
            raise ParseError(f"{line}: 产业码或产业名认不出")
        for j, code, indicator, unit, dims in columns:
            if j > len(row):
                raise ParseError(f"{line} 缺第 {j} 列")
            out.append(make(
                f"{area_code}:{ind_code}:{_raw(org_codes, j)}:{code}", row[j - 1], indicator=indicator,
                indicator_code=code, area_code=area_code, area_label=normalize_area_label(area_name),
                time_start=time_start, time_end=time_end, time_label=time_label, unit=unit,
                dims={"industry": ind_name, "industry_code": ind_code, "industry_level": ind_level, **dims},
            ))
    return out, []


_CONVERTERS: dict[str, Callable[[list[RawTable], Snapshot, TableEntry], tuple[list[Observation], list[str]]]] = {
    "wide_area_by_year": _wide_area_by_year,
    "census_area_by_measure": _census_area_by_measure,
}


class EstatTableParser(Parser):
    """按快照 meta 里的清单项转换 e-Stat 统计表文件；核对不通过或版式认不出抛 ParseError。"""

    name = "estat-tables"
    version = "1"
    source_kind = "estat_files"
    content_type = "*"  # 响应头不可信，按内容判断

    def parse(self, snapshot: Snapshot, data: bytes, meta: Mapping[str, JsonValue] | None = None) -> ParseResult:
        if snapshot.sha256 is None:
            raise ParseError(f"快照 {snapshot.snapshot_id} 没有正文")
        try:
            entry = TableEntry.model_validate(dict(meta or {}))
        except ValidationError as exc:
            raise ParseError(f"快照 meta 不是有效的清单项，版式不明，不猜：{exc}") from None
        if parse_qs(urlsplit(snapshot.resource_url).query).get("statInfId") != [entry.stat_inf_id]:
            raise ParseError(f"快照 URL 的 statInfId 和清单项 {entry.stat_inf_id} 不一致")
        fmt = LAYOUT_FORMATS[entry.layout][0]
        try:
            actual = sniff(data)
            if actual != fmt:
                raise ParseError(f"版式 {entry.layout} 要求 {fmt} 文件，内容是 {actual}")
            tables = read_xlsx(data) if fmt == "xlsx" else [read_csv(data)]
        except ParseError:
            raise
        except Exception as exc:  # 不可信的字节交给第三方读取器，任何失败都算输入损坏
            raise ParseError(f"读取 {fmt} 失败：{type(exc).__name__}: {exc}") from exc
        try:
            observations, warnings = _CONVERTERS[entry.layout](tables, snapshot, entry)
            if not observations:
                raise ParseError("统计表没有观测值，无法确定统计期间")
            earliest_observation = min(
                (obs for obs in observations if obs.time_start is not None),
                key=lambda obs: obs.time_start,
                default=None,
            )
            latest_observation = max(
                (obs for obs in observations if obs.time_start is not None),
                key=lambda obs: obs.time_start,
                default=None,
            )
            if earliest_observation is None or latest_observation is None:
                raise ParseError("统计表观测值没有时间标签，无法确定统计期间")
            earliest, latest = earliest_observation.time_label, latest_observation.time_label
            dataset_key = f"{snapshot.source_id}:{entry.stat_inf_id}"
            title_cell = _title_cell(tables, entry)
            dataset = Dataset(
                dataset_key=dataset_key,
                source_id=snapshot.source_id,
                title=entry.title,
                kind="stat_table",
                description=(
                    f"调查：{entry.survey}；统计期间：{earliest}～{latest}；"
                    f"地区层级：{entry.expected_area_level}"
                ),
                publisher=entry.publisher,
                url=snapshot.resource_url,
                tags=[entry.survey],
                extras={
                    "stat_inf_id": entry.stat_inf_id,
                    "survey": entry.survey,
                    "time_labels": {"earliest": earliest, "latest": latest},
                    "area_level": entry.expected_area_level,
                },
                snapshot_sha256=snapshot.sha256,
                locator=title_cell.locator,
            )
            document = Document(
                doc_key=dataset_key,
                source_id=snapshot.source_id,
                snapshot_sha256=snapshot.sha256,
                title=entry.title,
                lang="ja",
                kind="dataset",
                content_type=_MEDIA_TYPES[fmt],
            )
            title_chunk = Chunk(
                chunk_id=f"{dataset_key}#{title_cell.locator}",
                doc_key=dataset_key,
                locator=title_cell.locator,
                text=title_cell.raw,
                title_path=[entry.title],
                snapshot_sha256=snapshot.sha256,
                lang="ja",
            )
            resource = Resource(
                resource_id=f"{entry.stat_inf_id}:file",
                dataset_key=dataset_key,
                source_id=snapshot.source_id,
                url=snapshot.resource_url,
                format=fmt,
                media_type=_MEDIA_TYPES[fmt],
            )
            return ParseResult(
                documents=[document], chunks=[title_chunk], observations=observations,
                datasets=[dataset], resources=[resource], warnings=warnings,
            )
        except ValueError as exc:
            raise ParseError(f"{entry.layout} 转换失败：{exc}") from exc
