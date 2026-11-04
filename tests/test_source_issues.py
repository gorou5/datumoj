"""Offline gh subprocess tests; the executable only updates a temporary JSON file."""

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/source_issues.py"
SPEC = importlib.util.spec_from_file_location("source_issues", SCRIPT)
source_issues = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(source_issues)

FAKE_GH = '''
import json
import os
import sys
from pathlib import Path

path = Path(os.environ["FAKE_GH_STATE"])
state = json.loads(path.read_text())
args = sys.argv[1:]
state["calls"].append(args)
def save():
    path.write_text(json.dumps(state))
save()
if state.get("fail") in ("all", " ".join(args[:2])):
    print("https://synthetic.invalid/private?token=synthetic-secret", file=sys.stderr)
    print("synthetic-secret")
    sys.exit(1)
def value(flag):
    return args[args.index(flag) + 1]
command = args[:2]
if command == ["issue", "list"]:
    print(json.dumps([issue for issue in state["issues"] if not issue.get("closed")]))
elif command == ["label", "list"]:
    print(json.dumps([{"name": label} for label in state["labels"]]))
elif command == ["label", "create"]:
    state["labels"].append(args[2])
elif command == ["issue", "create"]:
    state["issues"].append({"number": len(state["issues"]) + 1, "title": value("--title"),
                            "body": value("--body"), "comments": []})
elif command in (["issue", "comment"], ["issue", "close"]):
    issue = next(issue for issue in state["issues"] if issue["number"] == int(args[2]))
    if command[1] == "comment":
        issue["comments"].append(value("--body"))
    else:
        issue["closed"] = True
else:
    raise AssertionError("unsupported fake command")
save()
'''


@pytest.fixture
def fake_gh(tmp_path, monkeypatch):
    executable = tmp_path / "gh"
    executable.write_text(f"#!{sys.executable}\n" + FAKE_GH)
    executable.chmod(0o755)
    state = tmp_path / "gh-state.json"
    state.write_text(json.dumps({"calls": [], "issues": [], "labels": []}))
    monkeypatch.setenv("PATH", str(tmp_path) + os.pathsep + os.environ["PATH"])
    monkeypatch.setenv("FAKE_GH_STATE", str(state))
    monkeypatch.setenv("GH_REPO", "synthetic/repository")
    monkeypatch.setenv("GH_TOKEN", "synthetic-secret")
    return state


def health(status="failed", count=2, source_id="jp-synthetic"):
    return {"source_id": source_id, "status": status, "phase": "crawl" if status == "failed" else None,
            "categories": {"http_error": 1} if status == "failed" else {},
            "consecutive_failures": count, "first_failure_at": "2026-11-04T00:00:00+00:00" if count else None}


def run_script(tmp_path, rows, generation=2, *options):
    path = tmp_path / "result.json"
    path.write_text(json.dumps({"generation": generation, "source_health": rows,
                                "url": "https://synthetic.invalid/?token=synthetic-secret",
                                "sources": [{"error": "synthetic-secret"}]}))
    return subprocess.run([sys.executable, str(SCRIPT), str(path), *options], capture_output=True, text=True, check=False)


def test_create_label_and_issue_then_comment_without_duplicate(fake_gh, tmp_path):
    assert run_script(tmp_path, [health()]).returncode == 0
    state = json.loads(fake_gh.read_text())
    assert state["labels"] == ["source-broken"]
    assert len(state["issues"]) == 1
    assert state["issues"][0]["title"] == "[source-broken] jp-synthetic"
    assert "http_error" in state["issues"][0]["body"]
    assert run_script(tmp_path, [health(count=3)], 3).returncode == 0
    state = json.loads(fake_gh.read_text())
    assert len(state["issues"]) == 1 and len(state["issues"][0]["comments"]) == 1
    assert "consecutive_failures: 3" in state["issues"][0]["comments"][0]
    assert state["calls"][0] == ["issue", "list", "--state", "open", "--label", "source-broken",
                                  "--limit", "1000", "--json", "number,title"]


