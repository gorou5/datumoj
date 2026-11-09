// HTML rendering for datumoj.com/tel. No inline client JS; search is a plain GET form.
import { formatNumber, splitNumber, anatomySegments } from './number.js';

export function esc(s) {
  return String(s ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

// External source link: only http(s) URLs become anchors. DB rows carry
// source URLs; HTML-escaping alone would still let a javascript:/data:
// value execute on click, so the scheme is whitelisted here.
export function extLink(url, label, attrs = '') {
  return /^https?:\/\//i.test(String(url || ''))
    ? `<a href="${esc(url)}"${attrs}>${label}</a>`
    : esc(label);
}

// Inline SVG action icons (2026-11-09): every action button on the site
// is icon-only — navigation/content links keep their text. Uniform line
// style (24-unit viewBox, 2px round-cap strokes, currentColor); each
// button carries its original word as aria-label + title instead.
const ICON_SEARCH = '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true" focusable="false"><circle cx="11" cy="11" r="7"/><path d="M21 21l-4.35-4.35"/></svg>';
const ICON_SEND = '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="M22 2 11 13"/><path d="M22 2 15 22l-4-9-9-4Z"/></svg>';

const CSS = `
:root{color-scheme:light}
*{box-sizing:border-box}
body{font-family:system-ui,-apple-system,"Hiragino Kaku Gothic ProN","Noto Sans JP",sans-serif;margin:0;line-height:1.7;background:#fafafa;color:#1a1a1a}
a{color:#0b57d0;text-decoration:none}a:hover{text-decoration:underline}
header{border-bottom:1px solid #ddd;padding:.8rem 1rem;display:flex;gap:1rem;align-items:baseline;flex-wrap:wrap}
header .logo{font-weight:700;font-size:1.15rem;color:inherit}
main{max-width:860px;margin:0 auto;padding:1rem}
.card{background:#fff;border:1px solid #e2e2e2;border-radius:10px;padding:1rem 1.1rem;margin:1rem 0}
h1{font-size:1.6rem;margin:.4rem 0;word-break:break-all}
h2{font-size:1.15rem;margin:1.4rem 0 .5rem}
table{border-collapse:collapse;width:100%}
th,td{border-bottom:1px solid #e5e5e5;padding:.45rem .5rem;text-align:left;vertical-align:top}
th{width:11rem;color:#555;font-weight:600}
.meta{color:#666;font-size:.9rem}
.crumbs{font-size:.9rem;color:#666;margin:.5rem 0}
.segs{display:flex;gap:.4rem;flex-wrap:wrap;margin:.6rem 0}
.seg{border:1px solid #ccc;border-radius:8px;padding:.35rem .6rem;text-align:center}
.seg b{display:block;font-size:1.15rem}
.seg span{font-size:.75rem;color:#666}
.flag{border-left:4px solid #c62828}
.badge{display:inline-block;border-radius:999px;padding:.05rem .55rem;font-size:.8rem;border:1px solid #999;margin-right:.3rem}
.searchbox{display:flex;gap:.5rem;margin:1rem 0}
.searchbox input{flex:1;min-width:0;font-size:1.1rem;padding:.6rem;border:1px solid #bbb;border-radius:8px}
.searchbox button{flex:none;display:inline-flex;align-items:center;justify-content:center;min-width:44px;min-height:44px;padding:.55rem .7rem;border-radius:8px;border:1px solid #0b57d0;background:#0b57d0;color:#fff;cursor:pointer}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:.4rem .8rem}
.n{display:inline-block;white-space:nowrap;vertical-align:top}
.n small{display:block;font-size:.72rem;color:#666;white-space:nowrap}
.grid a.on{font-weight:700}
a.segbtn{display:inline-block;text-decoration:none}
.hero-kicker{color:#71808d;font-size:.95rem;margin:0 0 .5rem}
.hero-num{display:flex;align-items:stretch;justify-content:center;gap:.35rem;flex-wrap:wrap}
.hero-seg{display:flex;gap:inherit;align-items:flex-start}
.segunit{display:inline-flex;flex-direction:column;align-items:center;gap:.15rem}
.segbtn{border:2px solid transparent;border-radius:14px;padding:.45rem .9rem;cursor:pointer;font:inherit;background:#eef1f4;text-align:center}
.segbtn .segdigits{display:block;font-size:clamp(1.9rem,8.5vw,3.6rem);font-weight:700;letter-spacing:.04em;line-height:1.15;color:#33424f}
.segcap{font-size:.72rem;color:#71808d}
.seg-a{background:#dcebf5}.seg-b{background:#e2ecd9}.seg-c{background:#f6ead0}.seg-d{background:#e9e0f2}
.hero-facts{max-width:640px;margin:.9rem auto 0;text-align:left;background:#fbfaf6;border:1px solid #e7e1d2;border-radius:10px;padding:.7rem 1.1rem}
.hero-fact{margin:.25rem 0;font-size:.92rem;color:#3d473a}
.hero-fact b{color:#22303c}
.segbtn.is-active{border-color:#5b84a6;box-shadow:0 3px 14px rgba(60,70,90,.18)}
.hero-hyphen{align-self:flex-start;margin-top:1.05rem;font-size:clamp(1.4rem,6vw,2.6rem);color:#a3aeb8}
/* min-height keeps a small reserve so switching segments does not push the
   buttons below up and down; sized to typical copy (10rem), not the longest
   outlier, to avoid a large blank area in the card. */
.seg-explain{max-width:640px;margin:1.1rem auto 0;text-align:left;background:#fff;border:1px solid #e7e1d2;border-left:6px solid #7d9db5;border-radius:10px;padding:.9rem 1.1rem;min-height:10rem}
.seg-explain[data-seg="1"]{border-left-color:#a3b899}
.seg-explain[data-seg="2"]{border-left-color:#dfc08a}
.seg-explain[data-seg="3"]{border-left-color:#b39ddb}
.seg-explain h2{font-size:1.05rem;margin:0 0 .3rem}
.seg-explain p{margin:0}
.dialrow{text-align:center;margin:.9rem 0 0}
.dialbtn{display:inline-block;padding:.55rem 1.5rem;border-radius:999px;border:1px solid #5b84a6;background:#5b84a6;color:#fff;font-weight:600;text-decoration:none}
.home{max-width:940px;margin:5vh auto 0;text-align:center}
.home svg{width:100%;height:auto;display:block}
.bigsearch{display:flex;gap:.5rem;max-width:620px;margin:26px auto 1.6rem}
.bigsearch input{flex:1;min-width:0;font-size:1.15rem;padding:.85rem 1.3rem;border:1px solid #cfc9ba;border-radius:999px;box-shadow:0 2px 12px rgba(70,60,30,.10);background:#fff;color:#1a1a1a}
.bigsearch button{flex:none;display:inline-flex;align-items:center;justify-content:center;min-width:46px;min-height:46px;padding:.7rem .8rem;border-radius:999px;border:1px solid #5b84a6;background:#5b84a6;color:#fff;cursor:pointer}
.home-links{margin-top:16px;font-size:.88rem;color:#8a94a0}
.home-links .navpill{display:inline-block;background:#FDEBD7;border:1px solid #F97316;color:#9A3412;font-weight:700;border-radius:999px;padding:1px 10px;text-decoration:none}
.soudan-num{font-size:1.55rem;font-weight:800;letter-spacing:.02em;color:#C2410C;text-decoration:none}
.videowrap{position:relative;aspect-ratio:16/9;max-width:640px;margin:10px auto 4px}
.videowrap iframe{position:absolute;inset:0;width:100%;height:100%;border:0;border-radius:8px}
.mapimg{width:100%;height:auto;display:block}
/* Homepage lower boxes side by side: latest comments left, popular right.
   DOM order is comments-then-popular, and narrow screens stack in that
   same order (owner decision 2026-11-09: comments first on mobile too —
   the earlier popular-first mobile ordering was removed). */
.home-cols{display:grid;grid-template-columns:1fr 1fr;gap:0 1rem;align-items:start;text-align:left}
.home-cols.single{grid-template-columns:1fr}
@media (max-width:560px){
.segbtn{padding:.38rem .5rem .28rem;border-radius:10px}
.segbtn .segdigits{font-size:clamp(1.2rem,5.8vw,1.6rem);letter-spacing:0}
.segcap{font-size:.62rem}
.hero-hyphen{font-size:1rem;margin-top:.55rem}
.hero-num{gap:.2rem}
.seg-explain{min-height:9rem}
.home-cols{grid-template-columns:1fr}
}
.flagbadges{display:flex;flex-wrap:wrap;gap:.4rem;margin:.2rem 0 .6rem}
#cmtForm button{display:inline-flex;align-items:center;justify-content:center;min-width:44px;min-height:44px;padding:.4rem .7rem;border-radius:8px;border:1px solid #5b84a6;background:#5b84a6;color:#fff;cursor:pointer;vertical-align:middle}
.fbadge{display:inline-block;padding:.28rem .6rem;border-radius:999px;font-size:.82rem;font-weight:600;border:1px solid}
.fb-A{background:#fdecea;color:#8e1414;border-color:#e3a49f}
.fb-B{background:#fff6e0;color:#7a5200;border-color:#e0cd8f}
.fb-C{background:#eef1f4;color:#44515c;border-color:#c3ccd4}
.sr-only{position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0 0 0 0);white-space:nowrap}
footer{border-top:1px solid #ddd;margin-top:2rem;padding:1rem;font-size:.85rem;color:#777}
ul.tight{margin:.3rem 0;padding-left:1.2rem}
`;

export function layout({ title, description, robots, canonical, jsonLd, body }) {
  return `<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="light">
<title>${esc(title)}</title>
<link rel="icon" href="/tel/favicon.png" type="image/png">
<link rel="apple-touch-icon" href="/tel/apple-touch-icon.png">
<meta name="description" content="${esc(description || '')}">
${robots ? `<meta name="robots" content="${esc(robots)}">` : ''}
${canonical ? `<link rel="canonical" href="${esc(canonical)}">` : ''}
${jsonLd ? `<script type="application/ld+json">${JSON.stringify(jsonLd).replace(/<\//g, '<\\/')}</script>` : ''}
<style>${CSS}</style>
</head>
<body>
<header><a class="logo" href="/tel/">日本の電話番号データベース</a><span class="meta">datumoj.com/tel</span></header>
<main>
${body}
</main>
<footer>
<p>データ出典：総務省 電気通信番号計画・番号区画表ほか公開資料（詳細は<a href="/tel/about">データについて</a>）。割当事業者は番号の「指定」情報であり、番号ポータビリティにより現在の契約事業者とは異なる場合があります。本サイトは公的機関の公表情報を出典付きで転載するもので、独自に番号の有害性を判定しません。</p>
<p><a href="https://datumoj.com/">datumoj.com</a> のプロジェクトです。</p>
</footer>
</body>
</html>`;
}

function searchForm(value = '') {
  return `<form class="searchbox" action="/tel/search" method="get" role="search">
<input type="tel" name="q" inputmode="tel" autocomplete="off" placeholder="電話番号を入力（例：090-1234-5678）" value="${esc(value)}" aria-label="電話番号">
<button type="submit" aria-label="調べる" title="調べる">${ICON_SEARCH}</button>
</form>`;
}

// Payload for the homepage hero (/random.json): the fullest explanation we
// can give for one randomly drawn, really-designated number — every line
// type, with the fixed-line exchange (市内局番, the old 分局) explained.
export function randomHero(f) {
  const formatted = formatNumber(f.digits, f);
  const carrier = f.block.carrierName;
  let segments;
  let defaultSeg = 0;
  if (f.lineType === 'fixed') {
    const parts = anatomySegments(f.digits, f);
    const ma = f.mas && f.mas.length === 1 ? f.mas[0] : null;
    segments = [
      {
        text: parts[0], label: '国内プレフィックス', title: '国内プレフィックス「0」',
        explain: '日本国内で市外へかける際の合図（トランクプレフィックス）です。海外からかける場合は +81 に置き換わり、この0は省略されます。',
      },
      {
        text: parts[1], label: '市外局番', title: `市外局番（0${parts[1]}）`,
        explain: `固定電話の地域を表す番号です。市外局番 0${parts[1]} は${f.prefecture || '日本'}のエリアで使われています。市外局番と市内局番は、合わせて5桁になるよう設計されています。`,
      },
      {
        text: parts[2], label: '市内局番', title: '市内局番（かつての「分局」）',
        explain: `もともとは電話交換局（分局）が担当するエリアごとに割り当てられた部分で、その電話がどの交換局の管轄かを示していました。交換の自動化・IP化で「分局」という実体は薄れましたが、市内局番は今も単位料金区域（MA）の中の番号区画として、事業者ごとに割り当てられています。${ma ? `この番号の区画は ${ma.charge_area_name}MA（番号区画コード ${ma.zone_code}）に属し、対象地域は ${ma.region} です。` : ''}${carrier ? `割当事業者は ${carrier} です。` : ''}`,
      },
      {
        text: parts[3], label: '加入者番号', title: '加入者番号',
        explain: '市内局番のエリアの中で、各契約回線に割り当てられる4桁の番号です。それ自体に地域や事業者の意味はありません。',
      },
    ];
    defaultSeg = 2;
  } else if (f.lineType === 'm2m020') {
    const p = f.block.prefix;
    segments = [
      {
        text: p, label: '番号区画', title: '020番号の区画',
        explain: `020は、携帯電話網を使うM2M（機器間通信）等専用の番号帯です。一般の音声通話用ではなく、IoT機器やデータ通信端末に割り当てられます。${carrier ? `この区画の割当事業者は ${carrier} です。` : ''}桁数は用途により12〜14桁です。`,
      },
      {
        text: f.digits.slice(p.length), label: '識別番号', title: '識別番号',
        explain: '個々の機器・回線を識別するための番号部分です。',
      },
    ];
  } else {
    const parts = splitNumber(f.digits, f);
    const band = parts[0];
    let bandExplain;
    if (f.lineType === 'mobile') {
      bandExplain = `${band} は携帯電話の番号帯です。${carrier ? `当初の割当事業者は ${carrier} です。` : ''}2006年に番号ポータビリティ（MNP）が始まって以降、事業者を変えても番号はそのまま使えるため、現在の契約事業者はこの番号からは分かりません。`;
    } else if (f.lineType === 'ip050') {
      bandExplain = `050 はIP電話の番号帯です（2003年に利用開始）。${carrier ? `割当事業者は ${carrier} です。` : ''}インターネット回線を使う電話で、市外局番と違い地域とは結びついていません。番号ポータビリティの対象です。`;
    } else if (f.lineType === 'free0120' || f.lineType === 'free0800') {
      bandExplain = `${band} は着信課金（フリーダイヤル）の番号帯です。通話料は着信側が負担し、かける側は無料です。${carrier ? `割当事業者は ${carrier} です。` : ''}企業や団体の問い合わせ窓口などに使われます。`;
    } else {
      bandExplain = `0570 はナビダイヤルの番号帯です。全国共通の窓口番号を実際の着信先へ振り分ける仕組みで、通話料は発信者負担（有料）です。${carrier ? `割当事業者は ${carrier} です。` : ''}`;
    }
    const restExplain =
      f.lineType === 'mobile' || f.lineType === 'ip050'
        ? '契約者ごとに割り当てられる加入者番号の一部です。携帯電話・IP電話には固定電話のような「分局」（交換局エリア）の仕組みがなく、この数字から地域や現在の契約事業者は分かりません。'
        : '契約した企業・団体ごとに割り当てられる加入者番号の一部です。数字から地域は分かりません。';
    segments = [
      { text: band, label: '番号帯', title: `番号帯（${band}）`, explain: bandExplain },
      { text: parts[1], label: '加入者番号', title: '加入者番号（前半）', explain: restExplain },
      { text: parts[2], label: '加入者番号', title: '加入者番号（後半）', explain: restExplain },
    ];
  }
  const facts = [{ k: '番号の種類', v: f.lineTypeName }];
  if (carrier) facts.push({ k: '割当事業者', v: carrier });
  if (f.lineType === 'fixed') {
    if (f.prefecture) facts.push({ k: '都道府県', v: f.prefecture });
    if (f.mas && f.mas.length === 1) {
      facts.push({ k: '単位料金区域（MA）', v: `${f.mas[0].charge_area_name}MA（番号区画コード ${f.mas[0].zone_code}）` });
      facts.push({ k: '対象地域', v: f.mas[0].region });
    }
  }
  if (f.lineType === 'mobile' || f.lineType === 'ip050') facts.push({ k: '地域', v: '番号から地域は特定できません' });
  facts.push({ k: 'データ出典', v: f.block.source || '総務省 電気通信番号計画' });
  return {
    digits: f.digits,
    formatted,
    kicker: `日本の電話番号の例（${f.lineTypeName}）`,
    segments,
    facts,
    defaultSeg,
  };
}

// Offline fallback heroes: real designated blocks whose carrier facts were
// verified against the MIC designation data on 2026-10-22 (090101 = NTT
// ドコモ, 0800777 / 0120923 = KDDI). Used when D1 is unavailable (e.g.
// free-tier daily quota) so the homepage hero and /random.json never
// dead-end on XXXX placeholders. Explanations are generated by randomHero
// itself from these facts, so wording never drifts from the live path.
// Fixed-line (0AB-J) examples are NOT in the hero pool (owner decision
// 2026-11-09) — the live draw in lib/record.js excludes them too; the
// fixed branch of randomHero itself stays, drill-down pages use it.
const FALLBACK_FACTS = [
  {
    digits: '09010123456', lineType: 'mobile', lineTypeName: '携帯電話',
    block: { prefix: '090101', carrierName: '株式会社NTTドコモ', source: '総務省 電気通信番号指定状況' },
  },
  {
    digits: '08007779633', lineType: 'free0800', lineTypeName: 'フリーダイヤル（0800）',
    block: { prefix: '0800777', carrierName: 'KDDI株式会社', source: '総務省 電気通信番号指定状況' },
  },
  {
    digits: '0120923499', lineType: 'free0120', lineTypeName: 'フリーダイヤル（0120）',
    block: { prefix: '0120923', carrierName: 'KDDI株式会社', source: '総務省 電気通信番号指定状況' },
  },
];
export const FALLBACK_HEROES = FALLBACK_FACTS.map((f) => randomHero(f));

// The number-anatomy showcase unit (owner's form ruling 2026-11-10):
// pastel rounded boxes side by side with hyphens between them — the
// segment's digits big inside the box (a drill-down link), the
// segment's name in small type UNDER the box. Shared by the homepage
// hero and /tel/examples so the form is identical site-wide. Number
// pages do NOT use this: their coloured boxes stay removed.
function segUnitsHtml(hero) {
  const segColors = ['seg-a', 'seg-b', 'seg-c', 'seg-d'];
  // Each segment links to the page for the digit string accumulated
  // up to it (band page, then block page), and the last one to the
  // number page itself. A lone trunk "0" has no page of its own, so
  // it shares the area-code accumulation.
  const segHref = (i) => {
    let acc = hero.segments.slice(0, i + 1).map((s) => s.text).join('');
    if (acc.length < 2 && hero.segments[i + 1]) acc += hero.segments[i + 1].text;
    return `/tel/${acc}`;
  };
  return hero.segments
    .map(
      (s, i) =>
        `<span class="hero-seg">${i > 0 ? '<span class="hero-hyphen">-</span>' : ''}<span class="segunit"><a class="segbtn ${segColors[i % 4]}" href="${segHref(i)}"><span class="segdigits">${esc(s.text)}</span></a><span class="segcap">${esc(s.label)}</span></span></span>`
    )
    .join('');
}

// Homepage hero: one server-drawn real number per load, segments as
// drill-down links. Owner rulings, layered: the shuffle button and the
// per-number detail link stay removed (a refresh redraws; the last
// segment link is the detail link); the plain-language sentence under
// the example stays removed (#149); the showcase itself was moved to
// /tel/examples and then restored here as well (2026-11-10) — both
// pages now render the same randomHero payload, /tel/examples with
// the full segment explanations and facts.
export function homePage(hero, popular, latestComments) {
  const segHtml = segUnitsHtml(hero);
  const popularHtml =
    popular && popular.length
      ? `<section class="card col-popular" aria-label="よく調べられている番号">
<h2>よく調べられている番号</h2>
<ul class="tight">
${popular.map((p) => `<li><a href="/tel/${esc(p.digits)}">${esc(p.formatted)}</a> <span class="meta">調べられた回数：${esc(String(p.views))}回</span></li>`).join('\n')}
</ul>
<p class="meta">このサイトで実際に調べられた回数が多い番号です。</p>
</section>`
      : '';
  const commentsHtml =
    latestComments && latestComments.length
      ? `<section class="card col-comments" aria-label="最新の口コミ">
<h2>最新の口コミ</h2>
<ul class="tight">
${latestComments.map((c) => `<li><a href="/tel/${esc(c.number)}">${esc(c.formatted)}</a>：${esc(c.body)} <span class="meta">（${esc(String(c.created_at).slice(0, 10))}）</span></li>`).join('\n')}
</ul>
</section>`
      : '';
  // Both boxes: two columns, comments left / popular right (stacked with
  // popular first on narrow screens). A lone box renders as before.
  const lowerHtml =
    popularHtml && commentsHtml
      ? `<div class="home-cols">${commentsHtml}\n${popularHtml}</div>`
      : commentsHtml
        ? `<div class="home-cols single">${commentsHtml}</div>`
        : popularHtml;
  const body = `
<div class="home">
<h1 class="sr-only">日本の電話番号データベース</h1>
<form class="bigsearch" action="/tel/search" method="get" role="search">
<input type="search" name="q" autocomplete="off" placeholder="電話番号を入力（例：090-1234-5678）" aria-label="電話番号">
<button type="submit" aria-label="調べる" title="調べる">${ICON_SEARCH}</button>
</form>
<p class="hero-kicker" id="heroKicker">${esc(hero.kicker)}</p>
<div class="hero-num" id="heroSegs" role="group" aria-label="電話番号の各部分">
${segHtml}
</div>
${lowerHtml}
<div class="card"><h2>発生状況</h2>
<p>SNS型投資・ロマンス詐欺を含む特殊詐欺（2025年1月～12月）：認知件数合計 43,000件、合計被害額 約3,257億4,258万7,000円。</p>
<p>厳重注意地域（認知件数 上位5）：東京 5,879／大阪 4,830／愛知 3,505／神奈川 3,338／兵庫 3,238</p>
<picture><source media="(max-width:640px)" srcset="/tel/assets/sumally_map_full_sp.png"><img class="mapimg" src="/tel/assets/sumally_map_full.png" alt="SNS型投資・ロマンス詐欺を含む特殊詐欺の都道府県別発生状況マップ（2025年1月～12月）" loading="lazy"></picture>
<p class="meta">出典：警察庁「<a href="https://www.npa.go.jp/bureau/safetylife/sos47/" rel="noopener">SOS47 特殊詐欺対策ページ</a>」／期間：2025年1月～12月、公共データ利用規約（PDL1.0）に基づき掲載</p></div>
<p class="home-links"><a class="navpill" href="/tel/soudan">公的相談窓口</a> ・ <a href="/tel/popular">よく検索される電話番号</a> ・ <a href="/tel/ranges">番号区段インデックス</a> ・ <a href="/tel/gov">政府機関の番号</a> ・ <a href="/tel/history">日本の電話番号の歴史</a> ・ <a href="/tel/examples">日本の電話番号の例</a> ・ <a href="/tel/about">このサイトについて</a></p>
</div>`;
  return layout({
    title: '日本の電話番号データベース',
    description: '日本の電話番号データベース。番号の種類・地域・単位料金区域（MA）・割当事業者・指定時期を公的データで表示。公的機関の公表情報も出典付きで転載。',
    canonical: 'https://datumoj.com/tel/',
    body,
  });
}

// tel: 直拨（「電話する」ボタン）は公的相談・通報窓口の3番号だけに出す
// （ユーザー指示 2026-11-07）。通常の番号に出すと、不審な番号への誤発信を
// 招くおそれがあるため、対象はこの集合に固定し、安易に広げないこと。
const DIAL_NUMBERS = new Set(['0120924839', '0361621111', '0120344999']);

export function numberPage(facts) {
  const f = facts;
  const formatted = formatNumber(f.digits, f);
  // Breadcrumb data feeds only the BreadcrumbList JSON-LD below. The
  // visible breadcrumb trail and the top back-link line were removed in
  // the 2026-11-09 slimming (owner's compromise: the one visible up-link
  // is the 番号区段 row in the facts table); the coloured segment boxes
  // and the seg-explain card went with them — segment explanations live
  // on the drill-down pages now, not on number pages.
  const crumbs = [{ name: 'ホーム', url: 'https://datumoj.com/tel/' }, { name: f.lineTypeName, url: `https://datumoj.com/tel/type/${f.lineType}` }];
  if (f.areaCode) crumbs.push({ name: `市外局番 ${f.areaCode}`, url: `https://datumoj.com/tel/area/${f.areaCode}` });
  crumbs.push({ name: formatted, url: `https://datumoj.com/tel/${f.digits}` });

  const rows = [];
  // Three rows, three notations (owner rulings 2026-11-10): hyphen /
  // parentheses / digits-only, one form per row, with the short row
  // labels ハイフン / かっこ / 数字のみ. The forms are the site's
  // single notation trio — the same forms the prefix pages use for
  // 範囲 rows and the group pages use in each number unit; the
  // parenthesised form is built from splitNumber, the same primitive
  // lib/drilldown.js parenFormat wraps.
  const segParts = splitNumber(f.digits, f);
  const paren = segParts.length >= 2 ? `(${segParts[0]})${segParts.slice(1).join('-')}` : f.digits;
  rows.push(`<tr><th>ハイフン</th><td>${esc(formatted)}</td></tr>`);
  rows.push(`<tr><th>かっこ</th><td>${esc(paren)}</td></tr>`);
  rows.push(`<tr><th>数字のみ</th><td>${esc(f.digits)}</td></tr>`);
  rows.push(`<tr><th>調べられた回数</th><td><span id="viewCount">${typeof f.viewCount === 'number' ? esc(String(f.viewCount)) + ' 回' : '集計中…'}</span></td></tr>`);
  rows.push(`<tr><th>口コミ件数</th><td><a href="#comments">${typeof f.commentCount === 'number' ? f.commentCount : 0} 件</a></td></tr>`);
  rows.push(`<tr><th>番号の種類</th><td>${esc(f.lineTypeName)}</td></tr>`);
  // Fixed-line anatomy （市外局番/市内局番/加入者番号）: rendered only when
  // the official decomposition really applies — a known area code whose
  // anatomy splits into exactly the four official parts.
  if (f.lineType === 'fixed' && f.areaCode) {
    const parts = anatomySegments(f.digits, f);
    if (parts.length === 4) {
      rows.push(`<tr><th>市外局番</th><td>0${esc(parts[1])}</td></tr>`);
      rows.push(`<tr><th>市内局番</th><td>${esc(parts[2])}</td></tr>`);
      rows.push(`<tr><th>加入者番号</th><td>${esc(parts[3])}</td></tr>`);
    }
  }
  // The one visible up-link on the page (owner's compromise, 2026-11-09):
  // the number's 番号区段 row, rendered only when the block page provably
  // exists (facts.blockExists, resolved in index.js against the bucket
  // manifest) — an ungenerated block must never be linked.
  const blockKey = f.digits.slice(0, 7);
  if (f.blockExists) {
    rows.push(`<tr><th>番号区段</th><td><a href="/tel/${blockKey}">${esc(blockKey)}</a></td></tr>`);
  }
  if (f.prefecture) rows.push(`<tr><th>都道府県</th><td>${esc(f.prefecture)}</td></tr>`);
  if (f.mas && f.mas.length === 1) {
    rows.push(`<tr><th>単位料金区域（MA）</th><td>${esc(f.mas[0].charge_area_name)}MA（番号区画コード ${esc(f.mas[0].zone_code)}）</td></tr>`);
    rows.push(`<tr><th>対象地域</th><td>${esc(f.mas[0].region)}</td></tr>`);
  } else if (f.mas && f.mas.length > 1) {
    const list = f.mas.map((m) => `${esc(m.charge_area_name)}MA（${esc(m.zone_code)}）：${esc(m.region)}`).join('<br>');
    rows.push(`<tr><th>単位料金区域（MA）</th><td>この市外局番には複数のMAがあり、市内局番によって分かれます。<br>${list}</td></tr>`);
  }
  rows.push(
    `<tr><th>割当事業者</th><td>${f.block.carrierName ? esc(f.block.carrierName) : '事業者別の指定データは整備中です（番号帯レベルの情報のみ表示しています）'}</td></tr>`
  );
  if (f.block.designatedOn) rows.push(`<tr><th>番号帯の指定時期</th><td>${esc(f.block.designatedOn)}（この番号帯が指定可能になった時期。個別番号の契約時期ではありません）</td></tr>`);
  if (f.block.note) rows.push(`<tr><th>番号帯について</th><td>${esc(f.block.note)}</td></tr>`);
  // Filler rows removed in the 2026-11-09 slimming: the 番号ポータビリティ
  // boilerplate row and the mobile/050 「地域は特定できません」 no-information
  // row. Portability stays covered site-wide by the layout footer note.

  let sections = '';
  // Government agency identification (official-numbers database). One number
  // may be shared by several agencies — list them all.
  if (f.gov) {
    const govItems = (f.govList && f.govList.length ? f.govList : [f.gov])
      .map(
        (g) => `<h2><span class="badge">政府機関</span>${esc(g.agency_name)}</h2>
<p>この番号は政府機関「${esc(g.agency_name)}」の${esc(g.number_kind)}番号として、公式サイトで公表されています。${g.label ? esc(g.label) : ''}</p>
<p class="meta">出典：${extLink(g.source_url, '公式ページ', ' rel="noopener"')}（${esc(g.verified_on || '-')} 確認）</p>`
      )
      .join('');
    sections += `<div class="card">${govItems}
<p class="meta">注意：詐欺では実在する政府機関の番号が偽装表示されることがあります。番号表示だけで相手を信用せず、不審な場合は一度切って公式サイトの番号へかけ直してください。</p></div>`;
  }
  // Public-authority published information (third-party flags). The
  // feature label was renamed from 第三者標識 to 公的機関の公表情報
  // (2026-11-09); the internal table/view names are unchanged.
  if (f.tpFlags.length) {
    const items = f.tpFlags
      .map(
        (x) => `<div class="card flag"><p><span class="badge">公的機関の公表情報・${esc(x.source_tier)}級</span><b>${esc(x.publisher)}（${esc(x.source_name)}）</b></p>
<p>${esc(x.label)}${x.published_on ? `（${esc(x.published_on)} 公表）` : ''}</p>
<p class="meta">出典：${x.evidence_url || x.source_url ? extLink(x.evidence_url || x.source_url, '元の公表ページ', ' rel="nofollow noopener"') : '公表資料'}／最終確認：${esc(x.last_verified || '-')}</p></div>`
      )
      .join('');
    sections += `<h2>公的機関の公表情報</h2>${items}
<p class="meta">注意：発信者番号は偽装されることがあります。この公表情報は「この番号表示が使われた」という記録であり、番号の契約者本人による行為とは限りません。</p>`;
  } else {
    sections += `<div class="card"><h2>公的機関の公表情報</h2>
<p>現在、この番号に対する公的機関の公表情報は登録されていません。<span class="meta">※公表情報が無いことは、その番号が安全であることを意味しません。不審な電話については警察相談専用電話「#9110」へご相談ください。</span></p></div>`;
  }
  // Business
  if (f.business) {
    sections += `<div class="card"><h2>事業者情報</h2><table>
<tr><th>名称</th><td>${esc(f.business.name)}</td></tr>
${f.business.address ? `<tr><th>所在地</th><td>${esc(f.business.address)}</td></tr>` : ''}
${f.business.category ? `<tr><th>業種</th><td>${esc(f.business.category)}</td></tr>` : ''}
</table><p class="meta">出典：${esc(f.business.source || '-')}（${esc(f.business.updated_at || '-')} 時点）</p></div>`;
  }
  // Reports
  if (f.reportCount > 0) {
    const items = f.reports.map((r) => `<li>${esc(r.category)}：${r.n}件（最新 ${esc(r.latest || '-')}）</li>`).join('');
    sections += `<div class="card"><h2>利用者からの報告</h2><p>この番号について ${f.reportCount} 件の報告が寄せられています。</p><ul class="tight">${items}</ul>
<p class="meta">報告は利用者の投稿を審査のうえ集計したもので、事実認定ではありません。</p></div>`;
  }
  // 口コミ comments (owner rules: <=10 chars, no hostile language —
  // enforced server-side in lib/social.js). Loaded by script because the
  // page HTML itself is edge-cached.
  sections += `<div class="card" id="comments"><h2>口コミ</h2>
<p class="meta">10文字以内。人を傷つける言葉は投稿できません。口コミは投稿者の感想であり、事実を示すものではありません。</p>
<ul class="tight" id="cmtList"><li class="meta">読み込み中…</li></ul>
<form id="cmtForm" autocomplete="off"><input type="text" id="cmtInput" maxlength="10" placeholder="10文字以内で感想を" aria-label="口コミ"> <button type="submit" aria-label="投稿する" title="投稿する">${ICON_SEND}</button></form>
<p class="meta" id="cmtMsg"></p></div>`;
  // Number-page FAQ: removed entirely (owner verdict 2026-11-09) —
  // visible Q&A and the FAQPage JSON-LD both go. Site-level questions
  // live on /tel/about instead. Page structure ends at 口コミ.

  const jsonLd = {
    '@context': 'https://schema.org',
    '@graph': [
      {
        '@type': 'WebPage',
        name: `${formatted} の電話番号情報`,
        url: `https://datumoj.com/tel/${f.digits}`,
        breadcrumb: {
          '@type': 'BreadcrumbList',
          itemListElement: crumbs.map((c, i) => ({ '@type': 'ListItem', position: i + 1, name: c.name, item: c.url })),
        },
      },
    ],
  };
  const dialHtml = DIAL_NUMBERS.has(f.digits)
    ? `<p class="dialrow"><a class="dialbtn" href="tel:${f.digits}">電話する（${esc(formatted)}）</a></p>`
    : '';
  const body = `
<h1>${esc(formatted)} の電話番号情報</h1>
${dialHtml}
<script>(function(){
  // This script sits BEFORE the facts table / comment form in the HTML,
  // so every getElementById below would return null if it ran during
  // parsing (that is exactly why the live view counter never updated:
  // vc was captured as null and the beacon's answer was discarded).
  // Defer all DOM work until the document has been parsed.
  function init(){
  var D='${f.digits}';
  fetch('/tel/view/'+D,{method:'POST'}).then(function(r){return r.json();}).then(function(j){var el=document.getElementById('viewCount');if(el)el.textContent=(j.views||0)+' 回';}).catch(function(){var el=document.getElementById('viewCount');if(el)el.textContent='-';});
  var list=document.getElementById('cmtList'),form=document.getElementById('cmtForm'),input=document.getElementById('cmtInput'),msg=document.getElementById('cmtMsg');
  function showComments(cs){
    list.innerHTML='';
    if(!cs.length){var li0=document.createElement('li');li0.className='meta';li0.textContent='まだ口コミはありません。';list.appendChild(li0);return;}
    cs.forEach(function(cm){var li=document.createElement('li');li.textContent=cm.body+' ';var s=document.createElement('span');s.className='meta';s.textContent='（'+String(cm.created_at).slice(0,10)+'）';li.appendChild(s);list.appendChild(li);});
  }
  fetch('/tel/comments.json?n='+D).then(function(r){return r.json();}).then(function(j){showComments(j.comments||[]);}).catch(function(){});
  form.addEventListener('submit',function(ev){
    ev.preventDefault();
    fetch('/tel/comment',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({number:D,body:input.value})})
      .then(function(r){return r.json().then(function(j){return{ok:r.ok,j:j};});})
      .then(function(res){
        if(res.ok){input.value='';msg.textContent='投稿しました。';showComments(res.j.comments||[]);}
        else{msg.textContent=res.j.message||'投稿できませんでした。';}
      }).catch(function(){msg.textContent='投稿できませんでした。';});
  });
  }
  if(document.readyState==='loading'){document.addEventListener('DOMContentLoaded',init);}else{init();}
})();</script>
<div class="card"><table>${rows.join('')}</table></div>
${sections}
${searchForm()}
<p class="meta">データ出典：${esc(f.block.source || '総務省 電気通信番号計画')}。このページは区画データから自動生成されています。</p>`;
  // tel/seo-watch（第3ラウンド）: バックログ4「タイトル・説明文の磨き込み」。
  // 番号そのものの検索意図（誰から／迷惑電話か／通話料は）に、本文と同じ事実
  // （総務省指定データ・第三者機関標識）で正直に答えられる範囲で書く。
  // 新たな断定・キーワード詰め込みはしない。
  const carrier = f.block.carrierName || '';
  const freeDial = f.lineType === 'free0800' || f.lineType === 'free0120';
  const titleBits = [
    f.gov ? f.gov.agency_name : '',
    f.lineTypeName,
    carrier ? `割当${carrier}` : '',
    f.prefecture || '',
    freeDial ? '通話料無料' : '',
  ].filter(Boolean);
  const flagNote = f.tpFlags.length
    ? `公的機関の公表情報が${f.tpFlags.length}件登録されています。`
    : '現時点で公的機関の公表情報の登録はありません。';
  return layout({
    title: `${formatted} の電話番号情報｜${titleBits.join('・')}`,
    description: `${formatted}（数字のみ表記：${f.digits}）は${f.lineTypeName}の番号です。${carrier ? `総務省の指定データでは割当事業者は${carrier}です。` : ''}${freeDial ? '通話料は着信側が負担し、かける側は無料です。' : ''}${f.prefecture ? `地域は${f.prefecture}です。` : ''}${flagNote}知らない番号への対処法も掲載しています。`,
    robots: f.tier === 1 ? undefined : 'noindex,follow',
    canonical: `https://datumoj.com/tel/${f.digits}`,
    jsonLd,
    body,
  });
}

// /tel/examples — 日本の電話番号の例. The number-anatomy showcase that
// used to sit on the homepage moved here wholesale (owner decision
// 2026-11-09: it meant little to first-time visitors there). One
// server-drawn real example per load: segment drill-down links, every
// segment's explanation, and the facts list — the full randomHero
// payload, rendered. The homepage links here not at all; the single
// inbound link lives on /tel/about.
// /tel/soudan — 公的相談窓口. The consultation-hotline block that used
// to sit on the homepage moved here (owner decision 2026-11-09) and got
// its own page: /tel/gov is the government-agency number directory, a
// different list. Copy is the homepage block's own text, unedited —
// names, numbers, purposes and sources as published by each operator.
// The embedded video is the 総務省動画チャンネル (MIC official) CM
// 「【特殊詐欺】＋番号など知らない番号は一旦スルー！　黒電話篇」
// (YouTube id XL86eC1TDZ4); embedding permission verified 2026-11-09
// via the YouTube oEmbed endpoint (HTTP 200, author 総務省動画
// チャンネル). Served from youtube-nocookie with lazy loading, in line
// with the site's no-tracking stance.
// Page order (2026-11-10): the MIC video card first, then the
// hotline cards. The 発生状況 block that launched here moved to the
// homepage the same day; its map is a self-hosted copy of the
// National Police Agency SOS47 page's map (公共データ利用規約
// PDL1.0; attribution printed beside it), stored in R2 under
// assets/ and served by the whitelisted /tel/assets route — no
// hotlinking to npa.go.jp. To refresh it, re-download from the
// SOS47 page and re-upload both map files.
const SOUDAN_VIDEO_ID = 'XL86eC1TDZ4';
const SOUDAN_VIDEO_TITLE = '【特殊詐欺】＋番号など知らない番号は一旦スルー！　黒電話篇';
export function soudanPage() {
  const body = `
<p class="crumbs"><a href="/tel/">ホーム</a> › 公的相談窓口</p>
<h1>公的相談窓口</h1>
<div class="card"><h2>総務省の啓発動画</h2>
<p class="meta">総務省動画チャンネル「${SOUDAN_VIDEO_TITLE}」</p>
<div class="videowrap"><iframe src="https://www.youtube-nocookie.com/embed/${SOUDAN_VIDEO_ID}" title="${SOUDAN_VIDEO_TITLE}" loading="lazy" allow="accelerometer; autoplay; encrypted-media; picture-in-picture" allowfullscreen referrerpolicy="strict-origin-when-cross-origin"></iframe></div></div>
<p>迷惑電話や特殊詐欺で困ったときの、公的機関・団体の相談・通報窓口です。番号を押すと、そのまま電話をかけられます。</p>
<div class="card"><h2>特殊詐欺の情報提供：警察庁「匿名通報ダイヤル」</h2>
<p><a class="soudan-num" href="tel:0120924839">0120-924-839</a>（通話料無料） <a href="/tel/0120924839">番号の詳細</a></p>
<p>特殊詐欺の被疑者や犯行拠点に関する情報をお持ちの方は、この窓口で情報提供できます（匿名で通報できます）。有力な情報には、警察庁の制度により最高で100万円の報奨金が支払われる場合があります。</p>
<p class="meta">出典：<a href="https://www.npa.go.jp/bureau/safetylife/sos47/apps/" rel="noopener">警察庁 SOS47</a></p></div>
<div class="card"><h2>迷惑電話の相談：でんわんセンター（迷惑電話対策相談センター）</h2>
<p><a class="soudan-num" href="tel:0361621111">03-6162-1111</a> <a href="/tel/0361621111">番号の詳細</a></p>
<p>総務省の無料相談窓口です。総務省の請負事業として、一般社団法人日本ユニファイド通信事業者協会（JUSA）が受託運営しています。受付時間は平日10時〜17時、相談は無料です（通話料は利用者負担）。</p>
<p class="meta">出典：<a href="https://www.denwan.jp/" rel="noopener">でんわんセンター</a></p></div>
<div class="card"><h2>警察相談専用窓口</h2>
<p><a class="soudan-num" href="tel:%239110">#9110</a></p>
<p>緊急の事件・事故（110番）ではない、警察への相談窓口です。</p>
<p class="meta">出典：警察庁</p></div>
<div class="card"><h2>消費者ホットライン</h2>
<p><a class="soudan-num" href="tel:188">188</a></p>
<p>消費生活に関する相談を、最寄りの消費生活センター等へつなぐ窓口です。</p>
<p class="meta">出典：消費者庁</p></div>
<div class="card"><h2>未公開株通報専用窓口（日本証券業協会）</h2>
<p><a class="soudan-num" href="tel:0120344999">0120-344-999</a> <a href="/tel/0120344999">番号の詳細</a></p>
<p>未公開株に関する通報を受け付ける、日本証券業協会の専用窓口です。</p>
<p class="meta">出典：<a href="https://www.npa.go.jp/bureau/safetylife/sos47/apps/" rel="noopener">警察庁 SOS47</a>（日本証券業協会の窓口として掲載）</p></div>
<p class="meta">当サイトは上記の機関・団体とは関係のない民間の電話番号データベースです。各窓口は、それぞれの運営主体が運営しています。</p>
${searchForm()}`;
  return layout({
    title: '公的相談窓口｜datumoj.com/tel',
    description: '迷惑電話・特殊詐欺で困ったときの公的機関・団体の相談・通報窓口（警察庁 匿名通報ダイヤル、でんわんセンター、#9110、消費者ホットライン188 ほか）。',
    canonical: 'https://datumoj.com/tel/soudan',
    body,
  });
}

export function examplesPage(hero) {
  const segHtml = segUnitsHtml(hero);
  const explains = hero.segments
    .map(
      (s, i) =>
        `<div class="seg-explain" data-seg="${i}"><h2>${esc(s.title)}</h2><p>${esc(s.explain)}</p></div>`
    )
    .join('\n');
  const factRows = hero.facts.map((f) => `<tr><th>${esc(f.k)}</th><td>${esc(f.v)}</td></tr>`).join('');
  const body = `
<p class="crumbs"><a href="/tel/">ホーム</a> › 日本の電話番号の例</p>
<h1>日本の電話番号の例</h1>
<p class="hero-kicker">${esc(hero.kicker)}</p>
<div class="hero-num" role="group" aria-label="電話番号の各部分">
${segHtml}
</div>
${explains}
<div class="card"><table>${factRows}</table></div>
${searchForm()}`;
  return layout({
    title: '日本の電話番号の例｜datumoj.com/tel',
    description: '日本の電話番号の構成（番号帯・市外局番・市内局番・加入者番号）を、実在の番号区画から選んだ例で説明します。',
    robots: 'noindex,follow',
    canonical: 'https://datumoj.com/tel/examples',
    body,
  });
}

export function areaPage(hub) {
  const rows = hub.mas
    .map((m) => `<tr><th>${esc(m.charge_area_name)}MA<br><span class="meta">番号区画コード ${esc(m.zone_code)}</span></th><td>${esc(m.region)}</td></tr>`)
    .join('');
  const body = `
<p class="crumbs"><a href="/tel/">ホーム</a> › <a href="/tel/pref/${encodeURIComponent(hub.prefecture)}">${esc(hub.prefecture)}</a> › 市外局番 ${esc(hub.areaCode)}</p>
<h1>市外局番 ${esc(hub.areaCode)}（${esc(hub.prefecture)}）</h1>
<p>市外局番 ${esc(hub.areaCode)} は ${esc(hub.prefecture)} の固定電話で使われます。単位料金区域（MA）は ${hub.mas.length} 件です。</p>
<div class="card"><table>${rows}</table></div>
${searchForm()}
<p class="meta">この市外局番の番号を調べるには、上の検索欄に完全な電話番号を入力してください。</p>`;
  return layout({
    title: `市外局番 ${hub.areaCode}（${hub.prefecture}）の地域・MA一覧`,
    description: `市外局番 ${hub.areaCode}（${hub.prefecture}）に含まれる単位料金区域（MA）と対象地域の一覧。`,
    canonical: `https://datumoj.com/tel/area/${hub.areaCode}`,
    body,
  });
}

export function prefPage(prefecture, areas) {
  const links = areas.map((a) => `<a href="/tel/area/${esc(a)}">${esc(a)}</a>`).join('');
  const body = `
<p class="crumbs"><a href="/tel/">ホーム</a> › ${esc(prefecture)}</p>
<h1>${esc(prefecture)} の市外局番</h1>
<div class="card"><div class="grid">${links}</div></div>
${searchForm()}`;
  return layout({
    title: `${prefecture} の市外局番一覧`,
    description: `${prefecture} の固定電話の市外局番一覧。各市外局番の単位料金区域（MA）と対象地域を確認できます。`,
    canonical: `https://datumoj.com/tel/pref/${encodeURIComponent(prefecture)}`,
    body,
  });
}

// tel/seo-watch（第2ラウンド）: 種類ハブから Tier 1 番号ページへの内部リンク。
// 対象番号は tel/seo/SEO_LOG.md の SEO 監視対象で、総務省指定データに
// 6 桁以上の区画＋割当事業者を持つ実データページ（sitemap 掲載済み）。
const FEATURED_NUMBERS = {
  free0800: [{ digits: '08007779633', lineType: 'free0800' }],
  free0120: [{ digits: '0120923499', lineType: 'free0120' }],
};

export function typePage(hub) {
  const lt = hub.lineType;
  const featured = FEATURED_NUMBERS[lt.code] || [];
  const featuredHtml = featured.length
    ? `<div class="card"><h2>番号の例</h2><p>この種類で、総務省の指定データに基づく詳細ページがある番号の例です。</p><ul class="tight">${featured
        .map((f) => `<li><a href="/tel/${f.digits}">${esc(formatNumber(f.digits, { lineType: f.lineType }))}</a></li>`)
        .join('')}</ul></div>`
    : '';
  const rows = hub.blocks
    .map(
      (b) => `<tr><th>${esc(b.prefix)}</th><td>${b.designated_on ? esc(b.designated_on) + '〜' : ''} ${esc(b.note || '')}</td></tr>`
    )
    .join('');
  const body = `
<p class="crumbs"><a href="/tel/">ホーム</a> › ${esc(lt.name_ja)}</p>
<h1>${esc(lt.name_ja)}</h1>
<p>${esc(lt.note || '')}（${lt.digits}桁）</p>
<div class="card"><h2>番号帯</h2><table>${rows}</table>
${hub.detailCount ? `<p class="meta">このほか、総務省の指定状況に基づく事業者別区画（6桁単位）が ${hub.detailCount} 件あり、各番号のページで割当事業者として表示されます。</p>` : ''}
</div>
${featuredHtml}
${searchForm()}`;
  return layout({
    title: `${lt.name_ja} の番号帯一覧`,
    description: `${lt.name_ja} の番号帯と指定時期の一覧。`,
    canonical: `https://datumoj.com/tel/type/${lt.code}`,
    body,
  });
}

export function aboutPage(tpSources) {
  const rows = tpSources
    .map(
      (s) => `<tr><th>${esc(s.publisher)}<br><span class="meta">${esc(s.name)}（${esc(s.tier)}級）</span></th><td>${esc(s.note || '')}<br><span class="meta">形式：${esc(s.form || '-')}／入手：${esc(s.access || '-')}</span></td></tr>`
    )
    .join('');
  const body = `
<h1>データについて</h1>
<div class="card"><h2>番号ページの作り方</h2>
<p>電話番号は総務省が「区画」単位で事業者に指定します。本サイトは、番号計画・番号区画（市外局番／単位料金区域MA／番号区画コード）などの公開データをデータベース化し、アクセス時に組み合わせて番号ページを生成しています。個別の加入者の氏名・契約情報は一切保持しません。</p>
<p>「割当事業者」は指定時点の情報です。番号ポータビリティ（MNP）により、現在の契約事業者とは異なる場合があります。事業者別の細かい指定区画は、総務省の指定資料を解析して順次投入します。</p>
</div>
<div class="card"><h2>公的機関の公表情報について</h2>
<p>詐欺等に関する公表情報は、本サイトが独自に判定するものではなく、下記の公的機関の公表情報を出典付きで転載します。公表情報が無いことは安全を意味しません。</p>
<table>${rows}</table></div>
<div class="card"><h2>収録方針</h2>
<p>検索エンジンへの収録は段階的に行います。事業者情報・利用者報告・公的機関の公表情報など実データのある番号ページから収録し、区画情報のみのページは当面 noindex としています。未指定の番号帯にはページを作りません。</p></div>
<div class="card"><h2>よくある質問</h2>
<h3>情報源は何ですか？</h3>
<p>総務省の電気通信番号計画・番号指定状況など、公的機関が公表する資料に基づいています。公的機関の公表情報は、出典を明記して転載します。</p>
<h3>番号の有害・安全を判定していますか？</h3>
<p>判定していません。本サイトが掲載するのは、公的機関の公表情報と番号区画などの実データのみです。公表情報が無いことは、その番号が安全であることを意味しません。</p>
<h3>掲載内容に間違いがある場合は？</h3>
<p>掲載内容は出典の公表情報に基づいています。出典の公表内容が訂正・削除された場合は、本サイトの掲載もそれに合わせて更新します。個別の訂正・削除依頼を受け付ける連絡先は、現在のところ設けていません。</p>
<h3>口コミはどのように扱われますか？</h3>
<p>口コミは利用者が投稿する感想であり、本サイトの判定や事実を示すものではありません。10文字以内で投稿でき、人を傷つける言葉を含む投稿はできません。</p></div>`;
  return layout({
    title: 'データについて｜datumoj.com/tel',
    description: 'datumoj.com/tel のデータ出典、番号ページの生成方法、公的機関の公表情報の転載方針について。',
    canonical: 'https://datumoj.com/tel/about',
    body,
  });
}

export function historyPage() {
  const events = [
    ['1890年', '日本で電話サービスが始まります（東京・横浜）。交換手が手作業で回線をつなぐ時代で、番号は短い通し番号でした。'],
    ['1927年', '火災を知らせる番号として「112」が導入されます。かけ間違いが多かったため、1948年に現在の「119」へ変更されました。'],
    ['1948年', '警察への緊急番号「110」が始まります。'],
    ['1950〜70年代', 'ダイヤル式への自動化と市外局番の整備が進み、固定電話は「市外局番＋市内局番＋加入者番号」で合計10桁（0AB〜J方式）の形に統一されていきます。'],
    ['1979年', '自動車電話が始まります（030＋7桁＝10桁）。持ち運べる電話、すなわち携帯電話の源流です。'],
    ['1985年', '0120フリーダイヤルが始まります。通話料を着信側が負担する仕組みで、企業の窓口番号として広がりました。'],
    ['1987年', '携帯電話サービスが始まります。当初の番号はまだ10桁でした。'],
    ['1995年', 'PHSが始まります（070番号帯）。のちにこの070は携帯電話にも使われるようになります。'],
    ['1999年1月', '携帯電話・PHSの番号が10桁から11桁になります（090へ集約）。契約数の爆発的な増加に番号容量が追いつかなくなったためです。「固定電話＝10桁、携帯電話＝11桁」という現代の形はここで決まりました。'],
    ['1990年代末', '0570ナビダイヤルが始まります。全国共通の10桁番号で、発信地に応じて最寄りの窓口につながります。'],
    ['2000年', '海上保安庁の緊急番号「118」が始まります。'],
    ['2002年3月', '携帯電話に080番号帯が開放されます（090だけでは足りなくなったため）。'],
    ['2003年', '050番号を使うIP電話が広がります。インターネット回線の電話で、11桁・地域に縛られません。'],
    ['2006年10月24日', '番号ポータビリティ（MNP）が始まります。事業者を変えても番号をそのまま使えるようになり、「番号帯＝現在の事業者」とは言えなくなりました。'],
    ['2013年11月', '070番号帯が携帯電話にも開放されます（元はPHS専用でした）。'],
    ['2015年7月', '3桁の公共番号が拡充されます：消費者ホットライン「188」、児童相談所「189」。'],
    ['2019年', '020番号帯がM2M（モノ同士の通信）用に運用開始。人ではなく機器のための番号で、12〜14桁あります。'],
    ['2024年12月', '060番号帯が携帯電話用として新たに指定されます（利用開始は2026年予定）。090/080/070に続く4つ目の携帯番号帯です。'],
    ['2025年1月', '固定電話網のIP網への移行が完了します。メタル回線の時代は終わりましたが、10桁の固定番号そのものはIP網の上で使われ続けています。'],
    ['現在', '携帯電話の契約は2億回線を超えました。番号は「人の声をつなぐ住所」から、「モノの識別子」にまで役割を広げています。'],
  ];
  const rows = events
    .map(([y, t]) => `<tr><th style="width:8.5rem;white-space:nowrap">${esc(y)}</th><td>${esc(t)}</td></tr>`)
    .join('\n');
  const body = `
<p class="crumbs"><a href="/tel/">ホーム</a> › 日本の電話番号の歴史</p>
<h1>日本の電話番号の歴史</h1>
<p>いまの電話番号の形——固定電話は10桁、携帯電話は11桁——は、130年あまりの積み重ねの結果です。桁数が変わるたびに、その背景には「番号が足りなくなる」という問題がありました。</p>
<div class="card"><table>${rows}</table></div>
<div class="card"><h2>このサイトで番号を調べる</h2>
<p>このページは制度の歴史の概説です。個別の番号の割当状況（種類・割当事業者・地域）は、各番号のページで総務省の指定データから確認できます。</p>
<ul class="tight">
<li><a href="/tel/type/free0120">0120フリーダイヤルの番号を調べる</a>（1985年開始）</li>
<li><a href="/tel/type/free0800">0800フリーダイヤルの番号を調べる</a></li>
<li><a href="/tel/gov">政府機関の電話番号一覧</a>（公式サイトの出典付き）</li>
<li><a href="/tel/about">データの出典と作り方</a></li>
</ul></div>
<p class="meta">本ページは総務省「電気通信番号制度」関係資料など公的資料に基づく概説です。個別の番号の割当状況は、各番号のページで確認できます。</p>
${searchForm()}`;
  return layout({
    title: '日本の電話番号の歴史｜datumoj.com/tel',
    description: '1890年の電話開通から、1999年の携帯11桁化、MNP、020や060の新番号帯まで。日本の電話番号が10桁と11桁に分かれた理由をたどる概説。',
    canonical: 'https://datumoj.com/tel/history',
    body,
  });
}

export function govPage(agencies, numbersByAgency) {
  const byAgency = new Map();
  for (const n of numbersByAgency) {
    if (!byAgency.has(n.agency_name)) byAgency.set(n.agency_name, []);
    byAgency.get(n.agency_name).push(n);
  }
  const blocks = agencies
    .map((a) => {
      const nums = (byAgency.get(a.name) || [])
        .map(
          (n) => `<li><a href="/tel/${esc(n.number)}">${esc(n.number)}</a>（${esc(n.number_kind)}）${n.label ? ' — ' + esc(n.label) : ''} <span class="meta">出典：${extLink(n.source_url, '公式ページ', ' rel="noopener"')}</span></li>`
        )
        .join('');
      return `<div class="card"><h2>${esc(a.name)}</h2><ul class="tight">${nums || '<li class="meta">番号は確認中です</li>'}</ul></div>`;
    })
    .join('');
  const body = `
<p class="crumbs"><a href="/tel/">ホーム</a> › 政府機関の電話番号</p>
<h1>政府機関の電話番号</h1>
<p>中央政府機関が公式サイトで公表している電話番号の一覧です。各番号の出典（公式ページ）へのリンク付きで収録しています。詐欺では政府機関の番号が偽装表示されることがあるため、着信番号の表示だけで判断せず、不審な場合は公式サイトで番号を確認のうえ、かけ直してください。</p>
${blocks}
${searchForm()}`;
  return layout({
    title: '政府機関の電話番号一覧｜datumoj.com/tel',
    description: '日本の中央政府機関（省庁・庁・委員会）が公式サイトで公表している代表番号・相談窓口の一覧。出典リンク付き。',
    canonical: 'https://datumoj.com/tel/gov',
    body,
  });
}

export function notAllocatedPage(a, suggestionOk) {
  const sug =
    a.suggestion && suggestionOk
      ? `<div class="card"><h2>もしかして</h2><p>先頭10桁の <a href="/tel/${esc(a.suggestion)}">${esc(a.suggestion)}</a> であれば、番号情報を表示できます。着信履歴の番号をもう一度ご確認ください。</p></div>`
      : '';
  const areaLink = a.areaCode
    ? `<p><a href="/tel/area/${esc(a.areaCode)}">市外局番 ${esc(a.areaCode)} の地域・MA情報を見る</a></p>`
    : '';
  const body = `
<h1>${esc(a.digits)}</h1>
<div class="card"><h2>この番号は割り当てが確認できません</h2>
<p>${esc(a.message)}</p>
<table>
<tr><th>入力された番号</th><td>${esc(a.digits)}（${a.digits.length}桁）</td></tr>
<tr><th>先頭3桁</th><td>${esc(a.p3)}${a.bandName ? ` — ${esc(a.bandName)}` : ''}</td></tr>
${a.expected ? `<tr><th>この番号帯の桁数</th><td>${esc(a.expected)}</td></tr>` : ''}
</table>
<p class="meta">本サイトは、割り当ての確認できない番号には情報ページを作成しません（この案内ページ自体も検索エンジンには収録されません）。</p>
</div>
${sug}
${areaLink}
${searchForm(a.digits)}`;
  return layout({
    title: `${a.digits} は割り当てが確認できません｜datumoj.com/tel`,
    robots: 'noindex',
    body,
  });
}

// "Hot numbers" hub: numbers with confirmed public search demand
// (src/data/hot_numbers.js, ordered by demand), 100 per page. Every entry
// is a real link to the number's own page; line type / carrier come from
// the same official designation snapshot the number pages compose from.
export function popularPage(entries, page, totalPages) {
  const pageUrl = (n) => (n <= 1 ? '/popular' : `/popular?page=${n}`);
  const items = entries
    .map((e) => {
      const formatted = formatNumber(e.digits, { lineType: e.lineType, areaCode: e.areaCode });
      const meta = [e.lineTypeName, e.carrierName].filter(Boolean).join('・');
      return `<li><a href="/tel/${esc(e.digits)}">${esc(formatted)}</a>${meta ? ` <span class="meta">${esc(meta)}</span>` : ''}</li>`;
    })
    .join('\n');
  const nav = [];
  if (page > 1) nav.push(`<a href="${pageUrl(page - 1)}">← 前の100件</a>`);
  nav.push(`<span class="meta">${page} / ${totalPages} ページ</span>`);
  if (page < totalPages) nav.push(`<a href="${pageUrl(page + 1)}">次の100件 →</a>`);
  const body = `
<p class="crumbs"><a href="/tel/">トップ</a> ＞ よく検索される電話番号</p>
<h1>よく検索される電話番号</h1>
<p class="meta">公開統計で検索需要が高いと確認できた番号を、需要の高い順に並べた一覧です（${(page - 1) * 100 + 1}〜${(page - 1) * 100 + entries.length}件目）。各番号の詳細ページでは、総務省の指定データに基づく番号の種類・割当事業者を表示します。</p>
<section class="card" aria-label="よく検索される電話番号">
<ul class="tight">
${items}
</ul>
</section>
<p>${nav.join('　')}</p>`;
  return layout({
    title: 'よく検索される電話番号｜datumoj.com/tel',
    description: '検索需要の高い日本の電話番号の一覧。各番号の種類・割当事業者を総務省の指定データで確認できます。',
    canonical: `https://datumoj.com/tel${pageUrl(page)}`,
    body,
  });
}

// Comment keyword search results (GET /tel/search?q= with a non-number
// query). Entries arrive pre-formatted from index.js ({ number, formatted,
// body, created_at }) and are the same visible comments number pages show.
// noindex like every search-results page; robots.txt already disallows
// /search as well.
export function commentSearchPage(query, results) {
  const items = results
    .map(
      (c) =>
        `<li><a href="/tel/${esc(c.number)}">${esc(c.formatted)}</a>：${esc(c.body)} <span class="meta">（${esc(String(c.created_at).slice(0, 10))}）</span></li>`
    )
    .join('\n');
  const body = `
<p class="crumbs"><a href="/tel/">ホーム</a> › 口コミ検索</p>
<h1>「${esc(query)}」を含む口コミ</h1>
${searchForm(query)}
${results.length ? `<div class="card"><ul class="tight">\n${items}\n</ul></div>` : `<p class="meta">「${esc(query)}」を含む口コミは見つかりませんでした。</p>`}`;
  return layout({
    title: `「${query}」を含む口コミの検索結果`,
    robots: 'noindex,follow',
    body,
  });
}

export function notFoundPage(query) {
  const body = `
<h1>見つかりませんでした</h1>
<p>${query ? `「${esc(query)}」は、日本の電話番号として確認できませんでした。` : 'お探しのページは見つかりませんでした。'}番号が未指定の帯域であるか、桁数が異なる可能性があります。</p>
${searchForm(query || '')}
<p class="meta">入力形式：市外局番から始まる国内番号（例：03-XXXX-XXXX、090-XXXX-XXXX）。国際番号（+81…）も入力できます。</p>`;
  return layout({ title: '見つかりませんでした｜datumoj.com/tel', robots: 'noindex', body });
}

export function unavailablePage() {
  return layout({
    title: '一時的に利用できません｜datumoj.com/tel',
    robots: 'noindex',
    body: `<h1>一時的に利用できません</h1>
<p>現在、データベースへのアクセスが集中しているため、このページを表示できません。しばらくしてから再度お試しください。</p>
<p class="meta">電話番号データベース datumoj.com/tel は総務省の指定データをもとに構成されています。時間をおいてアクセスすると表示できます。</p>
<p><a href="/tel/">トップページへ</a></p>`,
  });
}

// --- Drill-down pages (lib/drilldown.js builds the data) ----------------------

function fmtInt(n) {
  return String(n).replace(/\B(?=(\d{3})+(?!\d))/g, ',');
}

function crumbsHtml(crumbs) {
  return crumbs
    .map((c) => (c.href ? `<a href="${esc(c.href)}">${esc(c.label)}</a>` : esc(c.label)))
    .join(' ＞ ');
}

function explainCard(explain) {
  if (!explain) return '';
  return `<div class="seg-explain"><h2>${esc(explain.title)}</h2><p>${esc(explain.explain)}</p></div>`;
}

// /tel/ranges — 区段索引 hub. Mirrors the pre-generated index page
// (scripts/gen_blocks_index.py): same sections, same salt table, but the
// family links now point at the drill-down prefix pages /tel/<family>.
export function rangesPage(d) {
  const saltRows = d.sections
    .map(
      (s) =>
        `<tr><th>${esc(s.name)}</th><td>${fmtInt(s.agg.blocks)} 区段・対象番号 ${fmtInt(s.agg.numbers)} 件・信号区段 ${fmtInt(s.agg.signals)} 区段${
          s.agg.topCarriers.length
            ? `<br>主な割当事業者：${s.agg.topCarriers.map(([c, n]) => `${esc(c)}（${fmtInt(n)} 区段）`).join('、')}`
            : ''
        }</td></tr>`
    )
    .join('');
  const sections = d.sections
    .map(
      (s) =>
        `<h2>${esc(s.name)}</h2>\n<ul>${s.families
          .map((f) => `<li><a href="/tel/${esc(f.id)}">${esc(f.label)}</a> <span class="meta">${fmtInt(f.blocks)} 区段</span></li>`)
          .join('')}</ul>`
    )
    .join('\n');
  const body = `
<p class="crumbs"><a href="/tel/">ホーム</a> ＞ 区段インデックス</p>
<h1>電話番号の区段インデックス</h1>
<p class="meta">総務省の指定区段 ${fmtInt(d.total)} 件を、番号の種類と区段ごとに一覧できます。各区段のページでは、その区段に含まれる全番号を確認できます。</p>
<div class="card"><table>${saltRows}</table></div>
${sections}
${searchForm()}`;
  return layout({
    title: '電話番号の区段インデックス｜datumoj.com/tel',
    description: '総務省の指定区段を番号の種類・区段ごとに一覧できるインデックス。各区段ページへの入口です。',
    canonical: 'https://datumoj.com/tel/ranges',
    body,
  });
}

// /tel/<prefix> — prefix index: facts about everything beneath the
// prefix plus one level of children (prefixes, block keys, or hundred
// groups), each a real link, so the whole tree is click-reachable.
export function prefixPage(d) {
  const rows = [];
  if (d.typeRows.length === 1) {
    rows.push(`<tr><th>番号の種類</th><td>${esc(d.typeRows[0].name)}</td></tr>`);
  }
  rows.push(`<tr><th>区段数</th><td>${fmtInt(d.blocks)} 区段</td></tr>`);
  rows.push(`<tr><th>対象番号数</th><td>${fmtInt(d.numbers)} 件</td></tr>`);
  if (d.typeRows.length > 1) {
    for (const t of d.typeRows) {
      rows.push(`<tr><th>${esc(t.name)}</th><td>${fmtInt(t.blocks)} 区段・${fmtInt(t.numbers)} 件</td></tr>`);
    }
  }
  if (d.topCarriers.length) {
    rows.push(
      `<tr><th>主な割当事業者</th><td>${d.topCarriers.map(([c, n]) => `${esc(c)}（${fmtInt(n)} 区段）`).join('、')}</td></tr>`
    );
  }
  const childTitle = d.childKind === 'block' ? '区段一覧' : d.childKind === 'group' ? '百号グループ' : '下位の区段';
  const children = d.children.map((c) => `<a href="${esc(c.href)}">${esc(c.label)}</a>`).join(' ');
  const h1 = d.label.endsWith('の区段') ? d.label : `${d.label} の区段一覧`;
  const body = `
<p class="crumbs">${crumbsHtml(d.crumbs)}</p>
<h1>${esc(h1)}</h1>
<div class="card"><table>${rows.join('')}</table></div>
${explainCard(d.explain)}
<h2>${childTitle}</h2>
<p class="grid">${children}</p>
${searchForm()}`;
  return layout({
    title: `${h1}｜datumoj.com/tel`,
    description: `${d.prefix} ではじまる電話番号の区段一覧（全 ${fmtInt(d.blocks)} 区段・対象番号 ${fmtInt(d.numbers)} 件）。`,
    canonical: `https://datumoj.com/tel/${d.prefix}`,
    body,
  });
}

// /tel/<hundred-group prefix> — one hundred (or ten) consecutive numbers,
// every one a real link to its number page. Landed numbers are bold;
// numbers carrying a 公的機関の公表情報 flag get a small badge.
export function groupPage(d) {
  const crumbs = [
    { label: 'ホーム', href: '/tel/' },
    { label: '区段インデックス', href: '/tel/ranges' },
    ...(d.family ? [{ label: d.familyLabel, href: `/tel/${d.family}` }] : []),
    { label: `${d.blockLabel} 区段`, href: `/tel/${d.blockKey}` },
    { label: `${d.fmtLo}〜`, href: null },
  ];
  const rows = [
    `<tr><th>範囲（ハイフン表記）</th><td>${esc(d.fmtLo)} 〜 ${esc(d.fmtHi)}</td></tr>`,
    `<tr><th>範囲（かっこ表記）</th><td>${esc(d.parenLo)} 〜 ${esc(d.parenHi)}</td></tr>`,
    `<tr><th>範囲（数字のみ表記）</th><td>${esc(d.lo)} 〜 ${esc(d.hi)}</td></tr>`,
    `<tr><th>番号の種類</th><td>${esc(d.lineTypeName)}</td></tr>`,
    `<tr><th>割当事業者</th><td>${d.carrierName ? esc(d.carrierName) : '事業者別の指定データは整備中です'}</td></tr>`,
    `<tr><th>区段ページ</th><td><a href="/tel/${esc(d.blockKey)}">この範囲の区段ページ（${esc(d.blockLabel)}）</a></td></tr>`,
  ];
  // Three-form units, same shape as the block pages' signal units:
  // hyphen form is the link; parenthesised + digits-only forms ride
  // along as small text so every notation can be read / copied.
  const items = d.numbers
    .map(
      (n) =>
        `<span class="n"><a href="/tel/${esc(n.digits)}"${n.landed ? ' class="on"' : ''}>${esc(n.formatted)}</a>${
          n.flagged ? ' <span class="badge">公表情報あり</span>' : ''
        }<small>${esc(n.paren)} ${esc(n.digits)}</small></span>`
    )
    .join('\n');
  const body = `
<p class="crumbs">${crumbsHtml(crumbs)}</p>
<h1>${esc(d.fmtLo)} 〜 ${esc(d.fmtHi)} の電話番号一覧</h1>
<div class="card"><table>${rows.join('')}</table></div>
${explainCard(d.explain)}
<div class="grid">
${items}
</div>
<p class="meta">太字は、このサイトに情報が登録されている番号です。</p>
${searchForm()}`;
  return layout({
    title: `${d.fmtLo} 〜 ${d.fmtHi} の電話番号一覧｜datumoj.com/tel`,
    description: `${d.fmtLo} 〜 ${d.fmtHi}（${esc(d.lineTypeName)}）の電話番号一覧。各番号の種類・割当事業者を総務省の指定データで確認できます。`,
    canonical: `https://datumoj.com/tel/${d.prefix}`,
    body,
  });
}
