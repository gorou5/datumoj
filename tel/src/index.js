// tel.datumoj.com — Japanese phone-number database (Worker entry).
// Pages are composed at request time from sharded range tables in D1
// (see DESIGN.md / migrations/0001_init.sql). Hub pages are indexable;
// number pages are tiered (Tier 1 index / Tier 2 noindex / Tier 3 = 404).
import { normalize, splitNumber } from './lib/number.js';
import { bumpView, listComments, addComment, commentProblem, COMMENT_MESSAGES, hashIp } from './lib/social.js';
import {
  composeNumber, analyzeFallback, listPrefectures, listAreaCodes, getAreaHub,
  listLineTypes, getLineTypeHub, listTpSources, allAreaCodes,
  listGovAgencies, listGovNumbers, govNumbersByAgency, findAreaCode,
} from './lib/compose.js';
import {
  homePage, numberPage, areaPage, prefPage, typePage, aboutPage, notFoundPage, govPage, notAllocatedPage, historyPage, randomHero, FALLBACK_HEROES, unavailablePage, popularPage,
} from './lib/render.js';
import {
  findRecord, insertRecord, updateRecordFacts, factsFromRecord, getSnapshotVersion,
  bumpRecordView, bumpRecordComment, popularFromRecords,
  FIRST_RECORD_YEAR, currentRecordYear,
  drawRandomRecord, factsFromRecordSnapshot,
} from './lib/record.js';
import { HOT_NUMBERS } from './data/hot_numbers.js';
import { flagFingerprint, getSnapshot, putSnapshot } from './lib/snapshot.js';

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
const SITEMAP_SHARD_SIZE = 45000;
const POPULAR_PER_PAGE = 100;

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
  const popularUrls = ['https://tel.datumoj.com/popular'];
  for (let p = 2; p <= popularTotalPages(); p++) {
    popularUrls.push(`https://tel.datumoj.com/popular?page=${p}`);
  }
  return [
    'https://tel.datumoj.com/',
    'https://tel.datumoj.com/about',
    'https://tel.datumoj.com/history',
    'https://tel.datumoj.com/gov',
    ...popularUrls,
    ...types.map((t) => `https://tel.datumoj.com/type/${t.code}`),
    ...prefs.map((p) => `https://tel.datumoj.com/pref/${encodeURIComponent(p)}`),
    ...areas.map((a) => `https://tel.datumoj.com/area/${a.area_code}`),
    ...govNumbers.map((n) => `https://tel.datumoj.com/${n}`),
    // Numbers tracked by the SEO watch (tel/seo/SEO_LOG.md):
    // indexable designated-block pages surfaced for discovery.
    'https://tel.datumoj.com/08007779633',
    'https://tel.datumoj.com/0120923499',
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
      const lineType = lineTypeOf(x.number);
      let areaCode = null;
      if (lineType === 'fixed') {
        areaCode = await findAreaCode(db, x.number);
      }
      out.push({
        digits: x.number,
        formatted: splitNumber(x.number, { lineType, areaCode }).join('-'),
        views: x.views,
      });
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
    const db = env.DB;

    try {
      if (path === '/') {
        // Server-render the hero with a real drawn number (short edge cache
        // so the example rotates); fall back to verified static heroes when
        // D1 is unavailable instead of shipping XXXX placeholders.
        // The "popular numbers" box rides the same edge-cache window and is
        // omitted while D1 is down or before any real lookups exist.
        const [hero, popular] = await Promise.all([drawRandomHero(db), getPopularNumbers(db)]);
        return html(homePage(hero || pickFallbackHero(), FALLBACK_HEROES, popular), 200, 'public, max-age=0, s-maxage=300');
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
        const digits = normalize(url.searchParams.get('q') || '');
        if (!digits) return html(notFoundPage(url.searchParams.get('q') || ''), 404);
        return Response.redirect(`https://tel.datumoj.com/${digits}`, 302);
      }
      if (path === '/robots.txt') {
        return new Response(
          'User-agent: *\nAllow: /\nDisallow: /search\nSitemap: https://tel.datumoj.com/sitemap.xml\n',
          { headers: { 'content-type': 'text/plain; charset=utf-8', 'cache-control': CACHE_HUB } }
        );
      }
      let m;
      if (path === '/sitemap.xml') {
        const digits = await sitemapNumberDigits(db);
        const shards = Math.max(1, Math.ceil(digits.length / SITEMAP_SHARD_SIZE));
        const children = [
          'https://tel.datumoj.com/sitemap-main.xml',
          ...Array.from({ length: shards }, (_, i) => `https://tel.datumoj.com/sitemap-numbers-${i + 1}.xml`),
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
        return xmlResponse(urlsetXml(slice.map((d) => `https://tel.datumoj.com/${d}`)));
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
        if (origin && origin !== 'https://tel.datumoj.com') {
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
            const body = numberPage(facts);
            if (facts.tier === 1 && fp !== null) {
              defer(putSnapshot(env, m[1], body, { sv: rec.row.snapshot_version, fp }));
            }
            return html(body, 200, CACHE_NUM, { 'x-snapshot': 'miss' });
          }
          const facts = await factsFromRecord(db, rec.row);
          return html(numberPage(facts), 200, CACHE_NUM);
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
          const body = numberPage(facts);
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
