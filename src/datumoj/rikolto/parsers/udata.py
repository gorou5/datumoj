"""Parse udata directory pages into catalog metadata."""

import json
from collections.abc import Mapping
from datetime import UTC, datetime

from pydantic import JsonValue

from datumoj.rikolto.core.contracts import ParseError, Parser, ParseResult
from datumoj.rikolto.core.models import Dataset, Resource, Snapshot, redact_url


def _text(value: object) -> str | None:
    return value if isinstance(value, str) else None


class UdataParser(Parser):
    name = "udata"
    version = "1"
    source_kind = "udata"
    content_type = "*"

    def parse(self, snapshot: Snapshot, data: bytes, meta: Mapping[str, JsonValue] | None = None) -> ParseResult:
        try:
            payload = json.loads(data)
        except (ValueError, UnicodeDecodeError) as exc:
            raise ParseError("udata page is not valid JSON") from exc
        if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
            raise ParseError("udata page is missing data")
        datasets, resources, warnings = [], [], []
        for index, item in enumerate(payload["data"]):
            if not isinstance(item, dict) or not isinstance(item.get("id"), str) or not item["id"].strip():
                raise ParseError(f"data[{index}] is missing id")
            modified = None
            if isinstance(item.get("last_modified"), str):
                try:
                    modified = datetime.fromisoformat(item["last_modified"])
                    if modified.tzinfo is None:
                        modified = modified.replace(tzinfo=UTC)
                except ValueError:
                    pass
            org = item.get("organization")
            extras = {"frequency": item.get("frequency"), "temporal_coverage": item.get("temporal_coverage")}
            if item.get("last_modified") is not None and modified is None:
                extras["last_modified_raw"] = item["last_modified"]
            url = _text(item.get("page"))
            dataset = Dataset(dataset_key=f"{snapshot.source_id}:{item['id']}", source_id=snapshot.source_id,
                              title=_text(item.get("title")) or item["id"], kind="dataset",
                              description=_text(item.get("description")),
                              publisher=_text(org.get("name")) if isinstance(org, dict) else None,
                              url=redact_url(url) if url else None, license=_text(item.get("license")),
                              tags=[t for t in item.get("tags", []) if isinstance(t, str)]
                              if isinstance(item.get("tags"), list) else [],
                              modified_at=modified, extras=extras, snapshot_sha256=snapshot.sha256,
                              locator=f"entry={index}")
            datasets.append(dataset)
            entries = item.get("resources", [])
            if not isinstance(entries, list):
                raise ParseError(f"data[{index}].resources must be an array")
            for resource in entries:
                if not isinstance(resource, dict) or not _text(resource.get("id")) or not _text(resource.get("url")):
                    warnings.append(f"data[{index}] has a resource without id or URL")
                    continue
                resources.append(Resource(resource_id=resource["id"], dataset_key=dataset.dataset_key,
                                          source_id=snapshot.source_id, url=redact_url(resource["url"]),
                                          name=_text(resource.get("title")), format=_text(resource.get("format")),
                                          media_type=_text(resource.get("mime"))))
        return ParseResult(datasets=datasets, resources=resources, warnings=warnings)
