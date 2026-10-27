// DESIGN v2.0: per-year number record tables (the number foundation).
// A queried number becomes known: its fact snapshot + counters live in
// number_record_<year of first query>. Lookups probe the year tables from
// newest to oldest (primary-key probes, one row each) before composing.
// Snapshots carry a version; when site_meta.snapshot_version moves ahead
// (official data reloaded / compose rules changed), a stale record is
// re-composed and refreshed on its next query. Template content (FAQ etc.)
// is never stored — snapshots hold facts only.

export const FIRST_RECORD_YEAR = 2025;

const RECORD_DDL = (t) => `CREATE TABLE IF NOT EXISTS ${t} (
  digits TEXT PRIMARY KEY,
  line_type TEXT,
  line_type_name TEXT,
  portable INTEGER NOT NULL DEFAULT 0,
  block_prefix TEXT,
  carrier_name TEXT,
  designated_on TEXT,
  block_status TEXT,
  block_note TEXT,
  block_source TEXT,
  area_code TEXT,
  prefecture TEXT,
  mas_json TEXT,
  government_agency TEXT,
  designated INTEGER NOT NULL DEFAULT 0,
  tier INTEGER NOT NULL,
  view_count INTEGER NOT NULL DEFAULT 0,
  comment_count INTEGER NOT NULL DEFAULT 0,
  snapshot_version INTEGER NOT NULL DEFAULT 1,
  first_queried_at TEXT NOT NULL DEFAULT (datetime('now')),
  last_queried_at TEXT NOT NULL DEFAULT (datetime('now'))
)`;

export function currentRecordYear() {
  // JST year (site audience + owner are Japan-based).
  return new Date(Date.now() + 9 * 3600 * 1000).getUTCFullYear();
}

function tableFor(year) {
  const y = Number(year);
  if (!Number.isInteger(y) || y < FIRST_RECORD_YEAR || y > 2200) throw new Error('bad year');
  return `number_record_${y}`;
}

const ensuredYears = new Set();
export async function ensureYearTable(db, year) {
  if (ensuredYears.has(year)) return;
  const t = tableFor(year);
  await db.prepare(RECORD_DDL(t)).run();
  await db.prepare(`CREATE INDEX IF NOT EXISTS idx_${t}_views ON ${t} (view_count DESC)`).run();
  ensuredYears.add(year);
}

export async function getSnapshotVersion(db) {
  try {
    const row = await db.prepare("SELECT value FROM site_meta WHERE key = 'snapshot_version'").first();
    const v = row ? parseInt(row.value, 10) : NaN;
    return Number.isInteger(v) && v > 0 ? v : 1;
  } catch {
    return 1;
  }
}

// Probe year tables newest → oldest. Returns { year, row } or null.
// Missing tables (a year with no records yet) are treated as a miss.
export async function findRecord(db, digits) {
  const now = currentRecordYear();
  for (let y = now; y >= FIRST_RECORD_YEAR; y--) {
    try {
      const row = await db.prepare(`SELECT * FROM ${tableFor(y)} WHERE digits = ?`).bind(digits).first();
      if (row) return { year: y, row };
    } catch {
      /* table does not exist yet */
    }
  }
  return null;
}

function recordValues(facts, version) {
  return [
    facts.digits,
    facts.lineType || null,
    facts.lineTypeName || null,
    facts.portable ? 1 : 0,
    facts.block ? facts.block.prefix : null,
    facts.block ? facts.block.carrierName : null,
    facts.block ? facts.block.designatedOn : null,
    facts.block ? facts.block.status : null,
    facts.block ? facts.block.note : null,
    facts.block ? facts.block.source : null,
    facts.areaCode || null,
    facts.prefecture || null,
    JSON.stringify(facts.mas || []),
    facts.gov ? facts.gov.agency_name : null,
    facts.designated ? 1 : 0,
    facts.tier,
    version,
  ];
}

