"""Only the law source opts out of embeddings."""

from pathlib import Path

import yaml


def test_only_egov_law_disables_embeddings():
    sources = Path(__file__).resolve().parents[2] / "sources"
    disabled = []
    for path in sources.glob("*.yaml"):
        spec = yaml.safe_load(path.read_text(encoding="utf-8"))
        if spec.get("scope", {}).get("embed") is False:
            disabled.append(spec["id"])
    assert disabled == ["jp-egov-law"]
