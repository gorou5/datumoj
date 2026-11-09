// datumoj.com/tel — Japanese phone-number database (Worker entry).
// Pages are composed at request time from sharded range tables in D1
// (see DESIGN.md / migrations/0001_init.sql). Hub pages are indexable;
// number pages are tiered (Tier 1 index / Tier 2 noindex / Tier 3 = 404).
import { normalize, splitNumber } from './lib/number.js';
import { bumpView, listComments, addComment, commentProblem, COMMENT_MESSAGES, hashIp, searchComments, listLatestComments } from './lib/social.js';
import {
  composeNumber, analyzeFallback, listPrefectures, listAreaCodes, getAreaHub,
  listLineTypes, getLineTypeHub, listTpSources, allAreaCodes,
  listGovAgencies, listGovNumbers, govNumbersByAgency, findAreaCode, findBlock,
} from './lib/compose.js';
import {
  homePage, numberPage, areaPage, prefPage, typePage, aboutPage, notFoundPage, govPage, notAllocatedPage, historyPage, randomHero, FALLBACK_HEROES, unavailablePage, popularPage, commentSearchPage,
  rangesPage, prefixPage, groupPage,
} from './lib/render.js';
import {
  findRecord, insertRecord, updateRecordFacts, factsFromRecord, getSnapshotVersion,
  bumpRecordView, bumpRecordComment, popularFromRecords,
  FIRST_RECORD_YEAR, currentRecordYear,
  drawRandomRecord, factsFromRecordSnapshot,
} from './lib/record.js';
import { HOT_NUMBERS } from './data/hot_numbers.js';
import { faviconPngBytes, appleTouchIconBytes } from './lib/favicon.js';
import { flagFingerprint, getSnapshot, putSnapshot } from './lib/snapshot.js';
import { getR2Object } from './lib/r2.js';
import { blockPageExists } from './lib/block_manifest.js';
import { buildRangesData, buildPrefixData, buildGroupData, hundredGroupChildren } from './lib/drilldown.js';

const HTML = { 'content-type': 'text/html; charset=utf-8' };
const CACHE_HUB = 'public, max-age=3600, s-maxage=86400';
const CACHE_NUM = 'public, max-age=3600, s-maxage=604800';

function html(body, status = 200, cache = CACHE_HUB, extra = {}) {
  return new Response(body, { status, headers: { ...HTML, 'cache-control': cache, ...extra } });
}

// Draw one random number from the record pool — numbers real visitors
// have actually queried (DESIGN v2.0 per-year record tables) — and build
// its hero payload from the stored snapshot. No synthetic candidate is
// composed any more, so every hero is a real, fully-explained number page.
// Never throws: returns null when D1 is unavailable (e.g. free-tier daily
// quota exhausted), so callers can fall back to FALLBACK_HEROES and the
// homepage hero never dead-ends on placeholders.
async function drawRandomHero(db) {
  try {
    const row = await drawRandomRecord(db);
    if (row) return randomHero(factsFromRecordSnapshot(row));
  } catch {
    return null;
  }
  return null;
}

// Number-page render with the block back-link resolved first:
// facts.blockExists comes from the cached bucket manifest
// (lib/block_manifest.js), so the 所属区段 link is rendered only when the
// block page really exists — never as a guess. Snapshot hits bypass this
// entirely; the decision is baked into the HTML at compose time.
async function renderNumberPage(env, facts) {
  facts.blockExists = await blockPageExists(env, String(facts.digits || '').slice(0, 7));
  return numberPage(facts);
}

function pickFallbackHero() {
  return FALLBACK_HEROES[Math.floor(Math.random() * FALLBACK_HEROES.length)];
}

