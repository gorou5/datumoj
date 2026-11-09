"""Parser-owned catalog and row fields, projected for keyword retrieval only."""

import hashlib
from collections import defaultdict
from collections.abc import Iterable

from datumoj.rikolto.core.contracts import ScoredChunk
from datumoj.rikolto.core.models import Chunk, Dataset, Document, Observation

_MARKER = "#projection:"


def is_projection(chunk: Chunk | ScoredChunk) -> bool:
    return chunk.chunk_id.startswith(f"{chunk.doc_key}{_MARKER}")


def projection_fingerprint(chunks: list[Chunk]) -> str:
    return hashlib.sha256("\n".join(chunk.model_dump_json() for chunk in chunks).encode()).hexdigest()


def project(datasets: Iterable[Dataset], observations: Iterable[Observation]) -> list[tuple[Document, list[Chunk]]]:
    groups: dict[tuple[str, str], dict[str, Chunk]] = defaultdict(dict)
    for dataset in datasets:
        if dataset.locator is None:
            continue
        chunk = Chunk(
            chunk_id=f"{dataset.dataset_key}{_MARKER}entry:{dataset.locator}",
            doc_key=dataset.dataset_key, locator=dataset.locator,
            snapshot_sha256=dataset.snapshot_sha256, lang="und",
            title_path=[dataset.title], published_at=dataset.published_at,
            text="\n".join([dataset.title, dataset.description or "", *dataset.tags]),
        )
        groups[(chunk.doc_key, chunk.snapshot_sha256)][chunk.chunk_id] = chunk
    for obs in observations:
        identity = obs.model_dump_json(include={"table_key", "obs_id", "locator", "release_id"})
        row_id = hashlib.sha256(identity.encode()).hexdigest()
        chunk = Chunk(
            chunk_id=f"{obs.dataset_key}{_MARKER}row:{row_id}",
            doc_key=obs.dataset_key, locator=obs.locator,
            snapshot_sha256=obs.snapshot_sha256, lang="und",
            title_path=[obs.survey, obs.indicator], text=obs.model_dump_json(),
        )
        groups[(chunk.doc_key, chunk.snapshot_sha256)][chunk.chunk_id] = chunk
    result = []
    for (key, sha), entries in groups.items():
        chunks = list(entries.values())
        doc = Document(
            doc_key=key, source_id=chunks[0].source_id, snapshot_sha256=sha,
            title=chunks[0].title_path[0], lang="und",
            metadata={"projection": "keyword", "projection_fingerprint": projection_fingerprint(chunks)},
        )
        result.append((doc, chunks))
    return result


def protects_current(current: list[Chunk], candidate: list[Chunk]) -> bool:
    """Different snapshots cannot share current. Preserve body, then row evidence."""
    if not current or not candidate or current[0].snapshot_sha256 == candidate[0].snapshot_sha256:
        return False
    if any(not is_projection(chunk) for chunk in candidate):
        return False
    return any(not is_projection(chunk) for chunk in current) or (
        any("#projection:row:" in chunk.chunk_id for chunk in current)
        and all("#projection:entry:" in chunk.chunk_id for chunk in candidate)
    )
