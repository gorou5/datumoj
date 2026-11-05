#!/usr/bin/env python3
"""Offline input-boundary checks for source_issues.py at the investigated SHA.

No GitHub or storage calls are permitted by this experiment. This does not test
the live runner's result file and is not a project-wide pytest pass.
"""
import argparse
import contextlib
import hashlib
import importlib.util
import io
import tempfile
from pathlib import Path
from unittest.mock import patch


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True)
    args = parser.parse_args()
    path = args.repo / "scripts/source_issues.py"
    data = path.read_bytes().rstrip(b"\n") + b"\n"
    blob = hashlib.sha1(f"blob {len(data)}\0".encode() + data).hexdigest()
    if blob != "9b1da5aaeb4126c331dfce9bf400751a0cd85ec8":
        raise ValueError("source_issues.py differs from investigated SHA f7e665e")
    spec = importlib.util.spec_from_file_location("diagnostic_source_issues", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    cases = [
        ("empty result", "", 1, "source_issues failures=1\n"),
        ("truncated result", '{"status":', 1, "source_issues failures=1\n"),
        ("empty health control", '{"generation":25,"source_health":[]}', 0, ""),
    ]
    with tempfile.TemporaryDirectory() as directory:
        result = Path(directory) / "result.json"
        for label, payload, expected_code, expected_output in cases:
            result.write_text(payload, encoding="utf-8")
            output = io.StringIO()
            with patch.object(module.subprocess, "run", side_effect=AssertionError("network forbidden")) as call:
                with contextlib.redirect_stdout(output):
                    code = module.main([str(result)])
                assert code == expected_code, (label, code)
                assert output.getvalue() == expected_output, (label, output.getvalue())
                assert call.call_count == 0, (label, "unexpected gh call")
            print(f"PASS: {label}; exit={code}; gh calls=0")


if __name__ == "__main__":
    main()
