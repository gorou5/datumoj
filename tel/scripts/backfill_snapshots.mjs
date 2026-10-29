#!/usr/bin/env node
// Backfill SSG snapshots (src/lib/snapshot.js) for already-landed Tier 1
// number records. Strategy: warm each number's URL once with a unique
// cache-busting query (forces an origin fetch; the Worker renders via the
// record path and stores the snapshot via waitUntil), then fetch again
// with a different buster and require the `x-snapshot: hit` header as
// proof the R2 serve path works for that number. No JS runs, so the
// /view beacon never fires and view counters are untouched.
//
// Resumable: progress lives in a state file (default
// tel/scripts/backfill_snapshots.state.json, git-ignored); rerunning
// skips numbers already verified. D1 cost per number is only the normal
// record-path validation reads (site_meta + PK record probe + flag
// fingerprint + factsFromRecord's small indexed lookups on the first
// fetch) — on the order of 15 rows, i.e. ~50k rows for the whole
// number_record_2026 Tier 1 set, far inside the free daily read quota.
// R2 writes do not consume D1 quota.
//
// Usage:
//   node scripts/backfill_snapshots.mjs [--limit N] [--concurrency N]
//                                       [--state PATH] [--list-only]
// Credentials for the wrangler D1 listing are read from ~/.cloudflare-2.env
// (values are never printed).

import { execFile } from 'node:child_process';
import { promisify } from 'node:util';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const run = promisify(execFile);
const HERE = path.dirname(fileURLToPath(import.meta.url));
const WRANGLER = process.env.WRANGLER || path.join(os.homedir(), '.npm-global/bin/wrangler');
const BASE = 'https://tel.datumoj.com';

const args = process.argv.slice(2);
const opt = (name, dflt) => {
  const i = args.indexOf(`--${name}`);
  return i >= 0 ? args[i + 1] : dflt;
};
const LIMIT = Number(opt('limit', '0')) || 0;
const CONCURRENCY = Math.max(1, Number(opt('concurrency', '4')) || 4);
const STATE_PATH = opt('state', path.join(HERE, 'backfill_snapshots.state.json'));
const LIST_ONLY = args.includes('--list-only');

function loadEnv() {
  const envFile = path.join(os.homedir(), '.cloudflare-2.env');
  const env = { ...process.env };
  if (fs.existsSync(envFile)) {
    for (const line of fs.readFileSync(envFile, 'utf8').split('\n')) {
      const m = /^([A-Za-z_][A-Za-z0-9_]*)=(.*)$/.exec(line.trim());
      if (m && !env[m[1]]) env[m[1]] = m[2].replace(/^["']|["']$/g, '');
    }
  }
  return env;
}

async function d1(env, sql) {
  const { stdout } = await run(WRANGLER, ['d1', 'execute', 'tel_datumoj', '--remote', '--json', '--command', sql], {
    env,
    maxBuffer: 64 * 1024 * 1024,
  });
  const start = stdout.indexOf('[');
  const parsed = JSON.parse(stdout.slice(start));
  return parsed.flatMap((s) => s.results || []);
}

async function listTargets(env) {
  const tables = await d1(
    env,
    "SELECT name FROM sqlite_master WHERE type = 'table' AND name GLOB 'number_record_[0-9]*' ORDER BY name"
  );
  const digits = [];
  for (const { name } of tables) {
    const rows = await d1(env, `SELECT digits FROM ${name} WHERE tier = 1 ORDER BY digits`);
    digits.push(...rows.map((r) => r.digits));
    console.log(`[list] ${name}: ${rows.length} Tier 1 records`);
  }
  return [...new Set(digits)].sort();
}

function loadState() {
  try {
    return JSON.parse(fs.readFileSync(STATE_PATH, 'utf8'));
  } catch {
    return { verified: {}, skipped: {}, failed: {} };
  }
}
function saveState(state) {
  fs.writeFileSync(STATE_PATH, JSON.stringify(state, null, 1));
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function warm(digits, runId) {
  // First fetch: origin render + background snapshot store.
  const r1 = await fetch(`${BASE}/${digits}?ssgwarm=${runId}a`, { redirect: 'manual' });
  if (r1.status !== 200) return { outcome: 'failed', why: `warm HTTP ${r1.status}` };
  await r1.text();
  // Second fetch (fresh buster → origin again): must be a snapshot hit.
  for (let attempt = 0; attempt < 4; attempt++) {
    await sleep(500 + attempt * 500);
    const r2 = await fetch(`${BASE}/${digits}?ssgwarm=${runId}b${attempt}`, { redirect: 'manual' });
    const marker = r2.headers.get('x-snapshot');
    await r2.text();
    if (r2.status === 200 && marker === 'hit') return { outcome: 'verified' };
    if (r2.status === 200 && marker === null) {
      // No snapshot header at all: the page rendered without the snapshot
      // layer (e.g. tier recomputed below 1 in factsFromRecord).
      return { outcome: 'skipped', why: 'no snapshot header (tier < 1 on render?)' };
    }
    if (r2.status !== 200) return { outcome: 'failed', why: `verify HTTP ${r2.status}` };
  }
  return { outcome: 'failed', why: 'snapshot never hit after retries' };
}

async function main() {
  const env = loadEnv();
  const targets = await listTargets(env);
  console.log(`[list] total Tier 1 targets: ${targets.length}`);
  if (LIST_ONLY) return;

  const state = loadState();
  const pending = targets.filter((d) => !state.verified[d] && !state.skipped[d]);
  const todo = LIMIT ? pending.slice(0, LIMIT) : pending;
  console.log(`[run] already verified ${Object.keys(state.verified).length}, pending ${pending.length}, this run ${todo.length}`);

  const runId = Date.now().toString(36);
  let done = 0;
  let idx = 0;
  async function worker() {
    while (idx < todo.length) {
      const digits = todo[idx++];
      try {
        const res = await warm(digits, runId);
        if (res.outcome === 'verified') state.verified[digits] = new Date().toISOString();
        else if (res.outcome === 'skipped') state.skipped[digits] = res.why;
        else state.failed[digits] = res.why;
      } catch (e) {
        state.failed[digits] = String(e && e.message ? e.message : e);
      }
      done++;
      if (done % 50 === 0 || done === todo.length) {
        saveState(state);
        console.log(
          `[run] ${done}/${todo.length} — verified ${Object.keys(state.verified).length}, skipped ${Object.keys(state.skipped).length}, failed ${Object.keys(state.failed).length}`
        );
      }
    }
  }
  await Promise.all(Array.from({ length: CONCURRENCY }, worker));
  saveState(state);
  const remaining = targets.length - Object.keys(state.verified).length - Object.keys(state.skipped).length;
  console.log(
    `[done] targets ${targets.length} | verified ${Object.keys(state.verified).length} | skipped ${Object.keys(state.skipped).length} | failed ${Object.keys(state.failed).length} | remaining ${remaining}`
  );
  if (Object.keys(state.failed).length) process.exitCode = 1;
}

main().catch((e) => {
  console.error('[fatal]', e);
  process.exit(1);
});
