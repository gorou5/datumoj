"""Source registry schema and YAML loading (DATUMOJ-IVY-104)."""

from pathlib import Path

import pytest
import yaml

from datumoj.rikolto.core.contracts import Registry, RegistryError
from datumoj.rikolto.core.models import Redistribution, UsageScope
from datumoj.rikolto.core.registry import RegistryImpl, load_registry
from datumoj.rikolto.testing.contract_suites import RegistrySuite


class TestRegistryConformance(RegistrySuite):
    @pytest.fixture
    def make_registry(self):
        return RegistryImpl


def _source(**overrides):
    source = {
        "id": "jp-example",
        "name": "Example source",
        "country": "jp",
        "kind": "example",
        "base_url": "https://example.go.jp/api/",
        "allowed_hosts": ["example.go.jp"],
        "rate_per_sec": 1,
        "max_total_bytes": 1000000,
        "max_files": 100,
        "schedule": "daily",
        "license": {
            "name": None,
            "url": None,
            "attribution": None,
            "usage_scope": "internal_only",
            "redistribution": "unverified",
            "verified_at": None,
            "notes": "许可尚未核对",
        },
        "scope": {"endpoints": ["/api/"]},
        "needs_key": None,
        "retention_note": None,
    }
    source.update(overrides)
    return source


def _write(directory, filename, source):
    (directory / filename).write_text(yaml.safe_dump(source, allow_unicode=True, sort_keys=False), encoding="utf-8")


def test_loads_valid_source_and_uses_safe_license_defaults(tmp_path):
    source = _source()
    del source["license"]
    _write(tmp_path, "jp-example.yaml", source)

    registry = load_registry(tmp_path)

    assert isinstance(registry, Registry)
    spec = registry.get("jp-example")
    assert spec is not None
    assert spec.license.usage_scope is UsageScope.internal_only
    assert spec.license.redistribution is Redistribution.unverified
    assert registry.all() == [spec]


def test_checked_in_egov_source_loads():
    source_dir = Path(__file__).resolve().parents[2] / "sources"

    spec = load_registry(source_dir).get("jp-egov-law")

    assert spec is not None
    assert spec.license.usage_scope is UsageScope.internal_only
    assert spec.license.redistribution is Redistribution.unverified


def test_rejects_id_that_does_not_match_filename(tmp_path):
    _write(tmp_path, "jp-other.yaml", _source())

    with pytest.raises(RegistryError, match=r"jp-other\.yaml: id:.*文件名"):
        load_registry(tmp_path)


def test_rejects_duplicate_ids(tmp_path):
    _write(tmp_path, "jp-example.yaml", _source())
    _write(tmp_path, "jp-other.yaml", _source())

    with pytest.raises(RegistryError, match=r"jp-other\.yaml: id:.*重复"):
        load_registry(tmp_path)


def test_missing_field_error_names_file_and_field(tmp_path):
    source = _source()
    del source["max_files"]
    _write(tmp_path, "jp-example.yaml", source)

    with pytest.raises(RegistryError, match=r"jp-example\.yaml: max_files:"):
        load_registry(tmp_path)


def test_rejects_plaintext_key_without_echoing_it(tmp_path):
    secret = "private-token-value"
    _write(tmp_path, "jp-example.yaml", _source(api_token=secret))

    with pytest.raises(RegistryError, match=r"jp-example\.yaml: api_token:") as error:
        load_registry(tmp_path)
    assert secret not in str(error.value)


def test_rejects_empty_scope(tmp_path):
    _write(tmp_path, "jp-example.yaml", _source(scope={}))

    with pytest.raises(RegistryError, match=r"jp-example\.yaml: scope:"):
        load_registry(tmp_path)


def test_rejects_redistribution_without_license_verification(tmp_path):
    license_info = {
        "name": "Example license",
        "url": "https://example.go.jp/license",
        "attribution": "Source: Example",
        "usage_scope": "redistributable",
        "redistribution": "allowed",
        "verified_at": None,
        "notes": None,
    }
    _write(tmp_path, "jp-example.yaml", _source(license=license_info))

    with pytest.raises(RegistryError, match=r"jp-example\.yaml: license\.verified_at:"):
        load_registry(tmp_path)
