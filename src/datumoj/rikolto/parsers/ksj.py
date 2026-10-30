"""P05-2022 Saga: cp932 JMP metadata and UTF-8 GeoJSON, grouped by municipality."""

from __future__ import annotations

import json
import math
import re
from collections import defaultdict
from collections.abc import Mapping
from xml.etree import ElementTree as ET

from pydantic import JsonValue

from datumoj.rikolto.core.contracts import ParseError, Parser, ParseResult
from datumoj.rikolto.core.models import Chunk, Dataset, Document, Resource, Snapshot, format_locator
from datumoj.rikolto.parsers._zip import read_zip

_NS = "{http://zgate.gsi.go.jp/ch/jmp/}"


class KsjParser(Parser):
    name = "ksj-p05"
    version = "1"
    source_kind = "ksj_file"
    content_type = "*"

    def parse(self, snapshot: Snapshot, data: bytes, meta: Mapping[str, JsonValue] | None = None) -> ParseResult:
        if snapshot.sha256 is None:
            raise ParseError("KSJ snapshot has no body")
        members = read_zip(data)
        metadata_name, geo_name = "KS-META-P05-22_41.xml", "P05-22_41.geojson"
        try:
            # Explicit per-file encodings: shift_jis metadata includes Windows extensions.
            xml = members[metadata_name].decode("cp932")
            if re.search(r"<!\s*(?:DOCTYPE|ENTITY)\b", xml, re.IGNORECASE):
                raise ParseError("XML DTD/entities are forbidden")
            root = ET.fromstring(xml)
            citation = root.find(f"{_NS}identificationInfo/{_NS}MD_DataIdentification/{_NS}citation")
            title = citation.findtext(f"{_NS}title") if citation is not None else None
            if not title or "2022年度" not in title or "佐賀県" not in title or "市町村役場" not in title:
                raise ParseError("Metadata is not P05-2022 Saga")
            geo = json.loads(members[geo_name].decode("utf-8-sig"))
            if geo["type"] != "FeatureCollection" or geo["name"] != "P05-22_41" or not geo["features"]:
                raise ParseError("GeoJSON is not P05-2022 Saga")
            groups: dict[str, list[tuple[int, str]]] = defaultdict(list)
            for index, feature in enumerate(geo["features"], 1):
                p, geometry = feature["properties"], feature["geometry"]
                code, kind, name, address = (p[k] for k in ("P05_001", "P05_002", "P05_003", "P05_004"))
                if (feature["type"] != "Feature" or not isinstance(code, str) or not re.fullmatch(r"41[0-9]{3}", code)
                        or not all(isinstance(v, str) and v.strip() for v in (kind, name, address))):
                    raise ParseError(f"Invalid P05 properties at feature {index}")
                coords = geometry["coordinates"]
                if (geometry["type"] != "Point" or len(coords) != 2
                        or any(isinstance(v, bool) or not isinstance(v, int | float) or not math.isfinite(v) for v in coords)
                        or not -180 <= coords[0] <= 180 or not -90 <= coords[1] <= 90):
                    raise ParseError(f"Invalid Point at feature {index}")
                text = (f"施設名：{name}；種別（P05_002）：{kind}；所在地：{address}；"
                        f"経度：{coords[0]}；緯度：{coords[1]}；要素番号：{index}")
                groups[code].append((index, text))
            key = f"{snapshot.source_id}:P05-22_41"
            metadata_locator = format_locator({"member": metadata_name})
            chunks = [Chunk(
                chunk_id=f"{key}:metadata", doc_key=key, locator=metadata_locator, text=title,
                title_path=[title], snapshot_sha256=snapshot.sha256, lang="ja",
            )]
            for code, facilities in sorted(groups.items()):
                locator = format_locator({"member": geo_name, "feature": ",".join(str(i) for i, _ in facilities)})
                chunks.append(Chunk(
                    chunk_id=f"{key}:{code}", doc_key=key, locator=locator,
                    text=f"{title}；市町村コード：{code}\n" + "\n".join(text for _, text in facilities),
                    title_path=[title, code], snapshot_sha256=snapshot.sha256, lang="ja",
                ))
            return ParseResult(
                documents=[Document(doc_key=key, source_id=snapshot.source_id, snapshot_sha256=snapshot.sha256,
                                    title=title, lang="ja", kind="dataset", content_type="application/zip",
                                    metadata={"year": "2022", "crs": "JGD2011", "publisher": "国土交通省"})],
                chunks=chunks,
                datasets=[Dataset(dataset_key=key, source_id=snapshot.source_id, title=title, kind="geospatial",
                                  publisher="国土交通省", url=snapshot.resource_url, license="CC BY 4.0",
                                  extras={"year": 2022, "municipality_codes": sorted(groups),
                                          "feature_count": sum(len(g) for g in groups.values()), "crs": "JGD2011"},
                                  snapshot_sha256=snapshot.sha256, locator=metadata_locator)],
                resources=[Resource(resource_id="P05-22_41:zip", dataset_key=key, source_id=snapshot.source_id,
                                    url=snapshot.resource_url, format="zip", media_type="application/zip", license="CC BY 4.0")],
            )
        except (KeyError, TypeError, ValueError, AttributeError, ET.ParseError) as exc:
            raise ParseError(f"Invalid KSJ P05 archive: {exc}") from exc
