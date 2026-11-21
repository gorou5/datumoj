"""IMF WEO database files from the 2009-2010 archive (tab-separated text named ``.xls``, cp1252, CRLF).

Recognised by the header row, never by file name or extension. Same observation model as the SDMX-CSV
parser: ``table_key`` is ``WEO:<ISO>.<WEO Subject Code>.A`` and the observation identity does not depend
on the release, so one observation can carry several releases. Country is the ``ISO`` column (the numeric
``WEO Country Code`` is an IFS code and is kept only in the note).

Values are made full values like the SDMX ``OBS_VALUE``: the ``Scale`` column (Billions, Millions) is applied
and the original ``Scale`` goes into the note. Empty cells are skipped; other annotations (``n/a``) become
an observation with an empty value and the text in the note, never zero. ``release_id`` and ``released_at``
are left empty: the import command supplies them, nothing is inferred from the file.
"""

from __future__ import annotations

import csv
import io
import json
import re
from collections.abc import Mapping
from datetime import date
from decimal import Decimal, InvalidOperation
from hashlib import sha256

from pydantic import JsonValue

from datumoj.rikolto.core.contracts import ParseError, ParseResult
from datumoj.rikolto.core.models import Dataset, Observation, Snapshot, TimeGranularity, format_locator

HEAD = ("WEO Country Code", "ISO", "WEO Subject Code", "Country", "Subject Descriptor", "Subject Notes",
        "Units", "Scale", "Country/Series-specific Notes")
LAST = "Estimates Start After"
_YEAR = re.compile(r"[0-9]{4}")
_ISO = re.compile(r"[A-Z]{3}")
_CODE = re.compile(r"[A-Za-z0-9_]+")
SCALES = {"": Decimal(1), "Units": Decimal(1), "Billions": Decimal(10) ** 9, "Millions": Decimal(10) ** 6}
# "Percent of GDP" is the SDMX "PT"; the currency units use the codes the SDMX path publishes.
UNITS = {"Percent of GDP": "PT", "U.S. dollars": "USD", "National currency": "XDC"}
# Old IMF codes kept as published; the successor is written into the note, never substituted.
# 2010-04 起 IMF 改名，GFSM 2001 口径。Only these two: no other code is inferred by name.
RENAMED = {"GGB_NGDP": "GGXCNL_NGDP", "GGB": "GGXCNL"}
# 负责人 2026-11-20 定的导入范围：只导希腊一般政府赤字占 GDP（含旧代码 GGB_NGDP），其余行一律跳过。
ARCHIVE_INDICATORS = {"GGXCNL_NGDP"}
# 与 API 行同一序列：指标名取自 API 样本 tests/fixtures/imf_weo/weo_GRC_GGXCNL_NGDP_2000-2015.csv 的 SERIES_NAME；
# 存档原 Subject Descriptor 写进注记。GGB_NGDP 不在此表，保持原代码和原名称。
API_INDICATOR_NAMES = {
    "GGXCNL_NGDP": "Net lending (+) / net borrowing (-), General government, Percent of GDP"}
_ESTIMATES = re.compile(r"[0-9]{4}|0")
_EMPTY_VALUES = {"n/a", "--"}


def is_archive(data: bytes) -> bool:
    return data.removeprefix(b"\xef\xbb\xbf").startswith("\t".join(HEAD).encode("ascii") + b"\t")


