import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from datumoj.core.contracts import EmbeddingError, ModelMismatch
from datumoj.index.embed import HashEmbedder, OllamaEmbedder
from datumoj.index.vector import LanceVectorIndex
from datumoj.testing.contract_suites import EmbedderSuite, VectorIndexSuite
from datumoj.testing.samples import make_chunk

SHA = "ab" * 32


class FakeOllama:
    """本地假 Ollama：按 script 依次给出每次请求的动作（"ok" / 状态码 / ("sleep", 秒)），用完后一律 ok。"""

    def __init__(self, dim=8, script=()):
        self.dim, self.script, self.requests = dim, list(script), []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                outer.requests.append((self.path, body))
                action = outer.script.pop(0) if outer.script else "ok"
                if isinstance(action, tuple):
                    time.sleep(action[1])
                    action = "ok"
                if action != "ok":
                    self.send_response(action)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                data = json.dumps({"embeddings": HashEmbedder(dim=outer.dim).embed(body["input"])}).encode()
                try:
                    self.send_response(200)
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                except OSError:  # 客户端已因超时断开
                    pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def ollama():
    made = []

    def make(**kw):
        s = FakeOllama(**kw)
        made.append(s)
        return s

    yield make
    for s in made:
        s.close()


class TestHashEmbedder(EmbedderSuite):
    @pytest.fixture
    def embedder(self):
        return HashEmbedder()


class TestOllamaEmbedderContract(EmbedderSuite):
    @pytest.fixture
    def embedder(self, ollama):
        e = OllamaEmbedder(model="bge-m3", dim=8, base_url=ollama(dim=8).url, backoff=0)
        yield e
        e.close()


class TestLanceVectorIndex(VectorIndexSuite):
    @pytest.fixture
    def embedder(self):
        return HashEmbedder()

    @pytest.fixture
    def other_embedder(self):
        return HashEmbedder(model="hash-v2")

    @pytest.fixture
    def make_index(self, tmp_path):
        return lambda embedder: LanceVectorIndex(tmp_path / "vec", embedder)


# ---------- OllamaEmbedder ----------


def test_ollama_batches_and_request_shape(ollama):
    s = ollama(dim=4)
    e = OllamaEmbedder(dim=4, base_url=s.url, batch_size=2)
    assert len(e.embed(["a", "b", "c", "d", "e"])) == 5
    assert [len(b["input"]) for _, b in s.requests] == [2, 2, 1]
    assert s.requests[0][0] == "/api/embed" and s.requests[0][1]["model"] == "bge-m3"
    e.close()


def test_ollama_retries_5xx_then_succeeds(ollama):
    s = ollama(dim=4, script=[503, 500])
    e = OllamaEmbedder(dim=4, base_url=s.url, retries=2, backoff=0)
    assert len(e.embed(["a"])) == 1 and len(s.requests) == 3
    e.close()


def test_ollama_gives_up_after_retries(ollama):
    s = ollama(dim=4, script=[500] * 5)
    e = OllamaEmbedder(dim=4, base_url=s.url, retries=2, backoff=0)
    with pytest.raises(EmbeddingError):
        e.embed(["a"])
    assert len(s.requests) == 3
    e.close()


def test_ollama_4xx_not_retried(ollama):
    s = ollama(dim=4, script=[404])
    e = OllamaEmbedder(dim=4, base_url=s.url, retries=3, backoff=0)
    with pytest.raises(EmbeddingError):
        e.embed(["a"])
    assert len(s.requests) == 1
    e.close()


def test_ollama_timeout_is_retried(ollama):
    s = ollama(dim=4, script=[("sleep", 0.6)])
    e = OllamaEmbedder(dim=4, base_url=s.url, timeout=0.15, retries=1, backoff=0)
    assert len(e.embed(["a"])) == 1
    assert len(s.requests) == 2  # 第一次超时，第二次成功
    e.close()


def test_ollama_timeout_fails_after_retries(ollama):
    s = ollama(dim=4, script=[("sleep", 0.6)] * 2)
    e = OllamaEmbedder(dim=4, base_url=s.url, timeout=0.15, retries=1, backoff=0)
    started = time.monotonic()
    with pytest.raises(EmbeddingError):
        e.embed(["a"])
    assert len(s.requests) == 2 and time.monotonic() - started < 0.55
    e.close()


def test_ollama_dim_mismatch_and_connection_refused(ollama):
    s = ollama(dim=3)
    e = OllamaEmbedder(dim=4, base_url=s.url, backoff=0)
    with pytest.raises(EmbeddingError, match="维度"):
        e.embed(["a"])
    e.close()
    s.close()
    e = OllamaEmbedder(dim=4, base_url=s.url, retries=1, backoff=0)
    with pytest.raises(EmbeddingError):
        e.embed(["a"])
    e.close()


# ---------- LanceVectorIndex ----------


class FlakyEmbedder(HashEmbedder):
    """第 fail_on 次 embed 调用抛错。"""

    def __init__(self, fail_on):
        super().__init__()
        self.calls, self.fail_on, self.texts = 0, fail_on, 0

    def embed(self, texts):
        self.calls += 1
        if self.calls == self.fail_on:
            raise EmbeddingError("boom")
        self.texts += len(texts)
        return super().embed(texts)


def test_resume_after_midway_failure(tmp_path):
    chunks = [make_chunk("jp-law:r", SHA, f"art={n}", f"第{n}条の本文。") for n in range(5)]
    flaky = FlakyEmbedder(fail_on=3)
    idx = LanceVectorIndex(tmp_path / "v", flaky, batch_size=2)
    with pytest.raises(EmbeddingError):
        idx.add(chunks)
    assert flaky.texts == 4  # 前两批已落盘
    again = FlakyEmbedder(fail_on=0)
    assert LanceVectorIndex(tmp_path / "v", again, batch_size=2).add(chunks) == 1
    assert again.texts == 1


def test_persists_and_reopens_without_reembedding(tmp_path):
    chunks = [make_chunk("jp-law:p", SHA, "art=1", "永続化の確認。")]
    assert LanceVectorIndex(tmp_path / "v", HashEmbedder()).add(chunks) == 1
    idx = LanceVectorIndex(tmp_path / "v", HashEmbedder())
    assert idx.add(chunks) == 0
    assert [h.chunk_id for h in idx.search("永続化の確認。").items] == [chunks[0].chunk_id]


def test_dim_mismatch(tmp_path):
    LanceVectorIndex(tmp_path / "v", HashEmbedder(dim=64))
    with pytest.raises(ModelMismatch):
        LanceVectorIndex(tmp_path / "v", HashEmbedder(dim=32))


def test_changed_chunk_is_replaced_and_quotes_are_literal(tmp_path):
    idx = LanceVectorIndex(tmp_path / "v", HashEmbedder())
    c = make_chunk("jp-law:q", SHA, "art=1", "旧い本文。")
    idx.add([c])
    idx.add([c.model_copy(update={"text": "O'Brien の新しい本文。"})])
    assert [h.chunk_id for h in idx.search("新しい本文", k=10).items] == [c.chunk_id]
    idx.delete("jp-law:q'x")  # 引号不能破坏过滤表达式
    assert idx.search("新しい本文").items
    assert idx.search("新しい本文", source_ids=[]).items == []
