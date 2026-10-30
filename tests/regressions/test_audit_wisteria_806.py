"""806 security audit: durable contracts plus offline provider/storage integration."""

import sys
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest

from datumoj.eltiro.contracts import ProviderUnavailable, RerunCacheMiss, RouteInvalid
from datumoj.eltiro.model_cache import SQLiteModelCache
from datumoj.eltiro.models import ConsumersConfig, ManualQueueItem, ModelResult, RouteDecision
from datumoj.eltiro.providers import (
    CLAUDE_EXECUTABLE_ENV,
    CLAUDE_HEADLESS_ARGS,
    OLLAMA_URL_ENV,
    ClaudePlanProvider,
    CloudAPIProvider,
    OllamaProvider,
)
from datumoj.eltiro.routing import SQLiteModelRouter
from datumoj.eltiro.testing.contract_suites import ModelCacheSuite, ModelRouterSuite
from datumoj.eltiro.testing.fakes import FakeModelCache, FakeModelProvider
from datumoj.eltiro.testing.samples import (
    make_consumer,
    make_model_input,
    make_route_decision,
    make_routing_context,
    make_routing_policy,
)


def config(cap=1000):
    return ConsumersConfig.model_validate({"consumers": [
        {"id": name, "inbox": f"{name}/inbox", "outbox": f"{name}/outbox",
         "allowed_sources": ["jp-one", "jp-two"], "models": make_consumer(consumer=name),
         "max_active_demands": 1, "max_calls_per_run": cap}
        for name in ("example-consumer", "another-consumer")
    ]})


class TestAuditRouting(ModelRouterSuite):
    """Includes both cloud restrictions, mixed inputs, eight identities and revocation."""

    @pytest.fixture
    def make_router(self, tmp_path):
        routers = []

        def make():
            router = SQLiteModelRouter(tmp_path / str(len(routers)), consumers=config())
            routers.append(router)
            return router

        yield make
        for router in routers:
            router.close()


class TestAuditCache(ModelCacheSuite):
    @pytest.fixture
    def make_cache(self, tmp_path):
        caches = []

        def make():
            cache = SQLiteModelCache(tmp_path / str(len(caches)))
            caches.append(cache)
            return cache

        yield make
        for cache in caches:
            cache.close()


@pytest.mark.parametrize("demand_cap,consumer_cap", [(1, 8), (8, 1), (8, 0)])
def test_restart_pins_plan_call_attempts_and_manual_queue(tmp_path, demand_cap, consumer_cap):
    context = make_routing_context(inputs=[make_model_input(), make_model_input(id="note", step="concept_note")])
    context["budget"] = context["budget"].model_copy(update={"max_calls_per_run": demand_cap})
    remote, local = FakeModelProvider(), FakeModelProvider()
    remote.available = False
    invoke = dict(consumer=context["consumer"], licenses=context["licenses"],
                  providers={"cloud_api": remote, "ollama": local}, cache=FakeModelCache())
    with SQLiteModelRouter(tmp_path, consumers=config(consumer_cap)) as router:
        original = router.plan(**context)
        assert original.budget.max_calls_per_run == min(demand_cap, consumer_cap)
        if consumer_cap:
            with pytest.raises(ProviderUnavailable):
                router.invoke(original.run_id, 0, **invoke)
        else:
            assert isinstance(router.invoke(original.run_id, 0, **invoke), ManualQueueItem)
    with SQLiteModelRouter(tmp_path, consumers=config(consumer_cap)) as router:
        assert router.plan(**context) == original
        assert router.calls_used(original.run_id) == bool(consumer_cap)
        queued = router.invoke(original.run_id, 1, **invoke)
        assert isinstance(queued, ManualQueueItem)
        assert queued in router.list_manual(run_id=original.run_id, consumer=context["consumer"].consumer)
        assert router.list_manual(consumer="another-consumer") == []
        assert router.get_plan(original.run_id) == original
        if consumer_cap:
            with pytest.raises(RerunCacheMiss):
                router.invoke(original.run_id, 0, **{**invoke, "rerun": True})
    with SQLiteModelRouter(tmp_path, consumers=config(consumer_cap)) as router:
        assert queued in router.list_manual(run_id=original.run_id)
        assert router.calls_used(original.run_id) == bool(consumer_cap)
    assert len(remote.calls) == bool(consumer_cap) and not local.calls


def test_restart_cached_output_and_revocation_before_read(tmp_path):
    context = make_routing_context()
    with SQLiteModelRouter(tmp_path, consumers=config()) as router, SQLiteModelCache(tmp_path) as cache:
        plan = router.plan(**context)
        result = router.invoke(plan.run_id, 0, consumer=context["consumer"], licenses=context["licenses"],
                               providers={"cloud_api": FakeModelProvider()}, cache=cache)
    with SQLiteModelRouter(tmp_path, consumers=config()) as router, SQLiteModelCache(tmp_path) as cache:
        assert router.get_plan(plan.run_id) == plan
        assert router.invoke(plan.run_id, 0, consumer=context["consumer"], licenses=context["licenses"],
                             providers={}, cache=cache, rerun=True) == result
        assert router.calls_used(plan.run_id) == 1
        withdrawn = {"jp-one": context["licenses"]["jp-one"].model_copy(update={"cloud_model_ok": False})}
        with pytest.raises(RouteInvalid):
            router.invoke(plan.run_id, 0, consumer=context["consumer"], licenses=withdrawn,
                          providers={}, cache=cache, rerun=True)


