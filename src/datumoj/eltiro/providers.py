"""Text-only, bounded adapters. No retries, fallback, source execution or logging."""

from __future__ import annotations

import json
import math
import os
import re
import selectors
import signal
import subprocess
import tempfile
import time
from urllib.parse import urlsplit

import httpx

from datumoj.eltiro.contracts import ProviderUnavailable
from datumoj.eltiro.models import ModelResult, ModelUse, RouteDecision
from datumoj.eltiro.prompts import build_prompt

ANTHROPIC_MESSAGES_URL = "https://api.anthropic.com/v1/messages"
OLLAMA_URL_ENV = "ELTIRO_OLLAMA_URL"
CLAUDE_EXECUTABLE_ENV = "ELTIRO_CLAUDE_EXECUTABLE"
MAX_RESPONSE_BYTES = 256 * 1024
MAX_OUTPUT_BYTES = 16 * 1024
# The only dynamic argument is the pinned version after --model. Input is stdin only.
CLAUDE_HEADLESS_ARGS = (
    "--print", "--output-format", "json", "--tools", "", "--strict-mcp-config",
    "--mcp-config", '{"mcpServers":{}}', "--setting-sources", "",
    "--disable-slash-commands", "--no-session-persistence",
)


def _timeout(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
        raise ValueError("timeout must be finite and positive")
    return value


def _loopback_url(value):
    parsed = urlsplit(value)
    if (parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "::1", "localhost"}
            or parsed.username is not None or parsed.password is not None
            or parsed.path not in ("", "/") or parsed.query or parsed.fragment):
        raise ValueError("only a loopback HTTP origin is allowed")
    _ = parsed.port  # Reject malformed ports before connecting.
    return value.rstrip("/")


def _checked(decision, provider):
    decision = RouteDecision.model_validate(decision.model_dump())
    if decision.target.provider != provider or decision.target.identity is None:
        raise ValueError("adapter does not match the pinned provider")
    # Routers must split sources before prompt construction; adapters fail closed too.
    if len(decision.request.parts) != 1:
        raise ValueError("adapter requires a single-source decision")
    return decision


def _credential(decision):
    name = decision.target.credential_env
    credential = os.environ.get(name, "") if name else ""
    if not credential or not credential.strip():
        raise ValueError("credential unavailable")
    return credential


def _result(decision, text, credential=None):
    if not isinstance(text, str) or len(text.encode("utf-8")) > MAX_OUTPUT_BYTES:
        raise ValueError("invalid model text")
    if credential and credential in text:
        raise ValueError("credential echoed in model text")
    identity = decision.target.identity
    result = ModelResult(fields={"answer": {"value": text, "model_use": ModelUse(
        provider=decision.target.provider, model=identity.model, version=identity.version, tier=decision.tier,
    )}})
    result.validate_route(decision)
    return result


def _http_json(client, method, url, **kwargs):
    deadline = time.monotonic() + client.timeout.read
    with client.stream(method, url, **kwargs) as response:
        response.raise_for_status()
        chunks, size = [], 0
        for chunk in response.iter_bytes(chunk_size=8192):
            size += len(chunk)
            if size > MAX_RESPONSE_BYTES or time.monotonic() > deadline:
                raise ValueError("provider response limit exceeded")
            chunks.append(chunk)
    body = json.loads(b"".join(chunks))
    if not isinstance(body, dict):
        raise ValueError("invalid provider response")
    return body


def _subprocess_json(command, prompt, directory, environment, timeout):
    """Multiplex all pipes: bound memory and avoid blocked stdin/stderr deadlocks."""
    process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, cwd=directory, env=environment, start_new_session=True)
    deadline, output, size = time.monotonic() + timeout, bytearray(), 0
    pending = memoryview(prompt.encode("utf-8"))
    try:
        with selectors.DefaultSelector() as selector:
            for stream, event in ((process.stdin, selectors.EVENT_WRITE),
                                  (process.stdout, selectors.EVENT_READ), (process.stderr, selectors.EVENT_READ)):
                os.set_blocking(stream.fileno(), False)
                selector.register(stream, event)
            while selector.get_map():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("provider timed out")
                for key, event in selector.select(remaining):
                    stream = key.fileobj
                    if event == selectors.EVENT_WRITE:
                        written = os.write(stream.fileno(), pending[:8192])
                        pending = pending[written:]
                        if not pending:
                            selector.unregister(stream)
                            stream.close()
                    else:
                        chunk = os.read(stream.fileno(), 8192)
                        if not chunk:
                            selector.unregister(stream)
                        else:
                            size += len(chunk)
                            if size > MAX_RESPONSE_BYTES:
                                raise ValueError("provider response limit exceeded")
                            if stream is process.stdout:
                                output.extend(chunk)
        if process.wait(timeout=max(0.001, deadline - time.monotonic())) != 0:
            raise ValueError("provider process failed")
        body = json.loads(output)
        if not isinstance(body, dict):
            raise ValueError("invalid provider response")
        return body
    finally:
        # Kill the process group even when the parent exited but left children behind.
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait()
        for stream in (process.stdin, process.stdout, process.stderr):
            stream.close()


