"""Checked-in sources must declare retention without relying on model defaults."""

from pathlib import Path

import pytest
import yaml

from datumoj.rikolto.core.registry import load_registry

SOURCES = Path(__file__).resolve().parents[2] / "sources"


def _assert_registry_retention(directory: Path) -> None:
    registry = load_registry(directory)
    paths = sorted(directory.glob("*.yaml"))
    assert paths, "来源注册表不能为空"
    for path in paths:
        # Inspect the original mapping so model defaults cannot hide an omission.
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        scope = data["scope"]
        assert "raw_retention_days" in scope, f"{path.name}: 必须显式填写 raw_retention_days"
        days = scope["raw_retention_days"]
        assert type(days) is int and days > 0, f"{path.name}: raw_retention_days 必须是正整数"
        note = data.get("retention_note")
        assert isinstance(note, str) and note.strip(), f"{path.name}: retention_note 不能为空"
        assert "待统一确定" not in note and "unknown" not in note.casefold(), path.name
        assert registry.get(data["id"]) is not None


def test_all_sources_explicitly_declare_retention():
    _assert_registry_retention(SOURCES)


def test_sources_keep_agreed_retention_policy():
    for spec in load_registry(SOURCES).all():
        assert spec.scope["raw_retention_days"] == 2, spec.id
        assert spec.scope.get("raw_expire_latest_types", []) == (["entry"] if spec.id == "jp-jma" else [])


def test_missing_retention_in_temporary_yaml_fails_check(tmp_path):
    source = yaml.safe_load((SOURCES / "jp-egov-law.yaml").read_text(encoding="utf-8"))
    del source["scope"]["raw_retention_days"]
    (tmp_path / "jp-egov-law.yaml").write_text(
        yaml.safe_dump(source, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )

    # The existing loader permits omission; the checked-in registry policy must not.
    assert load_registry(tmp_path).get("jp-egov-law") is not None
    with pytest.raises(AssertionError, match=r"jp-egov-law\.yaml:.*raw_retention_days"):
        _assert_registry_retention(tmp_path)
