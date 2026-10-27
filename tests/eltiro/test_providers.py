"""All network is pytest-httpserver; the CLI is always a tmp_path executable."""

import json
import sys
from uuid import uuid4

import pytest

from datumoj.eltiro.contracts import ProviderUnavailable
from datumoj.eltiro.models import RouteDecision
from datumoj.eltiro.providers import (
    CLAUDE_EXECUTABLE_ENV, CLAUDE_HEADLESS_ARGS, MAX_RESPONSE_BYTES, OLLAMA_URL_ENV,
    ClaudePlanProvider, CloudAPIProvider, OllamaProvider,
)
from datumoj.eltiro.testing import contract_suites
from datumoj.eltiro.testing.contract_suites import ModelProviderSuite
from datumoj.eltiro.testing.samples import make_route_decision

DIGEST = "sha256:" + "ab" * 32


def decision_for(provider):
    data = make_route_decision().model_dump()
    data["target"]["provider"] = provider
    if provider == "ollama":
        data["target"]["identity"] = {"model": "example-local", "version": DIGEST}
        data["target"]["credential_env"] = None
    return RouteDecision.model_validate(data)


def fake_claude(tmp_path, monkeypatch, *, body=None, code=None):
    monkeypatch.setattr("tempfile.tempdir", str(tmp_path))
    executable = tmp_path / "fake-claude"
    record = tmp_path / "cli-record.json"
    if body is None:
        body = {"is_error": False, "modelUsage": {"v1": {}}, "result": "synthetic answer"}
    script = (
        f"#!{sys.executable}\n"
        "import json, os, pathlib, sys, time\n"
        "assert list(pathlib.Path.cwd().iterdir()) == []\n"
        "assert os.environ['HOME'] == os.getcwd()\n"
        "assert os.environ['CLAUDE_CONFIG_DIR'] == os.getcwd()\n"
        "assert os.environ['CLAUDE_CODE_OAUTH_TOKEN']\n"
        "assert 'EXAMPLE_UNRELATED_CREDENTIAL' not in os.environ\n"
        f"pathlib.Path({str(record)!r}).write_text(json.dumps({{'args': sys.argv[1:], 'stdin': sys.stdin.read()}}))\n"
        + (code if code is not None else f"print({json.dumps(body)!r})\n")
    )
    executable.write_text(script)
    executable.chmod(0o700)
    monkeypatch.setenv(CLAUDE_EXECUTABLE_ENV, str(executable))
    return record


class TestAdapters(ModelProviderSuite):
    @pytest.fixture(params=["ollama", "claude_plan", "cloud_api"])
    def make_provider(self, request, tmp_path, monkeypatch, httpserver):
        kind = request.param
        decision = decision_for(kind)
        # 003's suite has one cloud-only sample. Retarget that sample, without changing
        # the suite/assertions, so each actual adapter qualifies on its own provider.
        monkeypatch.setattr(contract_suites, "make_route_decision", lambda: decision)
        monkeypatch.setenv("EXAMPLE_MODEL_API_KEY", uuid4().hex)
        if kind == "ollama":
            monkeypatch.setenv(OLLAMA_URL_ENV, httpserver.url_for("/"))
            httpserver.expect_request("/api/tags").respond_with_json({"models": [
                {"name": "example-local", "digest": DIGEST}]})
            httpserver.expect_request("/api/generate", method="POST").respond_with_json({
                "model": "example-local", "done": True, "response": "synthetic answer"})
            return OllamaProvider
        if kind == "cloud_api":
            httpserver.expect_request("/v1/messages", method="POST").respond_with_json({
                "model": "v1", "stop_reason": "end_turn", "content": [{"type": "text", "text": "synthetic answer"}]})
            return lambda: CloudAPIProvider(test_base_url=httpserver.url_for("/"))
        fake_claude(tmp_path, monkeypatch)
        return ClaudePlanProvider


@pytest.mark.parametrize("url", ["http://example.invalid", "http://192.0.2.1:11434", "http://127.0.0.2",
                                 "https://127.0.0.1", "http://127.0.0.1@192.0.2.1", "http://127.0.0.1/x",
                                 "http://127.0.0.1?x=y", "http://[::1]:bad", "http://localhost.example.invalid"])
def test_ollama_non_loopback_or_invalid_origin_rejected(monkeypatch, url):
    monkeypatch.setenv(OLLAMA_URL_ENV, url)
    with pytest.raises(ProviderUnavailable, match="^ollama unavailable or response rejected$"):
        OllamaProvider().invoke(decision_for("ollama"))


