"""Offline structure and shell checks for manual read-only capacity inventory."""

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

WORKFLOWS = Path(__file__).resolve().parents[1] / ".github/workflows"


def test_usage_is_manual_serialized_pinned_and_uses_only_storage_credentials():
    workflow = yaml.safe_load((WORKFLOWS / "cloud-usage.yml").read_text())
    writer = yaml.safe_load((WORKFLOWS / "cloud-run.yml").read_text())
    assert set(workflow["on"]) == {"workflow_dispatch"}
    inputs = workflow["on"]["workflow_dispatch"]["inputs"]
    assert set(inputs) == {"db"}
    assert inputs["db"]["type"] == "boolean" and inputs["db"]["default"] is False
    assert workflow["permissions"] == {"contents": "read"}
    assert workflow["concurrency"] == writer["concurrency"] == {
        "group": "rikolto-cloud-run", "cancel-in-progress": False,
    }
    assert set(workflow["jobs"]) == {"run"}
    job = workflow["jobs"]["run"]
    assert job["if"] == "github.repository == 'gorou5/datumoj'"
    assert job["runs-on"] == "ubuntu-24.04" and job["timeout-minutes"] <= 60
    assert job["steps"][:3] == writer["jobs"]["run"]["steps"][:3]
    assert len(job["steps"]) == 4
    for step in job["steps"]:
        if "uses" in step:
            assert re.fullmatch(r"[^@]+@[0-9a-f]{40}", step["uses"])
    step = job["steps"][-1]
    assert step["shell"] == "bash"
    assert step["env"] == {**{key: "${{ secrets." + key + " }}" for key in (
        "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY", "R2_ENDPOINT", "DATUMOJ_S3_BUCKET", "DATUMOJ_S3_PREFIX",
    )}, "R2_RAW_CLEANUP": "${{ vars['R2_RAW_CLEANUP'] }}", "USAGE_DB": "${{ inputs.db }}"}
    assert "${{" not in step["run"]
    assert "ollama" not in json.dumps(workflow).lower()


@pytest.mark.parametrize("status", [0, 1])
@pytest.mark.parametrize("db", ["false", "true", ""])
def test_shell_writes_exact_json_to_summary_and_keeps_failure_status(tmp_path, status, db):
    workflow = yaml.safe_load((WORKFLOWS / "cloud-usage.yml").read_text())
    expected = {"scope": "this_bucket", "total": {"objects": 7, "bytes": 123}}
    uv = tmp_path / "uv"
    args = ["run", "--frozen", "rikolto", "cloud", "usage"] + (["--db"] if db == "true" else [])
    uv.write_text(f"#!{sys.executable}\nimport json, sys\n"
                  f"assert sys.argv[1:] == {args!r}\n"
                  f"print({json.dumps(expected)!r})\nsys.exit({status})\n")
    uv.chmod(0o755)
    summary = tmp_path / "summary"
    response = subprocess.run(
        ["bash", "--noprofile", "--norc", "-c", workflow["jobs"]["run"]["steps"][-1]["run"]],
        cwd=tmp_path, env={**os.environ, "PATH": str(tmp_path) + os.pathsep + os.environ["PATH"],
                           "GITHUB_STEP_SUMMARY": str(summary), "USAGE_DB": db},
        capture_output=True, text=True, check=False,
    )
    assert response.returncode == status and response.stderr == ""
    assert json.loads(response.stdout) == expected
    text = summary.read_text()
    assert "统计范围为本桶" in text
    assert json.loads(text.split("```json\n")[1].split("```", 1)[0]) == expected
