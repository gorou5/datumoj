// HTML rendering for tel.datumoj.com. No inline client JS; search is a plain GET form.
import { formatNumber, splitNumber, anatomySegments, neighbors } from './number.js';

export function esc(s) {
  return String(s ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

const CSS = `
:root{color-scheme:light dark}
*{box-sizing:border-box}
body{font-family:system-ui,-apple-system,"Hiragino Kaku Gothic ProN","Noto Sans JP",sans-serif;margin:0;line-height:1.7;background:#fafafa;color:#1a1a1a}
@media(prefers-color-scheme:dark){body{background:#14161a;color:#e8e8e8}a{color:#7db4ff}.card,.seg-explain{background:#1e2126;border-color:#333}.meta{color:#aaa}}
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
.searchbox input{flex:1;font-size:1.1rem;padding:.6rem;border:1px solid #bbb;border-radius:8px}
.searchbox button{font-size:1.05rem;padding:.6rem 1.1rem;border-radius:8px;border:1px solid #0b57d0;background:#0b57d0;color:#fff;cursor:pointer}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:.4rem .8rem}
.hero-kicker{color:#71808d;font-size:.95rem;margin:0 0 .5rem}
.hero-num{display:flex;align-items:stretch;justify-content:center;gap:.35rem}
.segbtn{border:2px solid transparent;border-radius:14px;padding:.5rem .9rem .35rem;cursor:pointer;font:inherit;background:#eef1f4;text-align:center}
.segbtn .segdigits{display:block;font-size:clamp(1.9rem,8.5vw,3.6rem);font-weight:700;letter-spacing:.04em;line-height:1.15;color:#33424f}
.segbtn .segcap{display:block;font-size:.72rem;color:#71808d;margin-top:.15rem}
.seg-a{background:#dcebf5}.seg-b{background:#e2ecd9}.seg-c{background:#f6ead0}.seg-d{background:#e9e0f2}
.hero-facts{max-width:640px;margin:.9rem auto 0;text-align:left;background:#fbfaf6;border:1px solid #e7e1d2;border-radius:10px;padding:.7rem 1.1rem}
.hero-fact{margin:.25rem 0;font-size:.92rem;color:#3d473a}
.hero-fact b{color:#22303c}
.segbtn.is-active{border-color:#5b84a6;box-shadow:0 3px 14px rgba(60,70,90,.18)}
.hero-hyphen{align-self:center;font-size:clamp(1.4rem,6vw,2.6rem);color:#a3aeb8}
.seg-explain{max-width:640px;margin:1.1rem auto 0;text-align:left;background:#fff;border:1px solid #e7e1d2;border-left:6px solid #7d9db5;border-radius:10px;padding:.9rem 1.1rem}
.seg-explain[data-seg="1"]{border-left-color:#a3b899}
.seg-explain[data-seg="2"]{border-left-color:#dfc08a}
.seg-explain[data-seg="3"]{border-left-color:#b39ddb}
.seg-explain h2{font-size:1.05rem;margin:0 0 .3rem}
.seg-explain p{margin:0}
.hero-note{color:#8a94a0;font-size:.88rem;margin-top:.9rem}
.shuffle{margin-left:.6rem;font:inherit;font-size:.85rem;padding:.25rem .8rem;border-radius:999px;border:1px solid #cfc9ba;background:#fff;cursor:pointer;color:#33424f}
.home{max-width:940px;margin:5vh auto 0;text-align:center}
.home svg{width:100%;height:auto;display:block}
.bigsearch{display:flex;gap:.5rem;max-width:620px;margin:26px auto 0}
.bigsearch input{flex:1;font-size:1.15rem;padding:.85rem 1.3rem;border:1px solid #cfc9ba;border-radius:999px;box-shadow:0 2px 12px rgba(70,60,30,.10);background:#fff;color:#1a1a1a}
.bigsearch button{font-size:1.05rem;padding:.85rem 1.6rem;border-radius:999px;border:1px solid #5b84a6;background:#5b84a6;color:#fff;cursor:pointer}
.home-links{margin-top:16px;font-size:.88rem;color:#8a94a0}
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
<title>${esc(title)}</title>
<meta name="description" content="${esc(description || '')}">
${robots ? `<meta name="robots" content="${esc(robots)}">` : ''}
${canonical ? `<link rel="canonical" href="${esc(canonical)}">` : ''}
${jsonLd ? `<script type="application/ld+json">${JSON.stringify(jsonLd)}</script>` : ''}
<style>${CSS}</style>
</head>
<body>
<header><a class="logo" href="/">tel.datumoj.com</a><span class="meta">日本の電話番号データベース</span></header>
<main>
${body}
</main>
<footer>
<p>データ出典：総務省 電気通信番号計画・番号区画表ほか公開資料（詳細は<a href="/about">データについて</a>）。割当事業者は番号の「指定」情報であり、番号ポータビリティにより現在の契約事業者とは異なる場合があります。本サイトは第三者機関の標識を出典付きで転載するもので、独自に番号の有害性を判定しません。</p>
<p><a href="https://datumoj.com/">datumoj.com</a> のプロジェクトです。</p>
</footer>
</body>
</html>`;
}

function searchForm(value = '') {
  return `<form class="searchbox" action="/search" method="get" role="search">
<input type="tel" name="q" inputmode="tel" autocomplete="off" placeholder="電話番号を入力（例：090-1234-5678）" value="${esc(value)}" aria-label="電話番号">
<button type="submit">調べる</button>
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

export function homePage() {
  const body = `
<div class="home">
<h1 class="sr-only">日本の電話番号データベース</h1>
<p class="hero-kicker" id="heroKicker">日本の電話番号の例</p>
<div class="hero-num" id="heroSegs" role="group" aria-label="電話番号の各部分">
<button type="button" class="segbtn seg-a is-active" data-seg="0" aria-pressed="true"><span class="segdigits" id="segd0">090</span><span class="segcap">番号帯</span></button>
<span class="hero-hyphen">-</span>
<button type="button" class="segbtn seg-b" data-seg="1" aria-pressed="false"><span class="segdigits" id="segd1">XXXX</span><span class="segcap">加入者番号</span></button>
<span class="hero-hyphen">-</span>
<button type="button" class="segbtn seg-c" data-seg="2" aria-pressed="false"><span class="segdigits" id="segd2">XXXX</span><span class="segcap">加入者番号</span></button>
</div>
<div class="seg-explain" id="segExplain" data-seg="0" aria-live="polite">
<h2 id="segTitle">番号帯（070 / 080 / 090）</h2>
<p id="segText">携帯電話の番号帯です。もともとは契約した事業者を表す目印でしたが、2006年に番号ポータビリティ（MNP）が始まって以降、事業者を変えても番号はそのまま使えるため、いまの事業者はこの3桁からは分かりません。2024年には新しい番号帯として060も指定されました。</p>
</div>
<p class="hero-note">表示される番号は、総務省の指定データにある実在の番号区画から毎回ランダムに選ばれています。色のついた部分を押すと、その部分の意味が表示されます。<button type="button" class="shuffle" id="shuffleBtn">別の番号を表示</button> <a id="heroDetail" href="/09012345678" hidden>この番号の詳細ページ</a></p>
<form class="bigsearch" action="/search" method="get" role="search">
<input type="search" name="q" inputmode="numeric" autocomplete="off" placeholder="電話番号を入力（例：090-1234-5678）" aria-label="電話番号">
<button type="submit">調べる</button>
</form>
<p class="home-links"><a href="/gov">政府機関の番号</a> ・ <a href="/history">日本の電話番号の歴史</a> ・ <a href="/about">このサイトについて</a></p>
</div>
<script>
(function(){
  var segsEl=document.getElementById('heroSegs');
  var kick=document.getElementById('heroKicker');
  var titleEl=document.getElementById('segTitle'), textEl=document.getElementById('segText'), card=document.getElementById('segExplain');
  var detail=document.getElementById('heroDetail');
  var COLORS=['seg-a','seg-b','seg-c','seg-d'];
  var data=null;
  function select(i){
    if(!data||!data.segments[i])return;
    var btns=segsEl.querySelectorAll('.segbtn');
    for(var j=0;j<btns.length;j++){btns[j].classList.toggle('is-active',j===i);btns[j].setAttribute('aria-pressed',j===i?'true':'false');}
    titleEl.textContent=data.segments[i].title;
    textEl.textContent=data.segments[i].explain;
    card.setAttribute('data-seg',String(i));
  }
  function render(d){
    data=d;
    kick.textContent=d.kicker;
    segsEl.innerHTML='';
    d.segments.forEach(function(s,i){
      if(i>0){var h=document.createElement('span');h.className='hero-hyphen';h.textContent='-';segsEl.appendChild(h);}
      var b=document.createElement('button');b.type='button';b.className='segbtn '+COLORS[i%COLORS.length];b.setAttribute('aria-pressed','false');
      var ds=document.createElement('span');ds.className='segdigits';ds.textContent=s.text;
      var cs=document.createElement('span');cs.className='segcap';cs.textContent=s.label;
      b.appendChild(ds);b.appendChild(cs);
      b.addEventListener('click',function(){select(i);});
      segsEl.appendChild(b);
    });
    detail.hidden=false;detail.href='/'+d.digits;detail.textContent='この番号（'+d.formatted+'）の詳細ページ';
    select(d.defaultSeg||0);
  }
  function load(){
    fetch('/random.json',{cache:'no-store'}).then(function(r){return r.json();}).then(render).catch(function(){});
  }
  document.getElementById('shuffleBtn').addEventListener('click',load);
  load();
})();
</script>`;
  return layout({
    title: '電話番号検索｜tel.datumoj.com',
    description: '日本の電話番号データベース。番号の種類・地域・単位料金区域（MA）・割当事業者・指定時期を公的データで表示。第三者機関の詐欺標識も出典付きで転載。',
    canonical: 'https://tel.datumoj.com/',
    body,
  });
}

export function numberPage(facts) {
  const f = facts;
  const formatted = formatNumber(f.digits, f);
  const hero = randomHero(f);
  const segColors = ['seg-a', 'seg-b', 'seg-c', 'seg-d'];
  const segHtml = hero.segments
    .map(
      (s, i) =>
        `${i > 0 ? '<span class="hero-hyphen">-</span>' : ''}<button type="button" class="segbtn ${segColors[i % 4]}${i === hero.defaultSeg ? ' is-active' : ''}" data-seg="${i}" aria-pressed="${i === hero.defaultSeg}"><span class="segdigits">${esc(s.text)}</span><span class="segcap">${esc(s.label)}</span></button>`
    )
    .join('');
  const heroJson = JSON.stringify(hero.segments).replace(/<\//g, '<\\/');
  const crumbs = [{ name: 'ホーム', url: 'https://tel.datumoj.com/' }, { name: f.lineTypeName, url: `https://tel.datumoj.com/type/${f.lineType}` }];
  if (f.areaCode) crumbs.push({ name: `市外局番 ${f.areaCode}`, url: `https://tel.datumoj.com/area/${f.areaCode}` });
  crumbs.push({ name: formatted, url: `https://tel.datumoj.com/${f.digits}` });
  const crumbHtml = crumbs.map((c) => `<a href="${esc(c.url.replace('https://tel.datumoj.com', ''))}">${esc(c.name)}</a>`).join(' › ');

  const rows = [];
  rows.push(`<tr><th>電話番号</th><td>${esc(formatted)}（${esc(f.digits)}）</td></tr>`);
  rows.push(`<tr><th>調べられた回数</th><td><span id="viewCount">集計中…</span></td></tr>`);
  rows.push(`<tr><th>番号の種類</th><td>${esc(f.lineTypeName)}</td></tr>`);
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
  if (f.portable) rows.push(`<tr><th>番号ポータビリティ</th><td>対象番号です。割当（指定）事業者と現在の契約事業者が異なる場合があります。</td></tr>`);
  if (f.lineType === 'mobile' || f.lineType === 'ip050') rows.push(`<tr><th>地域</th><td>携帯電話・050番号から地域は特定できません。</td></tr>`);

  let sections = '';
  // Government agency identification (official-numbers database). One number
  // may be shared by several agencies — list them all.
  if (f.gov) {
    const govItems = (f.govList && f.govList.length ? f.govList : [f.gov])
      .map(
        (g) => `<h2><span class="badge">政府機関</span>${esc(g.agency_name)}</h2>
<p>この番号は政府機関「${esc(g.agency_name)}」の${esc(g.number_kind)}番号として、公式サイトで公表されています。${g.label ? esc(g.label) : ''}</p>
<p class="meta">出典：<a href="${esc(g.source_url)}" rel="noopener">公式ページ</a>（${esc(g.verified_on || '-')} 確認）</p>`
      )
      .join('');
    sections += `<div class="card">${govItems}
<p class="meta">注意：詐欺では実在する政府機関の番号が偽装表示されることがあります。番号表示だけで相手を信用せず、不審な場合は一度切って公式サイトの番号へかけ直してください。</p></div>`;
  }
  // Third-party flags
  if (f.tpFlags.length) {
    const items = f.tpFlags
      .map(
        (x) => `<div class="card flag"><p><span class="badge">第三者標識・${esc(x.source_tier)}級</span><b>${esc(x.publisher)}（${esc(x.source_name)}）</b></p>
<p>${esc(x.label)}${x.published_on ? `（${esc(x.published_on)} 公表）` : ''}</p>
<p class="meta">出典：${x.evidence_url || x.source_url ? `<a href="${esc(x.evidence_url || x.source_url)}" rel="nofollow noopener">元の公表ページ</a>` : '公表資料'}／最終確認：${esc(x.last_verified || '-')}</p></div>`
      )
      .join('');
    sections += `<h2>第三者機関による標識</h2>${items}
<p class="meta">注意：発信者番号は偽装されることがあります。この標識は「この番号表示が使われた」という記録であり、番号の契約者本人による行為とは限りません。</p>`;
  } else {
    sections += `<div class="card"><h2>第三者機関による標識</h2>
<p>現在、この番号に対する第三者機関の標識は登録されていません。<span class="meta">※標識が無いことは、その番号が安全であることを意味しません。不審な電話については警察相談専用電話「#9110」へご相談ください。</span></p></div>`;
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
  // Neighbors
  const nb = neighbors(f.digits, 3);
  const nbLinks = [...nb.prev.reverse(), ...nb.next]
    .map((n) => `<a href="/${n}">${esc(formatNumber(n, f))}</a>`)
    .join(' ／ ');
  sections += `<div class="card"><h2>近い番号</h2><p>${nbLinks}</p></div>`;

  // FAQ: visible Q&A generated from this number's facts, mirrored into
  // FAQPage structured data below (the two must stay in sync).
  const who = f.block.carrierName
    ? `割当事業者は ${f.block.carrierName} です。`
    : '割当事業者の詳細データは整備中です。';
  const faq = [
    {
      q: `${formatted} はどこからの電話ですか？`,
      a: `${formatted} は${f.lineTypeName}の番号です。${who}${f.prefecture ? `地域は${f.prefecture}です。` : ''}${f.portable ? '番号ポータビリティの対象のため、現在の契約事業者が割当事業者と異なる場合があります。' : ''}なお発信者番号は偽装されることもあるため、番号表示だけで相手を断定はできません。`,
    },
    {
      q: `${formatted} に電話をかけると料金はかかりますか？`,
      a:
        f.lineType === 'free0120' || f.lineType === 'free0800'
          ? `${formatted} は着信課金（フリーダイヤル）の番号で、発信者の通話料は無料です（着信側が負担します）。`
          : f.lineType === 'navi0570'
            ? 'ナビダイヤル（0570）は発信者負担の有料通話で、通話時間に応じた料金がかかります。'
            : `${formatted} への通話は発信者に通話料がかかります（金額はご契約のプランによります）。`,
    },
    {
      q: '知らない番号から着信がありました。どうすればいいですか？',
      a: 'すぐに折り返さず、まず番号の情報を確認してください。不在着信だけでは通話料は発生しません。金銭の振り込みや個人情報を求められた場合は応じず一度切り、消費生活の相談は消費者ホットライン「188」、事件性がある場合は警察相談専用電話「#9110」へご相談ください。',
    },
  ];
  sections += `<div class="card"><h2>よくある質問</h2>${faq.map((x) => `<h3>${esc(x.q)}</h3><p>${esc(x.a)}</p>`).join('')}</div>`;
  // Anonymous comments (owner rules: <=10 chars, no hostile language —
  // enforced server-side in lib/social.js). Loaded by script because the
  // page HTML itself is edge-cached.
  sections += `<div class="card"><h2>匿名コメント</h2>
<p class="meta">10文字以内・匿名で投稿できます。人を傷つける言葉は投稿できません。コメントは投稿者の感想であり、事実を示すものではありません。</p>
<ul class="tight" id="cmtList"><li class="meta">読み込み中…</li></ul>
<form id="cmtForm" autocomplete="off"><input type="text" id="cmtInput" maxlength="10" placeholder="10文字以内で感想を" aria-label="匿名コメント"> <button type="submit">投稿</button></form>
<p class="meta" id="cmtMsg"></p></div>`;

  const jsonLd = {
    '@context': 'https://schema.org',
    '@graph': [
      {
        '@type': 'WebPage',
        name: `${formatted} の電話番号情報`,
        url: `https://tel.datumoj.com/${f.digits}`,
        breadcrumb: {
          '@type': 'BreadcrumbList',
          itemListElement: crumbs.map((c, i) => ({ '@type': 'ListItem', position: i + 1, name: c.name, item: c.url })),
        },
      },
      {
        '@type': 'FAQPage',
        mainEntity: faq.map((x) => ({
          '@type': 'Question',
          name: x.q,
          acceptedAnswer: { '@type': 'Answer', text: x.a },
        })),
      },
    ],
  };
  const body = `
<p class="crumbs">${crumbHtml}</p>
<h1 class="sr-only">${esc(formatted)} の電話番号情報</h1>
<div class="hero-num" role="group" aria-label="電話番号の各部分">${segHtml}</div>
<div class="seg-explain" id="segExplain" data-seg="${hero.defaultSeg}" aria-live="polite"><h2 id="segTitle">${esc(hero.segments[hero.defaultSeg].title)}</h2><p id="segText">${esc(hero.segments[hero.defaultSeg].explain)}</p></div>
<script>(function(){var SEGS=${heroJson};var btns=document.querySelectorAll('.segbtn');var t=document.getElementById('segTitle'),x=document.getElementById('segText'),c=document.getElementById('segExplain');function sel(i){for(var j=0;j<btns.length;j++){btns[j].classList.toggle('is-active',j===i);btns[j].setAttribute('aria-pressed',j===i?'true':'false');}t.textContent=SEGS[i].title;x.textContent=SEGS[i].explain;c.setAttribute('data-seg',String(i));}for(var k=0;k<btns.length;k++){(function(k){btns[k].addEventListener('click',function(){sel(k);});})(k);}})();</script>
<script>(function(){
  var D='${f.digits}';
  var vc=document.getElementById('viewCount');
  fetch('/view/'+D,{method:'POST'}).then(function(r){return r.json();}).then(function(j){if(vc)vc.textContent=(j.views||0)+' 回';}).catch(function(){if(vc)vc.textContent='-';});
  var list=document.getElementById('cmtList'),form=document.getElementById('cmtForm'),input=document.getElementById('cmtInput'),msg=document.getElementById('cmtMsg');
  function showComments(cs){
    list.innerHTML='';
    if(!cs.length){var li0=document.createElement('li');li0.className='meta';li0.textContent='まだコメントはありません。';list.appendChild(li0);return;}
    cs.forEach(function(cm){var li=document.createElement('li');li.textContent=cm.body+' ';var s=document.createElement('span');s.className='meta';s.textContent='（'+String(cm.created_at).slice(0,10)+'）';li.appendChild(s);list.appendChild(li);});
  }
  fetch('/comments.json?n='+D).then(function(r){return r.json();}).then(function(j){showComments(j.comments||[]);}).catch(function(){});
  form.addEventListener('submit',function(ev){
    ev.preventDefault();
    fetch('/comment',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({number:D,body:input.value})})
      .then(function(r){return r.json().then(function(j){return{ok:r.ok,j:j};});})
      .then(function(res){
        if(res.ok){input.value='';msg.textContent='投稿しました。';showComments(res.j.comments||[]);}
        else{msg.textContent=res.j.message||'投稿できませんでした。';}
      }).catch(function(){msg.textContent='投稿できませんでした。';});
  });
})();</script>
<div class="card"><table>${rows.join('')}</table></div>
${sections}
${searchForm()}
<p class="meta">データ出典：${esc(f.block.source || '総務省 電気通信番号計画')}。このページは区画データから自動生成されています。</p>`;
  return layout({
    title: `${formatted} の電話番号情報｜${f.gov ? f.gov.agency_name + '・' : ''}${f.lineTypeName}${f.prefecture ? '・' + f.prefecture : ''}`,
    description: `${formatted} は${f.lineTypeName}の番号です。${f.prefecture ? '地域は' + f.prefecture + '。' : ''}単位料金区域（MA）・割当事業者・番号帯の指定時期などを公的データで表示。第三者機関の標識も確認できます。`,
    robots: f.tier === 1 ? undefined : 'noindex,follow',
    canonical: `https://tel.datumoj.com/${f.digits}`,
    jsonLd,
    body,
  });
}

export function areaPage(hub) {
  const rows = hub.mas
    .map((m) => `<tr><th>${esc(m.charge_area_name)}MA<br><span class="meta">番号区画コード ${esc(m.zone_code)}</span></th><td>${esc(m.region)}</td></tr>`)
    .join('');
  const body = `
<p class="crumbs"><a href="/">ホーム</a> › <a href="/pref/${encodeURIComponent(hub.prefecture)}">${esc(hub.prefecture)}</a> › 市外局番 ${esc(hub.areaCode)}</p>
<h1>市外局番 ${esc(hub.areaCode)}（${esc(hub.prefecture)}）</h1>
<p>市外局番 ${esc(hub.areaCode)} は ${esc(hub.prefecture)} の固定電話で使われます。単位料金区域（MA）は ${hub.mas.length} 件です。</p>
<div class="card"><table>${rows}</table></div>
${searchForm()}
<p class="meta">この市外局番の番号を調べるには、上の検索欄に完全な電話番号を入力してください。</p>`;
  return layout({
    title: `市外局番 ${hub.areaCode}（${hub.prefecture}）の地域・MA一覧`,
    description: `市外局番 ${hub.areaCode}（${hub.prefecture}）に含まれる単位料金区域（MA）と対象地域の一覧。`,
    canonical: `https://tel.datumoj.com/area/${hub.areaCode}`,
    body,
  });
}

export function prefPage(prefecture, areas) {
  const links = areas.map((a) => `<a href="/area/${esc(a)}">${esc(a)}</a>`).join('');
  const body = `
<p class="crumbs"><a href="/">ホーム</a> › ${esc(prefecture)}</p>
<h1>${esc(prefecture)} の市外局番</h1>
<div class="card"><div class="grid">${links}</div></div>
${searchForm()}`;
  return layout({
    title: `${prefecture} の市外局番一覧`,
    description: `${prefecture} の固定電話の市外局番一覧。各市外局番の単位料金区域（MA）と対象地域を確認できます。`,
    canonical: `https://tel.datumoj.com/pref/${encodeURIComponent(prefecture)}`,
    body,
  });
}

export function typePage(hub) {
  const lt = hub.lineType;
  const rows = hub.blocks
    .map(
      (b) => `<tr><th>${esc(b.prefix)}</th><td>${b.designated_on ? esc(b.designated_on) + '〜' : ''} ${esc(b.note || '')}</td></tr>`
    )
    .join('');
  const body = `
<p class="crumbs"><a href="/">ホーム</a> › ${esc(lt.name_ja)}</p>
<h1>${esc(lt.name_ja)}</h1>
<p>${esc(lt.note || '')}（${lt.digits}桁）</p>
<div class="card"><h2>番号帯</h2><table>${rows}</table>
${hub.detailCount ? `<p class="meta">このほか、総務省の指定状況に基づく事業者別区画（6桁単位）が ${hub.detailCount} 件あり、各番号のページで割当事業者として表示されます。</p>` : ''}
</div>
${searchForm()}`;
  return layout({
    title: `${lt.name_ja} の番号帯一覧`,
    description: `${lt.name_ja} の番号帯と指定時期の一覧。`,
    canonical: `https://tel.datumoj.com/type/${lt.code}`,
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
<p>「割当事業者」は指定時点の情報です。番号ポータビリティ（MNP）により、現在の契約事業者とは異なる場合があります。事業者別の細かい指定区画は、総務省の指定資料を解析して順次投入します。</p></div>
<div class="card"><h2>第三者標識について</h2>
<p>詐欺等に関する標識は、本サイトが独自に判定するものではなく、下記の第三者機関の公表情報を出典付きで転載します。標識が無いことは安全を意味しません。</p>
<table>${rows}</table></div>
<div class="card"><h2>収録方針</h2>
<p>検索エンジンへの収録は段階的に行います。事業者情報・利用者報告・第三者標識など実データのある番号ページから収録し、区画情報のみのページは当面 noindex としています。未指定の番号帯にはページを作りません。</p></div>`;
  return layout({
    title: 'データについて｜tel.datumoj.com',
    description: 'tel.datumoj.com のデータ出典、番号ページの生成方法、第三者標識の転載方針について。',
    canonical: 'https://tel.datumoj.com/about',
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
<p class="crumbs"><a href="/">ホーム</a> › 日本の電話番号の歴史</p>
<h1>日本の電話番号の歴史</h1>
<p>いまの電話番号の形——固定電話は10桁、携帯電話は11桁——は、130年あまりの積み重ねの結果です。桁数が変わるたびに、その背景には「番号が足りなくなる」という問題がありました。</p>
<div class="card"><table>${rows}</table></div>
<p class="meta">本ページは総務省「電気通信番号制度」関係資料など公的資料に基づく概説です。個別の番号の割当状況は、各番号のページで確認できます。</p>
${searchForm()}`;
  return layout({
    title: '日本の電話番号の歴史｜tel.datumoj.com',
    description: '1890年の電話開通から、1999年の携帯11桁化、MNP、020や060の新番号帯まで。日本の電話番号が10桁と11桁に分かれた理由をたどる概説。',
    canonical: 'https://tel.datumoj.com/history',
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
          (n) => `<li><a href="/${esc(n.number)}">${esc(n.number)}</a>（${esc(n.number_kind)}）${n.label ? ' — ' + esc(n.label) : ''} <span class="meta">出典：<a href="${esc(n.source_url)}" rel="noopener">公式ページ</a></span></li>`
        )
        .join('');
      return `<div class="card"><h2>${esc(a.name)}</h2><ul class="tight">${nums || '<li class="meta">番号は確認中です</li>'}</ul></div>`;
    })
    .join('');
  const body = `
<p class="crumbs"><a href="/">ホーム</a> › 政府機関の電話番号</p>
<h1>政府機関の電話番号</h1>
<p>中央政府機関が公式サイトで公表している電話番号の一覧です。各番号の出典（公式ページ）へのリンク付きで収録しています。詐欺では政府機関の番号が偽装表示されることがあるため、着信番号の表示だけで判断せず、不審な場合は公式サイトで番号を確認のうえ、かけ直してください。</p>
${blocks}
${searchForm()}`;
  return layout({
    title: '政府機関の電話番号一覧｜tel.datumoj.com',
    description: '日本の中央政府機関（省庁・庁・委員会）が公式サイトで公表している代表番号・相談窓口の一覧。出典リンク付き。',
    canonical: 'https://tel.datumoj.com/gov',
    body,
  });
}

export function notAllocatedPage(a, suggestionOk) {
  const sug =
    a.suggestion && suggestionOk
      ? `<div class="card"><h2>もしかして</h2><p>先頭10桁の <a href="/${esc(a.suggestion)}">${esc(a.suggestion)}</a> であれば、番号情報を表示できます。着信履歴の番号をもう一度ご確認ください。</p></div>`
      : '';
  const areaLink = a.areaCode
    ? `<p><a href="/area/${esc(a.areaCode)}">市外局番 ${esc(a.areaCode)} の地域・MA情報を見る</a></p>`
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
    title: `${a.digits} は割り当てが確認できません｜tel.datumoj.com`,
    robots: 'noindex',
    body,
  });
}

export function notFoundPage(query) {
  const body = `
<h1>見つかりませんでした</h1>
<p>${query ? `「${esc(query)}」は、日本の電話番号として確認できませんでした。` : 'お探しのページは見つかりませんでした。'}番号が未指定の帯域であるか、桁数が異なる可能性があります。</p>
${searchForm(query || '')}
<p class="meta">入力形式：市外局番から始まる国内番号（例：03-XXXX-XXXX、090-XXXX-XXXX）。国際番号（+81…）も入力できます。</p>`;
  return layout({ title: '見つかりませんでした｜tel.datumoj.com', robots: 'noindex', body });
}