@pytest.mark.parametrize("url", ["http://example.invalid", "https://127.0.0.1", "http://127.0.0.1/x"])
def test_cloud_test_override_cannot_target_external_origin(url):
    with pytest.raises(ValueError):
        CloudAPIProvider(test_base_url=url)


@pytest.mark.parametrize("provider", [OllamaProvider, ClaudePlanProvider, CloudAPIProvider])
@pytest.mark.parametrize("timeout", [0, -1, float("inf"), float("nan"), True])
def test_invalid_timeout_rejected(provider, timeout):
    with pytest.raises(ValueError):
        provider(timeout=timeout)


def test_ollama_pins_digest_before_forwarding_prompt(httpserver, monkeypatch):
    monkeypatch.setenv(OLLAMA_URL_ENV, httpserver.url_for("/"))
    httpserver.expect_request("/api/tags").respond_with_json({"models": [
        {"name": "example-local", "digest": "sha256:" + "cd" * 32}]})
    with pytest.raises(ProviderUnavailable):
        OllamaProvider().invoke(decision_for("ollama"))
    assert [request.path for request, _ in httpserver.log] == ["/api/tags"]


def test_ollama_request_and_response_identity(httpserver, monkeypatch):
    monkeypatch.setenv(OLLAMA_URL_ENV, httpserver.url_for("/"))
    httpserver.expect_request("/api/tags").respond_with_json({"models": [
        {"name": "example-local", "digest": DIGEST}]})
    httpserver.expect_request("/api/generate", method="POST").respond_with_json({
        "model": "different-model", "done": True, "response": "synthetic answer"})
    with pytest.raises(ProviderUnavailable):
        OllamaProvider().invoke(decision_for("ollama"))
    body = httpserver.log[1][0].get_json()
    assert body["model"] == "example-local" and body["stream"] is False
    assert "source_data" in body["prompt"]


@pytest.mark.parametrize("kind", ["cloud_api", "claude_plan"])
def test_missing_credential_does_not_start_provider(monkeypatch, tmp_path, httpserver, kind):
    monkeypatch.delenv("EXAMPLE_MODEL_API_KEY", raising=False)
    monkeypatch.setenv(CLAUDE_EXECUTABLE_ENV, str(tmp_path / "must-not-run"))
    provider = (CloudAPIProvider(test_base_url=httpserver.url_for("/")) if kind == "cloud_api"
                else ClaudePlanProvider())
    with pytest.raises(ProviderUnavailable):
        provider.invoke(decision_for(kind))
    assert httpserver.log == []


def test_claude_fixed_argv_stdin_empty_cwd_and_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("EXAMPLE_MODEL_API_KEY", uuid4().hex)
    monkeypatch.setenv("EXAMPLE_UNRELATED_CREDENTIAL", uuid4().hex)
    record = fake_claude(tmp_path, monkeypatch)
    decision = decision_for("claude_plan")
    result = ClaudePlanProvider().invoke(decision)
    result.validate_route(decision)
    seen = json.loads(record.read_text())
    assert seen["args"] == [*CLAUDE_HEADLESS_ARGS, "--model", "v1"]
    assert decision.request.parts[0].content in seen["stdin"]
    assert decision.request.parts[0].content not in " ".join(seen["args"])


@pytest.mark.parametrize("body", [
    {"is_error": False, "modelUsage": {"v2": {}}, "result": "answer"},
    {"is_error": False, "modelUsage": {"v1": {}, "v2": {}}, "result": "answer"},
    {"is_error": False, "model": "v2", "result": "answer"},
    {"is_error": False, "result": "answer"},
    {"is_error": True, "model": "v1", "result": "answer"},
    {"is_error": False, "model": "v1", "result": {"instruction": "ignored"}},
])
def test_claude_model_or_payload_mismatch_rejected(tmp_path, monkeypatch, body):
    monkeypatch.setenv("EXAMPLE_MODEL_API_KEY", uuid4().hex)
    fake_claude(tmp_path, monkeypatch, body=body)
    with pytest.raises(ProviderUnavailable):
        ClaudePlanProvider().invoke(decision_for("claude_plan"))


