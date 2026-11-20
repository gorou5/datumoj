"""Offline structure and shell checks for the manual historical-release importer."""

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

WORKFLOWS = Path(__file__).resolve().parents[1] / ".github/workflows"


def load():
    return yaml.safe_load((WORKFLOWS / "cloud-import.yml").read_text())


def test_manual_import_workflow_structure():
    workflow = load()
    project = yaml.safe_load((WORKFLOWS / "cloud-project.yml").read_text())
    assert workflow["on"] == {"workflow_dispatch": {"inputs": {
        "manifest": {"description": "Import manifest path in the repository", "type": "string",
                     "default": "imports/imf-weo-2009-2010.json"},
        "write": {"description": "Write a new generation after reviewing the dry run", "type": "boolean",
                  "default": False},
    }}}
    assert workflow["permissions"] == {"contents": "read"}
    assert workflow["concurrency"] == project["concurrency"] == {"group": "rikolto-cloud-run",
                                                                  "cancel-in-progress": False}
    job = workflow["jobs"]["run"]
    assert set(workflow["jobs"]) == {"run"}
    assert job["if"] == "github.repository == 'gorou5/datumoj'"
    assert job["steps"][:3] == project["jobs"]["run"]["steps"][:3]
    for step in job["steps"]:
        if "uses" in step:
            assert re.fullmatch(r"[^@]+@[0-9a-f]{40}", step["uses"])
    download, run = job["steps"][3:]
    assert download["env"] == {"GH_TOKEN": "${{ github.token }}", "GH_REPO": "${{ github.repository }}"}
    assert "gh release download imports-imf-weo-2009-2010" in download["run"]
    assert run["env"] == {
        **{key: "${{ secrets." + key + " }}" for key in (
            "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY", "R2_ENDPOINT", "DATUMOJ_S3_BUCKET", "DATUMOJ_S3_PREFIX",
        )}, "DATUMOJ_RAW_BACKEND": "s3", "DATUMOJ_VECTORS": "off",
        "MANIFEST": "${{ inputs.manifest }}", "WRITE": "${{ inputs.write }}",
    }
    assert "${{" not in run["run"] and "${{" not in download["run"]
    assert "echo" not in run["run"] and "ENDPOINT" not in run["run"]


@pytest.mark.parametrize("write,mode", [("false", "--dry-run"), ("true", "--write"), ("", "--dry-run")])
def test_import_shell_selects_mode_without_interpolating_input(tmp_path, write, mode):
    uv = tmp_path / "uv"
    uv.write_text(f"#!{sys.executable}\nimport json, sys\nprint(json.dumps(sys.argv[1:]))\n")
    uv.chmod(0o755)
    response = subprocess.run(
        ["bash", "--noprofile", "--norc", "-c", load()["jobs"]["run"]["steps"][-1]["run"]],
        cwd=tmp_path, env={**os.environ, "PATH": str(tmp_path) + os.pathsep + os.environ["PATH"], "WRITE": write,
                           "MANIFEST": "imports/x y.json", "RUNNER_TEMP": "/tmp/runner"},
        capture_output=True, text=True, check=False,
    )
    assert response.returncode == 0 and response.stderr == ""
    assert json.loads(response.stdout) == ["run", "--frozen", "rikolto", "cloud", "import-release", "--manifest",
                                           "imports/x y.json", "--files-dir", "/tmp/runner/import-files", mode]
