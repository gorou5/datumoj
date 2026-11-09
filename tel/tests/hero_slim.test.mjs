// Tests for the 2026-11-09 hero batches: icon buttons everywhere, the
// hero pool without fixed-line examples, and the showcase's move from
// the homepage to /tel/examples (homepage keeps no hero at all).
// Run: node tests/hero_slim.test.mjs
import assert from 'node:assert/strict';
import { DatabaseSync } from 'node:sqlite';
import { homePage, examplesPage, soudanPage, aboutPage, numberPage, randomHero, FALLBACK_HEROES } from '../src/lib/render.js';
import { drawRandomRecord } from '../src/lib/record.js';

let passed = 0;
function ok(cond, label) {
  assert.ok(cond, label);
  passed++;
}

// --- Homepage: hero showcase restored (2026-11-10 ruling) ----------------------
const homeHero = randomHero({
  digits: '09010123456', lineType: 'mobile', lineTypeName: '携帯電話',
  block: { prefix: '090101', carrierName: '株式会社NTTドコモ', source: '総務省 電気通信番号指定状況' },
});
const home = homePage(homeHero, [{ digits: '08007779633', formatted: '0800-777-9633', views: 9 }], [
  { number: '0361621111', formatted: '03-6162-1111', body: '着信履歴で発見', created_at: '2026-11-08 17:46:51' },
  { number: '05018071465', formatted: '050-1807-1465', body: '一度だけ着信あり', created_at: '2026-11-09 12:46:51' },
  { number: '0120923499', formatted: '0120-923-499', body: '平日の昼に着信', created_at: '2026-11-09 08:46:51' },
]);
ok(home.includes('<div class="hero-num" id="heroSegs"'), 'homepage hero showcase is back');
ok(!home.includes('<p class="hero-kicker"'), 'homepage kicker text block removed (boxes only)');
ok(home.includes('href="/tel/090"') && home.includes('href="/tel/09010123456"'), 'hero segments are drill-down links');
// Showcase form (2026-11-10): pastel box = digits link, segment name
// in small type UNDER the box, on both the homepage and /tel/examples.
const unit090 = '<span class="segunit"><a class="segbtn seg-a" href="/tel/090"><span class="segdigits">090</span></a><span class="segcap">番号帯</span></span>';
ok(!home.includes('<span class="segcap">'), 'homepage boxes carry no printed captions');
ok(home.includes('<a class="segbtn seg-a" href="/tel/090" title="番号帯" aria-label="番号帯：090">'), 'homepage box: segment name on title + aria-label');
ok(home.includes('.segunit{display:inline-flex'), 'segunit layout rule present (examples page)');
// Comments grouped by date: one small grey header per date, entries
// dateless and single-line, groups newest first even when the input
// order is shuffled.
ok((home.match(/class="cmt-date"/g) || []).length === 2, 'two date group headers');
ok(home.indexOf('>2026-11-09<') < home.indexOf('>2026-11-08<'), 'date groups newest first');
ok(!home.includes('（2026-11-09）'), 'entries no longer carry their own dates');
const cmtBlock = home.slice(home.indexOf('最新の口コミ'), home.indexOf('よく調べられている番号'));
ok(cmtBlock.indexOf('一度だけ着信あり') < cmtBlock.indexOf('平日の昼に着信'), 'same-date entries keep delivered order');
ok(/\.col-comments li\{white-space:nowrap\}/.test(home), 'comment entries are single-line');
ok(home.indexOf('class="bigsearch"') < home.indexOf('id="heroSegs"'), 'hero sits below the search form');
ok(!home.includes('shuffleBtn') && !home.includes('別の番号を表示'), 'no shuffle button');
ok(!home.includes('<script'), 'no client script on the homepage');
ok((home.match(/href="\/tel\/examples"/g) || []).length === 1, 'homepage nav row links to /tel/examples exactly once');
ok(!home.includes('aria-label="公的相談窓口"') && !home.includes('でんわんセンター'), 'homepage no longer carries the consultation block (item 13a)');
// 発生状況 lives on the homepage now (moved from /tel/soudan, 2026-11-10).
ok(home.includes('<h2>発生状況</h2>'), 'homepage has the occurrence block');
ok(home.includes('認知件数合計 43,000件') && home.includes('約3,257億4,258万7,000円'), 'occurrence totals verbatim on homepage');
ok(home.includes('東京 5,879／大阪 4,830／愛知 3,505／神奈川 3,338／兵庫 3,238'), 'top-5 regions verbatim on homepage');
ok(home.includes('src="/tel/assets/sumally_map_full.png"') && home.includes('公共データ利用規約（PDL1.0）に基づき掲載'), 'self-hosted map + PDL attribution on homepage');
ok(home.indexOf('class="bigsearch"') < home.indexOf('<h2>発生状況</h2>'), 'occurrence block is below the search area');
ok(home.includes('<a class="navpill" href="/tel/soudan">公的相談窓口</a>'), 'nav row has the highlighted 公的相談窓口 pill (item 13b)');
ok(home.indexOf('href="/tel/history">日本の電話番号の歴史') < home.indexOf('href="/tel/examples">日本の電話番号の例') && home.indexOf('href="/tel/examples">日本の電話番号の例') < home.indexOf('href="/tel/about">このサイトについて'), 'examples entry sits inside the nav row');
// bigsearch submit is the magnifier icon, not text
ok(home.includes('<button type="submit" aria-label="調べる" title="調べる"><svg'), 'bigsearch icon button with aria-label/title');
ok(home.includes('placeholder="電話番号・口コミを検索"'), 'bigsearch hint names both numbers and口コミ');
ok(home.includes('aria-label="電話番号・口コミ"'), 'bigsearch aria-label aligned');
ok(!home.includes('>調べる</button>'), 'no text search button anywhere');
ok(home.includes('<svg width="20" height="20" viewBox="0 0 24 24"'), 'inline SVG, no external resource');
// lower boxes: comments block renders; DOM order comments -> popular
ok(home.indexOf('最新の口コミ') < home.indexOf('よく調べられている番号'), 'comments block precedes popular in DOM');
// shared stylesheet rules
ok(/\.bigsearch button\{[^}]*flex:none/.test(home), 'bigsearch button flex:none');
ok(/\.bigsearch button\{[^}]*min-width:46px/.test(home) && /\.bigsearch button\{[^}]*min-height:46px/.test(home), 'bigsearch hit area >= 44px');
ok(/\.bigsearch input\{[^}]*min-width:0/.test(home), 'bigsearch input min-width:0');
ok(/\.searchbox button\{[^}]*flex:none/.test(home) && /\.searchbox button\{[^}]*min-height:44px/.test(home), 'searchbox icon button rules');
ok(!home.includes('order:-1'), 'mobile popular-first ordering removed');