// --- Sitemaps ---------------------------------------------------------------
// /sitemap.xml is an index: /sitemap-main.xml carries the site structure
// (home, hubs, government numbers, the /popular hub), and
// /sitemap-numbers-N.xml shards carry number pages: the hot list
// (src/data/hot_numbers.js — demand-verified, Tier 1 validated) first in
// demand order, then every Tier 1 digit already laid down in the per-year
// record tables, in digit order. Shards stay under the 50k protocol limit.
//
// /sitemap-blocks-N.xml shards carry block pages (flat /tel/<7-digit key>)
// in a staged rollout — the stages, in order:
//   1: signal blocks — blocks holding at least one signal number. The key
//      set is read from the D1 signal_block table, which the generation
//      pipeline refreshes with the exact signal computation that renders
//      signal numbers as real links on the block pages (scripts/
//      sync_signal_blocks.py) — sitemap and pages share one source.
//   2: every mobile block.  3: every 050 / 0800 / 0120 / 0570 block.
//   4: every fixed-line block in the 21 metro area codes.
// Shards are numbered sequentially across stages (each stage's keys are
// deduped against all earlier stages and chunked at SITEMAP_SHARD_SIZE).
// SITEMAP_BLOCKS_STAGE controls how many stages the /sitemap.xml index
// advertises; shard URLs beyond it still resolve when fetched directly,
// mirroring the pre-staging behaviour of the single blocks shard. Raise
// the constant one stage at a time as Search Console coverage allows.
const SITEMAP_SHARD_SIZE = 45000;
const POPULAR_PER_PAGE = 100;
const SITEMAP_BLOCKS_STAGE = 1;
const BLOCK_STAGE_COUNT = 4;
// The 21 metro area codes of the fixed-line rollout (same string-prefix
// membership as the generation pipeline's area_of()).
const METRO_AREA_CODES = [
  '011', '022', '025', '03', '042', '043', '044', '045', '048',
  '052', '053', '054', '06', '072', '075', '078', '082', '086',
  '092', '093', '096',
];

function xmlResponse(xml) {
  return new Response(xml, { headers: { 'content-type': 'application/xml; charset=utf-8', 'cache-control': CACHE_HUB } });
}

function urlsetXml(urls) {
  return `<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n${urls
    .map((u) => `<url><loc>${u}</loc></url>`)
    .join('\n')}\n</urlset>`;
}

function popularTotalPages() {
  return Math.max(1, Math.ceil(HOT_NUMBERS.length / POPULAR_PER_PAGE));
}

async function mainSitemapUrls(db) {
  const areas = await allAreaCodes(db);
  const prefs = [...new Set(areas.map((a) => a.prefecture))];
  const types = await listLineTypes(db);
  const govNumbers = await listGovNumbers(db);
  const popularUrls = ['https://datumoj.com/tel/popular'];
  for (let p = 2; p <= popularTotalPages(); p++) {
    popularUrls.push(`https://datumoj.com/tel/popular?page=${p}`);
  }
  return [
    'https://datumoj.com/tel/',
    'https://datumoj.com/tel/about',
    'https://datumoj.com/tel/history',
    'https://datumoj.com/tel/gov',
    ...popularUrls,
    ...types.map((t) => `https://datumoj.com/tel/type/${t.code}`),
    ...prefs.map((p) => `https://datumoj.com/tel/pref/${encodeURIComponent(p)}`),
    ...areas.map((a) => `https://datumoj.com/tel/area/${a.area_code}`),
    ...govNumbers.map((n) => `https://datumoj.com/tel/${n}`),
    // Numbers tracked by the SEO watch (tel/seo/SEO_LOG.md):
    // indexable designated-block pages surfaced for discovery.
    'https://datumoj.com/tel/08007779633',
    'https://datumoj.com/tel/0120923499',
  ];
}

// Tier 1 digits already laid down in the per-year record tables. The
// tables are small (one row per queried number), but reads still go
// through the digits primary key in bounded pages — never a LIKE scan.
async function listRecordTier1Digits(db) {
  const out = [];
  const now = currentRecordYear();
  for (let y = now; y >= FIRST_RECORD_YEAR; y--) {
    let last = '';
    for (;;) {
      let r;
      try {
        r = await db
          .prepare(`SELECT digits FROM number_record_${y} WHERE tier = 1 AND digits > ? ORDER BY digits LIMIT 5000`)
          .bind(last)
          .all();
      } catch {
        break; // year table does not exist yet
      }
      const rows = r.results || [];
      if (!rows.length) break;
      for (const row of rows) {
        out.push(row.digits);
        last = row.digits;
      }
      if (rows.length < 5000) break;
    }
  }
  return out;
}

