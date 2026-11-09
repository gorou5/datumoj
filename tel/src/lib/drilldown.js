// Drill-down pages: prefix indexes (/tel/<2-6 digit prefix>), hundred-
// group pages (/tel/<number-length-2 digit prefix>) and the ranges hub
// (/tel/ranges) — all composed at request time by the Worker.
//
// Ground truth for "which block pages exist" is the same bucket manifest
// the number pages use (lib/block_manifest.js): a prefix page exists
// exactly when generated block pages sit beneath it, and a hundred-group
// page exists exactly when its parent 7-digit block page exists. Facts
// (line types, carriers, landed numbers, flags) come from D1 — the same
// sources and counting rules as the pre-generated 区段索引 salt in
// scripts/gen_blocks_index.py, whose family table is ported below so the
// Worker-rendered hub can never drift from the generator's口径.

import { getBlockKeys } from './block_manifest.js';
import { findBlock, findAreaCode, composeNumber } from './compose.js';
import { formatNumber, splitNumber } from './number.js';
import { randomHero } from './render.js';
import { FIRST_RECORD_YEAR, currentRecordYear } from './record.js';

// --- Family table (port of scripts/gen_blocks_index.py) ---------------------

export const AREA_CITY = [
  ['011', '札幌'], ['022', '仙台'], ['048', 'さいたま'], ['043', '千葉'],
  ['03', '東京'], ['045', '横浜'], ['044', '川崎'], ['042', '相模原'],
  ['025', '新潟'], ['054', '静岡'], ['053', '浜松'], ['052', '名古屋'],
  ['075', '京都'], ['06', '大阪'], ['072', '堺'], ['078', '神戸'],
  ['086', '岡山'], ['082', '広島'], ['093', '北九州'], ['092', '福岡'],
  ['096', '熊本'],
];

export const SECTIONS = [
  ['携帯電話', [['060', '携帯電話 060'], ['070', '携帯電話 070'], ['080', '携帯電話 080'], ['090', '携帯電話 090']]],
  ['IP電話', [['050', 'IP電話 050']]],
  ['フリーダイヤル', [['0120', 'フリーダイヤル 0120'], ['0800', 'フリーダイヤル 0800']]],
  ['ナビダイヤル', [['0570', 'ナビダイヤル 0570']]],
  ['固定電話', AREA_CITY.map(([code, city]) => [code, `固定電話 ${code}（${city}）`])],
];

export const FAMILIES = new Map();
for (const [, fams] of SECTIONS) for (const [id, label] of fams) FAMILIES.set(id, label);

// Longest/most-specific prefixes first ("0800" before "080", "060"
// before "06") — the collision-avoidance order of the generator.
const PREFIX_ORDER = [
  '0120', '0800', '0570', '060', '070', '080', '090', '050',
  ...AREA_CITY.map(([c]) => c).filter((c) => c.length === 3),
  ...AREA_CITY.map(([c]) => c).filter((c) => c.length === 2),
];

export function classify(key) {
  for (const p of PREFIX_ORDER) if (key.startsWith(p)) return p;
  return null;
}

export const FAMILY_LINE_TYPE = {
  '060': 'mobile', '070': 'mobile', '080': 'mobile', '090': 'mobile',
  '050': 'ip050', '0120': 'free0120', '0800': 'free0800', '0570': 'navi0570',
};
for (const [code] of AREA_CITY) FAMILY_LINE_TYPE[code] = 'fixed';

export const LINE_TYPE_NAME = {
  mobile: '携帯電話', ip050: 'IP電話', free0120: 'フリーダイヤル',
  free0800: 'フリーダイヤル', navi0570: 'ナビダイヤル', fixed: '固定電話',
};
// Numbers per block page, by line type （万号页 / 千号页）.
export const LINE_TYPE_SPAN = {
  mobile: 10000, ip050: 10000, free0800: 10000,
  free0120: 1000, navi0570: 1000, fixed: 1000,
};
// Allocation prefix length at which carrier designations are recorded.
const ALLOC_PLEN = { mobile: 6, ip050: 7, free0800: 7, free0120: 7, navi0570: 7, fixed: 6 };

// --- Formatting ---------------------------------------------------------------

export function fmtKey(key, plen) {
  return `${key.slice(0, plen)}-${key.slice(plen)}`;
}

// Parenthesised notation: (050)1807-0466 — first display segment in
// parentheses, the rest hyphen-joined as usual.
export function parenFormat(digits, facts) {
  const parts = splitNumber(digits, facts);
  if (parts.length < 2) return digits;
  return `(${parts[0]})${parts.slice(1).join('-')}`;
}

// --- Manifest-derived structure ----------------------------------------------

