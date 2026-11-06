"""Only the law and JMA sources opt out of embeddings."""

from pathlib import Path

import yaml


def test_only_law_and_jma_disable_embeddings():
    sources = Path(__file__).resolve().parents[2] / "sources"
    disabled = []
    for path in sources.glob("*.yaml"):
        spec = yaml.safe_load(path.read_text(encoding="utf-8"))
        if spec.get("scope", {}).get("embed") is False:
            disabled.append(spec["id"])
    # JMA bulletins are one oversized chunk each and time out on CPU embedding.
    assert sorted(disabled) == ["jp-egov-law", "jp-jma"]