async function sitemapNumberDigits(db) {
  const seen = new Set();
  const out = [];
  for (const h of HOT_NUMBERS) {
    if (!seen.has(h.digits)) {
      seen.add(h.digits);
      out.push(h.digits);
    }
  }
  const recDigits = await listRecordTier1Digits(db).catch(() => []);
  recDigits.sort();
  for (const d of recDigits) {
    if (!seen.has(d)) {
      seen.add(d);
      out.push(d);
    }
  }
  return out;
}

// --- Block sitemap (staged rollout) ------------------------------------------

// Stage 1: signal block keys from the D1 signal_block table. That table is
// the sitemap-side copy of the block generators' signal set (landed
// records + seed list_v1 in full + list_v2 search_count>=10 + comment /
// report / third-party-flag numbers, intersected with the generated block
// plan), written by scripts/sync_signal_blocks.py from the generators' own
// computation — never re-derived here, so the sitemap can never drift from
// what the block pages render as signal links. A missing table (migration
// 0012 not applied yet) reads as an empty stage, not an error.
async function listSignalBlockKeys(db) {
  try {
    const r = await db.prepare('SELECT block_key FROM signal_block ORDER BY block_key').all();
    return (r.results || []).map((row) => row.block_key);
  } catch {
    return [];
  }
}

async function listAllocationPrefixes(db, lineType, prefixLength) {
  const r = await db
    .prepare(
      `SELECT prefix FROM number_block_allocation WHERE line_type = ? AND prefix_length = ? AND status = 'designated' ORDER BY prefix`
    )
    .bind(lineType, prefixLength)
    .all();
  return (r.results || []).map((row) => row.prefix);
}

// Keys of one rollout stage, minus every key already claimed by an earlier
// stage (the caller threads `exclude` through the stages in order).
async function blockStageKeys(db, stage, exclude) {
  const fresh = (keys) => keys.filter((k) => !exclude.has(k));
  if (stage === 1) {
    return fresh(await listSignalBlockKeys(db));
  }
  if (stage === 2) {
    // Mobile allocations are designated at 6 digits; each expands to the
    // ten 7-digit block keys of its 万号 blocks.
    const keys = [];
    for (const p of await listAllocationPrefixes(db, 'mobile', 6)) {
      for (let d = 0; d < 10; d++) keys.push(p + d);
    }
    return fresh(keys.sort());
  }
  if (stage === 3) {
    // 050 / 0800 / 0120 / 0570 allocations are designated at the full
    // 7-digit block key already.
    const keys = [];
    for (const lt of ['ip050', 'free0800', 'free0120', 'navi0570']) {
      keys.push(...(await listAllocationPrefixes(db, lt, 7)));
    }
    return fresh(keys.sort());
  }
  if (stage === 4) {
    // Fixed-line allocations sit at 6 digits (area + exchange); the metro
    // scope is the 21 area codes, matched as string prefixes exactly as
    // the generation pipeline picks them (nearby MA exchanges included).
    const keys = [];
    for (const p of await listAllocationPrefixes(db, 'fixed', 6)) {
      if (!METRO_AREA_CODES.some((c) => p.startsWith(c))) continue;
      for (let d = 0; d < 10; d++) keys.push(p + d);
    }
    return fresh(keys.sort());
  }
  return [];
}

// Ordered shard plan for stages 1..maxStage: [{ stage, keys }], chunked at
// SITEMAP_SHARD_SIZE. A stage with no keys contributes no shard, so shard
// numbers shift if an early stage is empty — acceptable while stages are
// mounted in order (an empty stage 1 mounts nothing at all).
async function blockSitemapPlan(db, maxStage = BLOCK_STAGE_COUNT) {
  const seen = new Set();
  const shards = [];
  for (let stage = 1; stage <= maxStage; stage++) {
    const keys = await blockStageKeys(db, stage, seen);
    for (const k of keys) seen.add(k);
    for (let i = 0; i < keys.length; i += SITEMAP_SHARD_SIZE) {
      shards.push({ stage, keys: keys.slice(i, i + SITEMAP_SHARD_SIZE) });
    }
  }
  return shards;
}

