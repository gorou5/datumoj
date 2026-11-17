from datumoj.rikolto.index.vector import LanceVectorIndex, delete_vectors
from datumoj.rikolto.testing.fakes import FakeEmbedder
from datumoj.rikolto.testing.samples import make_chunk, sha


def test_delete_vectors_removes_only_listed_ids_and_tolerates_missing(tmp_path):
    index = LanceVectorIndex(tmp_path / "v", FakeEmbedder())
    digest = sha(b"x")
    index.add([make_chunk("jp-test:a", digest, f"row={i}", f"text {i}") for i in range(1, 5)])
    removed = delete_vectors(tmp_path / "v", ["jp-test:a#row=1", "jp-test:a#row=3", "jp-test:a#row=9"])
    assert removed == 2
    fresh = LanceVectorIndex(tmp_path / "v", FakeEmbedder())
    assert {i.chunk_id for i in fresh.search("text", k=10).items} == {"jp-test:a#row=2", "jp-test:a#row=4"}
    assert delete_vectors(tmp_path / "v", []) == 0
    assert delete_vectors(tmp_path / "absent", ["jp-test:a#row=2"]) == 0
