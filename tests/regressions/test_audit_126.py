"""拆批或其他嵌入失败时，客户端错误消息不带入输入正文。全部使用离线 transport。"""

import json
import traceback

import httpx
import pytest

from datumoj.rikolto.core.contracts import EmbeddingError
from datumoj.rikolto.index.embed import OllamaEmbedder

PRIVATE = (
    "https://synthetic.invalid/audit-126/private",
    "/synthetic/private/audit-126",
    "synthetic-secret-key-126",
)


@pytest.mark.parametrize("n", [1, 4])
@pytest.mark.parametrize("failure", [
    "timeout", "connect", "5xx", "4xx", "json", "missing", "count", "dim", "nonfinite", "nonnumeric",
])
def test_embedding_failure_messages_do_not_include_input(failure, n):
    text = " ".join(PRIVATE)
    texts = [f"{text} item-{i}" for i in range(n)]
    batches = []

    def respond(request):
        batch = json.loads(request.content)["input"]
        batches.append(batch)
        # Transport diagnostics may themselves contain the entire input.
        if failure == "timeout":
            raise httpx.ReadTimeout(" ".join(batch))
        if failure == "connect":
            raise httpx.ConnectError(" ".join(batch))
        if failure == "5xx":
            return httpx.Response(503, text=text)
        if failure == "4xx":
            return httpx.Response(400, text="synthetic denied")
        if failure == "json":
            return httpx.Response(200, text=text)
        if failure == "missing":
            return httpx.Response(200, json={text: []})
        vectors = {
            "count": [],
            "dim": [[0.0] for _ in batch],
            "nonfinite": [[float("nan"), 1.0] for _ in batch],
            "nonnumeric": [[text, 1.0] for _ in batch],
        }[failure]
        # httpx's JSON encoder rejects NaN; emulate a server's nonstandard JSON.
        return httpx.Response(200, text=json.dumps({"embeddings": vectors}))

    with httpx.Client(base_url="http://synthetic.invalid", transport=httpx.MockTransport(respond)) as client:
        e = OllamaEmbedder(dim=2, client=client, retries=2, backoff=0)
        with pytest.raises(EmbeddingError) as exc:
            e.embed(texts)
    message = str(exc.value)
    assert all(value not in message for value in PRIVATE)
    rendered = "".join(traceback.format_exception(exc.value))
    assert all(value not in rendered for value in PRIVATE)
    assert len(batches) <= (2 * n - 1) * (e.retries + 1)
    if failure == "timeout":
        assert batches[-(e.retries + 1):] == [texts[:1]] * (e.retries + 1)
        if n > 1:
            assert batches[:2] == [texts, texts[:n // 2]]
        else:
            assert len(batches) == e.retries + 1
    elif failure in {"connect", "5xx"}:
        assert batches == [texts] * (e.retries + 1)
    else:
        assert batches == [texts]
    if failure == "4xx":
        assert "synthetic denied" in message
