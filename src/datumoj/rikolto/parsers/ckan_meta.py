"""Parse CKAN package metadata into the catalog models."""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import UTC, datetime

from pydantic import JsonValue

from datumoj.rikolto.core.contracts import ParseError, Parser, ParseResult
from datumoj.rikolto.core.models import Dataset, Resource, Snapshot, redact_url


def _text(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _timestamp(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        result = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return result.replace(tzinfo=UTC) if result.tzinfo is None else result


def _license(record: Mapping[str, object]) -> str | None:
    """Prefer CKAN's identifier while retaining empty strings and explicit nulls."""
    key = "license_id" if "license_id" in record else "license"
    return _text(record.get(key))


def _tags(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    result = []
    for tag in value:
        name = tag.get("name") if isinstance(tag, dict) else tag
        if isinstance(name, str):
            result.append(name)
    return result


def _resource_url(record: Mapping[str, object]) -> str | None:
    for key in ("url", "download_url", "access_url"):
        value = record.get(key)
        if isinstance(value, str) and value.strip():
            return redact_url(value)
    return None


class CkanMetaParser(Parser):
    name = "ckan_meta"
    version = "2"
    source_kind = "ckan"
    # CKAN servers and proxies do not consistently set a reliable media type.
    content_type = "*"

    def parse(self, snapshot: Snapshot, data: bytes, meta: Mapping[str, JsonValue] | None = None) -> ParseResult:
        del meta  # CKAN package data is self-contained.
        try:
            payload = json.loads(data)
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
            raise ParseError("CKAN 元数据不是有效 JSON") from exc
        if not isinstance(payload, dict):
            raise ParseError("CKAN 元数据顶层必须是对象")
        package = payload.get("result", payload)
        if not isinstance(package, dict):
            raise ParseError("CKAN 元数据缺少 package 对象")

        return self._parse_package(snapshot, package)

    def _parse_package(self, snapshot: Snapshot, package: Mapping[str, object], *, locator: str | None = None) -> ParseResult:
        """Parse one package; package_search callers provide its position in the saved page."""

        package_id = _text(package.get("id")) or _text(package.get("name"))
        if not package_id or not package_id.strip():
            raise ParseError("CKAN package 缺少 id/name")
        title = _text(package.get("title")) or _text(package.get("name")) or package_id
        organization = package.get("organization")
        organization = organization if isinstance(organization, dict) else {}
        publisher = _text(organization.get("title")) or _text(package.get("publisher"))
        modified_at = _timestamp(package.get("metadata_modified"))

        dataset_url = _text(package.get("url")) or _text(package.get("landingPage"))
        if dataset_url and dataset_url.strip():
            dataset_url = redact_url(dataset_url)
        else:
            dataset_url = None
        extras: dict[str, JsonValue] = {
            "frequency_of_update": package.get("frequency_of_update"),
            "spatial": package.get("spatial"),
            "local_government": package.get("local_government"),
            "organization": organization,
            "license_title": package.get("license_title"),
            "ckan_extras": package.get("extras", []),
        }
        # Retain the original timestamp text when it cannot be represented by the aware model field.
        if package.get("metadata_modified") is not None and modified_at is None:
            extras["metadata_modified_raw"] = package.get("metadata_modified")
        try:
            dataset = Dataset(
                dataset_key=f"{snapshot.source_id}:{package_id}",
                source_id=snapshot.source_id,
                title=title,
                kind=_text(package.get("type")) or "dataset",
                description=_text(package.get("notes")),
                publisher=publisher,
                url=dataset_url,
                license=_license(package),
                tags=_tags(package.get("tags")),
                modified_at=modified_at,
                extras=extras,
                snapshot_sha256=snapshot.sha256 if locator is not None else None,
                locator=locator,
            )
        except ValueError as exc:
            raise ParseError("CKAN package 字段无效") from exc

        raw_resources = package.get("resources", [])
        if not isinstance(raw_resources, list):
            raise ParseError("CKAN package resources 必须是数组")
        resources: list[Resource] = []
        warnings: list[str] = []
        for index, item in enumerate(raw_resources):
            if not isinstance(item, dict):
                warnings.append(f"resources[{index}] 不是对象，已跳过")
                continue
            url = _resource_url(item)
            resource_id = _text(item.get("id")) or _text(item.get("name"))
            if not resource_id or not resource_id.strip() or url is None:
                warnings.append(f"resources[{index}] 缺少 id/name 或 URL，已跳过")
                continue
            size = item.get("size")
            if isinstance(size, bool) or not isinstance(size, int) or size < 0:
                size = None
            resource_modified = _timestamp(item.get("last_modified"))
            try:
                resources.append(Resource(
                    resource_id=resource_id,
                    dataset_key=dataset.dataset_key,
                    source_id=snapshot.source_id,
                    url=url,
                    name=_text(item.get("name")),
                    format=_text(item.get("format")),
                    media_type=_text(item.get("mimetype")),
                    license=_license(item),
                    # CKAN's declared size is advisory metadata, never used as a fetched byte count.
                    size=size,
                    modified_at=resource_modified,
                ))
            except ValueError as exc:
                raise ParseError(f"CKAN resources[{index}] 字段无效") from exc
        return ParseResult(datasets=[dataset], resources=resources, warnings=warnings)
