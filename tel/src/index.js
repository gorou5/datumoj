// tel.datumoj.com — Japanese phone-number database (Worker entry).
// Pages are composed at request time from sharded range tables in D1
// (see DESIGN.md / migrations/0001_init.sql). Hub pages are indexable;
// number pages are tiered (Tier 1 index / Tier 2 noindex / Tier 3 = 404).
import { normalize } from './lib/number.js';
import {
  composeNumber, analyzeFallback, listPrefectures, listAreaCodes, getAreaHub,
  listLineTypes, getLineTypeHub, listTpSources, allAreaCodes,
  listGovAgencies, listGovNumbers, govNumbersByAgency,
} from './lib/compose.js';
import {
  homePage, numberPage, areaPage, prefPage, typePage, aboutPage, notFoundPage, govPage, notAllocatedPage,
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
          'https://tel.datumoj.com/gov',
          ...types.map((t) => `https://tel.datumoj.com/type/${t.code}`),
          ...prefs.map((p) => `https://tel.datumoj.com/pref/${encodeURIComponent(p)}`),
          ...areas.map((a) => `https://tel.datumoj.com/area/${a.area_code}`),
          ...govNumbers.map((n) => `https://tel.datumoj.com/${n}`),
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
