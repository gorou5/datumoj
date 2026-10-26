"""Export the validated source registry to the interoperability catalog."""

from __future__ import annotations

import json
import re
from pathlib import Path

from datumoj.rikolto.core.contracts import SourceSpec
from datumoj.rikolto.core.models import Redistribution
from datumoj.rikolto.core.registry import load_registry


_ACCESS_METHODS = {
    "ckan": "api",
    "egov_law": "api",
    "stat_dashboard": "api",
    "jma_feed": "api",
    "estat_files": "download",
    "gov_web": "web",
}


def _publisher(attribution: str | None) -> str:
    if not attribution:
        return "unknown"
    value = re.sub(r"^出典[：:]\s*", "", attribution.strip())
    value = re.sub(r"\s*[（(].*$", "", value)
    value = re.sub(r"「[^」]*」.*$", "", value)
    return value.strip() or "unknown"


def _description(spec: SourceSpec) -> dict[str, object]:
    license_data: dict[str, object] = {
        "name": spec.license.name or "unknown",
        "allows_redistribution": {
            Redistribution.allowed: True,
            Redistribution.forbidden: False,
            Redistribution.unverified: "unknown",
        }[spec.license.redistribution],
        "requires_attribution": True if spec.license.attribution else "unknown",
    }
    if spec.license.url:
        license_data["url"] = spec.license.url

    rate = int(spec.rate_per_sec) if spec.rate_per_sec.is_integer() else spec.rate_per_sec
    return {
        "id": spec.id,
        "name": spec.name,
        "country_region": spec.country.upper(),
        "publisher": _publisher(spec.license.attribution),
        "official_url": spec.base_url,
        "access_method": _ACCESS_METHODS.get(spec.kind, "other"),
        "rate_limit": f"{rate} req/s",
        "auth_required": spec.needs_key is not None,
        "license": license_data,
        "last_verified": spec.license.verified_at.isoformat() if spec.license.verified_at else "unknown",
        "status": "experimental",
    }


def export_registry(sources_dir: str | Path, out_dir: str | Path) -> list[Path]:
    """Write one deterministic JSON description for each validated source YAML."""
    registry = load_registry(sources_dir)
    output = Path(out_dir)
    output.mkdir(parents=True, exist_ok=True)

    paths: list[Path] = []
    for spec in registry.all():
        path = output / f"{spec.id}.json"
        document = json.dumps(_description(spec), ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        path.write_text(document, encoding="utf-8", newline="\n")
        paths.append(path)
    return paths


__all__ = ["export_registry"]
