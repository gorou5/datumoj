"""Parse DCAT JSON-LD repo records without fetching distributions."""

import json
from collections.abc import Mapping

from datumoj.rikolto.core.contracts import ParseError, Parser, ParseResult
from datumoj.rikolto.core.models import Dataset, Resource, Snapshot, redact_url


def _items(value):
    return value if isinstance(value, list) else ([] if value is None else [value])


def _id(value):
    if isinstance(value, dict):
        value = value.get("@id", value.get("@value"))
    return value if isinstance(value, str) else None


def _texts(value):
    return [item for item in _items(value) if isinstance(item, dict) and isinstance(item.get("@value"), str)]


def _preferred(items):
    return next((x["@value"] for x in items if x.get("@language") == "en"),
                items[0]["@value"] if items else None)


class DataEuropaParser(Parser):
    name = "data_europa"
    version = "1"
    source_kind = "data_europa_repo"
    content_type = "*"

    def parse(self, snapshot: Snapshot, data: bytes, meta: Mapping | None = None) -> ParseResult:
        try:
            payload = json.loads(data)
        except (ValueError, UnicodeDecodeError) as exc:
            raise ParseError("invalid JSON-LD") from exc
        if isinstance(payload, list) and meta and meta.get("role") == "identifiers":
            return ParseResult()
        graph = payload.get("@graph") if isinstance(payload, dict) else None
        if not isinstance(graph, list):
            raise ParseError("JSON-LD graph missing")
        nodes = [n for n in graph if isinstance(n, dict)]
        datasets = [n for n in nodes if "dcat:Dataset" in _items(n.get("@type"))]
        if len(datasets) != 1:
            raise ParseError("expected one DCAT dataset")
        record = datasets[0]
        identifier = meta.get("dataset_id") if meta else None
        identifier = identifier or _id(record.get("dct:identifier"))
        if not isinstance(identifier, str) or not identifier:
            raise ParseError("dataset ID missing")
        titles, descriptions = _texts(record.get("dct:title")), _texts(record.get("dct:description"))
        # The catalog indexes title, description and tags, not extras.
        search_text = [x["@value"] for x in titles + descriptions]
        dataset = Dataset(dataset_key=f"{snapshot.source_id}:{identifier}", source_id=snapshot.source_id,
                          title=_preferred(titles) or identifier, kind="dataset",
                          description="\n".join(search_text) or None,
                          publisher=_id(record.get("dct:publisher")),
                          url=redact_url(_id(record["dcat:landingPage"])) if _id(record.get("dcat:landingPage")) else None,
                          license=_id(record.get("dct:license")),
                          tags=[x["@value"] for x in _texts(record.get("dcat:keyword"))],
                          extras={"titles": titles, "descriptions": descriptions, "dataset_uri": record.get("@id"),
                                  "theme": record.get("dcat:theme"), "publisher": record.get("dct:publisher"),
                                  "license": record.get("dct:license"), "issued": record.get("dct:issued"),
                                  "modified": record.get("dct:modified"), "catalogue": "publ"},
                          snapshot_sha256=snapshot.sha256, locator=f"entry={graph.index(record)}")
        by_id = {n.get("@id"): n for n in nodes if isinstance(n.get("@id"), str)}
        resources = []
        for ref in _items(record.get("dcat:distribution")):
            uri = _id(ref)
            item = by_id.get(uri, {})
            url = _id(item.get("dcat:downloadURL")) or _id(item.get("dcat:accessURL"))
            if not uri or not url:
                raise ParseError("distribution missing ID or URL")
            resources.append(Resource(resource_id=uri, dataset_key=dataset.dataset_key, source_id=snapshot.source_id,
                                      url=redact_url(url), name=_preferred(_texts(item.get("dct:title"))),
                                      format=_id(item.get("dct:format")), media_type=_id(item.get("dcat:mediaType")),
                                      license=_id(item.get("dct:license"))))
        return ParseResult(datasets=[dataset], resources=resources)
