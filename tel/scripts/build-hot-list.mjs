#!/usr/bin/env node
// Build src/data/hot_numbers.js: the "hot numbers" list behind
// /sitemap-numbers-*.xml and the /popular hub pages.
//
// Data source & methodology (evidence chain):
//   - Demand signal: jpnumber.com public per-number search counts
//     (検索回数, cumulative), harvested 2026-10-27 into
//     ~/workspace/tel-datumoj/seed50k/list_v1.csv
//     (columns: digits,source,metric_name,metric_value; metric_name
//     'search_count' = the jpnumber cumulative search count).
//     The CSV is an ops-side harvest artifact, not repo data; pass its
//     path with --csv or HOT_LIST_CSV.
//   - Tier validation: a harvested number only qualifies when the site's
//     own composition rules (src/lib/compose.js composeNumber) would give
//     it a Tier 1 page via an official designation — i.e. the longest
//     matching block in number_block_allocation is designated, the number
//     length matches its line type, the block carries a carrier name and
//     the matched prefix is 6+ digits (compose.js: designated =
//     carrier_name && prefix.length >= 6). Validation runs locally
//     against a D1 snapshot of the block table so no production reads are
//     burned. Produce the snapshot with:
//       wrangler d1 execute tel_datumoj --remote --json \
//         --command "SELECT b.prefix, b.line_type, b.status, \
//           c.name AS carrier_name, lt.digits AS line_digits, \
//           lt.name_ja AS line_type_name \
//           FROM number_block_allocation b \
//           LEFT JOIN carrier c ON c.id = b.carrier_id \
//           JOIN line_type lt ON lt.code = b.line_type" > blocks_raw.json
//     and (optionally, for fixed-line display formatting on /popular)
//       wrangler d1 execute tel_datumoj --remote --json \
//         --command "SELECT area_code FROM area_code" > area_codes_raw.json
//     Both wrangler --json envelopes and plain row arrays are accepted.
//
// Rollout phases: phase 1 ships search_count >= 100 only (HOT_MIN_COUNT
// default). Later phases lower the threshold / swap in a bigger CSV;
// nothing else in the code changes.
//
// Usage:
//   node scripts/build-hot-list.mjs --blocks blocks_raw.json \
//        [--csv list_v1.csv] [--areas area_codes_raw.json] \
//        [--min 100] [--out src/data/hot_numbers.js] [--report report.json]
import { readFileSync, writeFileSync, mkdirSync } from 'node:fs';
import { homedir } from 'node:os';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const args = {};
for (let i = 2; i < process.argv.length; i += 2) {
  args[process.argv[i].replace(/^--/, '')] = process.argv[i + 1];
}
const here = dirname(fileURLToPath(import.meta.url));
const csvPath = resolve(
  args.csv || process.env.HOT_LIST_CSV || `${homedir()}/workspace/tel-datumoj/seed50k/list_v1.csv`
);
const outPath = resolve(args.out || `${here}/../src/data/hot_numbers.js`);
const minCount = Number(args.min ?? process.env.HOT_MIN_COUNT ?? 100);
if (!args.blocks) {
  console.error('error: --blocks <blocks_raw.json> is required (see header for the wrangler command)');
  process.exit(1);
}

function rowsOf(path) {
  const parsed = JSON.parse(readFileSync(path, 'utf8'));
  // wrangler d1 execute --json wraps rows: [{ results: [...], ... }].
  if (parsed && Array.isArray(parsed[0]?.results)) return parsed[0].results;
  if (Array.isArray(parsed)) return parsed;
  throw new Error(`unrecognized JSON shape in ${path}`);
}

// --- Load demand list -------------------------------------------------------
const csvLines = readFileSync(csvPath, 'utf8').trim().split(/\r?\n/);
const header = csvLines[0].split(',');
const idx = Object.fromEntries(header.map((h, i) => [h, i]));
const candidates = [];
for (const line of csvLines.slice(1)) {
  if (!line.trim()) continue;
  const cols = line.split(',');
  if (cols[idx.metric_name] !== 'search_count') continue;
  const count = Number(cols[idx.metric_value]);
  if (!Number.isFinite(count) || count < minCount) continue;
  candidates.push({ digits: cols[idx.digits], count });
}
// Deterministic order: demand desc, digits asc as tiebreak.
candidates.sort((a, b) => b.count - a.count || (a.digits < b.digits ? -1 : 1));

