"""Static and offline shell checks for the manual, read-only Actions reader."""

import json
import os
import subprocess
import sys
from pathlib import Path

import yaml

WORKFLOWS = Path(__file__).resolve().parents[1] / ".github/workflows"


def test_pull_workflow_permissions_setup_concurrency_and_secret_boundary():
    workflow = yaml.safe_load((WORKFLOWS / "cloud-pull-check.yml").read_text())
    cleanup = yaml.safe_load((WORKFLOWS / "cloud-cleanup.yml").read_text())
    writer = yaml.safe_load((WORKFLOWS / "cloud-run.yml").read_text())
    assert workflow["name"] == "Cloud pull check"
    assert workflow["on"] == {"workflow_dispatch": None}
    assert workflow["permissions"] == {"contents": "read"}
    assert workflow["concurrency"] == writer["concurrency"] == {
        "group": "rikolto-cloud-run", "cancel-in-progress": False,
    }
    assert set(workflow) == {"name", "on", "permissions", "concurrency", "jobs"}
    assert set(workflow["jobs"]) == {"run"}
    job = workflow["jobs"]["run"]
    assert set(job) == {"if", "runs-on", "timeout-minutes", "steps"}
    assert job["if"] == "github.repository == 'gorou5/datumoj'"
    assert job["runs-on"] == "ubuntu-24.04"
    assert 0 < job["timeout-minutes"] <= 60
    assert len(job["steps"]) == 4
    assert job["steps"][:3] == cleanup["jobs"]["run"]["steps"][:3]
    step = job["steps"][-1]
    assert step["shell"] == "bash"
    assert step["env"] == {
        **{key: "${{ secrets." + key + " }}" for key in (
            "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY", "R2_ENDPOINT", "DATUMOJ_S3_BUCKET", "DATUMOJ_S3_PREFIX",
        )},
        "DATUMOJ_RAW_BACKEND": "s3", "DATUMOJ_VECTORS": "off",
    }
    assert step["run"] == (
        'set -euo pipefail\nuv run --frozen python scripts/pull_check.py "$RUNNER_TEMP/pull-check"\n'
    )
    assert "secrets." not in json.dumps({**step, "env": {}})


def test_pull_shell_uses_quoted_temporary_path(tmp_path):
    workflow = yaml.safe_load((WORKFLOWS / "cloud-pull-check.yml").read_text())
    uv = tmp_path / "uv"
    uv.write_text(f"#!{sys.executable}\nimport json, sys\nprint(json.dumps(sys.argv[1:]))\n")
    uv.chmod(0o755)
    destination = str(tmp_path / "space $(touch injected)")
    response = subprocess.run(
        ["bash", "--noprofile", "--norc", "-c", workflow["jobs"]["run"]["steps"][-1]["run"]],
        cwd=tmp_path, env={**os.environ, "PATH": str(tmp_path) + os.pathsep + os.environ["PATH"],
                          "RUNNER_TEMP": destination}, capture_output=True, text=True, check=False,
    )
    assert response.returncode == 0 and response.stderr == ""
    assert json.loads(response.stdout) == [
        "run", "--frozen", "python", "scripts/pull_check.py", destination + "/pull-check",
    ]
    assert not (tmp_path / "injected").exists()