// Prefix-only line-type detection for the homepage "popular numbers" box.
// Same numbering-plan rules as splitNumber() in lib/number.js; non-fixed
// types need no D1 lookup at all.
function lineTypeOf(digits) {
  if (digits.startsWith('020')) return 'm2m020';
  if (digits.startsWith('050')) return 'ip050';
  if (digits.startsWith('0120')) return 'free0120';
  if (digits.startsWith('0800')) return 'free0800';
  if (digits.startsWith('0570')) return 'navi0570';
  if (/^0[789]0/.test(digits)) return 'mobile';
  return 'fixed';
}

// Display formatting for number lists (popular box, comment lists): the
// same split rules as number pages; only fixed lines cost a D1 probe.
async function formatForList(db, digits) {
  const lineType = lineTypeOf(digits);
  let areaCode = null;
  if (lineType === 'fixed') {
    areaCode = await findAreaCode(db, digits);
  }
  return splitNumber(digits, { lineType, areaCode }).join('-');
}

// Most-looked-up numbers from the site's own view counter (number_view).
// Never throws: returns null when D1 is unavailable or no real lookups exist
// yet, in which case the homepage simply omits the box.
async function getPopularNumbers(db, limit = 6) {
  try {
    // DESIGN v2.0: real demand lives on the per-year record tables.
    let raw = await popularFromRecords(db, limit);
    if (!raw.length) {
      const r = await db
        .prepare('SELECT number, views FROM number_view ORDER BY views DESC, number ASC LIMIT ?')
        .bind(limit)
        .all();
      raw = r.results || [];
    }
    const rows = raw.filter(
      (x) => x.views > 0 && typeof x.number === 'string' && /^\d{10,14}$/.test(x.number)
    );
    if (!rows.length) return null;
    const out = [];
    for (const x of rows) {
      out.push({
        digits: x.number,
        formatted: await formatForList(db, x.number),
        views: x.views,
      });
    }
    return out;
  } catch {
    return null;
  }
}

// Newest visible comments for the homepage "最新の口コミ" box.
// Never throws and returns null while no visible comment exists, in which
// case the homepage omits the box (and the popular box renders as before).
async function getLatestComments(db, limit = 5) {
  try {
    const rows = await listLatestComments(db, limit);
    const valid = rows.filter((x) => typeof x.number === 'string' && /^\d{10,14}$/.test(x.number));
    if (!valid.length) return null;
    const out = [];
    for (const x of valid) {
      out.push({ number: x.number, body: x.body, created_at: x.created_at, formatted: await formatForList(db, x.number) });
    }
    return out;
  } catch {
    return null;
  }
}

