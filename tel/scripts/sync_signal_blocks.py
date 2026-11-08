#!/usr/bin/env python3
"""
Sync the D1 `signal_block` table with the block generators' signal set.

Why this exists: the Worker's staged block sitemap (src/index.js,
SITEMAP_BLOCKS_STAGE) mounts stage 1 = signal blocks only. The signal
definition lives in the generation pipeline — landed records (D1), the
seed lists list_v1 (all) + list_v2 (search_count >= 10), which are FILES
in the generation workspace, and D1 comment / user_report /
third_party_flag numbers — so the Worker cannot recompute it at request
time. This script materializes the set into D1 (migration
0012_signal_block.sql) by IMPORTING the production generators
(~/workspace/tel-datumoj/blocks/gen_blocks_prod.py, which
gen_blocks_fixed.py also builds on) and calling their own prep
functions. Nothing about the signal definition is re-implemented here;
if the generators' definition changes, this table follows automatically
on the next run.

Scope of the block plan the signal set is intersected with — identical
to what has actually been generated as block pages:
  - main scope: mobile / ip050 / free0800 / free0120 / navi0570
    (gen_blocks_prod.build_block_plan over designated allocations);
  - fixed metro scope: fixed prefix_length=6 designated allocations
    whose prefix starts with one of the 21 metro area codes
    (gen_blocks_fixed.build_plan over pull_fixed_allocations).

Usage:
  python3 scripts/sync_signal_blocks.py            # dry-run: compute + report only
  python3 scripts/sync_signal_blocks.py --local    # write the local D1 (wrangler dev)
  python3 scripts/sync_signal_blocks.py --remote   # write production D1 (deploy-time ops)

--local / --remote replace the whole table (DELETE + INSERT in one SQL
file) and verify the row count afterwards. Production writes are never
the default.
"""
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
TEL_DIR = HERE.parent
BLOCKS_DIR = Path(os.environ.get(
    "TEL_BLOCKS_DIR", os.path.expanduser("~/workspace/tel-datumoj/blocks")))
WRANGLER = os.path.expanduser("~/.npm-global/bin/wrangler")
DB_NAME = "tel_datumoj"
BATCH = 200

DDL = """CREATE TABLE IF NOT EXISTS signal_block (
  block_key    TEXT PRIMARY KEY,
  signal_count INTEGER NOT NULL DEFAULT 0,
  updated_at   TEXT NOT NULL DEFAULT ''
);"""


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def load_env_file(path):
    env = {}
    for line in Path(os.path.expanduser(path)).read_text().splitlines():
        if "=" in line and not line.strip().startswith("#"):
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def compute_signal_blocks():
    """Returns (rows, report): rows = [(block_key, signal_count)] sorted."""
    prod = load_module("gen_blocks_prod", BLOCKS_DIR / "gen_blocks_prod.py")
    fixed = load_module("gen_blocks_fixed", BLOCKS_DIR / "gen_blocks_fixed.py")

    main_plan = prod.build_block_plan(prod.pull_allocations())
    main_keys = {p["key"] for p in main_plan}
    _, picked = fixed.pull_fixed_allocations()
    fixed_keys = {p["key"] for p in fixed.build_plan(picked)}
    clash = main_keys & fixed_keys
    if clash:
        raise RuntimeError(f"plan key collision main/fixed: {sorted(clash)[:10]}")
    plan_keys = main_keys | fixed_keys

    landed, comment_nums, report_nums, flags, _ = prod.pull_db_numbers()
    signals, summary = prod.build_signal_set(
        landed, comment_nums, report_nums, flags)

    counts = Counter(d[:7] for d in signals if d[:7] in plan_keys)
    rows = sorted(counts.items())
    report = {
        "signal_summary": summary,
        "plan_pages_main": len(main_keys),
        "plan_pages_fixed": len(fixed_keys),
        "signal_blocks_main": sum(1 for k, _ in rows if k in main_keys),
        "signal_blocks_fixed": sum(1 for k, _ in rows if k in fixed_keys),
        "signal_blocks_total": len(rows),
    }
    return rows, report


def build_sql(rows, updated_at):
    lines = [DDL, "DELETE FROM signal_block;"]
    for i in range(0, len(rows), BATCH):
        vals = ",\n".join(
            f"('{k}', {c}, '{updated_at}')" for k, c in rows[i:i + BATCH])
        lines.append(f"INSERT INTO signal_block VALUES\n{vals};")
    return "\n".join(lines) + "\n"


def wrangler_env():
    env = dict(os.environ)
    cred = Path(os.path.expanduser("~/.cloudflare-2.env"))
    if cred.exists():
        env.update(load_env_file(cred))
    return env


def apply_sql(sql, mode):
    with tempfile.NamedTemporaryFile(
            "w", suffix=".sql", delete=False, dir="/tmp") as f:
        f.write(sql)
        path = f.name
    out = subprocess.run(
        [WRANGLER, "d1", "execute", DB_NAME, f"--{mode}", "--file", path],
        capture_output=True, text=True, cwd=TEL_DIR, env=wrangler_env())
    if out.returncode != 0:
        raise RuntimeError(f"wrangler d1 --{mode} failed: " + out.stderr[-500:])
    out = subprocess.run(
        [WRANGLER, "d1", "execute", DB_NAME, f"--{mode}", "--json",
         "--command", "SELECT COUNT(*) AS n FROM signal_block"],
        capture_output=True, text=True, cwd=TEL_DIR, env=wrangler_env())
    if out.returncode != 0:
        raise RuntimeError("verify query failed: " + out.stderr[-500:])
    return json.loads(out.stdout)[0]["results"][0]["n"]


def main():
    mode = None
    if "--local" in sys.argv:
        mode = "local"
    elif "--remote" in sys.argv:
        mode = "remote"

    rows, report = compute_signal_blocks()
    print(json.dumps(report, ensure_ascii=False, indent=1))
    if rows:
        print(f"first key: {rows[0][0]}  last key: {rows[-1][0]}")
    if mode is None:
        print("dry-run: no D1 writes. Pass --local or --remote to apply.")
        return
    updated_at = datetime.now(timezone(timedelta(hours=9))).isoformat(
        timespec="seconds")
    n = apply_sql(build_sql(rows, updated_at), mode)
    print(f"applied ({mode}): signal_block rows = {n}")
    if n != len(rows):
        raise RuntimeError(f"row count mismatch: wrote {len(rows)}, read {n}")


if __name__ == "__main__":
    main()
