"""WISTERIA-102：来源许可等级缺省、兼容校验及现有来源迁移。"""

from datetime import date
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from datumoj.rikolto.analysis.tools import INTERNAL_ONLY, _usage_notice
from datumoj.rikolto.core.contracts import RegistryError, citation_for_chunk
from datumoj.rikolto.core.models import LicenseInfo, Outflow
from datumoj.rikolto.core.registry import load_registry
from datumoj.rikolto.testing.samples import make_chunk, make_snapshot

ROOT = Path(__file__).resolve().parents[2]
SOURCE_DIR = ROOT / "sources"


def test_license_defaults_are_strictest_and_cloud_permission_is_strict_boolean():
    license_info = LicenseInfo()

    assert license_info.outflow is Outflow.no_outflow
    assert license_info.cloud_model_ok is False
    with pytest.raises(ValidationError):
        LicenseInfo(cloud_model_ok="false")


@pytest.mark.parametrize(
    "values",
    [
        {"usage_scope": "redistributable", "redistribution": "unverified", "outflow": "no_outflow"},
        {"usage_scope": "redistributable", "redistribution": "forbidden", "outflow": "no_outflow"},
        {"usage_scope": "internal_only", "redistribution": "allowed", "outflow": "raw_ok"},
        {"usage_scope": "redistributable", "redistribution": "allowed", "outflow": "derived_only"},
    ],
)
def test_license_rejects_all_legacy_and_outflow_conflict_forms(values):
    with pytest.raises(ValidationError) as exc_info:
        LicenseInfo(**values)

    message = str(exc_info.value)
    assert "usage_scope" in message or "redistribution" in message
    assert "outflow" in message or "redistribution" in message


@pytest.mark.parametrize("missing", ["name", "url", "verified_at", "notes"])
def test_registry_rejects_relaxed_outflow_without_evidence(tmp_path, missing):
    license_data = {
        "name": "Example license",
        "url": "https://example.go.jp/license",
        "attribution": None,
        "usage_scope": "internal_only",
        "redistribution": "unverified",
        "outflow": "derived_only",
        "cloud_model_ok": False,
        "verified_at": date(2026, 10, 20).isoformat(),
        "notes": "核对依据",
    }
    license_data[missing] = None
    source = {
        "id": "jp-example",
        "name": "Example source",
        "country": "jp",
        "kind": "example",
        "base_url": "https://example.go.jp/api/",
        "allowed_hosts": ["example.go.jp"],
        "rate_per_sec": 1,
        "max_total_bytes": 1000,
        "max_files": 10,
        "schedule": "daily",
        "license": license_data,
        "scope": {"endpoints": ["/api/"]},
        "needs_key": None,
        "retention_note": None,
    }
    (tmp_path / "jp-example.yaml").write_text(
        yaml.safe_dump(source, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )

    with pytest.raises(RegistryError, match=rf"license\.{missing}"):
        load_registry(tmp_path)


def test_all_existing_sources_keep_legacy_citation_and_usage_notice_behavior():
    registry = load_registry(SOURCE_DIR)
    legacy = [
        "jp-bodik", "jp-egov-data", "jp-egov-law", "jp-estat-files", "jp-gov-web", "jp-jma",
        "jp-stat-dashboard",
    ]
    # New sources (MAPLE 201+) may be added; only the sources that existed at 102 are pinned here.
    specs = [spec for spec in registry.all() if spec.id in legacy]

    assert [spec.id for spec in specs] == legacy
    for spec in specs:
        license_info = spec.license
        source_data = yaml.safe_load((SOURCE_DIR / f"{spec.id}.yaml").read_text(encoding="utf-8"))
        assert source_data["license"]["outflow"] == "no_outflow"
        assert source_data["license"]["cloud_model_ok"] is False
        assert license_info.outflow is Outflow.no_outflow
        assert license_info.cloud_model_ok is False

        snapshot = make_snapshot(spec.id, data=b"source")
        chunk = make_chunk(f"{spec.id}:example", snapshot.sha256, "section=1", "text")
        citation = citation_for_chunk(chunk, snapshot, license_info)
        legacy = source_data["license"]
        assert citation.license == legacy["name"]
        assert citation.attribution == legacy.get("attribution")
        assert citation.usage_scope.value == legacy["usage_scope"]
        assert _usage_notice(citation.usage_scope) == INTERNAL_ONLY
