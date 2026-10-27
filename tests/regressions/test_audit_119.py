"""Rikolto rename: offline checks against the pre-rename contracts and storage."""

from __future__ import annotations

import ast
import asyncio
import hashlib
import importlib
import json
import plistlib
import re
import subprocess
import tempfile
import tomllib
from pathlib import Path

import pytest
from mcp import Client

from datumoj.rikolto import cli, mcp_server, wiring
from datumoj.rikolto.core import contracts, schedule, state
from datumoj.rikolto.core.http import USER_AGENT
from datumoj.rikolto.core.lease import LeaseManager
from datumoj.rikolto.core.models import StateWriter
from datumoj.rikolto.testing.fakes import FakeStateObjectStore
from datumoj.rikolto.testing.samples import T0
from datumoj.rikolto.core.models import export_schemas
from datumoj.rikolto.store.observations import _ORDER_KEY

ROOT = Path(__file__).resolve().parents[2]
PACKAGE = ROOT / "src" / "datumoj" / "rikolto"
OLD_MODULES = (
    "admin", "analysis", "core", "crawlers", "eval", "index", "parsers", "reference",
    "store", "tabular", "testing", "text", "changes", "cli", "cloud", "diagnose",
    "mcp_server", "pipeline", "wiring",
)
OLD_DOTTED = re.compile(r"\bdatumoj\.(?=(?:" + "|".join(OLD_MODULES) + r")\b)")
OLD_PATH = re.compile(r"\b(?:src/)?datumoj/(?:" + "|".join(OLD_MODULES) + r")(?:/|\.py\b)")
CURRENT_DOCS = (
    "README.md", "CONTRIBUTING.md", "docs/SETUP.md", "docs/architecture.md",
    "docs/interoperability.md", "docs/data-sources.md", "docs/deploy/nas.md", "sources/README.md",
)
# Captured from the pre-rename source and schemas.
ENVIRONMENT = (
    'DATUMOJ_DATA_DIR',
    'DATUMOJ_FAKE_DECIMAL',
    'DATUMOJ_LEASE_HOLDER',
    'DATUMOJ_LEASE_TOKEN',
    'DATUMOJ_RAW_BACKEND',
    'DATUMOJ_S3',
    'DATUMOJ_S3_ACCESS_KEY_ID',
    'DATUMOJ_S3_BUCKET',
    'DATUMOJ_S3_ENDPOINT',
    'DATUMOJ_S3_PREFIX',
    'DATUMOJ_S3_REGION',
    'DATUMOJ_S3_SECRET_ACCESS_KEY',
    'DATUMOJ_TOOLS_FACTORY',
    'DATUMOJ_VECTORS',
    'DATUMOJ_VECTOR_LIMIT',
    'DATUMOJ_VECTOR_RETAIN_HOURS',
    'DATUMOJ_WRITER_KIND',
)
# 301 adds query_sql as_of_release and generation_token.
MCP_TOOLS_HASH = 'ea5c4390d48f5197db1cf1ecb4f948e66786a1fa0e19457b4b499e0856e494f3'


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


@pytest.mark.parametrize("name", OLD_MODULES)
def test_old_modules_cannot_be_imported(name):
    with pytest.raises(ModuleNotFoundError, match="datumoj"):
        importlib.import_module("datumoj." + name)
    assert importlib.import_module("datumoj.rikolto." + name)
    assert not (ROOT / "src" / "datumoj" / name).exists()
    assert not (ROOT / "src" / "datumoj" / (name + ".py")).exists()


def test_entrypoints_and_project_name():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
    assert project["name"] == "datumoj"
    assert project["scripts"]["rikolto"] == project["scripts"]["datumoj"] == "datumoj.rikolto.cli:app"
    import datumoj.__main__ as main
    assert main.app is cli.app


