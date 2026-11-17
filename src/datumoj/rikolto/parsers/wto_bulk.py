"""WTO merchandise trade annual bulk ZIP (one cp1252 CSV) → observations.

Values stay as published (million US dollars). The ZIP's own Last-Modified, ETag
and SHA-256 identify the release; nothing is taken from the fetch time.
The series is SITC3 aggregates: never merge it with Comtrade (HS) by name.
"""

from __future__ import annotations

import csv
import io
import json
from collections.abc import Mapping
from datetime import date
from decimal import Decimal, InvalidOperation
from email.utils import parsedate_to_datetime
from hashlib import sha256

from pydantic import JsonValue

from datumoj.rikolto.core.contracts import ParseError, Parser, ParseResult
from datumoj.rikolto.core.models import Dataset, Observation, Snapshot, TimeGranularity, format_locator
from datumoj.rikolto.parsers._zip import read_zip

TABLE = "merchandise_values_annual"
_MISSING = {"", "-", "…", "...", "x", "X", ":", "秘匿"}
_COLUMNS = ("IndicatorCode", "Indicator", "ReporterCode", "Reporter", "PartnerCode", "ProductCode",
            "ProductClassificationCode", "Unit", "Year", "ValueFlagCode", "ValueFlag", "Value")
_DESCRIPTION = ("WTO 商品贸易年度总额（百万美元），口径是 SITC3 汇总（Merchandise - SITC Revision 3 aggregates）；"
                "报告方用 WTO 自己的 3 位代码（美国 840、法国 250）。不与 Comtrade（HS）按名称合并。")


def _release(snapshot: Snapshot) -> tuple[str | None, date | None]:
    """Only the ZIP's own headers and hash; missing or malformed headers leave both empty."""
    if not snapshot.last_modified or not snapshot.etag:
        return None, None
    try:
        released_at = parsedate_to_datetime(snapshot.last_modified).date()
    except (TypeError, ValueError):
        return None, None
    return f"{snapshot.last_modified}|{snapshot.etag}|{snapshot.sha256}", released_at


def _scope(meta: Mapping[str, JsonValue] | None) -> tuple[set, set, str, str, int, int]:
    meta = dict(meta or {})
    try:
        indicators, reporters = set(meta["indicators"]), set(meta["reporters"])
        start, end = int(meta["start_year"]), int(meta["end_year"])
        return indicators, reporters, str(meta["partner"]), str(meta["product"]), start, end
    except (KeyError, TypeError, ValueError) as exc:
        raise ParseError("WTO 快照缺少采集器保存的 scope（indicators、reporters、partner、product、年份）") from exc


class WTOBulkParser(Parser):
    name = "wto_bulk"
    version = "1"
    source_kind = "wto_bulk"
    content_type = "*"

    def parse(self, snapshot: Snapshot, data: bytes, meta: Mapping[str, JsonValue] | None = None) -> ParseResult:
        indicators, reporters, partner, product, start, end = _scope(meta)
        files = read_zip(data)
        members = [name for name in files if name.lower().endswith(".csv")]
        if len(members) != 1:
            raise ParseError("WTO ZIP 必须恰好含一个 CSV")
        try:
            text = files[members[0]].decode("cp1252")
        except UnicodeDecodeError as exc:
            raise ParseError("WTO CSV 不是有效的 cp1252") from exc
        reader = csv.reader(io.StringIO(text, newline=""))
        header = next(reader, None)
        if header is None or any(name not in header for name in _COLUMNS):
            raise ParseError("WTO CSV 表头缺少必需列")
        col = {name: header.index(name) for name in _COLUMNS}
        release_id, released_at = _release(snapshot)
        observations: dict[str, Observation] = {}
        for number, row in enumerate(reader, 1):
            if not row:
                continue
            if len(row) != len(header):
                raise ParseError(f"WTO CSV 第 {number} 行列数与表头不符")
            cell = {name: row[i] for name, i in col.items()}
            try:
                year = int(cell["Year"])
            except ValueError:
                continue  # Non-annual period labels are never in scope.
            if (cell["IndicatorCode"] not in indicators or cell["ReporterCode"] not in reporters
                    or cell["PartnerCode"] != partner or cell["ProductCode"] != product
                    or not start <= year <= end):
                continue
            notes = []
            if cell["ValueFlagCode"]:
                notes.append(f"ValueFlagCode={cell['ValueFlagCode']}"
                             + (f" ({cell['ValueFlag']})" if cell["ValueFlag"] else ""))
            raw = cell["Value"].strip()
            if raw in _MISSING:
                value = None
                notes.append(f"Value={raw}" if raw else "Value=missing")
            else:
                try:
                    value = Decimal(raw)
                except InvalidOperation as exc:
                    raise ParseError(f"WTO Value 无效：{raw!r}（第 {number} 行）") from exc
                if not value.is_finite():
                    raise ParseError("WTO Value 必须是有限数")
            dims = {"partner": cell["PartnerCode"], "product": cell["ProductCode"],
                    "classification": cell["ProductClassificationCode"], "year": cell["Year"]}
            identity = json.dumps([cell["IndicatorCode"], cell["ReporterCode"], sorted(dims.items())],
                                  ensure_ascii=False, separators=(",", ":"))
            obs_id = "wto:" + sha256(identity.encode()).hexdigest()
            obs = Observation(
                obs_id=obs_id, source_id=snapshot.source_id, dataset_key=f"{snapshot.source_id}:{TABLE}",
                table_key=TABLE, survey="WTO Merchandise trade values, annual (SITC3 aggregates)",
                indicator=cell["Indicator"], indicator_code=cell["IndicatorCode"],
                area_code=cell["ReporterCode"], area_label=cell["Reporter"], dims=dims,
                time_label=cell["Year"], time_start=date(year, 1, 1), time_end=date(year, 12, 31),
                granularity=TimeGranularity.year, unit=cell["Unit"] or None, value=value,
                note="; ".join(notes) or None, snapshot_sha256=snapshot.sha256, fetched_at=snapshot.fetched_at,
                locator=format_locator({"row": number}), release_id=release_id, released_at=released_at,
            )
            previous = observations.get(obs_id)
            if previous is not None:
                if previous.model_copy(update={"locator": obs.locator}) != obs:
                    raise ParseError("WTO CSV 含同一观测键的冲突值")
                continue
            observations[obs_id] = obs
        warnings = []
        if release_id is None:
            warnings.append("ZIP 缺少 Last-Modified 或 ETag，release_id 留空")
        dataset = Dataset(
            dataset_key=f"{snapshot.source_id}:{TABLE}", source_id=snapshot.source_id,
            title="WTO merchandise trade values, annual (SITC3 aggregates)", description=_DESCRIPTION,
            kind="stat_dataset", publisher="World Trade Organization", url=snapshot.resource_url,
            extras={"classification": "SITC3", "unit": "Million US dollar", "not_comparable_by_name_with": "UN Comtrade (HS)"},
            snapshot_sha256=snapshot.sha256,
        )
        return ParseResult(datasets=[dataset], observations=list(observations.values()), warnings=warnings)