const INSERT_COLS =
  'digits, line_type, line_type_name, portable, block_prefix, carrier_name, designated_on, block_status, block_note, block_source, area_code, prefecture, mas_json, government_agency, designated, tier, snapshot_version';

// First query for a number: persist its snapshot into the current year's
// table with zeroed counters (the /view beacon raises view_count to 1).
export async function insertRecord(db, facts, version) {
  const year = currentRecordYear();
  await ensureYearTable(db, year);
  await db
    .prepare(`INSERT OR IGNORE INTO ${tableFor(year)} (${INSERT_COLS}) VALUES (${INSERT_COLS.split(',').map(() => '?').join(',')})`)
    .bind(...recordValues(facts, version))
    .run();
  return year;
}

// Refresh a stale snapshot in place (counters and first_queried_at kept).
export async function updateRecordFacts(db, year, facts, version) {
  const v = recordValues(facts, version);
  await db
    .prepare(
      `UPDATE ${tableFor(year)} SET line_type=?, line_type_name=?, portable=?, block_prefix=?, carrier_name=?, designated_on=?, block_status=?, block_note=?, block_source=?, area_code=?, prefecture=?, mas_json=?, government_agency=?, designated=?, tier=?, snapshot_version=?, last_queried_at=datetime('now') WHERE digits=?`
    )
    .bind(v[1], v[2], v[3], v[4], v[5], v[6], v[7], v[8], v[9], v[10], v[11], v[12], v[13], v[14], v[15], v[16], facts.digits)
    .run();
}

// Rebuild a facts object from a stored snapshot. Overlays that change
// independently of the official designation data (government detail,
// business registration, user reports, third-party flags) are re-read live
// with small indexed lookups, and the tier is recomputed with the same
// formula composeNumber uses, so a record page never goes stale where it
// matters. FAQ / explanations are template-rendered from these facts.
export async function factsFromRecord(db, row) {
  const facts = {
    digits: row.digits,
    lineType: row.line_type,
    lineTypeName: row.line_type_name,
    portable: !!row.portable,
    block: {
      prefix: row.block_prefix,
      carrierName: row.carrier_name,
      designatedOn: row.designated_on,
      status: row.block_status,
      note: row.block_note,
      source: row.block_source,
    },
    areaCode: row.area_code || undefined,
    prefecture: row.prefecture || undefined,
    mas: [],
    designated: !!row.designated,
    viewCount: row.view_count,
    commentCount: row.comment_count,
  };
  try {
    facts.mas = JSON.parse(row.mas_json || '[]');
  } catch {
    facts.mas = [];
  }
  const govAll = await db
    .prepare('SELECT * FROM v_government_number_detail WHERE number = ? ORDER BY agency_name')
    .bind(row.digits)
    .all();
  facts.govList = govAll.results || [];
  facts.gov = facts.govList[0] || null;
  facts.business = await db.prepare('SELECT * FROM business_registration WHERE number = ?').bind(row.digits).first();
  const rep = await db
    .prepare(
      `SELECT category, COUNT(*) AS n, MAX(created_at) AS latest
         FROM user_report WHERE number = ? AND status = 'approved'
        GROUP BY category ORDER BY n DESC`
    )
    .bind(row.digits)
    .all();
  facts.reports = rep.results || [];
  facts.reportCount = facts.reports.reduce((a, r) => a + r.n, 0);
  const flags = await db
    .prepare(
      `SELECT * FROM v_third_party_flag_detail
        WHERE number = ? AND status = 'active'
        ORDER BY source_tier, published_on DESC`
    )
    .bind(row.digits)
    .all();
  facts.tpFlags = flags.results || [];
  const hasABFlag = facts.tpFlags.some((f) => f.source_tier === 'A' || f.source_tier === 'B');
  facts.tier = facts.gov || facts.business || facts.reportCount > 0 || hasABFlag || facts.designated ? 1 : 2;
  return facts;
}