def test_discovery_factory_and_repository_paths(tmp_path, monkeypatch):
    assert contracts.discover_crawlers.__kwdefaults__ == {"skip_broken": False}
    assert contracts.discover_crawlers.__defaults__ == ("datumoj.rikolto.crawlers",)
    assert contracts.discover_parsers.__defaults__ == ("datumoj.rikolto.parsers",)
    assert "jma_feed" in contracts.discover_crawlers()
    assert contracts.discover_parsers()
    assert mcp_server.SKILL_PATH == ROOT / "skills" / "rikolto" / "SKILL.md"
    assert mcp_server.answer_rules() == mcp_server.SKILL_PATH.read_text()
    monkeypatch.delenv("DATUMOJ_TOOLS_FACTORY", raising=False)
    monkeypatch.setattr(mcp_server, "main", lambda: None)
    cli.mcp()
    assert mcp_server._factory_from_env() is wiring.build_tools
    # Repository-relative resources still resolve after adding a package level.
    for name in ENVIRONMENT:
        monkeypatch.delenv(name, raising=False)
    for name in ("R2_ENDPOINT", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    deps = wiring.build(tmp_path, {"vectors": None})
    try:
        assert deps.registry.get("jp-jma").kind == "jma_feed"
        assert {"manifest.sqlite", "chunks.sqlite", "keyword.sqlite", "catalog.sqlite",
                "observations.duckdb", "changes.sqlite"} <= {p.name for p in tmp_path.iterdir()}
        content = b"synthetic rename audit"
        sha = deps.raw.put(content)
        key = f"raw/{sha[:2]}/{sha}"
        assert (tmp_path / key).read_bytes() == content
        assert state._raw_key("raw", sha) == key
        remote = FakeStateObjectStore()
        manager = state.StateManager(remote, tmp_path, writer=StateWriter(kind="local", run_id="rename"))
        generation = manager.push()
        assert json.loads(remote.get("current.json")[0]) == {"generation": 1}
        manifest = json.loads(remote.get("generations/1.json")[0])
        assert set(manifest) == {
            "generation", "previous_generation", "created_at", "writer", "stores", "raw_prefix",
            "vector_coverage", "last_successful_runs",
        }
        assert manifest["writer"] == {"kind": "local", "run_id": "rename"}
        assert generation.raw_prefix == "raw"
        assert remote.get(key)[0] == content
        for copy in generation.stores:
            assert copy.key == f"copies/sha256/{copy.sha256}"
            assert copy.format in {"sqlite-block-v1", "sqlite-blocks-v1", "duckdb-empty"}
        assert state._SMALL == {"runs.jsonl", "control.json", "vectors-state.json"}
        assert state._VECTOR_STATE == "vectors/vectors-state.json"
    finally:
        for name in ("manifest", "chunks", "keyword", "catalog", "change_log"):
            getattr(deps, name).close()


def test_mcp_name_tools_and_tool_definitions():
    server = mcp_server.create_server(lambda: pytest.fail("listing must not access stores"))
    assert server.name == "rikolto"

    async def listed():
        async with Client(server) as client:
            return await client.list_tools(), await client.list_prompts()

    tools, prompts = asyncio.run(listed())
    assert {tool.name for tool in tools.tools} == {
        "search_docs", "list_changes", "search_catalog", "query_sql", "get_chunk", "cite", "version", "coverage",
    }
    assert [prompt.name for prompt in prompts.prompts] == ["datumoj_answer_rules"]
    tree = ast.parse((PACKAGE / "mcp_server.py").read_text())
    functions = sorted((node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)
                        and any(isinstance(d, ast.Call) and ast.unparse(d.func) == "server.tool"
                                for d in node.decorator_list)), key=lambda node: node.name)
    signatures = "\n".join(node.name + ast.dump(node.args) + ast.dump(node.returns) for node in functions)
    assert digest(signatures) == MCP_TOOLS_HASH


@pytest.mark.parametrize("kind", ["local", "actions", "nas"])
def test_lease_holder_format_and_persisted_key(kind):
    remote = FakeStateObjectStore()
    lease = LeaseManager(remote, now=lambda: T0).acquire(kind + "/rename")
    assert remote.list("") == ["write-lease.json"]
    stored = json.loads(remote.get("write-lease.json")[0])
    assert stored == lease.model_dump(mode="json")
    assert stored["holder"] == kind + "/rename"
    assert set(stored) == {"holder", "token", "acquired_at", "expires_at"}


def test_environment_user_agent_and_parquet_key_unchanged():
    text = "\n".join(path.read_text() for path in PACKAGE.rglob("*.py"))
    assert tuple(sorted(set(re.findall(r"\bDATUMOJ_[A-Z0-9_]+\b", text)))) == ENVIRONMENT
    assert USER_AGENT == "DATUMOJ/0.1 (+https://github.com/gorou5/datumoj)"
    assert _ORDER_KEY == b"datumoj.snapshot_order"