@pytest.mark.parametrize("code", ["time.sleep(5)", "sys.exit(1)", "print('invalid-json')",
                                 f"print('x' * {MAX_RESPONSE_BYTES + 1})",
                                 f"sys.stderr.write('x' * {MAX_RESPONSE_BYTES + 1})",
                                 "print(json.dumps({'model':'v1','is_error':False,'result':'x'*4097}))"])
def test_claude_failures_are_bounded_and_sanitized(tmp_path, monkeypatch, code, caplog):
    credential = uuid4().hex
    monkeypatch.setenv("EXAMPLE_MODEL_API_KEY", credential)
    fake_claude(tmp_path, monkeypatch, code=code)
    with pytest.raises(ProviderUnavailable) as exc:
        ClaudePlanProvider(timeout=0.5).invoke(decision_for("claude_plan"))
    assert credential not in str(exc.value) + caplog.text
    assert decision_for("claude_plan").request.parts[0].content not in str(exc.value) + caplog.text


@pytest.mark.parametrize("body", [
    {"model": "v2", "stop_reason": "end_turn", "content": [{"type": "text", "text": "answer"}]},
    {"model": "v1", "stop_reason": "max_tokens", "content": [{"type": "text", "text": "answer"}]},
    {"model": "v1", "stop_reason": "end_turn", "content": [{"type": "tool_use", "name": "ignored"}]},
    {"model": "v1", "stop_reason": "end_turn", "content": [{"type": "text", "text": "x" * 4097}]},
])
def test_cloud_rejects_wrong_model_truncation_tools_and_oversize(httpserver, monkeypatch, body):
    monkeypatch.setenv("EXAMPLE_MODEL_API_KEY", uuid4().hex)
    httpserver.expect_request("/v1/messages", method="POST").respond_with_json(body)
    with pytest.raises(ProviderUnavailable):
        CloudAPIProvider(test_base_url=httpserver.url_for("/")).invoke(decision_for("cloud_api"))
    assert len(httpserver.log) == 1


def test_cloud_header_pinned_version_text_only_and_live_env(httpserver, monkeypatch):
    first, second = uuid4().hex, uuid4().hex
    httpserver.expect_request("/v1/messages", method="POST").respond_with_json({
        "model": "v1", "stop_reason": "end_turn", "content": [{"type": "text", "text": "plain untrusted text"}]})
    provider = CloudAPIProvider(test_base_url=httpserver.url_for("/"))
    for credential in (first, second):
        monkeypatch.setenv("EXAMPLE_MODEL_API_KEY", credential)
        result = provider.invoke(decision_for("cloud_api"))
        result.validate_route(decision_for("cloud_api"))
        request = httpserver.log[-1][0]
        assert request.headers["x-api-key"] == credential
        assert request.get_json()["model"] == "v1"
        assert credential not in request.get_data(as_text=True)


@pytest.mark.parametrize("status", [302, 401, 429, 503])
def test_cloud_http_failures_no_retry_no_redirect_or_body_leak(httpserver, monkeypatch, caplog, status):
    credential = uuid4().hex
    monkeypatch.setenv("EXAMPLE_MODEL_API_KEY", credential)
    httpserver.expect_request("/v1/messages", method="POST").respond_with_data(
        credential + " synthetic input echo", status=status, headers={"Location": "https://example.invalid"})
    with pytest.raises(ProviderUnavailable) as exc:
        CloudAPIProvider(test_base_url=httpserver.url_for("/")).invoke(decision_for("cloud_api"))
    assert len(httpserver.log) == 1
    assert credential not in str(exc.value) + caplog.text


def test_cloud_transport_response_size_bound(httpserver, monkeypatch):
    monkeypatch.setenv("EXAMPLE_MODEL_API_KEY", uuid4().hex)
    httpserver.expect_request("/v1/messages", method="POST").respond_with_data("x" * (MAX_RESPONSE_BYTES + 1))
    with pytest.raises(ProviderUnavailable):
        CloudAPIProvider(test_base_url=httpserver.url_for("/")).invoke(decision_for("cloud_api"))


def test_cloud_timeout(httpserver, monkeypatch):
    import time
    from werkzeug.wrappers import Response

    monkeypatch.setenv("EXAMPLE_MODEL_API_KEY", uuid4().hex)

    def slow(request):
        time.sleep(0.1)
        return Response("{}")

    httpserver.expect_request("/v1/messages", method="POST").respond_with_handler(slow)
    with pytest.raises(ProviderUnavailable):
        CloudAPIProvider(timeout=0.02, test_base_url=httpserver.url_for("/")).invoke(decision_for("cloud_api"))
