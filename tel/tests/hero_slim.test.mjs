// Tests for the 2026-11-09 hero slimming + icon buttons batch:
//   * homepage hero has no shuffle button / detail link / rotation script
//   * every action button is an inline-SVG icon with aria-label + title
//   * .bigsearch / .searchbox flex rules (flex:none buttons, min-width:0
//     inputs, >=44px hit areas); no mobile popular-first ordering left
//   * hero pool: no fixed-line (0AB-J) examples — neither in the offline
//     fallback set nor in the live record draw (stub D1 over node:sqlite)
// Run: node tests/hero_slim.test.mjs
import assert from 'node:assert/strict';
import { DatabaseSync } from 'node:sqlite';
import { homePage, numberPage, randomHero, FALLBACK_HEROES } from '../src/lib/render.js';
import { drawRandomRecord } from '../src/lib/record.js';

let passed = 0;
function ok(cond, label) {
  assert.ok(cond, label);
  passed++;
}

// --- Homepage hero + icon buttons -------------------------------------------
const hero = randomHero({
  digits: '09010123456', lineType: 'mobile', lineTypeName: '携帯電話',
  block: { prefix: '090101', carrierName: '株式会社NTTドコモ', source: '総務省 電気通信番号指定状況' },
});
const home = homePage(hero, [{ digits: '08007779633', formatted: '0800-777-9633', views: 9 }], [
  { number: '05018071465', formatted: '050-1807-1465', body: '一度だけ着信あり', created_at: '2026-11-09 10:00:00' },
]);
ok(!home.includes('shuffleBtn'), 'no shuffle button');
ok(!home.includes('別の番号を表示'), 'no shuffle label');
ok(!home.includes('heroDetail'), 'no hero detail link');
ok(!home.includes('hero-note'), 'no hero note paragraph');
ok(!home.includes('<script'), 'no client script left on the homepage');
ok(home.includes('href="/tel/090"') && home.includes('href="/tel/0901012"') && home.includes('href="/tel/09010123456"'), 'segment drill-down links kept');
// bigsearch submit is the magnifier icon, not text
ok(home.includes('<button type="submit" aria-label="調べる" title="調べる"><svg'), 'bigsearch icon button with aria-label/title');
ok(!home.includes('>調べる</button>'), 'no text search button anywhere');
ok(home.includes('<svg width="20" height="20" viewBox="0 0 24 24"'), 'inline SVG, no external resource');
// lower boxes: comments block renders; DOM order comments -> popular
ok(home.indexOf('最新の口コミ') < home.indexOf('よく調べられている番号'), 'comments block precedes popular in DOM');

// --- Shared stylesheet rules --------------------------------------------------
ok(/\.bigsearch button\{[^}]*flex:none/.test(home), 'bigsearch button flex:none');
ok(/\.bigsearch button\{[^}]*min-width:46px/.test(home) && /\.bigsearch button\{[^}]*min-height:46px/.test(home), 'bigsearch hit area >= 44px');
ok(/\.bigsearch input\{[^}]*min-width:0/.test(home), 'bigsearch input min-width:0');
ok(/\.searchbox button\{[^}]*flex:none/.test(home) && /\.searchbox button\{[^}]*min-height:44px/.test(home), 'searchbox icon button rules');
ok(!home.includes('order:-1'), 'mobile popular-first ordering removed');

// --- Number page: searchbox + comment submit icons ---------------------------
const page = numberPage({
  digits: '05018071465', lineType: 'ip050', lineTypeName: 'IP電話（050）', portable: true,
  block: { prefix: '0501807', carrierName: 'ソフトバンク株式会社', designatedOn: null, status: 'designated', note: null, source: '総務省' },
  viewCount: 1, commentCount: 0, blockExists: true,
  gov: null, govList: [], business: null, reports: [], reportCount: 0, tpFlags: [], tier: 1,
});
ok(page.includes('<button type="submit" aria-label="調べる" title="調べる"><svg'), 'page searchbox icon button');
ok(page.includes('<button type="submit" aria-label="投稿する" title="投稿する"><svg'), 'comment submit is the send icon');
ok(!page.includes('>投稿</button>'), 'no text comment submit');
ok(/#cmtForm button\{[^}]*min-height:44px/.test(page), 'comment button hit area 44px');

// --- Hero pool: fallback set has no fixed-line example ------------------------
ok(FALLBACK_HEROES.length >= 3, 'fallback pool still populated');
for (const h of FALLBACK_HEROES) {
  ok(!h.kicker.includes('固定電話'), `fallback hero is not fixed-line: ${h.digits}`);
  ok(h.digits !== '0331015678', 'the old 0AB-J fallback example is gone');
}

// --- Hero pool: live draw never returns a fixed-line record -------------------
const db0 = new DatabaseSync(':memory:');
db0.exec(`CREATE TABLE number_record_2026 (
  digits TEXT, line_type TEXT, line_type_name TEXT, portable INTEGER,
  block_prefix TEXT, carrier_name TEXT, designated_on TEXT, block_status TEXT,
  block_note TEXT, block_source TEXT, area_code TEXT, prefecture TEXT,
  mas_json TEXT, government_agency TEXT, designated INTEGER, tier INTEGER,
  snapshot_version INTEGER, view_count INTEGER, comment_count INTEGER,
  last_queried_at TEXT);
INSERT INTO number_record_2026 (digits, line_type, tier) VALUES
  ('0312345678', 'fixed', 1), ('0398765432', 'fixed', 1), ('09011112222', 'mobile', 1);`);
const stub = {
  prepare(sql) {
    const st = db0.prepare(sql);
    const bound = (...args) => ({
      async first() { const r = st.get(...args); return r === undefined ? null : r; },
      async all() { return { results: st.all(...args) }; },
      async run() { const r = st.run(...args); return { meta: { changes: r.changes } }; },
    });
    return { bind: (...args) => bound(...args), ...bound() };
  },
};
for (let i = 0; i < 30; i++) {
  const row = await drawRandomRecord(stub);
  ok(row && row.line_type !== 'fixed' && row.digits === '09011112222', `draw ${i} skips fixed-line records`);
}

console.log(`hero_slim.test: ${passed} assertions passed`);
