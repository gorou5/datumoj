// tel.datumoj.com — Japanese phone-number database (Worker entry).
// Pages are composed at request time from sharded range tables in D1
// (see DESIGN.md / migrations/0001_init.sql). Hub pages are indexable;
// number pages are tiered (Tier 1 index / Tier 2 noindex / Tier 3 = 404).
import { normalize } from './lib/number.js';
import { bumpView, listComments, addComment, commentProblem, COMMENT_MESSAGES, hashIp } from './lib/social.js';
import {
  composeNumber, analyzeFallback, listPrefectures, listAreaCodes, getAreaHub,
  listLineTypes, getLineTypeHub, listTpSources, allAreaCodes,
  listGovAgencies, listGovNumbers, govNumbersByAgency,
} from './lib/compose.js';
import {
  homePage, numberPage, areaPage, prefPage, typePage, aboutPage, notFoundPage, govPage, notAllocatedPage, historyPage, randomHero,
} from './lib/render.js';

const HTML = { 'content-type': 'text/html; charset=utf-8' };
const CACHE_HUB = 'public, max-age=3600, s-maxage=86400';
const CACHE_NUM = 'public, max-age=3600, s-maxage=604800';

function html(body, status = 200, cache = CACHE_HUB) {
  return new Response(body, { status, headers: { ...HTML, 'cache-control': cache } });
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    const path = decodeURIComponent(url.pathname);
    const db = env.DB;

    try {
      if (path === '/') {
        return html(homePage());
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
      if (path === '/sitemap.xml') {
        const areas = await allAreaCodes(db);
        const prefs = [...new Set(areas.map((a) => a.prefecture))];
        const types = await listLineTypes(db);
        const govNumbers = await listGovNumbers(db);
        const urls = [
          'https://tel.datumoj.com/',
          'https://tel.datumoj.com/about',
          'https://tel.datumoj.com/history',
          'https://tel.datumoj.com/gov',
          ...types.map((t) => `https://tel.datumoj.com/type/${t.code}`),
          ...prefs.map((p) => `https://tel.datumoj.com/pref/${encodeURIComponent(p)}`),
          ...areas.map((a) => `https://tel.datumoj.com/area/${a.area_code}`),
          ...govNumbers.map((n) => `https://tel.datumoj.com/${n}`),
          // Numbers tracked by the SEO watch (tel/seo/SEO_LOG.md):
          // indexable designated-block pages surfaced for discovery.
          'https://tel.datumoj.com/08007779633',
          'https://tel.datumoj.com/0120923499',
        ];
        const xml = `<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n${urls
          .map((u) => `<url><loc>${u}</loc></url>`)
          .join('\n')}\n</urlset>`;
        return new Response(xml, { headers: { 'content-type': 'application/xml; charset=utf-8', 'cache-control': CACHE_HUB } });
      }
      let m;
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
        // Homepage hero: draw a real designated block at random (type first,
        // so all line types rotate), fill it out with random subscriber
        // digits, and return the fullest explanation compose can give.
        const types = ['fixed', 'mobile', 'ip050', 'free0120', 'free0800', 'navi0570', 'm2m020'];
        let payload = null;
        for (let attempt = 0; attempt < 6 && !payload; attempt++) {
          const lt = types[Math.floor(Math.random() * types.length)];
          const brow = await db
            .prepare(
              "SELECT prefix FROM number_block_allocation WHERE line_type = ? AND carrier_id IS NOT NULL AND status = 'designated' AND length(prefix) >= 6 ORDER BY RANDOM() LIMIT 1"
            )
            .bind(lt)
            .first();
          if (!brow) continue;
          const ltRow = await db.prepare('SELECT digits FROM line_type WHERE code = ?').bind(lt).first();
          let digits = brow.prefix;
          while (digits.length < ltRow.digits) digits += Math.floor(Math.random() * 10);
          const facts = await composeNumber(db, digits);
          if (facts) payload = randomHero(facts);
        }
        if (!payload) return new Response('{}', { status: 503, headers: { 'cache-control': 'no-store' } });
        return new Response(JSON.stringify(payload), {
          headers: { 'content-type': 'application/json; charset=utf-8', 'cache-control': 'no-store' },
        });
      }
      if ((m = path.match(/^\/view\/(\d{10,14})$/)) && request.method === 'POST') {
        // Lookup counter: fired by the page's own script on every real
        // browser view (including arrivals from search engines).
        const facts = await composeNumber(db, m[1]);
        if (!facts) return new Response('{}', { status: 404, headers: { 'cache-control': 'no-store' } });
        let views = 0;
        try {
          views = await bumpView(db, m[1]);
        } catch {
          // Write path unavailable (e.g. daily write quota): still show the
          // last known count instead of failing the beacon.
          const row = await db.prepare('SELECT views FROM number_view WHERE number = ?').bind(m[1]).first();
          views = row ? row.views : 0;
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
        const ipHash = await hashIp(request.headers.get('CF-Connecting-IP'));
        const rateProblem = await addComment(db, n, payload.body, ipHash);
        if (rateProblem) return json({ error: rateProblem, message: COMMENT_MESSAGES[rateProblem] }, 429);
        return json({ ok: true, comments: await listComments(db, n) });
      }
      if ((m = path.match(/^\/(\d{10,14})$/))) {
        const facts = await composeNumber(db, m[1]);
        if (facts) return html(numberPage(facts), 200, CACHE_NUM);
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
      return new Response('Internal error', { status: 500 });
    }
  },
};
