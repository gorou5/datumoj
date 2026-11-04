"""Sync public source-health issues via gh; no source text or gh output is logged.

Usage: python scripts/source_issues.py RESULT.json [--threshold 2]
GH_REPO and GH_TOKEN select/authenticate the repository. Requires Python 3.12+
and gh; returns 1 on input/gh failure, leaving publication to the caller.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from datetime import datetime
from pathlib import Path

LABEL = "source-broken"


def issue_text(health, generation):
    """Validate and project only the public fields, then render all issue text."""
    source_id = health["source_id"]
    if not isinstance(source_id, str) or not re.fullmatch(r"[a-z]{2,4}(?:-[a-z0-9]+)+", source_id):
        raise ValueError("invalid_source")
    status, phase = health["status"], health["phase"]
    if status not in {"ok", "failed", "deadline", "missing"} or phase not in {
        None, "crawl", "parse_index", "embedding", "schedule",
    }:
        raise ValueError("invalid_state")
    count = health["consecutive_failures"]
    if type(count) is not int or count < 0 or type(generation) is not int or generation < 1:
        raise ValueError("invalid_count")
    categories = {}
    for category, value in health["categories"].items():
        if not isinstance(category, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,79}", category):
            raise ValueError("invalid_category")
        if type(value) is not int or value < 0:
            raise ValueError("invalid_count")
        categories[category] = value
    first = health["first_failure_at"]
    if first is not None:
        # Render a parsed datetime, never the original untrusted value.
        first = datetime.fromisoformat(first).isoformat()
    title = f"[{LABEL}] {source_id}"
    body = (f"source_id: {source_id}\nstatus: {status}\nphase: {phase}\n"
            f"categories: {json.dumps(categories, sort_keys=True)}\n"
            f"consecutive_failures: {count}\nfirst_failure_at: {first}\n第 {generation} 代")
    comment = f"已恢复（第 {generation} 代）" if status == "ok" else body
    return title, body, comment


class GitHub:
    def __init__(self):
        self.failures = 0

    def call(self, *args, read_json=False):
        try:
            result = subprocess.run(
                ["gh", *args], stdin=subprocess.DEVNULL, capture_output=True,
                text=True, timeout=30, check=True,
            )
            return json.loads(result.stdout) if read_json else True
        except (OSError, subprocess.SubprocessError, ValueError):
            self.failures += 1
            return None


def sync_issues(result, threshold=2):
    if type(threshold) is not int or threshold < 1:
        raise ValueError("invalid_threshold")
    # Validate every rendered field before any network operation. Ignore all
    # other result keys, including sources, exception details and storage data.
    entries = [(row, issue_text(row, result["generation"])) for row in result["source_health"]]
    active = [(row, text) for row, text in entries
              if row["status"] == "ok" or row["consecutive_failures"] >= threshold]
    if not active:
        return 0
    gh = GitHub()
    issues = gh.call("issue", "list", "--state", "open", "--label", LABEL,
                     "--limit", "1000", "--json", "number,title", read_json=True)
    if issues is None:
        return gh.failures
    if not isinstance(issues, list) or any(
        not isinstance(issue, dict) or type(issue.get("number")) is not int or issue["number"] < 1
        or not isinstance(issue.get("title"), str) for issue in issues
    ):
        return gh.failures + 1
    label_ready = False
    for row, (title, body, comment) in active:
        matching = [issue for issue in issues if re.match(re.escape(title) + r"(?:$|[^a-z0-9-])", issue["title"])]
        if row["status"] == "ok":
            for issue in matching:
                number = str(issue["number"])
                if gh.call("issue", "comment", number, "--body", comment):
                    gh.call("issue", "close", number)
        elif matching:
            for issue in matching:
                gh.call("issue", "comment", str(issue["number"]), "--body", comment)
        else:
            if not label_ready:
                labels = gh.call("label", "list", "--limit", "1000", "--json", "name", read_json=True)
                if labels is None:
                    continue
                if not isinstance(labels, list) or any(not isinstance(label, dict) for label in labels):
                    gh.failures += 1
                    continue
                label_ready = any(label.get("name") == LABEL for label in labels)
                if not label_ready:
                    label_ready = bool(gh.call("label", "create", LABEL, "--color", "B60205",
                                               "--description", "来源连续失败或缺少成功运行"))
            if label_ready:
                gh.call("issue", "create", "--label", LABEL, "--title", title, "--body", body)
    return gh.failures


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("result", type=Path)
    parser.add_argument("--threshold", type=int, default=2)
    args = parser.parse_args(argv)
    try:
        failures = sync_issues(json.loads(args.result.read_text(encoding="utf-8")), args.threshold)
    except Exception:  # noqa: BLE001 -- Public job boundary must never expose input or exception text.
        failures = 1
    if failures:
        print(f"source_issues failures={failures}")
    return int(bool(failures))


if __name__ == "__main__":
    raise SystemExit(main())