def parse_archive(snapshot: Snapshot, data: bytes, meta: Mapping[str, JsonValue] | None = None) -> ParseResult:
    # The import command tries the file against each planned request; exactly one request may claim it:
    # the WEO flow with a planned indicator, so the other flows and series do not make the match ambiguous.
    meta = meta or {}
    flow, key = meta.get("dataflow"), meta.get("key")
    parts = key.split(".") if isinstance(key, str) else []
    if flow != "WEO" or len(parts) != 3 or parts[1] not in ARCHIVE_INDICATORS:
        raise ParseError(f"旧版 WEO 存档只由 WEO 数据流的计划内指标请求认领，不是 {flow!r} {key!r}")
    countries = set(parts[0].split("+"))
    try:
        text = data.removeprefix(b"\xef\xbb\xbf").decode("cp1252")
    except UnicodeDecodeError as exc:
        raise ParseError("WEO 存档不是 cp1252 文本") from exc
    reader = csv.reader(io.StringIO(text, newline=""), delimiter="\t")
    try:
        header = next(reader)
        if tuple(header[:9]) != HEAD or header[-1] != LAST or not all(_YEAR.fullmatch(y) for y in header[9:-1]) or len(header) < 11:
            raise ParseError("不是旧版 WEO 数据库文件的表头")
        observations: dict[str, Observation] = {}
        descriptions: dict[str, tuple[str, str | None]] = {}
        citation = None  # the source line comes after the data, so datasets are built at the end
        for row in reader:
            line = reader.line_num
            if not any(cell.strip() for cell in row[1:]):
                citation = row[0].strip() if row and row[0].strip() else citation
                continue  # blank line, or the source line after the data (quoted, trailing tabs or not)
            if len(row) != len(header):
                raise ParseError(f"WEO 存档第 {line} 行列数与表头不符")
            ifs, iso, code, _country, subject, notes, units, scale, _, *cells = row
            *cells, estimates = cells
            if not _ISO.fullmatch(iso) or not _CODE.fullmatch(code):
                raise ParseError(f"WEO 存档第 {line} 行的 ISO 或指标代码无效：{iso!r} {code!r}")
            if iso not in countries or RENAMED.get(code, code) not in ARCHIVE_INDICATORS:
                continue  # outside the planned import: skipped before Scale and values are checked
            if scale not in SCALES:
                raise ParseError(f"WEO 存档第 {line} 行的 Scale 不认识：{scale!r}")
            estimates = estimates.strip()
            if estimates and not _ESTIMATES.fullmatch(estimates):
                raise ParseError(f"WEO 存档第 {line} 行的 Estimates Start After 无效：{estimates!r}")
            known = estimates not in ("", "0")
            table_key = f"WEO:{iso}.{code}.A"
            dataset_key = f"{snapshot.source_id}:{table_key}"
            descriptions[dataset_key] = (f"{iso}.{code}.A", notes or None)
            for period, cell in zip(header[9:-1], cells, strict=True):
                raw = cell.strip()
                if not raw or raw in _EMPTY_VALUES:
                    continue
                year = int(period)
                status = (("预测" if year > int(estimates) else "实绩") if known
                          else "实绩/预测未知（Estimates Start After 缺失或为 0）")
                note = [status, f"Scale={scale}" if scale else "Scale=（空）", f"Units={units}",
                        f"WEO Country Code={ifs}"]
                try:
                    value = Decimal(raw.replace(",", ""))
                    if not value.is_finite():
                        raise InvalidOperation
                    value *= SCALES[scale]
                except InvalidOperation as exc:
                    raise ParseError(f"WEO 存档第 {line} 行的数值不认识：{raw!r}") from exc
                if code in API_INDICATOR_NAMES:
                    note.append(f"Subject Descriptor={subject}")
                if code in RENAMED:
                    note.append(f"代码对应：{code} → {RENAMED[code]}（2010-04 起 IMF 改名，GFSM 2001 口径；保留原代码）")
                identity = json.dumps([iso, code, "A", period], separators=(",", ":"))
                obs_id = "imf:" + sha256(identity.encode()).hexdigest()
                if obs_id in observations:
                    raise ParseError(f"WEO 存档包含重复观测值：{iso}.{code} {period}")
                try:
                    observations[obs_id] = Observation(
                        obs_id=obs_id, source_id=snapshot.source_id, dataset_key=dataset_key, table_key=table_key,
                        survey="World Economic Outlook (WEO)", indicator=API_INDICATOR_NAMES.get(code) or subject or code, indicator_code=code,
                        area_code=iso, time_label=period, time_start=date(year, 1, 1), time_end=date(year, 12, 31),
                        granularity=TimeGranularity.year,
                        dims={"COUNTRY": iso, "INDICATOR": code, "FREQUENCY": "A", "TIME_PERIOD": period},
                        unit=UNITS.get(units, units) or None, value=value, note="; ".join(note),
                        snapshot_sha256=snapshot.sha256, locator=format_locator({"row": line}),
                        fetched_at=snapshot.fetched_at, release_id=None, released_at=None)
                except ValueError as exc:
                    raise ParseError(f"WEO 存档第 {line} 行无法转成观测值：{exc}") from exc
    except csv.Error as exc:
        raise ParseError(f"WEO 存档 CSV 无效：{exc}") from exc
    except StopIteration as exc:
        raise ParseError("WEO 存档为空") from exc
    if not observations:
        raise ParseError("WEO 存档没有数据行")
    datasets = [
        Dataset(dataset_key=key, source_id=snapshot.source_id, title=f"World Economic Outlook (WEO): {name}",
                kind="stat_dataset", description=notes, publisher="International Monetary Fund",
                url=snapshot.resource_url, license=None,
                extras={"attribution": citation, "dataflow": "WEO archive"}, snapshot_sha256=snapshot.sha256)
        for key, (name, notes) in descriptions.items()]
    return ParseResult(datasets=datasets, observations=list(observations.values()),
                       warnings=["旧版 WEO 存档：release_id、released_at 由导入命令给出"])