def test_recovery_comments_and_closes_only_matching_source(fake_gh, tmp_path):
    assert run_script(tmp_path, [health(), health(source_id="jp-synthetic2")]).returncode == 0
    assert run_script(tmp_path, [health("ok", 0)], 3).returncode == 0
    issues = json.loads(fake_gh.read_text())["issues"]
    assert issues[0]["comments"] == ["已恢复（第 3 代）"] and issues[0]["closed"]
    assert not issues[1].get("closed") and not issues[1]["comments"]


@pytest.mark.parametrize("row", [health(count=1), health("deadline", 0)])
def test_below_threshold_does_not_call_gh(fake_gh, tmp_path, row):
    assert run_script(tmp_path, [row]).returncode == 0
    assert json.loads(fake_gh.read_text())["calls"] == []


def test_missing_bypasses_threshold_and_custom_threshold(fake_gh, tmp_path):
    assert run_script(tmp_path, [health(count=2)], 2, "--threshold", "3").returncode == 0
    assert json.loads(fake_gh.read_text())["calls"] == []
    assert run_script(tmp_path, [health("missing", 0)], 2, "--threshold", "3").returncode == 0
    assert len(json.loads(fake_gh.read_text())["issues"]) == 1


@pytest.mark.parametrize("command", ["issue list", "label list", "label create", "issue create", "issue comment", "issue close"])
def test_gh_failure_is_counted_and_redacted(fake_gh, tmp_path, command):
    if command in {"issue comment", "issue close"}:
        assert run_script(tmp_path, [health()]).returncode == 0
    state = json.loads(fake_gh.read_text())
    state["fail"] = command
    fake_gh.write_text(json.dumps(state))
    row = health("ok", 0) if command == "issue close" else health()
    response = run_script(tmp_path, [row])
    assert response.returncode == 1
    assert response.stdout == "source_issues failures=1\n" and response.stderr == ""
    assert "synthetic-secret" not in response.stdout


def test_failed_recovery_comment_keeps_issue_open(fake_gh, tmp_path):
    assert run_script(tmp_path, [health()]).returncode == 0
    state = json.loads(fake_gh.read_text())
    state["fail"] = "issue comment"
    fake_gh.write_text(json.dumps(state))
    assert run_script(tmp_path, [health("ok", 0)], 3).returncode == 1
    assert not json.loads(fake_gh.read_text())["issues"][0].get("closed")


def test_text_projects_whitelist_and_drops_untrusted_details():
    row = {**health(), "url": "https://synthetic.invalid/?token=secret", "error": "private body"}
    text = "\n".join(source_issues.issue_text(row, 3))
    assert all(value not in text for value in ("https", "://", "?", "token", "secret", "private body"))
    assert "http_error" in text  # A fixed FetchOutcome category, never an HTTP URL.


@pytest.mark.parametrize("key,value", [
    ("source_id", "jp-test\nhttps://synthetic.invalid/?key=secret"),
    ("phase", "https://synthetic.invalid/"), ("status", "https://synthetic.invalid/"),
    ("categories", {"https://synthetic.invalid/?key=secret": 1}),
    ("categories", {"other": "https://synthetic.invalid/"}),
    ("consecutive_failures", "https://synthetic.invalid/"),
    ("first_failure_at", "https://synthetic.invalid/"),
])
def test_invalid_public_field_never_reaches_gh(fake_gh, tmp_path, key, value):
    row = {**health(), key: value}
    response = run_script(tmp_path, [row])
    assert response.returncode == 1 and response.stdout == "source_issues failures=1\n" and response.stderr == ""
    assert json.loads(fake_gh.read_text())["calls"] == []


@pytest.mark.parametrize("suffix", [" needs repair", ": needs repair", " — needs repair"])
def test_existing_title_suffix_and_existing_label(fake_gh, tmp_path, suffix):
    state = json.loads(fake_gh.read_text())
    state["labels"] = ["source-broken"]
    state["issues"] = [{"number": 7, "title": "[source-broken] jp-synthetic" + suffix, "comments": []}]
    fake_gh.write_text(json.dumps(state))
    assert run_script(tmp_path, [health()]).returncode == 0
    state = json.loads(fake_gh.read_text())
    assert len(state["issues"]) == 1 and len(state["issues"][0]["comments"]) == 1
    assert not any(call[:2] == ["issue", "create"] for call in state["calls"])
