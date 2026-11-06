"""Static checks on the public writer workflow; no Actions execution or network."""

import re
from pathlib import Path

import pytest
import yaml

WORKFLOWS = Path(__file__).resolve().parents[1] / ".github/workflows"
SHA256 = "9f9ff52b8297d6e40904f22bac09eac587a05111762bf278359621fbdb53dbb9"
SECRETS = {"R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY", "R2_ENDPOINT", "DATUMOJ_S3_BUCKET", "DATUMOJ_S3_PREFIX", "ESTAT_APP_ID"}


@pytest.fixture(params=["run", "embed"])
def workflow_path(request):
    return WORKFLOWS / f"cloud-{request.param}.yml"


def load(workflow_path):
    return yaml.safe_load(workflow_path.read_text())


def test_triggers_concurrency_permissions_and_timeout(workflow_path):
    workflow = load(workflow_path)
    assert set(workflow["on"]) == {"workflow_dispatch"}
    assert workflow["concurrency"] == {"group": "rikolto-cloud-run", "cancel-in-progress": False}
    expected = {"contents": "read", "issues": "write"} if workflow_path.stem == "cloud-run" else {"contents": "read"}
    assert workflow["permissions"] == expected
    assert set(workflow["jobs"]) == {"run"}
    job = workflow["jobs"]["run"]
    assert job["if"] == "github.repository == 'gorou5/datumoj'"
    if workflow_path.stem == "cloud-run":
        assert 0 < job["timeout-minutes"] <= 90
    else:
        assert job["timeout-minutes"] == 150
    assert "permissions" not in job
    text = workflow_path.read_text()
    assert "pull_request" not in text and "pull_request_target" not in text


def test_secrets_are_only_env_references_and_no_public_storage_vars(workflow_path):
    secrets = SECRETS if workflow_path.stem == "cloud-run" else SECRETS - {"ESTAT_APP_ID"}
    workflow = load(workflow_path)
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
                assert path[-1] in secrets
                assert value == '${{ secrets.' + path[-1] + ' }}'
                references.append(path[-1])

    walk(workflow)
    assert set(references) == secrets and len(references) == len(secrets)


def test_actions_pinned_dependencies_frozen_model_cached_and_no_data_artifacts(workflow_path):
    steps = load(workflow_path)["jobs"]["run"]["steps"]
    for step in steps:
        if "uses" in step:
            assert re.fullmatch(r"[\w-]+/[\w-]+(?:/[\w-]+)?@[a-f0-9]{40}", step["uses"])
            assert "upload-artifact" not in step["uses"]
        script = step.get("run", "")
        assert not re.search(r"(?m)^\s*(?:env|printenv|set\s+-x|export\s+-p)\s*(?:$|[|;])", script)
        assert not re.search(r"(?:echo|printf).*\$(?:\{|)(?:R2_|DATUMOJ_S3_|ESTAT_)", script)
        assert not re.search(r"toJSON\s*\(\s*(?:env|secrets|vars)\s*\)", script)
    assert any(s.get("run") == "uv sync --frozen --python 3.12" for s in steps)
    setup = next(s for s in steps if s.get("uses", "").startswith("astral-sh/setup-uv@"))
    assert re.fullmatch(r"\d+\.\d+\.\d+", setup["with"]["version"])
    cache = next(s for s in steps if s.get("uses", "").startswith("actions/cache@"))
    assert cache["with"]["path"] == "~/.ollama/models"
    assert "bge-m3" in cache["with"]["key"]
    assert any("ollama pull bge-m3" in s.get("run", "") for s in steps)
    subcommand = "run" if workflow_path.stem == "cloud-run" else "embed"
    command = next(s for s in steps if f"rikolto cloud {subcommand}" in s.get("run", ""))
    if subcommand == "embed":
        assert command["run"] == "uv run --frozen rikolto cloud embed --max-minutes 100 --reserve-minutes 15"
    assert "uv run --frozen" in command["run"]
    assert command["env"]["DATUMOJ_RAW_BACKEND"] == "s3"


def test_ollama_fixed_release_checksum_checked_before_install(workflow_path):
    script = next(s["run"] for s in load(workflow_path)["jobs"]["run"]["steps"] if s.get("name") == "Install verified Ollama")
    assert "set -euo pipefail" in script
    assert "releases/download/v0.6.8/ollama-linux-amd64.tgz" in script
    assert SHA256 in script and "sha256sum --check --status" in script
    assert script.index(SHA256) < script.index("sudo tar")


def test_workflows_share_concurrency_and_identical_setup():
    run = load(WORKFLOWS / "cloud-run.yml")
    embed = load(WORKFLOWS / "cloud-embed.yml")
    assert run["concurrency"] == embed["concurrency"]
    assert run["jobs"]["run"]["steps"][:-2] == embed["jobs"]["run"]["steps"][:-1]


def test_source_issues_follow_publication_are_nonblocking_and_use_temporary_result():
    steps = load(WORKFLOWS / "cloud-run.yml")["jobs"]["run"]["steps"]
    run = next(step for step in steps if step.get("name") == "Run due sources and publish")
    issues = next(step for step in steps if step.get("name") == "Sync source health issues")
    assert steps.index(issues) > steps.index(run)
    assert run["shell"] == "bash" and "set -euo pipefail" in run["run"]
    assert '| tee "$RUNNER_TEMP/cloud-result.json"' in run["run"]
    assert issues["if"] == "always()" and issues["continue-on-error"] is True
    assert '[ -f "$RUNNER_TEMP/cloud-result.json" ]' in issues["run"]
    assert 'scripts/source_issues.py "$RUNNER_TEMP/cloud-result.json"' in issues["run"]
    assert issues["env"] == {"GH_REPO": "${{ github.repository }}", "GH_TOKEN": "${{ github.token }}"}


def test_only_retention_switches_are_repository_variables(workflow_path):
    steps = load(workflow_path)["jobs"]["run"]["steps"]
    command = next(step for step in steps if "rikolto cloud " in step.get("run", ""))
    assert command["env"]["R2_CLEANUP"] == "${{ vars['R2_CLEANUP'] }}"
    assert command["env"]["R2_RAW_CLEANUP"] == "${{ vars['R2_RAW_CLEANUP'] }}"


def test_state_blocks_are_never_stored_in_actions_cache(workflow_path):
    steps = load(workflow_path)['jobs']['run']['steps']
    assert not any(s.get('name') in {'Restore state blocks', 'Save state blocks'} for s in steps)
    for step in steps:
        if step.get('uses', '').startswith('actions/cache'):
            assert 'rikolto-blocks' not in str(step)
            assert 'rikolto-state-' not in str(step)


def test_secrets_stay_on_the_writer_step(workflow_path):
    job = load(workflow_path)['jobs']['run']
    assert 'env' not in job
    writer = next(s for s in job['steps'] if 'rikolto cloud ' in s.get('run', ''))
    assert 'R2_SECRET_ACCESS_KEY' in writer['env']
    assert not any('secrets.' in str(s) for s in job['steps'] if s is not writer)