class OllamaProvider:
    """ELTIRO_OLLAMA_URL (loopback only); identity.version is a sha256 model digest."""

    def __init__(self, *, timeout: float = 30):
        self.timeout = _timeout(timeout)

    def invoke(self, decision):
        try:
            decision = _checked(decision, "ollama")
            identity = decision.target.identity
            if not re.fullmatch(r"sha256:[0-9a-f]{64}", identity.version):
                raise ValueError("a model digest is required")
            base = _loopback_url(os.environ.get(OLLAMA_URL_ENV, "http://127.0.0.1:11434"))
            with httpx.Client(timeout=self.timeout, trust_env=False, follow_redirects=False) as client:
                tags = _http_json(client, "GET", base + "/api/tags")
                matches = [item for item in tags["models"] if item.get("name") == identity.model]
                if len(matches) != 1 or matches[0].get("digest") != identity.version:
                    raise ValueError("model digest mismatch")
                body = _http_json(client, "POST", base + "/api/generate", json={
                    "model": identity.model, "prompt": build_prompt(decision.request), "stream": False,
                })
            if body.get("model") != identity.model or body.get("done") is not True:
                raise ValueError("model response mismatch")
            return _result(decision, body["response"])
        except Exception:
            raise ProviderUnavailable("ollama unavailable or response rejected") from None


class ClaudePlanProvider:
    """Environment-only OAuth credential, isolated CLI home/cwd, no tool permissions.

    identity.version is the exact CLI/API model id, not a moving alias. H0 approval is
    required before enabling this provider in a real host configuration.
    """

    def __init__(self, *, timeout: float = 60):
        self.timeout = _timeout(timeout)

    def invoke(self, decision):
        try:
            decision = _checked(decision, "claude_plan")
            credential = _credential(decision)
            version = decision.target.identity.version
            executable = os.environ.get(CLAUDE_EXECUTABLE_ENV, "claude")
            with tempfile.TemporaryDirectory(prefix="eltiro-model-") as directory:
                directory = os.path.realpath(directory)
                environment = {
                    "PATH": os.environ.get("PATH", os.defpath), "HOME": directory,
                    "CLAUDE_CONFIG_DIR": directory, "CLAUDE_CODE_OAUTH_TOKEN": credential,
                }
                body = _subprocess_json([executable, *CLAUDE_HEADLESS_ARGS, "--model", version],
                                        build_prompt(decision.request), directory, environment, self.timeout)
            reported = body.get("modelUsage")
            # CLI JSON normally reports modelUsage keys; accept an explicit model too.
            if reported is None:
                if body.get("model") != version:
                    raise ValueError("reported model missing or mismatched")
            elif not isinstance(reported, dict) or set(reported) != {version}:
                raise ValueError("reported model mismatch")
            if "model" in body and body["model"] != version:
                raise ValueError("reported model mismatch")
            if body.get("is_error") is not False:
                raise ValueError("provider reported failure")
            return _result(decision, body["result"], credential)
        except Exception:
            raise ProviderUnavailable("claude_plan unavailable or response rejected") from None


class CloudAPIProvider:
    """Anthropic Messages API. test_base_url is restricted to a loopback fake server."""

    def __init__(self, *, timeout: float = 30, test_base_url: str | None = None):
        self.timeout = _timeout(timeout)
        self._url = (ANTHROPIC_MESSAGES_URL if test_base_url is None
                     else _loopback_url(test_base_url) + "/v1/messages")

    def invoke(self, decision):
        try:
            decision = _checked(decision, "cloud_api")
            credential = _credential(decision)
            version = decision.target.identity.version
            with httpx.Client(timeout=self.timeout, trust_env=False, follow_redirects=False) as client:
                body = _http_json(client, "POST", self._url, headers={
                    "x-api-key": credential, "anthropic-version": "2023-06-01",
                }, json={"model": version, "max_tokens": 1024,
                         "messages": [{"role": "user", "content": build_prompt(decision.request)}]})
            if body.get("model") != version or body.get("stop_reason") != "end_turn":
                raise ValueError("model response mismatch or incomplete")
            blocks = body["content"]
            if not isinstance(blocks, list) or any(block.get("type") != "text" for block in blocks):
                raise ValueError("only text responses are accepted")
            return _result(decision, "\n".join(block["text"] for block in blocks), credential)
        except Exception:
            raise ProviderUnavailable("cloud_api unavailable or response rejected") from None
