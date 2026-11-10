"""Offline structure and shell checks for the manual projection writer."""

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

WORKFLOWS = Path(__file__).resolve().parents[1] / ".github/workflows"


def test_manual_project_workflow_uses_writer_concurrency_and_secrets():
    workflow = yaml.safe_load((WORKFLOWS / "cloud-project.yml").read_text())
    embed = yaml.safe_load((WORKFLOWS / "cloud-embed.yml").read_text())
    assert workflow["on"] == {"workflow_dispatch": {"inputs": {"write": {
        "description": "Write projections after reviewing dry-run counts", "type": "boolean", "default": False,
    }}}}
    assert workflow["permissions"] == {"contents": "read"}
    assert workflow["concurrency"] == embed["concurrency"]
    assert set(workflow["jobs"]) == {"run"}
    job = workflow["jobs"]["run"]
    assert job["if"] == "github.repository == 'gorou5/datumoj'"
    assert job["runs-on"] == "ubuntu-24.04" and job["timeout-minutes"] <= 60
    assert job["steps"][:3] == embed["jobs"]["run"]["steps"][:3]
    assert len(job["steps"]) == 4
    for step in job["steps"]:
        if "uses" in step:
            assert re.fullmatch(r"[^@]+@[0-9a-f]{40}", step["uses"])
    step = job["steps"][-1]
    assert step["shell"] == "bash"
    assert step["env"] == {
        **{key: "${{ secrets." + key + " }}" for key in (
            "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY", "R2_ENDPOINT", "DATUMOJ_S3_BUCKET", "DATUMOJ_S3_PREFIX",
        )}, "DATUMOJ_RAW_BACKEND": "s3", "DATUMOJ_VECTORS": "off", "WRITE": "${{ inputs.write }}",
    }
    assert "${{" not in step["run"] and "ollama" not in json.dumps(workflow).lower()


@pytest.mark.parametrize("write,mode", [("false", "--dry-run"), ("true", "--write"), ("", "--dry-run")])
def test_project_shell_selects_mode_without_interpolating_input(tmp_path, write, mode):
    workflow = yaml.safe_load((WORKFLOWS / "cloud-project.yml").read_text())
    uv = tmp_path / "uv"
    uv.write_text(f"#!{sys.executable}\nimport json, sys\nprint(json.dumps(sys.argv[1:]))\n")
    uv.chmod(0o755)
    response = subprocess.run(
        ["bash", "--noprofile", "--norc", "-c", workflow["jobs"]["run"]["steps"][-1]["run"]],
        cwd=tmp_path, env={**os.environ, "PATH": str(tmp_path) + os.pathsep + os.environ["PATH"], "WRITE": write},
        capture_output=True, text=True, check=False,
    )
    assert response.returncode == 0 and response.stderr == ""
    assert json.loads(response.stdout) == ["run", "--frozen", "rikolto", "cloud", "project", mode]