def test_two_connections_cannot_overspend_run(tmp_path):
    context = make_routing_context()
    with SQLiteModelRouter(tmp_path, consumers=config(1)) as router:
        router.plan(**context)
    provider = FakeModelProvider()

    def invoke(_):
        with SQLiteModelRouter(tmp_path, consumers=config(1)) as router:
            return router.invoke(context["run_id"], 0, consumer=context["consumer"], licenses=context["licenses"],
                                 providers={"cloud_api": provider}, cache=FakeModelCache())

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(invoke, range(2)))
    assert sum(isinstance(item, ManualQueueItem) for item in outcomes) == 1
    assert sum(isinstance(item, ModelResult) for item in outcomes) == 1
    assert len(provider.calls) == 1
    with SQLiteModelRouter(tmp_path, consumers=config(1)) as router:
        assert router.calls_used(context["run_id"]) == 1
        assert len(router.list_manual()) == 1


@pytest.mark.parametrize("kind", ["cloud_api", "claude_plan"])
def test_credentials_never_persist_or_leak_and_failure_never_reroutes(tmp_path, monkeypatch, httpserver, caplog, kind):
    credential = uuid4().hex
    monkeypatch.setenv("EXAMPLE_MODEL_API_KEY", credential)
    context = make_routing_context(policy=make_routing_policy(cloud_provider=kind))
    if kind == "cloud_api":
        httpserver.expect_request("/v1/messages", method="POST").respond_with_json({
            "model": "v1", "stop_reason": "end_turn", "content": [{"type": "text", "text": "synthetic answer"}]})
        provider = CloudAPIProvider(test_base_url=httpserver.url_for("/"))
    else:
        monkeypatch.setattr("tempfile.tempdir", str(tmp_path))
        executable = tmp_path / "fake-claude"
        # Executable records no credential, asserts argv/stdin and isolation itself.
        executable.write_text(
            f"#!{sys.executable}\n"
            "import json,os,pathlib,sys\n"
            f"assert sys.argv[1:] == {[*CLAUDE_HEADLESS_ARGS, '--model', 'v1']!r}\n"
            "assert not list(pathlib.Path.cwd().iterdir())\n"
            "assert '<source_data>' in sys.stdin.read()\n"
            "assert os.environ['CLAUDE_CODE_OAUTH_TOKEN']\n"
            "print(json.dumps({'is_error':False,'modelUsage':{'v1':{}},'result':'synthetic answer'}))\n")
        executable.chmod(0o700)
        monkeypatch.setenv(CLAUDE_EXECUTABLE_ENV, str(executable))
        provider = ClaudePlanProvider()
    local, alternative = FakeModelProvider(), FakeModelProvider()
    with SQLiteModelRouter(tmp_path, consumers=config()) as router, SQLiteModelCache(tmp_path) as cache:
        plan = router.plan(**context)
        result = router.invoke(plan.run_id, 0, consumer=context["consumer"], licenses=context["licenses"],
                               providers={kind: provider}, cache=cache)
        result.validate_route(plan.items[0])
        newer = {**context, "run_id": "failed-run", "inputs": [make_model_input(id="uncached_judge")]}
        router.plan(**newer)
        # A malicious response echoing credentials is rejected, including on successful HTTP/CLI exit.
        if kind == "cloud_api":
            httpserver.clear()
            httpserver.expect_request("/v1/messages", method="POST").respond_with_json({
                "model": "v1", "stop_reason": "end_turn", "content": [{"type": "text", "text": credential}]})
        else:
            executable.write_text(
                f"#!{sys.executable}\nimport json,os,sys\nsys.stdin.read()\n"
                "print(json.dumps({'is_error':False,'model':'v1','result':os.environ['CLAUDE_CODE_OAUTH_TOKEN']}))\n")
        with pytest.raises(ProviderUnavailable) as exc:
            router.invoke("failed-run", 0, consumer=context["consumer"], licenses=context["licenses"],
                          providers={kind: provider, "ollama": local,
                                     ("claude_plan" if kind == "cloud_api" else "cloud_api"): alternative}, cache=cache)
        assert credential not in str(exc.value) + caplog.text
        assert router.calls_used("failed-run") == 1
        assert router.get_plan("failed-run").items[0].target.provider == kind
        assert not local.calls and not alternative.calls
        paths = (router.path, cache.path)
    for path in paths:
        assert credential.encode() not in path.read_bytes()
        for sidecar in path.parent.glob(path.name + "-*"):
            assert credential.encode() not in sidecar.read_bytes()


def test_ollama_remote_and_digest_mismatch_never_send_input(monkeypatch, httpserver):
    data = make_route_decision().model_dump()
    data["target"] = {"provider": "ollama", "identity": {"model": "example-local", "version": "sha256:" + "ab" * 32}}
    decision = RouteDecision.model_validate(data)
    monkeypatch.setenv(OLLAMA_URL_ENV, "http://192.0.2.1:11434")
    with pytest.raises(ProviderUnavailable):
        OllamaProvider().invoke(decision)
    monkeypatch.setenv(OLLAMA_URL_ENV, httpserver.url_for("/"))
    httpserver.expect_request("/api/tags").respond_with_json({"models": [
        {"name": "example-local", "digest": "sha256:" + "cd" * 32}]})
    with pytest.raises(ProviderUnavailable):
        OllamaProvider().invoke(decision)
    assert [request.path for request, _ in httpserver.log] == ["/api/tags"]


def test_cloud_actual_version_mismatch_rejected(httpserver, monkeypatch):
    monkeypatch.setenv("EXAMPLE_MODEL_API_KEY", uuid4().hex)
    httpserver.expect_request("/v1/messages", method="POST").respond_with_json({
        "model": "v2", "stop_reason": "end_turn", "content": [{"type": "text", "text": "ignored"}]})
    with pytest.raises(ProviderUnavailable):
        CloudAPIProvider(test_base_url=httpserver.url_for("/")).invoke(make_route_decision())