export function keysUnder(keys, prefix) {
  const out = [];
  for (const k of keys) if (k.startsWith(prefix)) out.push(k);
  return out.sort();
}

// Distinct child prefixes one digit longer than `prefix`, sorted. When
// prefix.length is 6 the children are the 7-digit block keys themselves.
export function childPrefixes(keys, prefix) {
  const next = new Set();
  for (const k of keys) {
    if (k.startsWith(prefix)) next.add(k.slice(0, prefix.length + 1));
  }
  return [...next].sort();
}

// Per-family statistics over a key list, salt口径: block counts from the
// manifest, number counts = blocks × span, carriers counted per block key
// through the D1 allocation map (carrierMaps from loadCarrierMaps).
export function familyStats(keys, carrierMaps) {
  const byFamily = new Map();
  for (const k of keys) {
    const fid = classify(k);
    if (!fid) continue;
    if (!byFamily.has(fid)) byFamily.set(fid, { blocks: 0, carriers: new Map() });
    const st = byFamily.get(fid);
    st.blocks += 1;
    const lt = FAMILY_LINE_TYPE[fid];
    const map = carrierMaps[lt] || {};
    const carrier = map[k] || map[k.slice(0, 6)] || '';
    if (carrier) st.carriers.set(carrier, (st.carriers.get(carrier) || 0) + 1);
  }
  for (const [fid, st] of byFamily) {
    st.lineType = FAMILY_LINE_TYPE[fid];
    st.numbers = st.blocks * LINE_TYPE_SPAN[st.lineType];
    st.topCarriers = [...st.carriers.entries()].sort((a, b) => b[1] - a[1]).slice(0, 3);
  }
  return byFamily;
}

// D1 allocation maps: line type -> { allocation prefix: carrier name },
// at the designation granularity of ALLOC_PLEN (the generator's salt
// does exactly the same six queries).
export async function loadCarrierMaps(db) {
  const maps = {};
  for (const [lt, plen] of Object.entries(ALLOC_PLEN)) {
    try {
      const r = await db
        .prepare(
          `SELECT a.prefix AS prefix, c.name AS carrier FROM number_block_allocation a
             LEFT JOIN carrier c ON c.id = a.carrier_id
            WHERE a.line_type = ? AND a.prefix_length = ? AND a.status = 'designated'`
        )
        .bind(lt, plen)
        .all();
      const map = {};
      for (const row of r.results || []) map[row.prefix] = row.carrier || '';
      maps[lt] = map;
    } catch {
      maps[lt] = {};
    }
  }
  return maps;
}

// --- Explanations (homepage seg texts, relocated) -----------------------------
//
// The homepage used to carry the segment explanations in its .seg-explain
// card; they now live on the drill-down pages. The texts are reused
// verbatim from the hero payload (render.js randomHero): for a prefix we
// compose one representative real number beneath it and pick the segment
// whose role matches the page's level — band explanations on band-level
// pages, subscriber explanations on deep prefixes and group pages. No
// representative (or a failed compose) means no explanation, never a
// fabricated one.

async function representativeFacts(db, sampleDigits) {
  try {
    const facts = await composeNumber(db, sampleDigits);
    return facts || null;
  } catch {
    return null;
  }
}

export function pickExplain(hero, facts, prefixLength, mode) {
  if (!hero || !hero.segments || !hero.segments.length) return null;
  const segs = hero.segments;
  if (mode === 'group') {
    // Subscriber-number segment: fixed heroes carry it last (anatomy
    // split), every other type as segment 1/2 (identical texts).
    const seg = facts.lineType === 'fixed' ? segs[segs.length - 1] : segs[Math.min(1, segs.length - 1)];
    return seg ? { title: seg.title, explain: seg.explain } : null;
  }
  if (facts.lineType === 'fixed') {
    const areaLen = facts.areaCode ? facts.areaCode.length : 2;
    const seg = prefixLength <= areaLen ? segs[1] : segs[2];
    return seg ? { title: seg.title, explain: seg.explain } : null;
  }
  const bandLen = segs[0].text.length;
  const seg = prefixLength <= bandLen ? segs[0] : segs[Math.min(1, segs.length - 1)];
  return seg ? { title: seg.title, explain: seg.explain } : null;
}

// --- Range overlays for group pages -------------------------------------------

async function landedInRange(db, lo, hi) {
  const out = new Set();
  const now = currentRecordYear();
  for (let y = now; y >= FIRST_RECORD_YEAR; y--) {
    try {
      const r = await db
        .prepare(`SELECT digits FROM number_record_${y} WHERE digits >= ? AND digits <= ?`)
        .bind(lo, hi)
        .all();
      for (const row of r.results || []) out.add(row.digits);
    } catch {
      // year table does not exist yet — same tolerance as the sitemap scan
    }
  }
  return out;
}

