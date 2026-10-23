"""Resolve evidence within a resource before choosing its latest successful observation."""

from urllib.parse import parse_qs, urlsplit

from datumoj.core.contracts import AmbiguousCitation, Manifest
from datumoj.core.models import Document, FetchOutcome, Snapshot, parse_context_fingerprint, redact_url


def select_snapshot(
    manifest: Manifest, sha256: str, source_id: str | None = None,
    *, resource_url: str | None = None, snapshot_id: str | None = None,
) -> Snapshot | None:
    """Exact supplied identity wins; a SHA spanning resources must never guess."""
    matches = [
        s for s in manifest.snapshots_by_sha256(sha256)
        if s.outcome is FetchOutcome.ok
        and (source_id is None or s.source_id == source_id)
        and (resource_url is None or s.resource_url == redact_url(resource_url))
        and (snapshot_id is None or s.snapshot_id == snapshot_id)
    ]
    if len({(s.source_id, s.resource_url) for s in matches}) > 1:
        raise AmbiguousCitation("SHA 对应多个资源，需 snapshot_id 或 resource_url")
    # Manifest orders by fetched_at and then persistent insertion sequence.
    return matches[-1] if matches else None


def document_snapshot(
    manifest: Manifest, sha256: str, source_id: str, doc_key: str,
    document: Document | None = None, *, resource_url: str | None = None,
) -> Snapshot | None:
    """Use explicit URL, document metadata, or the recorded parsing context."""
    if resource_url is None and document is not None:
        resource_url = document.metadata.get("resource_url") or document.metadata.get("url")
    if resource_url is None:
        writer = manifest.last_writer(doc_key, sha256)
        candidates = [
            s for s in manifest.snapshots_by_sha256(sha256)
            if s.source_id == source_id and s.outcome is FetchOutcome.ok
        ]
        if writer:
            candidates = [
                s for s in candidates
                if parse_context_fingerprint(s, manifest.snapshot_meta(s.snapshot_id)) == writer
            ]
            if not candidates:
                return None
        else:
            # Legacy parsers identify documents by URL, URL filename or an id in fetch meta.
            matched = []
            for snap in candidates:
                meta = manifest.snapshot_meta(snap.snapshot_id)
                identities = [snap.resource_url, snap.resource_url.rstrip("/").rsplit("/", 1)[-1]]
                identities.extend(parse_qs(urlsplit(snap.resource_url).query).get("statInfId", []))
                identities.extend(meta.get(key) for key in ("doc_id", "law_id", "stat_inf_id", "table_key"))
                if any(doc_key == f"{source_id}:{identity}" for identity in identities if identity):
                    matched.append(snap)
            if matched:
                candidates = matched
        urls = {s.resource_url for s in candidates}
        if len(urls) > 1:
            raise AmbiguousCitation("文档资源 URL 无法唯一确定，引用有歧义")
        if not candidates:
            return None
        resource_url = candidates[-1].resource_url
    return select_snapshot(manifest, sha256, source_id, resource_url=resource_url)
