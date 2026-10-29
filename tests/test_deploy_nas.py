"""Offline checks of the NAS build context and optional network topology."""

import json
import re
from pathlib import Path
from urllib.parse import urlsplit

import yaml

ROOT = Path(__file__).resolve().parents[1]
NAS = ROOT / "deploy/nas"


def test_dockerfile_uses_locked_dependencies_python_and_nonroot_entrypoint():
    dockerfile = (NAS / "Dockerfile").read_text()
    workflow = (ROOT / ".github/workflows/ci.yml").read_text()
    python = re.search(r'python-version: "([^"]+)"', workflow).group(1)
    assert f"FROM python:{python}-slim" in dockerfile
    assert "uv sync --frozen" in dockerfile
    assert not re.search(r"(?:pip install|uv add|uv pip install)", dockerfile)
    user = re.search(r"^USER (.+)$", dockerfile, re.MULTILINE).group(1)
    assert user == "10001:10001" and "useradd" in dockerfile
    for instruction, expected in (("ENTRYPOINT", ["rikolto"]), ("CMD", ["cloud", "run"])):
        value = re.search(rf"^{instruction} (.+)$", dockerfile, re.MULTILINE).group(1)
        assert json.loads(value) == expected
    copies = re.findall(r"^COPY (?!-)(.+)$", dockerfile, re.MULTILINE)
    assert copies == ["pyproject.toml uv.lock README.md ./", "src/ ./src/", "sources/ ./sources/", "schemas/ ./schemas/"]


def test_compose_has_no_ports_or_inline_configuration_and_ollama_is_optional():
    base = yaml.safe_load((NAS / "compose.example.yaml").read_text())
    overlay = yaml.safe_load((NAS / "compose.ollama.yaml").read_text())
    for document in (base, overlay):
        for service in document["services"].values():
            assert "ports" not in service and "environment" not in service
    datumoj = base["services"]["rikolto"]
    assert datumoj["env_file"] == ["./env.local"]
    assert datumoj["command"] == ["cloud", "run", "--max-embed", "0"]
    assert datumoj["restart"] == "no"
    assert "network_mode" not in datumoj and "depends_on" not in datumoj
    assert base["services"]["ollama"]["profiles"] == ["ollama"]
    enabled = overlay["services"]["rikolto"]
    assert enabled["network_mode"] == "service:ollama"
    assert enabled["depends_on"] == ["ollama"]
    assert enabled["command"] == ["cloud", "run"]


def test_examples_only_contain_fictional_credentials_and_hosts():
    values = dict(line.split("=", 1) for line in (NAS / "env.example").read_text().splitlines()
                  if line and not line.startswith("#"))
    assert urlsplit(values["DATUMOJ_S3_ENDPOINT"]).hostname == "nas.example"
    assert values["DATUMOJ_S3_BUCKET"] == "your-bucket-name"
    assert values["DATUMOJ_S3_ACCESS_KEY_ID"] == "your-access-key-id"
    assert values["DATUMOJ_S3_SECRET_ACCESS_KEY"] == "your-secret-access-key"
    assert values["DATUMOJ_RAW_BACKEND"] == "s3"
    for path in (*NAS.glob("*"), ROOT / ".dockerignore", ROOT / "docs/deploy/nas.md"):
        text = path.read_text()
        assert not re.search(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b|-----BEGIN .*PRIVATE KEY-----", text)
        assert not re.search(r"\b(?:10\.\d+\.\d+\.\d+|192\.168\.\d+\.\d+|172\.(?:1[6-9]|2\d|3[01])\.\d+\.\d+)\b", text)
        assert "/Users/" not in text and "/home/" not in text
        for host in re.findall(r"https?://([^/\s:`]+)", text):
            assert host == "nas.example"


def test_dockerignore_excludes_local_data_and_secrets():
    patterns = (ROOT / ".dockerignore").read_text().splitlines()
    assert {".env*", "!.env.example", "data/", ".orch/", "results/", ".git/", ".venv/", "handoff/"} <= set(patterns)
    assert "deploy/nas/env.*" in patterns and "!deploy/nas/env.example" in patterns
    assert "**/*credentials*" in patterns and "**/*.key" in patterns
