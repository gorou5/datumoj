// Render-level tests for the 2026-11-09 number-page slimming batch.
// Pure-function coverage: numberPage / groupPage HTML for representative
// fact shapes (flag number, fixed line, plain mobile), the terminology
// unification （コメント->口コミ, 「匿名」 removal, 第三者標識->
// 公的機関の公表情報）, and the three-form group units. The number-page
// FAQ and the visible 基本情報 heading were removed later the same
// night (owner verdicts); absence is asserted below.
// Run: node tests/number_page_slim.test.mjs
import assert from 'node:assert/strict';
import { numberPage, groupPage } from '../src/lib/render.js';
import { parenFormat } from '../src/lib/drilldown.js';

let passed = 0;
function ok(cond, label) {
  assert.ok(cond, label);
  passed++;
}
function notContains(html, needle, label) {
  ok(!html.includes(needle), `${label} — unexpected: ${needle}`);
}

// --- Flag number (050-1807-1465, the site's one C-tier flag) ---------------
const flagFacts = {
  digits: '05018071465',
  lineType: 'ip050',
  lineTypeName: 'IP電話（050）',
  portable: true,
  block: { prefix: '0501807', carrierName: 'ソフトバンク株式会社', designatedOn: '2003年', status: 'designated', note: null, source: '総務省 電気通信番号指定状況' },
  viewCount: 32,
  commentCount: 3,
  blockExists: true,
  gov: null,
  govList: [],
  business: null,
  reports: [],
  reportCount: 0,
  tpFlags: [
    {
      source_tier: 'C', publisher: '国民生活センター', source_name: '事業者の名称等を公表',
      label: '国民生活センターが事業者の名称等を公表した際に併せて公表された電話番号',
      published_on: '2025-01-01', evidence_url: null, source_url: 'https://example.jp/', last_verified: '2026-11-01',
    },
  ],
  tier: 1,
};
const flagHtml = numberPage(flagFacts);
ok(flagHtml.includes('公的機関の公表情報 C級｜国民生活センター'), 'flag badge strip renamed');
ok(flagHtml.includes('<span class="badge">公的機関の公表情報・C級</span>'), 'flag card badge renamed');
ok(flagHtml.includes('<h2>公的機関の公表情報</h2>'), 'flag section heading renamed');
notContains(flagHtml, '第三者標識', 'no old flag label anywhere');
notContains(flagHtml, 'class="crumbs"', 'no visible breadcrumb trail');
notContains(flagHtml, 'この番号の区段ページ', 'no top back-link line');
notContains(flagHtml, '番号区段インデックス', 'no ranges link line');
ok(flagHtml.includes('<h1>050-1807-1465 の電話番号情報</h1>'), 'plain visible h1');
notContains(flagHtml, '<button type="button" class="segbtn', 'no coloured segment boxes');
notContains(flagHtml, 'id="segExplain"', 'no seg-explain card');
notContains(flagHtml, '番号ポータビリティ</th>', 'no portability filler row');
notContains(flagHtml, '地域は特定できません', 'no no-information region row');
// Three notation rows, one form each (owner ruling 2026-11-10), in
// the site's shared notation vocabulary (same trio as the prefix-page
// 範囲 rows and the group-page units).
ok(flagHtml.includes('<tr><th>電話番号（ハイフン表記）</th><td>050-1807-1465</td></tr>'), 'hyphen notation row');
ok(flagHtml.includes('<tr><th>電話番号（かっこ表記）</th><td>(050)1807-1465</td></tr>'), 'parenthesis notation row');
ok(flagHtml.includes('<tr><th>電話番号（数字のみ表記）</th><td>05018071465</td></tr>'), 'digits-only notation row');
notContains(flagHtml, '<th>電話番号</th>', 'old combined notation row gone');
notContains(flagHtml, '<th>数字のみ表記</th>', 'old standalone digits row gone');
notContains(flagHtml, '<h2>基本情報</h2>', 'visible 基本情報 heading removed (item 12)');
ok(flagHtml.includes('<th>番号の種類</th>'), 'facts table itself still rendered');
ok(flagHtml.includes('<th>番号区段</th><td><a href="/tel/0501807">0501807</a></td>'), '番号区段 row links the block');
ok(flagHtml.includes('<th>口コミ件数</th><td><a href="#comments">3 件</a></td>'), '口コミ件数 row anchors to #comments');
ok(flagHtml.includes('<div class="card" id="comments"><h2>口コミ</h2>'), 'comment card renamed + anchored');
notContains(flagHtml, '匿名', 'no 匿名 anywhere on the number page');
notContains(flagHtml, 'コメント', 'no コメント anywhere on the number page');
ok(flagHtml.includes('まだ口コミはありません'), 'comment empty state renamed');
// FAQ: removed entirely (owner verdict 2026-11-09) — no visible Q&A
// section and no FAQPage JSON-LD. BreadcrumbList stays.
notContains(flagHtml, '<h2>よくある質問</h2>', 'no FAQ section on number pages');
notContains(flagHtml, '知らない番号から着信がありました', 'no FAQ question text left');
const ld = JSON.parse(flagHtml.match(/<script type="application\/ld\+json">(\{.*?\})<\/script>/s)[1]);
ok(ld['@graph'].every((x) => x['@type'] !== 'FAQPage'), 'no FAQPage JSON-LD');
const crumbsLd = ld['@graph'].find((x) => x['@type'] === 'WebPage').breadcrumb;
ok(crumbsLd && crumbsLd.itemListElement.length >= 2, 'BreadcrumbList JSON-LD kept');
// Page structure ends at 口コミ: it is the last h2 section on the page.
const lastH2 = [...flagHtml.matchAll(/<h2>([^<]+)<\/h2>/g)].map((m) => m[1]).pop();
ok(lastH2 === '口コミ', '口コミ is the final section');