// --- /tel/examples: the showcase dataset, moved wholesale ---------------------
const mobileHero = randomHero({
  digits: '09010123456', lineType: 'mobile', lineTypeName: '携帯電話',
  block: { prefix: '090101', carrierName: '株式会社NTTドコモ', source: '総務省 電気通信番号指定状況' },
});
const ex = examplesPage(mobileHero);
ok(ex.includes(unit090), 'examples page uses the identical showcase unit form');
ok(ex.includes('<h1>日本の電話番号の例</h1>'), 'examples h1');
ok(ex.includes('href="/tel/090"') && ex.includes('href="/tel/0901012"') && ex.includes('href="/tel/09010123456"'), 'segment drill-down links kept');
ok(ex.includes('番号帯（090）') && ex.includes('加入者番号（前半）'), 'every segment explanation rendered');
ok(ex.includes('<th>番号の種類</th><td>携帯電話</td>'), 'hero facts list rendered');
ok(!ex.includes('shuffleBtn'), 'no shuffle button on examples either');
const fixedHero = randomHero({
  digits: '0331015678', lineType: 'fixed', lineTypeName: '固定電話（0AB-J）',
  areaCode: '03', prefecture: '東京都', mas: [],
  block: { prefix: '033101', carrierName: '大江戸テレコム株式会社', source: '総務省 電気通信番号指定状況' },
});
const exFixed = examplesPage(fixedHero);
ok(exFixed.includes('市内局番（かつての「分局」）'), 'fixed-line anatomy explanations work on examples (randomHero fixed branch intact)');

// --- The /tel/examples entry lives in the homepage nav row, not on /tel/about --
const about = aboutPage([]);
ok(!about.includes('href="/tel/examples"'), 'about carries no /tel/examples link (entry is in the nav row)');
// Site-level FAQ lives on /tel/about (number pages have none).
ok(about.includes('<h2>よくある質問</h2>'), 'about carries the site FAQ');
ok(about.includes('判定していません'), 'FAQ states the no-verdict policy');
ok(about.includes('情報源は何ですか'), 'FAQ covers the source question');
ok(about.includes('口コミはどのように扱われますか'), 'FAQ covers comment handling');
ok(!about.includes('mailto:'), 'FAQ invents no contact address');

// --- /tel/soudan: the moved consultation block, card layout + MIC video --------
const soudan = soudanPage();
for (const tel of ['tel:0120924839', 'tel:0361621111', 'tel:%239110', 'tel:188', 'tel:0120344999']) {
  ok(soudan.includes(`href="${tel}"`), `soudan tel link: ${tel}`);
}
ok((soudan.match(/class="soudan-num"/g) || []).length === 5, 'five big-number hotline cards');
ok(soudan.includes('https://www.youtube-nocookie.com/embed/XL86eC1TDZ4'), 'MIC video embedded via youtube-nocookie');
ok(soudan.includes('loading="lazy"'), 'video iframe is lazy-loaded');
ok(soudan.includes('title="【特殊詐欺】＋番号など知らない番号は一旦スルー！　黒電話篇"'), 'iframe carries the official video title');
ok(!soudan.includes('www.youtube.com/embed'), 'no tracking-domain embed');
ok(!soudan.includes('発生状況'), 'occurrence block has left the soudan page (moved to homepage)');
ok(!soudan.includes('sumally_map'), 'no map assets on the soudan page');
ok(soudan.indexOf('youtube-nocookie.com/embed/XL86eC1TDZ4') < soudan.indexOf('<h2>特殊詐欺の情報提供'), 'MIC video card sits above the hotline cards');

// --- Number page: searchbox + comment submit icons ------------------------------
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

// --- Hero pool: fallback set has no fixed-line example ---------------------------
ok(FALLBACK_HEROES.length >= 3, 'fallback pool still populated');
for (const h of FALLBACK_HEROES) {
  ok(!h.kicker.includes('固定電話'), `fallback hero is not fixed-line: ${h.digits}`);
  ok(h.digits !== '0331015678', 'the old 0AB-J fallback example is gone');
}

// --- Hero pool: live draw never returns a fixed-line record ----------------------
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
