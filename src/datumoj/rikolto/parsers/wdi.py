"""Parse WDI indicator metadata and complete observation tables."""
import json
from collections import Counter
from collections.abc import Mapping
from datetime import date
from decimal import Decimal, InvalidOperation
from hashlib import sha256

from pydantic import JsonValue

from datumoj.rikolto.core.contracts import ParseError, Parser, ParseResult
from datumoj.rikolto.core.models import Dataset, Observation, Snapshot, TimeGranularity, format_locator

_LICENSE = "CC BY 4.0 + World Bank 数据集附加条款"


class WDIParser(Parser):
    name = "worldbank_wdi"
    version = "1"
    source_kind = "worldbank_wdi"
    content_type = "*"

    def parse(self, snapshot: Snapshot, data: bytes, meta: Mapping[str, JsonValue] | None = None) -> ParseResult:
        try:
            payload = json.loads(data, parse_float=Decimal)
            if not isinstance(payload, list) or len(payload) != 2:
                raise ValueError("响应结构无效")
            header, rows = payload
            if not isinstance(header, dict) or not isinstance(rows, list):
                raise ValueError("响应缺少分页信息或记录")
            if int(header["pages"]) != 1 or int(header["page"]) != 1 or len(rows) != int(header["total"]):
                raise ValueError("响应不是完整的单页")
            if any(not isinstance(row, dict) for row in rows):
                raise ValueError("记录必须是对象")
            if not meta:
                raise ValueError("缺少请求上下文")
            if meta.get("request_type") == "metadata":
                datasets = []
                for index, row in enumerate(rows):
                    code = row["id"]
                    if code != meta.get("indicator") or not isinstance(row.get("name"), str) or not row["name"]:
                        raise ValueError("指标元数据不匹配")
                    datasets.append(Dataset(dataset_key=f"{snapshot.source_id}:{code}", source_id=snapshot.source_id,
                        title=row["name"], kind="wdi_indicator", description=row.get("sourceNote"),
                        publisher=row.get("sourceOrganization"), license=_LICENSE,
                        extras={"attribution": f"The World Bank: World Development Indicators: {row.get('sourceOrganization') or ''}"},
                        snapshot_sha256=snapshot.sha256, locator=format_locator({"row": index + 1})))
                if len(datasets) != 1:
                    raise ValueError("指标元数据缺失")
                return ParseResult(datasets=datasets)
            if meta.get("request_type") != "data":
                raise ValueError("未知请求类型")
            units, countries = meta["indicators"], meta["countries"]
            start, end = meta["start_year"], meta["end_year"]
            if not isinstance(units, dict) or not isinstance(countries, list):
                raise ValueError("缺少指标或国家范围")
            observations = []
            missing: Counter[str] = Counter()
            seen = set()
            for index, row in enumerate(rows):
                indicator = row["indicator"]
                code, title = indicator["id"], indicator["value"]
                country, year = row["countryiso3code"], int(row["date"])
                if code not in units or country not in countries or not start <= year <= end:
                    raise ValueError("记录超出请求范围")
                identity = (code, country, year)
                if identity in seen:
                    raise ValueError("重复观测键")
                seen.add(identity)
                if row["value"] is None:
                    missing[code] += 1
                    continue
                value = Decimal(str(row["value"]))
                if not value.is_finite():
                    raise ValueError("数值不是有限数")
                obs_id = "wdi:" + sha256(json.dumps(identity).encode()).hexdigest()
                observations.append(Observation(obs_id=obs_id, source_id=snapshot.source_id,
                    dataset_key=f"{snapshot.source_id}:{code}", table_key=code, survey="World Development Indicators",
                    indicator=title, indicator_code=code, area_code=country, area_label=row["country"]["value"],
                    time_start=date(year, 1, 1), time_end=date(year, 12, 31), time_label=str(year),
                    granularity=TimeGranularity.year, dims={"currency": "各国本币，未换算"} if code == "NY.GDP.MKTP.CN" else {},
                    unit=units[code], value=value, note=row.get("obs_status") or None,
                    snapshot_sha256=snapshot.sha256, locator=format_locator({"row": index + 1}), fetched_at=snapshot.fetched_at))
            return ParseResult(observations=observations, warnings=[f"{code}: {count} null values" for code, count in sorted(missing.items())])
        except (ValueError, TypeError, KeyError, IndexError, InvalidOperation) as exc:
            raise ParseError(f"WDI 响应无效：{exc}") from exc