// --- Fixed line (03-3581-3251): anatomy rows + existing MA facts -----------
const fixedFacts = {
  digits: '0335813251',
  lineType: 'fixed',
  lineTypeName: '固定電話（0AB-J）',
  portable: false,
  areaCode: '03',
  prefecture: '東京都',
  mas: [{ charge_area_name: '東京', zone_code: '003', region: '東京都（特別区など）' }],
  block: { prefix: '033581', carrierName: null, designatedOn: null, status: 'designated', note: null, source: '総務省 電気通信番号計画' },
  viewCount: 5,
  commentCount: 0,
  blockExists: true,
  gov: null, govList: [], business: null, reports: [], reportCount: 0, tpFlags: [], tier: 1,
};
const fixedHtml = numberPage(fixedFacts);
ok(fixedHtml.includes('<th>市外局番</th><td>03</td>'), 'fixed: 市外局番 row');
ok(fixedHtml.includes('<th>市内局番</th><td>3581</td>'), 'fixed: 市内局番 row');
ok(fixedHtml.includes('<th>加入者番号</th><td>3251</td>'), 'fixed: 加入者番号 row');
ok(fixedHtml.includes('<th>単位料金区域（MA）</th>'), 'fixed: MA row (real data) kept');
ok(fixedHtml.includes('<th>対象地域</th>'), 'fixed: 対象地域 row (real data) kept');
ok(fixedHtml.includes('<h2>公的機関の公表情報</h2>'), 'empty flag section uses the new label');
ok(fixedHtml.includes('公的機関の公表情報は登録されていません'), 'empty flag text renamed');
ok(fixedHtml.includes('<th>口コミ件数</th><td><a href="#comments">0 件</a></td>'), 'zero comment count renders');

// --- Plain mobile, no block page: no 区段 row, no anatomy ------------------
const mobileFacts = {
  digits: '09012345678',
  lineType: 'mobile',
  lineTypeName: '携帯電話',
  portable: true,
  block: { prefix: '090123', carrierName: '株式会社NTTドコモ', designatedOn: null, status: 'designated', note: null, source: '総務省' },
  viewCount: 1,
  commentCount: 0,
  blockExists: false,
  gov: null, govList: [], business: null, reports: [], reportCount: 0, tpFlags: [], tier: 2,
};
const mobileHtml = numberPage(mobileFacts);
notContains(mobileHtml, '<th>番号区段</th>', 'no block row when the block page does not exist');
notContains(mobileHtml, '<th>市外局番</th>', 'no anatomy rows for mobile');
notContains(mobileHtml, '<th>加入者番号</th>', 'no subscriber row for mobile');

// --- Group pages: three-form units ------------------------------------------
function groupFixture(count) {
  const numbers = [];
  for (let i = 0; i < count; i++) {
    const d = '01200520' + String(i).padStart(2, '0');
    numbers.push({
      digits: d,
      formatted: `0120-052-0${String(i).padStart(2, '0')}`,
      paren: parenFormat(d, { lineType: 'free0120' }),
      landed: i === 0,
      flagged: i === 1,
    });
  }
  return {
    prefix: '01200520', blockKey: '0120052', blockLabel: '0120-052', family: '0120', familyLabel: 'フリーダイヤル 0120',
    lo: '0120052000', hi: '0120052099', fmtLo: '0120-052-000', fmtHi: '0120-052-099',
    parenLo: '(0120)052-000', parenHi: '(0120)052-099',
    lineTypeName: 'フリーダイヤル', carrierName: 'KDDI株式会社', numbers, explain: null,
  };
}
const hundred = groupPage(groupFixture(100));
const units = hundred.match(/<span class="n">/g) || [];
ok(units.length === 100, `hundred-group page has 100 units (got ${units.length})`);
ok(hundred.includes('<span class="n"><a href="/tel/0120052000" class="on">0120-052-000</a><small>(0120)052-000 0120052000</small></span>'), 'unit shape: hyphen link + paren/digits small');
ok(hundred.includes(' <span class="badge">公表情報あり</span><small>'), 'flag badge stays inside the unit');
const ten = groupPage({ ...groupFixture(10), prefix: '012005200', lo: '0120052000', hi: '0120052009' });
ok((ten.match(/<span class="n">/g) || []).length === 10, 'ten-group page has 10 units');

console.log(`number_page_slim.test: ${passed} assertions passed`);