// --- Load block snapshot ----------------------------------------------------
const blocks = rowsOf(resolve(args.blocks));
const byPrefix = new Map(blocks.map((b) => [b.prefix, b]));
// Longest-prefix probe, mirroring compose.js findBlock (len from full
// length down to 2, exact prefix equality).
function findBlock(digits) {
  for (let len = digits.length; len >= 2; len--) {
    const row = byPrefix.get(digits.slice(0, len));
    if (row) return row;
  }
  return null;
}

let areaCodes = [];
if (args.areas) {
  const raw = rowsOf(resolve(args.areas));
  areaCodes = raw.map((r) => (typeof r === 'string' ? r : r.area_code)).filter(Boolean);
  areaCodes.sort((a, b) => b.length - a.length || (a < b ? -1 : 1));
}
function findAreaCode(digits) {
  for (const ac of areaCodes) if (digits.startsWith(ac)) return ac;
  return null;
}

// --- Validate (compose.js designation path to Tier 1) -----------------------
const accepted = [];
const rejected = { no_block: [], unallocated: [], length_mismatch: [], not_designated: [] };
for (const c of candidates) {
  const block = findBlock(c.digits);
  if (!block) { rejected.no_block.push(c.digits); continue; }
  if (block.status === 'unallocated') { rejected.unallocated.push(c.digits); continue; }
  if (c.digits.length !== Number(block.line_digits)) { rejected.length_mismatch.push(c.digits); continue; }
  // compose.js: designated = !!(carrier_name && prefix.length >= 6); a
  // designated block makes the page Tier 1.
  const designated = !!(block.carrier_name && block.prefix && block.prefix.length >= 6);
  if (!designated) { rejected.not_designated.push(c.digits); continue; }
  accepted.push({
    digits: c.digits,
    count: c.count,
    lineType: block.line_type,
    lineTypeName: block.line_type_name,
    carrierName: block.carrier_name,
    ...(block.line_type === 'fixed' && areaCodes.length
      ? { areaCode: findAreaCode(c.digits) || undefined }
      : {}),
  });
}

const report = {
  csv: csvPath,
  min_search_count: minCount,
  candidates: candidates.length,
  accepted: accepted.length,
  rejected: Object.fromEntries(Object.entries(rejected).map(([k, v]) => [k, v.length])),
  rejected_samples: Object.fromEntries(
    Object.entries(rejected).map(([k, v]) => [k, v.slice(0, 10)])
  ),
};
console.log(JSON.stringify(report, null, 2));
if (args.report) writeFileSync(resolve(args.report), JSON.stringify(report, null, 2) + '\n');

// --- Emit data module -------------------------------------------------------
const body = accepted
  .map(
    (a) =>
      `  { digits: '${a.digits}', count: ${a.count}, lineType: '${a.lineType}', lineTypeName: '${String(a.lineTypeName).replace(/'/g, "\\'")}', carrierName: '${String(a.carrierName).replace(/'/g, "\\'")}'${a.areaCode ? `, areaCode: '${a.areaCode}'` : ''} },`
  )
  .join('\n');
mkdirSync(dirname(outPath), { recursive: true });
writeFileSync(
  outPath,
  `// GENERATED by scripts/build-hot-list.mjs — do not edit by hand.
// Demand signal: jpnumber.com public cumulative search counts
// (検索回数), harvested 2026-10-27 (seed50k/list_v1.csv), phase 1:
// search_count >= ${minCount}. Every entry passed the compose.js Tier 1
// designation check against the number_block_allocation snapshot
// (longest-prefix block designated, carrier named, prefix >= 6 digits).
// Re-run the generator to change the phase threshold or refresh data.
export const HOT_NUMBERS = [
${body}
];
`
);
console.log(`wrote ${accepted.length} entries -> ${outPath}`);
