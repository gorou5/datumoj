from __future__ import annotations

import json
from pathlib import Path

import jsonschema
import pytest
import yaml
from typer.testing import CliRunner

from datumoj.rikolto import cli
from datumoj.rikolto.core.registry_export import export_registry

ROOT = Path(__file__).resolve().parents[2]


def _source(source_id: str = "jp-example", **overrides):
    source = {
        "id": source_id,
        "name": "Example source",
        "country": "jp",
        "kind": "ckan",
        "base_url": "https://example.go.jp/api",
        "allowed_hosts": ["example.go.jp"],
        "rate_per_sec": 0.5,
        "max_total_bytes": 1000,
        "max_files": 10,
        "license": {
            "name": "Example license",
            "url": "https://example.go.jp/license",
            "attribution": "出典：Example Agency (https://example.go.jp/)",
            "usage_scope": "internal_only",
            "redistribution": "unverified",
            "verified_at": None,
            "notes": None,
        },
        "scope": {"endpoints": ["/api"]},
        "needs_key": None,
    }
    source.update(overrides)
    return source


def _write_source(directory: Path, source: dict) -> None:
    (directory / f"{source['id']}.yaml").write_text(
        yaml.safe_dump(source, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )


@pytest.mark.parametrize(
    ("kind", "expected"),
    [
        ("ckan", "api"),
        ("egov_law", "api"),
        ("stat_dashboard", "api"),
        ("jma_feed", "api"),
        ("estat_files", "download"),
        ("gov_web", "web"),
        ("future_kind", "other"),
    ],
)
def test_kind_maps_to_schema_access_method(tmp_path, kind, expected):
    sources = tmp_path / "sources"
    sources.mkdir()
    _write_source(sources, _source(kind=kind))

    result = json.loads(export_registry(sources, tmp_path / "catalog")[0].read_text(encoding="utf-8"))

    assert result["access_method"] == expected


@pytest.mark.parametrize(
    ("redistribution", "expected"),
    [("allowed", True), ("forbidden", False), ("unverified", "unknown")],
)
def test_license_redistribution_mapping(tmp_path, redistribution, expected):
    sources = tmp_path / "sources"
    sources.mkdir()
    license_data = _source()["license"] | {"redistribution": redistribution}
    if redistribution == "allowed":
        license_data["verified_at"] = "2026-10-22"
    _write_source(sources, _source(license=license_data))

    result = json.loads(export_registry(sources, tmp_path / "catalog")[0].read_text(encoding="utf-8"))

    actual = result["license"]["allows_redistribution"]
    if isinstance(expected, bool):
        assert actual is expected
    else:
        assert actual == expected
    assert result["last_verified"] == ("2026-10-22" if redistribution == "allowed" else "unknown")
    assert result["license"]["url"] == "https://example.go.jp/license"


def test_export_maps_fields_and_marks_missing_values_unknown(tmp_path):
    sources = tmp_path / "sources"
    sources.mkdir()
    source = _source(
        kind="ckan",
        rate_per_sec=1,
        needs_key="env:EXAMPLE_KEY",
        license={
            "name": None,
            "url": None,
            "attribution": "出典：総務省統計局「統計ダッシュボード」 (https://example.go.jp/)",
            "usage_scope": "internal_only",
            "redistribution": "unverified",
            "verified_at": None,
            "notes": None,
        },
    )
    _write_source(sources, source)

    result = json.loads(export_registry(sources, tmp_path / "catalog")[0].read_text(encoding="utf-8"))

    assert result["id"] == "jp-example"
    assert result["name"] == "Example source"
    assert result["country_region"] == "JP"
    assert result["publisher"] == "総務省統計局"
    assert result["official_url"] == "https://example.go.jp/api"
    assert result["rate_limit"] == "1 req/s"
    assert result["auth_required"] is True
    assert result["license"] == {
        "name": "unknown",
        "allows_redistribution": "unknown",
        "requires_attribution": True,
    }
    assert result["last_verified"] == "unknown"
    assert result["status"] == "experimental"
    assert set(result) <= set(json.loads((ROOT / "schemas/registry/data-source.schema.json").read_text()).get("properties", {}))


def test_missing_publisher_license_url_and_key_are_unknown_or_omitted(tmp_path):
    sources = tmp_path / "sources"
    sources.mkdir()
    license_data = _source()["license"] | {"url": None, "attribution": None}
    _write_source(sources, _source(license=license_data))

    result = json.loads(export_registry(sources, tmp_path / "catalog")[0].read_text(encoding="utf-8"))

    assert result["publisher"] == "unknown"
    assert result["auth_required"] is False
    assert result["license"]["requires_attribution"] == "unknown"
    assert "url" not in result["license"]


def test_export_is_deterministic_sorted_utf8_and_newline_terminated(tmp_path):
    sources = tmp_path / "sources"
    sources.mkdir()
    _write_source(sources, _source("jp-zeta"))
    _write_source(sources, _source("jp-alpha"))
    output = tmp_path / "catalog"

    paths = export_registry(sources, output)
    first = {path.name: path.read_bytes() for path in paths}
    repeated = export_registry(sources, output)

    assert [path.name for path in paths] == ["jp-alpha.json", "jp-zeta.json"]
    assert [path.name for path in repeated] == ["jp-alpha.json", "jp-zeta.json"]
    assert first == {path.name: path.read_bytes() for path in repeated}
    assert all(data.endswith(b"\n") for data in first.values())
    assert b"Example source" in first["jp-alpha.json"]
    assert b"\\u" not in first["jp-alpha.json"]
    parsed = json.loads(first["jp-alpha.json"])
    assert list(parsed) == sorted(parsed)
    assert list(parsed["license"]) == sorted(parsed["license"])
    assert parsed["id"] == "jp-alpha"


def test_checked_in_catalog_matches_sources_and_validates_against_schema(tmp_path):
    output = tmp_path / "catalog"
    export_registry(ROOT / "sources", output)
    checked_in = ROOT / "catalog"

    assert {path.name for path in output.glob("*.json")} == {path.name for path in checked_in.glob("*.json")}
    for generated in sorted(output.glob("*.json")):
        committed = checked_in / generated.name
        assert generated.read_bytes() == committed.read_bytes()

    schema = json.loads((ROOT / "schemas/registry/data-source.schema.json").read_text(encoding="utf-8"))
    validator = jsonschema.Draft202012Validator(schema, format_checker=jsonschema.FormatChecker())
    for generated in sorted(output.glob("*.json")):
        validator.validate(json.loads(generated.read_text(encoding="utf-8")))


def test_registry_export_cli_writes_requested_directory(tmp_path):
    output = tmp_path / "catalog"

    result = CliRunner().invoke(cli.app, ["registry", "export", "--out", str(output)])

    assert result.exit_code == 0, result.output
    expected = len(list((ROOT / "sources").glob("*.yaml")))
    assert f"已导出 {expected} 个来源描述" in result.output
    assert len(list(output.glob("*.json"))) == expected