def test_schema_export_is_byte_identical(tmp_path):
    written = export_schemas(tmp_path)
    committed = ROOT / "schemas"
    assert {p.name for p in written} == {p.name for p in committed.glob("*.schema.json")}
    for path in written:
        assert path.read_bytes() == (committed / path.name).read_bytes()


@pytest.mark.parametrize("current_exists", [False, True])
def test_install_unloads_legacy_before_loading_new(tmp_path, current_exists):
    repo, data = tmp_path / "repo", tmp_path / "data"
    path = tmp_path / (schedule.LABEL + ".plist")
    legacy = tmp_path / (schedule.LEGACY_LABEL + ".plist")
    legacy.write_bytes(b"legacy")
    if current_exists:
        path.write_bytes(b"current")
    calls = []

    def launchctl(args, **kwargs):
        assert kwargs == {"check": True}
        calls.append(args)
        if args[1] == "load":
            assert not legacy.exists()
            assert path.exists()
        else:
            assert legacy.read_bytes() == b"legacy"

    assert schedule.install_launchd(repo, data, install_path=path, launchctl=launchctl, uv="uv") == path
    assert calls == [["launchctl", "unload", str(legacy)], ["launchctl", "load", str(path)]]
    plist = plistlib.loads(path.read_bytes())
    assert plist["Label"] == "com.datumoj.rikolto.schedule"
    assert plist["ProgramArguments"][1:] == ["run", "--frozen", "rikolto", "--data-dir",
                                             str(data), "schedule", "tick"]
    assert plist["WorkingDirectory"] == str(repo)
    assert plist["StandardOutPath"] == plist["StandardErrorPath"] == str(data / "logs" / "schedule.log")
    assert plist["StartInterval"] == 300


def test_failed_legacy_unload_prevents_new_install(tmp_path):
    path = tmp_path / (schedule.LABEL + ".plist")
    legacy = tmp_path / (schedule.LEGACY_LABEL + ".plist")
    legacy.write_bytes(b"legacy")
    calls = []

    def launchctl(args, **kwargs):
        calls.append(args)
        raise subprocess.CalledProcessError(1, args)

    with pytest.raises(subprocess.CalledProcessError):
        schedule.install_launchd(tmp_path, tmp_path / "data", install_path=path, launchctl=launchctl)
    assert legacy.read_bytes() == b"legacy" and not path.exists()
    assert calls == [["launchctl", "unload", str(legacy)]]


@pytest.mark.parametrize("labels", [("legacy",), ("current",), ("legacy", "current"), ()])
def test_uninstall_cleans_both_labels(tmp_path, labels):
    paths = {"legacy": tmp_path / (schedule.LEGACY_LABEL + ".plist"),
             "current": tmp_path / (schedule.LABEL + ".plist")}
    for label in labels:
        paths[label].write_bytes(b"plist")
    calls = []

    def launchctl(args, **kwargs):
        assert kwargs == {"check": True} and Path(args[2]).exists()
        calls.append(args)

    schedule.uninstall_launchd(install_path=paths["current"], launchctl=launchctl)
    assert calls == [["launchctl", "unload", str(paths[label])] for label in labels]
    assert not any(path.exists() for path in paths.values())


def test_no_old_module_imports_or_paths_in_current_sources():
    files = [*ROOT.joinpath("src").rglob("*.py"),
             *(p for p in ROOT.joinpath("tests").rglob("*.py") if "fixtures" not in p.parts),
             *ROOT.joinpath("skills").rglob("*.md"),
             *(ROOT / name for name in CURRENT_DOCS)]
    for path in files:
        text = path.read_text()
        assert not OLD_DOTTED.search(text), path.relative_to(ROOT)
        assert not OLD_PATH.search(text), path.relative_to(ROOT)
        if path.suffix == ".py":
            for node in ast.walk(ast.parse(text)):
                if isinstance(node, ast.ImportFrom) and node.module == "datumoj":
                    assert not {alias.name for alias in node.names}.intersection(OLD_MODULES), path