// The /view beacon: raise the counter on the record row itself. Returns the
// new count, or null when the number has no record yet.
export async function bumpRecordView(db, digits) {
  const rec = await findRecord(db, digits);
  if (!rec) return null;
  await db
    .prepare(`UPDATE ${tableFor(rec.year)} SET view_count = view_count + 1, last_queried_at = datetime('now') WHERE digits = ?`)
    .bind(digits)
    .run();
  const row = await db.prepare(`SELECT view_count FROM ${tableFor(rec.year)} WHERE digits = ?`).bind(digits).first();
  return row ? row.view_count : null;
}

export async function bumpRecordComment(db, digits) {
  const rec = await findRecord(db, digits);
  if (!rec) return;
  await db
    .prepare(`UPDATE ${tableFor(rec.year)} SET comment_count = comment_count + 1 WHERE digits = ?`)
    .bind(digits)
    .run();
}

// Names of the record tables that actually exist, newest year first.
// Discovered from sqlite_master so years with no table yet are skipped
// without probing blindly.
export async function listRecordTables(db) {
  const r = await db
    .prepare("SELECT name FROM sqlite_master WHERE type = 'table' AND name GLOB 'number_record_[0-9]*'")
    .all();
  const years = [];
  for (const row of r.results || []) {
    const m = /^number_record_(\d{4})$/.exec(row.name);
    if (!m) continue;
    const y = Number(m[1]);
    if (y >= FIRST_RECORD_YEAR && y <= 2200) years.push(y);
  }
  return years.sort((a, b) => b - a).map((y) => tableFor(y));
}

// Draw one random Tier 1 record for the homepage hero. The record pool is
// the site's real query history, so the line-type mix is whatever people
// actually looked up — no synthetic balancing. Tables are tried in random
// order so no single year dominates; each table holds at most a few
// thousand rows, so ORDER BY RANDOM() stays a handful of reads.
export async function drawRandomRecord(db) {
  const tables = await listRecordTables(db);
  for (let i = tables.length - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1));
    [tables[i], tables[j]] = [tables[j], tables[i]];
  }
  for (const t of tables) {
    const row = await db.prepare(`SELECT * FROM ${t} WHERE tier = 1 ORDER BY RANDOM() LIMIT 1`).first();
    if (row) return row;
  }
  return null;
}

// Facts rebuilt from the stored snapshot alone (no live overlay lookups):
// the hero explanation renders entirely from fields the record already
// carries — block prefix / carrier / area code / MA list — so drawing a
// hero costs no composition and no extra reads beyond the draw itself.
export function factsFromRecordSnapshot(row) {
  let mas = [];
  try {
    mas = JSON.parse(row.mas_json || '[]');
  } catch {
    mas = [];
  }
  return {
    digits: row.digits,
    lineType: row.line_type,
    lineTypeName: row.line_type_name,
    portable: !!row.portable,
    block: {
      prefix: row.block_prefix,
      carrierName: row.carrier_name,
      designatedOn: row.designated_on,
      status: row.block_status,
      note: row.block_note,
      source: row.block_source,
    },
    areaCode: row.area_code || undefined,
    prefecture: row.prefecture || undefined,
    mas,
    designated: !!row.designated,
    tier: row.tier,
    viewCount: row.view_count,
    commentCount: row.comment_count,
  };
}

// Most-queried numbers across all year tables (real demand, replaces the
// old number_view ranking). Returns [{ number, views }].
export async function popularFromRecords(db, limit = 6) {
  const now = currentRecordYear();
  const parts = [];
  for (let y = now; y >= FIRST_RECORD_YEAR; y--) parts.push(`SELECT digits AS number, view_count AS views FROM ${tableFor(y)}`);
  try {
    const r = await db
      .prepare(`SELECT number, views FROM (${parts.join(' UNION ALL ')}) WHERE views > 0 ORDER BY views DESC, number ASC LIMIT ?`)
      .bind(limit)
      .all();
    return r.results || [];
  } catch {
    return [];
  }
}