async function flaggedInRange(db, lo, hi) {
  try {
    const r = await db
      .prepare(`SELECT DISTINCT number FROM third_party_flag WHERE number >= ? AND number <= ? AND status = 'active'`)
      .bind(lo, hi)
      .all();
    return new Set((r.results || []).map((row) => row.number));
  } catch {
    return new Set();
  }
}

// --- Page data builders (render-ready, consumed by lib/render.js) --------------

// /tel/ranges — the 区段索引 hub. Sections and family counts mirror the
// pre-generated index page; family links now point at /tel/<family>.
export async function buildRangesData(env, db, signalKeys) {
  const keys = await getBlockKeys(env);
  if (!keys) return null;
  const carrierMaps = await loadCarrierMaps(db);
  const stats = familyStats([...keys], carrierMaps);
  const signalByFamily = new Map();
  for (const k of signalKeys || []) {
    const fid = classify(k);
    if (fid) signalByFamily.set(fid, (signalByFamily.get(fid) || 0) + 1);
  }
  const sections = [];
  for (const [name, fams] of SECTIONS) {
    const famRows = [];
    const agg = { blocks: 0, numbers: 0, signals: 0, carriers: new Map() };
    for (const [fid, label] of fams) {
      const st = stats.get(fid);
      if (!st) continue;
      famRows.push({ id: fid, label, blocks: st.blocks });
      agg.blocks += st.blocks;
      agg.numbers += st.numbers;
      agg.signals += signalByFamily.get(fid) || 0;
      for (const [c, n] of st.carriers) agg.carriers.set(c, (agg.carriers.get(c) || 0) + n);
    }
    if (!famRows.length) continue;
    const top = [...agg.carriers.entries()].sort((a, b) => b[1] - a[1]).slice(0, 3);
    sections.push({ name, families: famRows, agg: { ...agg, topCarriers: top } });
  }
  return { total: keys.size, sections };
}

// Shared facts block for prefix pages (salt口径 over the keys beneath).
function prefixFacts(keys, prefix, carrierMaps) {
  const under = keysUnder(keys, prefix);
  if (!under.length) return null;
  const stats = familyStats(under, carrierMaps);
  let blocks = 0;
  let numbers = 0;
  const carriers = new Map();
  const typeRows = [];
  for (const [, st] of stats) {
    blocks += st.blocks;
    numbers += st.numbers;
    typeRows.push({ name: LINE_TYPE_NAME[st.lineType], blocks: st.blocks, numbers: st.numbers });
    for (const [c, n] of st.carriers) carriers.set(c, (carriers.get(c) || 0) + n);
  }
  const topCarriers = [...carriers.entries()].sort((a, b) => b[1] - a[1]).slice(0, 3);
  return { under, blocks, numbers, typeRows, topCarriers };
}

function crumbsFor(prefix, family) {
  const crumbs = [
    { label: 'ホーム', href: '/tel/' },
    { label: '区段インデックス', href: '/tel/ranges' },
  ];
  const start = family ? family.length : 2;
  for (let len = start; len < prefix.length; len++) {
    const p = prefix.slice(0, len);
    crumbs.push({ label: FAMILIES.get(p) || p, href: `/tel/${p}` });
  }
  const famLabel = FAMILIES.get(prefix);
  crumbs.push({ label: famLabel || prefix, href: null });
  return crumbs;
}