export default {
  async fetch(request, env, ctx) {
    const url = new URL(request.url);
    let path;
    try {
      path = decodeURIComponent(url.pathname);
    } catch {
      path = url.pathname; // malformed %-encoding: route on the raw path (404s)
    }
    // Subdirectory migration (2026-10-29): the app is mounted at datumoj.com/tel.
    // Strip the mount prefix before routing; generated links carry /tel themselves.
    if (path === '/tel') path = '/';
    else if (path.startsWith('/tel/')) path = path.slice(4);
    const db = env.DB;

    try {
      if (path === '/') {
        // Server-render the hero with a real drawn number (short edge cache
        // so the example rotates); fall back to verified static heroes when
        // D1 is unavailable instead of shipping XXXX placeholders.
        // The "popular numbers" box rides the same edge-cache window and is
        // omitted while D1 is down or before any real lookups exist.
        const [hero, popular, latestComments] = await Promise.all([
          drawRandomHero(db),
          getPopularNumbers(db),
          getLatestComments(db),
        ]);
        return html(homePage(hero || pickFallbackHero(), FALLBACK_HEROES, popular, latestComments), 200, 'public, max-age=0, s-maxage=300');
      }
      if (path === '/about') {
        return html(aboutPage(await listTpSources(db)));
      }
      if (path === '/history') {
        return html(historyPage());
      }
      if (path === '/gov') {
        const [agencies, numbers] = await Promise.all([listGovAgencies(db), govNumbersByAgency(db)]);
        return html(govPage(agencies, numbers));
      }
      if (path === '/search') {
        const raw = (url.searchParams.get('q') || '').trim();
        const digits = normalize(raw);
        // Number-shaped input keeps the original behaviour: straight to
        // the number page. Anything else is a keyword search over the
        // visible comments (the same set number pages display).
        if (digits) return Response.redirect(`https://datumoj.com/tel/${digits}`, 302);
        if (!raw) return html(notFoundPage(''), 404);
        const found = await searchComments(db, raw, 50).catch(() => []);
        const results = [];
        for (const c of found) {
          if (typeof c.number !== 'string' || !/^\d{10,14}$/.test(c.number)) continue;
          results.push({
            number: c.number,
            body: c.body,
            created_at: c.created_at,
            formatted: await formatForList(db, c.number),
          });
        }
        return html(commentSearchPage(raw, results), 200, 'no-store');
      }
      if (path === '/favicon.png') {
        return new Response(faviconPngBytes(), {
          headers: { 'content-type': 'image/png', 'cache-control': 'public, max-age=604800, s-maxage=604800' },
        });
      }
      if (path === '/apple-touch-icon.png') {
        return new Response(appleTouchIconBytes(), {
          headers: { 'content-type': 'image/png', 'cache-control': 'public, max-age=604800, s-maxage=604800' },
        });
      }
      if (path === '/robots.txt') {
        return new Response(
          'User-agent: *\nAllow: /\nDisallow: /search\nSitemap: https://datumoj.com/tel/sitemap.xml\n',
          { headers: { 'content-type': 'text/plain; charset=utf-8', 'cache-control': CACHE_HUB } }
        );
      }
      let m;
      if (path === '/sitemap.xml') {
        const digits = await sitemapNumberDigits(db);
        const shards = Math.max(1, Math.ceil(digits.length / SITEMAP_SHARD_SIZE));
        // Block shards join the index only up to the mounted rollout stage
        // (SITEMAP_BLOCKS_STAGE = 0 reproduces the pre-staging index).
        const blockShards =
          SITEMAP_BLOCKS_STAGE >= 1 ? await blockSitemapPlan(db, SITEMAP_BLOCKS_STAGE) : [];
        const children = [
          'https://datumoj.com/tel/sitemap-main.xml',
          ...Array.from({ length: shards }, (_, i) => `https://datumoj.com/tel/sitemap-numbers-${i + 1}.xml`),
          ...blockShards.map((_, i) => `https://datumoj.com/tel/sitemap-blocks-${i + 1}.xml`),
        ];
        const xml = `<?xml version="1.0" encoding="UTF-8"?>\n<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n${children
          .map((u) => `<sitemap><loc>${u}</loc></sitemap>`)
          .join('\n')}\n</sitemapindex>`;
        return xmlResponse(xml);
      }
      if (path === '/sitemap-main.xml') {
        return xmlResponse(urlsetXml(await mainSitemapUrls(db)));
      }
      if ((m = path.match(/^\/sitemap-numbers-(\d+)\.xml$/))) {
        const shard = Number(m[1]);
        const digits = await sitemapNumberDigits(db);
        const shards = Math.max(1, Math.ceil(digits.length / SITEMAP_SHARD_SIZE));
        if (shard < 1 || shard > shards) return html(notFoundPage(path), 404);
        const slice = digits.slice((shard - 1) * SITEMAP_SHARD_SIZE, shard * SITEMAP_SHARD_SIZE);
        return xmlResponse(urlsetXml(slice.map((d) => `https://datumoj.com/tel/${d}`)));
      }
      if ((m = path.match(/^\/sitemap-blocks-(\d+)\.xml$/))) {
        const shard = Number(m[1]);
        const plan = await blockSitemapPlan(db);
        if (shard < 1 || shard > plan.length) return html(notFoundPage(path), 404);
        return xmlResponse(urlsetXml(plan[shard - 1].keys.map((k) => `https://datumoj.com/tel/${k}`)));
      }
      if (path === '/ranges') {
        // Block index （区段索引） hub, Worker-rendered from the bucket
        // manifest + D1 facts (lib/drilldown.js) — successor of the
        // pre-generated /tel/blocks index. The family summary keeps the
        // generator's salt口径 (scripts/gen_blocks_index.py), but family
        // links now point at the drill-down pages /tel/<family>. A
        // missing manifest is a real 404, never a guessed page.
        const data = await buildRangesData(env, db, await listSignalBlockKeys(db));
        if (!data) return html(notFoundPage('ranges'), 404);
        return html(rangesPage(data), 200, CACHE_NUM);
      }
      if (path === '/ranges.js') {
        // Successor of /tel/blocks.js: drill-down pages navigate by real
        // links only, so no delegated script is needed any more. The
        // route stays as an intentionally empty, commented script so
        // pages that still reference the old URL (edge caches, in-flight
        // block regenerations) degrade silently instead of failing a
        // script load with a 404.
        return new Response(
          '/* datumoj.com/tel: block and group pages navigate by real links; this script is intentionally empty. */\n',
          { headers: { 'content-type': 'application/javascript; charset=utf-8', 'cache-control': CACHE_HUB } }
        );
      }
      if (path === '/blocks') {
        return Response.redirect('https://datumoj.com/tel/ranges', 301);
      }
      if ((m = path.match(/^\/blocks\/(\d{2,4})(?:\/(\d{4}))?$/))) {
        // Old family / chunk index URLs land on the equivalent level of
        // the drill-down tree: family -> /tel/<family>, chunk (first 4
        // digits) -> /tel/<chunk>.
        return Response.redirect(`https://datumoj.com/tel/${m[2] || m[1]}`, 301);
      }
      if (path === '/blocks.js') {
        return Response.redirect('https://datumoj.com/tel/ranges.js', 301);
      }
      if ((m = path.match(/^\/(\d{2,6})$/))) {
        // Prefix index: existence and children derive from the block
        // manifest (the same ground truth as the block pages), facts
        // from D1. A prefix with no generated block beneath it — i.e.
        // outside every generated allocation — is a real 404.
        const data = await buildPrefixData(env, db, m[1]);
        if (!data) return html(notFoundPage(m[1]), 404);
        return html(prefixPage(data), 200, CACHE_NUM);
      }
      if ((m = path.match(/^\/(\d{8,9})$/))) {
        // Between the 7-digit block page and the 10-14-digit number
        // page: the hundred-group page (prefix = number length - 2,
        // 100 real number links), its 10-number sibling one digit
        // deeper (number length - 1), or — for 8-digit prefixes of
        // 11-digit families — a prefix index whose children are the
        // hundred groups. The governing allocation decides which.
        const p = m[1];
        const block = await findBlock(db, p).catch(() => null);
        let body = null;
        if (block && p.length >= block.line_digits - 2) {
          const data = await buildGroupData(env, db, p);
          if (data) body = groupPage(data);
        } else if (block) {
          const children = await hundredGroupChildren(db, p);
          const data = children ? await buildPrefixData(env, db, p, children) : null;
          if (data) body = prefixPage(data);
        }
        if (!body) return html(notFoundPage(p), 404);
        return html(body, 200, CACHE_NUM);
      }
      if ((m = path.match(/^\/(\d{7})$/))) {
        // Block page, flat URL /tel/<7-digit block key>: bare-digit paths
        // dispatch by length — 7 digits is a block key (a block is an
        // official allocation, not a number; "block" as a path segment
        // was dropped because it reads as 封堵/blocking in a phone
        // context, and 7 digits can never collide with the 10–14-digit
        // number route below). The page is a pre-generated static listing
        // of one 10k-number block, stored in the nbfun bucket as
        // blocks/<key>.html and served verbatim. No object (or storage
        // unavailable) is a real 404 — block pages are never composed on
        // the fly.
        const obj = await getR2Object(env, `blocks/${m[1]}.html`);
        if (!obj) return html(notFoundPage(m[1]), 404);
        return html(obj.body, 200, CACHE_NUM, { 'x-block': 'hit' });
      }
      if (path === '/popular') {
        const totalPages = popularTotalPages();
        const raw = parseInt(url.searchParams.get('page') || '1', 10);
        const page = Number.isInteger(raw) && raw >= 1 ? raw : 1;
        if (page > totalPages) return html(notFoundPage('popular'), 404);
        const entries = HOT_NUMBERS.slice((page - 1) * POPULAR_PER_PAGE, page * POPULAR_PER_PAGE);
        return html(popularPage(entries, page, totalPages));
      }
      if ((m = path.match(/^\/pref\/(.+)$/))) {
        const areas = await listAreaCodes(db, m[1]);
        if (!areas.length) return html(notFoundPage(m[1]), 404);
        return html(prefPage(m[1], areas));
      }
      if ((m = path.match(/^\/area\/(0\d{1,4})$/))) {
        const hub = await getAreaHub(db, m[1]);
        if (!hub) return html(notFoundPage(m[1]), 404);
        return html(areaPage(hub));
      }
      if ((m = path.match(/^\/type\/([a-z0-9]+)$/))) {
        const hub = await getLineTypeHub(db, m[1]);
        if (!hub) return html(notFoundPage(m[1]), 404);
        return html(typePage(hub));
      }
      if (path === '/random.json') {
        // Homepage hero: draw at random from the record pool (numbers real
        // visitors have queried) and return the fullest explanation the
        // stored snapshot supports.
        // Degrades to a verified static hero (still 200) when D1 is down.
        const payload = (await drawRandomHero(db)) || pickFallbackHero();
        return new Response(JSON.stringify(payload), {
          headers: { 'content-type': 'application/json; charset=utf-8', 'cache-control': 'no-store' },
        });
      }
      if ((m = path.match(/^\/view\/(\d{10,14})$/)) && request.method === 'POST') {
        // Lookup counter: fired by the page's own script on every real
        // browser view (including arrivals from search engines). A browser
        // POST always carries Origin; if it names another site, this is a
        // cross-site drive-by trying to inflate the counter — do not count
        // it. Requests without Origin (curl, the ops cleanup job) count.
        const origin = request.headers.get('Origin');
        if (origin && origin !== 'https://datumoj.com') {
          return new Response(JSON.stringify({ views: 0 }), {
            status: 403,
            headers: { 'content-type': 'application/json; charset=utf-8', 'cache-control': 'no-store' },
          });
        }
        // DESIGN v2.0: the counter lives on the number's record row.
        let views = await bumpRecordView(db, m[1]).catch(() => null);
        if (views === null) {
          // No record yet (page HTML was edge-cached from before v2.0, or
          // the beacon raced the first render): compose once to validate
          // the number and lay its record down, then count.
          const facts = await composeNumber(db, m[1]);
          if (!facts) return new Response('{}', { status: 404, headers: { 'cache-control': 'no-store' } });
          await insertRecord(db, facts, await getSnapshotVersion(db)).catch(() => {});
          views = await bumpRecordView(db, m[1]).catch(() => null);
        }
        if (views === null) {
          try {
            views = await bumpView(db, m[1]);
          } catch {
            const row = await db.prepare('SELECT views FROM number_view WHERE number = ?').bind(m[1]).first();
            views = row ? row.views : 0;
          }
        }
        return new Response(JSON.stringify({ views }), {
          headers: { 'content-type': 'application/json; charset=utf-8', 'cache-control': 'no-store' },
        });
      }
      if (path === '/comments.json') {
        const n = url.searchParams.get('n') || '';
        const comments = /^\d{10,14}$/.test(n) ? await listComments(db, n) : [];
        return new Response(JSON.stringify({ comments }), {
          headers: { 'content-type': 'application/json; charset=utf-8', 'cache-control': 'no-store' },
        });
      }
      if (path === '/comment' && request.method === 'POST') {
        const json = (obj, status = 200) =>
          new Response(JSON.stringify(obj), {
            status,
            headers: { 'content-type': 'application/json; charset=utf-8', 'cache-control': 'no-store' },
          });
        let payload = null;
        try {
          payload = await request.json();
        } catch {
          payload = null;
        }
        const n = payload && payload.number;
        if (!n || !/^\d{10,14}$/.test(n)) return json({ error: 'bad_number' }, 400);
        const facts = await composeNumber(db, n);
        if (!facts) return json({ error: 'bad_number' }, 404);
        const problem = commentProblem(payload.body);
        if (problem) return json({ error: problem, message: COMMENT_MESSAGES[problem] }, 400);
        const ipHash = await hashIp(request.headers.get('CF-Connecting-IP'), env.IP_HASH_SECRET);
        const rateProblem = await addComment(db, n, payload.body, ipHash);
        if (rateProblem) return json({ error: rateProblem, message: COMMENT_MESSAGES[rateProblem] }, 429);
        await bumpRecordComment(db, n).catch(() => {});
        return json({ ok: true, comments: await listComments(db, n) });
      }
      if ((m = path.match(/^\/(\d{10,14})$/))) {
        // DESIGN v2.0: a number that has been queried before is served from
        // its record snapshot (per-year foundation tables); only a first
        // query — or a snapshot older than the current snapshot_version —
        // goes through composition, and the result is laid down / refreshed.
        // SSG snapshots (lib/snapshot.js): a fresh Tier 1 record's rendered
        // HTML is served from R2 when the stored snapshot provably matches
        // the record state + flag fingerprint; renders on any other path
        // (re)generate the snapshot in the background. Content, URL, tier
        // and 404 behaviour are identical either way, and any snapshot
        // failure falls back to the plain SSR render below.
        const defer = (p) => { if (ctx && ctx.waitUntil) ctx.waitUntil(p); };
        const version = await getSnapshotVersion(db);
        const rec = await findRecord(db, m[1]);
        if (rec && rec.row.snapshot_version >= version) {
          if (rec.row.tier === 1) {
            const fp = await flagFingerprint(db, m[1]);
            const snap = fp !== null ? await getSnapshot(env, m[1]) : null;
            if (snap && Number(snap.meta.sv) === rec.row.snapshot_version && snap.meta.fp === fp) {
              return html(snap.body, 200, CACHE_NUM, { 'x-snapshot': 'hit' });
            }
            const facts = await factsFromRecord(db, rec.row);
            const body = await renderNumberPage(env, facts);
            if (facts.tier === 1 && fp !== null) {
              defer(putSnapshot(env, m[1], body, { sv: rec.row.snapshot_version, fp }));
            }
            return html(body, 200, CACHE_NUM, { 'x-snapshot': 'miss' });
          }
          const facts = await factsFromRecord(db, rec.row);
          return html(await renderNumberPage(env, facts), 200, CACHE_NUM);
        }
        const facts = await composeNumber(db, m[1]);
        if (facts) {
          let laidDown = false;
          try {
            if (rec) {
              await updateRecordFacts(db, rec.year, facts, version);
              facts.viewCount = rec.row.view_count;
              facts.commentCount = rec.row.comment_count;
            } else {
              await insertRecord(db, facts, version);
              facts.viewCount = 0;
              facts.commentCount = 0;
            }
            laidDown = true;
          } catch { /* record write must never break the page */ }
          const body = await renderNumberPage(env, facts);
          if (laidDown && facts.tier === 1) {
            // Snapshot the just-landed page so later queries can be served
            // statically (the v2.0 record path above validates it against
            // the record row and the flag fingerprint before serving).
            const fp = await flagFingerprint(db, m[1]);
            if (fp !== null) defer(putSnapshot(env, m[1], body, { sv: version, fp }));
            return html(body, 200, CACHE_NUM, { 'x-snapshot': 'miss' });
          }
          return html(body, 200, CACHE_NUM);
        }
        // Catch-all: any 11-digit 0-leading input (and any other digit string
        // whose band can be identified) gets an explanatory page keyed by the
        // first three digits — a real 404, but with full guidance.
        const analysis = await analyzeFallback(db, m[1]);
        let suggestionOk = false;
        if (analysis.suggestion) {
          suggestionOk = !!(await composeNumber(db, analysis.suggestion));
        }
        if (m[1].startsWith('0') && (m[1].length === 11 || analysis.bandName || analysis.areaCode)) {
          return html(notAllocatedPage(analysis, suggestionOk), 404);
        }
        return html(notFoundPage(m[1]), 404);
      }
      return html(notFoundPage(path), 404);
    } catch (e) {
      // D1 unavailable (e.g. daily free-tier quota) or a compose failure:
      // serve an honest, styled 503 instead of a bare "Internal error", and
      // keep crawlers from caching it.
      return new Response(unavailablePage(), {
        status: 503,
        headers: { ...HTML, 'cache-control': 'no-store', 'retry-after': '3600' },
      });
    }
  },
};
