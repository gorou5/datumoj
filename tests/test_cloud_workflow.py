"""Static checks on the public writer workflow; no Actions execution or network."""

import re
from pathlib import Path

import yaml

WORKFLOW = Path(__file__).resolve().parents[1] / ".github/workflows/cloud-run.yml"
SHA256 = "9f9ff52b8297d6e40904f22bac09eac587a05111762bf278359621fbdb53dbb9"
SECRETS = {"R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY", "R2_ENDPOINT", "DATUMOJ_S3_BUCKET", "DATUMOJ_S3_PREFIX"}


def load():
    return yaml.safe_load(WORKFLOW.read_text())


def test_triggers_concurrency_permissions_and_timeout():
    workflow = load()
    assert set(workflow["on"]) == {"schedule", "workflow_dispatch"}
    assert workflow["on"]["schedule"] == [{"cron": "17 * * * *"}]
    assert workflow["concurrency"] == {"group": "datumoj-cloud-run", "cancel-in-progress": False}
    assert workflow["permissions"] == {"contents": "read"}
    assert set(workflow["jobs"]) == {"run"}
    job = workflow["jobs"]["run"]
    assert job["if"] == "github.repository == 'gorou5/datumoj'"
    assert 0 < job["timeout-minutes"] <= 60
    assert "permissions" not in job
    text = WORKFLOW.read_text()
    assert "pull_request" not in text and "pull_request_target" not in text


def test_secrets_are_only_env_references_and_no_public_storage_vars():
    workflow = load()
    references = []

    def walk(value, path=()):
        if isinstance(value, dict):
            for key, item in value.items():
                walk(item, (*path, key))
        elif isinstance(value, list):
            for index, item in enumerate(value):
                walk(item, (*path, index))
        elif isinstance(value, str):
            assert "vars." not in value
            if "secrets." in value:
                assert path[-2] == "env"
                assert path[-1] in SECRETS
                assert value == '${{ secrets.' + path[-1] + ' }}'
                references.append(path[-1])

    walk(workflow)
    assert set(references) == SECRETS and len(references) == len(SECRETS)


def test_actions_pinned_dependencies_frozen_model_cached_and_no_data_artifacts():
    steps = load()["jobs"]["run"]["steps"]
    for step in steps:
        if "uses" in step:
            assert re.fullmatch(r"[\w-]+/[\w-]+@[a-f0-9]{40}", step["uses"])
            assert "upload-artifact" not in step["uses"]
        script = step.get("run", "")
        assert not re.search(r"(?m)^\s*(?:env|printenv|set\s+-x|export\s+-p)\s*(?:$|[|;])", script)
        assert not re.search(r"(?:echo|printf).*\$(?:\{|)(?:R2_|DATUMOJ_S3_)", script)
        assert not re.search(r"toJSON\s*\(\s*(?:env|secrets|vars)\s*\)", script)
    assert any(s.get("run") == "uv sync --frozen --python 3.12" for s in steps)
    setup = next(s for s in steps if s.get("uses", "").startswith("astral-sh/setup-uv@"))
    assert re.fullmatch(r"\d+\.\d+\.\d+", setup["with"]["version"])
    cache = next(s for s in steps if s.get("uses", "").startswith("actions/cache@"))
    assert cache["with"]["path"] == "~/.ollama/models"
    assert "bge-m3" in cache["with"]["key"]
    assert any("ollama pull bge-m3" in s.get("run", "") for s in steps)
    command = next(s for s in steps if "datumoj cloud run" in s.get("run", ""))
    assert "uv run --frozen" in command["run"]
    assert command["env"]["DATUMOJ_RAW_BACKEND"] == "s3"


def test_ollama_fixed_release_checksum_checked_before_install():
    script = next(s["run"] for s in load()["jobs"]["run"]["steps"] if s.get("name") == "Install verified Ollama")
    assert "set -euo pipefail" in script
    assert "releases/download/v0.6.8/ollama-linux-amd64.tgz" in script
    assert SHA256 in script and "sha256sum --check --status" in script
    assert script.index(SHA256) < script.index("sudo tar")