// /tel/<2-6 digit prefix> — prefix index. `groupChildren` switches the
// child list from manifest-derived prefixes to hundred groups (used for
// 8-digit prefixes of 11-digit families, whose children are not in the
// manifest because they are not block keys).
export async function buildPrefixData(env, db, prefix, groupChildren = null) {
  const keys = await getBlockKeys(env);
  if (!keys) return null;
  const carrierMaps = await loadCarrierMaps(db);
  let facts;
  if (groupChildren) {
    // 8-digit prefix of an 11-digit family: no manifest key sits *under*
    // the prefix (keys are 7 digits), so existence and facts anchor on
    // the containing block; the number count is scoped to the prefix
    // itself (10^(line digits - prefix length) numbers beneath it).
    const blockKey = prefix.slice(0, 7);
    if (!keys.has(blockKey)) return null;
    const block = await findBlock(db, prefix).catch(() => null);
    if (!block) return null;
    facts = prefixFacts(keys, blockKey, carrierMaps);
    if (!facts) return null;
    const span = 10 ** (block.line_digits - prefix.length);
    facts = { ...facts, numbers: span, typeRows: facts.typeRows.map((t) => ({ ...t, numbers: span })) };
  } else {
    facts = prefixFacts(keys, prefix, carrierMaps);
    if (!facts) return null;
  }
  const { under } = facts;
  const family = classify(under[0]);
  const singleFamily = under.every((k) => classify(k) === family);

  let children;
  let childKind;
  if (groupChildren) {
    children = groupChildren;
    childKind = 'group';
  } else if (prefix.length === 6) {
    children = under.map((k) => ({ href: `/tel/${k}`, label: fmtKey(k, family ? family.length : 3) }));
    childKind = 'block';
  } else {
    children = childPrefixes(keys, prefix).map((c) => ({
      href: `/tel/${c}`,
      label: FAMILIES.get(c) || c,
    }));
    childKind = 'prefix';
  }

  let explain = null;
  if (singleFamily) {
    const block = await findBlock(db, under[0]).catch(() => null);
    if (block) {
      const sample = under[0] + '0'.repeat(block.line_digits - 7);
      const repFacts = await representativeFacts(db, sample);
      if (repFacts) explain = pickExplain(randomHero(repFacts), repFacts, prefix.length, 'prefix');
    }
  }

  const label = FAMILIES.get(prefix) || `${prefix} の区段`;
  return {
    kind: 'prefix',
    prefix,
    label,
    crumbs: crumbsFor(prefix, singleFamily ? family : null),
    blocks: facts.blocks,
    numbers: facts.numbers,
    typeRows: facts.typeRows,
    topCarriers: facts.topCarriers,
    children,
    childKind,
    explain,
  };
}

// /tel/<(number length - 2) or (number length - 1) digit prefix> — the
// hundred-group page (and its 10-number sibling one level deeper). The
// parent block (first 7 digits) must positively exist in the manifest.
export async function buildGroupData(env, db, prefix) {
  const keys = await getBlockKeys(env);
  if (!keys) return null;
  const blockKey = prefix.slice(0, 7);
  if (!keys.has(blockKey)) return null;
  const block = await findBlock(db, prefix).catch(() => null);
  if (!block) return null;
  const totalLen = block.line_digits;
  if (prefix.length < totalLen - 2 || prefix.length > totalLen - 1) return null;
  const areaCode = block.line_type === 'fixed' ? await findAreaCode(db, prefix).catch(() => null) : null;
  const fmtFacts = { lineType: block.line_type, areaCode };
  const lo = prefix + '0'.repeat(totalLen - prefix.length);
  const hi = prefix + '9'.repeat(totalLen - prefix.length);
  const [landed, flagged] = await Promise.all([
    landedInRange(db, lo, hi),
    flaggedInRange(db, lo, hi),
  ]);
  const numbers = [];
  const count = totalLen - prefix.length === 2 ? 100 : 10;
  for (let i = 0; i < count; i++) {
    const d = prefix + String(i).padStart(totalLen - prefix.length, '0');
    numbers.push({
      digits: d,
      formatted: formatNumber(d, fmtFacts),
      landed: landed.has(d),
      flagged: flagged.has(d),
    });
  }
  const family = classify(blockKey);
  const repFacts = await representativeFacts(db, lo);
  const explain = repFacts ? pickExplain(randomHero(repFacts), repFacts, prefix.length, 'group') : null;
  return {
    kind: 'group',
    prefix,
    blockKey,
    blockLabel: fmtKey(blockKey, family ? family.length : 3),
    family,
    familyLabel: family ? FAMILIES.get(family) : null,
    lo,
    hi,
    fmtLo: formatNumber(lo, fmtFacts),
    fmtHi: formatNumber(hi, fmtFacts),
    parenLo: parenFormat(lo, fmtFacts),
    parenHi: parenFormat(hi, fmtFacts),
    lineTypeName: block.line_type_name,
    carrierName: block.carrier_name,
    numbers,
    explain,
  };
}

// Children of an 8-digit prefix in an 11-digit family: its ten hundred
// groups, labelled by range like the block pages' group links.
export async function hundredGroupChildren(db, prefix) {
  const block = await findBlock(db, prefix).catch(() => null);
  if (!block) return null;
  const areaCode = block.line_type === 'fixed' ? await findAreaCode(db, prefix).catch(() => null) : null;
  const fmtFacts = { lineType: block.line_type, areaCode };
  const out = [];
  for (let d = 0; d < 10; d++) {
    const g = prefix + d;
    const lo = g + '00';
    const hi = g + '99';
    out.push({
      href: `/tel/${g}`,
      label: `${formatNumber(lo, fmtFacts)}〜${formatNumber(hi, fmtFacts).split('-').pop()}`,
    });
  }
  return out;
}
