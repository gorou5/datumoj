// HTML rendering for tel.datumoj.com. No inline client JS; search is a plain GET form.
import { formatNumber, splitNumber, segmentLabels, neighbors } from './number.js';

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
@media(prefers-color-scheme:dark){body{background:#14161a;color:#e8e8e8}a{color:#7db4ff}.card{background:#1e2126;border-color:#333}.meta{color:#aaa}}
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

// Complete structural deconstruction of Japanese phone numbers, drawn as one
// inline SVG (no client JS, no image request). Calm low-saturation palette:
// pale blue = band / area segment, pale sage = middle segment, pale sand =
// subscriber segment, pale rose = toll-free band.
const STRUCTURE_SVG = `
<svg viewBox="0 0 960 690" role="img" aria-label="日本の電話番号の構造図" xmlns="http://www.w3.org/2000/svg">
<rect x="1" y="1" width="958" height="688" rx="18" fill="#fdfcf8" stroke="#e7e1d2"/>
<text x="36" y="46" font-size="21" font-weight="700" fill="#33424f">日本の電話番号の構造</text>
<text x="36" y="70" font-size="12.5" fill="#71808d">先頭の「0」は国内専用の印（トランク）。海外からかける国際表記では「+81」に置き換わります。</text>
<text x="924" y="46" font-size="12" fill="#a3aeb8" text-anchor="end">tel.datumoj.com</text>

<text x="36" y="112" font-size="13.5" font-weight="700" fill="#33424f">固定電話</text>
<text x="36" y="132" font-size="11.5" fill="#71808d">10桁</text>
<g font-size="16.5" font-weight="600" fill="#33424f" text-anchor="middle">
<rect x="112" y="92" width="92" height="38" rx="8" fill="#b9cfdf"/><text x="158" y="117">03</text>
<rect x="212" y="92" width="150" height="38" rx="8" fill="#c3d4b8"/><text x="287" y="117">XXXX</text>
<rect x="370" y="92" width="150" height="38" rx="8" fill="#ecd9ac"/><text x="445" y="117">XXXX</text>
<rect x="112" y="152" width="150" height="38" rx="8" fill="#b9cfdf"/><text x="187" y="177">01372</text>
<rect x="270" y="152" width="56" height="38" rx="8" fill="#c3d4b8"/><text x="298" y="177">X</text>
<rect x="334" y="152" width="150" height="38" rx="8" fill="#ecd9ac"/><text x="409" y="177">XXXX</text>
</g>
<g font-size="11" fill="#71808d" text-anchor="middle">
<text x="158" y="145">市外局番（東京）</text><text x="287" y="145">市内局番</text><text x="445" y="145">加入者番号</text>
<text x="187" y="205">市外局番（北海道・鹿部）</text><text x="298" y="205">市内局番</text><text x="409" y="205">加入者番号</text>
</g>
<text x="548" y="112" font-size="12" fill="#71808d">市外局番は2〜5桁。長いほど市内局番は短く、</text>
<text x="548" y="132" font-size="12" fill="#71808d">合計は常に10桁です（0AB〜J方式）。</text>
<text x="548" y="168" font-size="12" fill="#71808d">市外局番が地域（単位料金区域＝MA）を決めます。</text>

<text x="36" y="252" font-size="13.5" font-weight="700" fill="#33424f">携帯電話</text>
<text x="36" y="272" font-size="11.5" fill="#71808d">11桁</text>
<g font-size="16.5" font-weight="600" fill="#33424f" text-anchor="middle">
<rect x="112" y="232" width="92" height="38" rx="8" fill="#b9cfdf"/><text x="158" y="257">090</text>
<rect x="212" y="232" width="150" height="38" rx="8" fill="#c3d4b8"/><text x="287" y="257">XXXX</text>
<rect x="370" y="232" width="150" height="38" rx="8" fill="#ecd9ac"/><text x="445" y="257">XXXX</text>
</g>
<g font-size="11" fill="#71808d" text-anchor="middle">
<text x="158" y="285">番号帯</text><text x="287" y="285">加入者番号</text><text x="445" y="285">加入者番号</text>
</g>
<text x="548" y="252" font-size="12" fill="#71808d">番号帯は 070 / 080 / 090（060 は 2024 年指定・開始延期中）。</text>
<text x="548" y="272" font-size="12" fill="#71808d">番号ポータビリティで事業者は移るため、帯域から現在の事業者は特定できません。</text>

<text x="36" y="330" font-size="13.5" font-weight="700" fill="#33424f">IP電話</text>
<text x="36" y="350" font-size="11.5" fill="#71808d">11桁</text>
<g font-size="16.5" font-weight="600" fill="#33424f" text-anchor="middle">
<rect x="112" y="310" width="92" height="38" rx="8" fill="#b9cfdf"/><text x="158" y="335">050</text>
<rect x="212" y="310" width="150" height="38" rx="8" fill="#c3d4b8"/><text x="287" y="335">XXXX</text>
<rect x="370" y="310" width="150" height="38" rx="8" fill="#ecd9ac"/><text x="445" y="335">XXXX</text>
</g>
<g font-size="11" fill="#71808d" text-anchor="middle">
<text x="158" y="363">050番号帯</text><text x="287" y="363">加入者番号</text><text x="445" y="363">加入者番号</text>
</g>
<text x="548" y="330" font-size="12" fill="#71808d">インターネット回線の電話。地域に紐づかない全国共通の番号帯です。</text>

<text x="36" y="408" font-size="13.5" font-weight="700" fill="#33424f">フリー</text>
<text x="36" y="428" font-size="11.5" fill="#71808d">10 / 11桁</text>
<g font-size="16.5" font-weight="600" fill="#33424f" text-anchor="middle">
<rect x="112" y="388" width="110" height="38" rx="8" fill="#e3c3c8"/><text x="167" y="413">0120</text>
<rect x="230" y="388" width="110" height="38" rx="8" fill="#ecd9ac"/><text x="285" y="413">XXX</text>
<rect x="348" y="388" width="110" height="38" rx="8" fill="#ecd9ac"/><text x="403" y="413">XXX</text>
<rect x="486" y="388" width="110" height="38" rx="8" fill="#e3c3c8"/><text x="541" y="413">0800</text>
<rect x="604" y="388" width="110" height="38" rx="8" fill="#ecd9ac"/><text x="659" y="413">XXX</text>
<rect x="722" y="388" width="110" height="38" rx="8" fill="#ecd9ac"/><text x="777" y="413">XXXX</text>
</g>
<g font-size="11" fill="#71808d" text-anchor="middle">
<text x="285" y="441">フリーダイヤル（10桁）</text><text x="659" y="441">フリーコール（11桁）</text>
</g>
<text x="36" y="470" font-size="12" fill="#71808d">通話料は着信側（企業など）が負担します。0570 のナビダイヤル（10桁）は全国共通番号で、発信地に応じて最寄りの窓口につながります。</text>

<text x="36" y="512" font-size="13.5" font-weight="700" fill="#33424f">M2M専用</text>
<text x="36" y="532" font-size="11.5" fill="#71808d">12〜14桁</text>
<g font-size="16.5" font-weight="600" fill="#33424f" text-anchor="middle">
<rect x="112" y="492" width="92" height="38" rx="8" fill="#b9cfdf"/><text x="158" y="517">020</text>
<rect x="212" y="492" width="308" height="38" rx="8" fill="#ecd9ac"/><text x="366" y="517">XXXX XXXX X</text>
</g>
<g font-size="11" fill="#71808d" text-anchor="middle">
<text x="158" y="545">020番号帯</text><text x="366" y="545">機器ごとの識別番号</text>
</g>
<text x="548" y="512" font-size="12" fill="#71808d">IoT機器・データ通信専用。音声通話の番号ではありません。</text>

<text x="36" y="592" font-size="13.5" font-weight="700" fill="#33424f">3桁番号</text>
<text x="36" y="612" font-size="11.5" fill="#71808d">特殊番号</text>
<g font-size="12" fill="#33424f" text-anchor="middle">
<rect x="112" y="566" width="86" height="32" rx="16" fill="#efe9db"/><text x="155" y="587">110 警察</text>
<rect x="206" y="566" width="122" height="32" rx="16" fill="#efe9db"/><text x="267" y="587">119 消防・救急</text>
<rect x="336" y="566" width="122" height="32" rx="16" fill="#efe9db"/><text x="397" y="587">118 海上保安庁</text>
<rect x="466" y="566" width="104" height="32" rx="16" fill="#efe9db"/><text x="518" y="587">104 番号案内</text>
<rect x="578" y="566" width="76" height="32" rx="16" fill="#efe9db"/><text x="616" y="587">117 時報</text>
<rect x="662" y="566" width="104" height="32" rx="16" fill="#efe9db"/><text x="714" y="587">177 天気予報</text>
<rect x="112" y="608" width="170" height="32" rx="16" fill="#efe9db"/><text x="197" y="629">188 消費者ホットライン</text>
<rect x="290" y="608" width="128" height="32" rx="16" fill="#efe9db"/><text x="354" y="629">189 児童相談所</text>
<rect x="426" y="608" width="122" height="32" rx="16" fill="#efe9db"/><text x="487" y="629">#9110 警察相談</text>
</g>
<text x="570" y="629" font-size="12" fill="#71808d">短い番号は緊急・公共サービス専用です。</text>
</svg>`;

export function homePage() {
  const body = `
<div class="home">
<h1 class="sr-only">日本の電話番号データベース</h1>
${STRUCTURE_SVG}
<form class="bigsearch" action="/search" method="get" role="search">
<input type="search" name="q" inputmode="numeric" autocomplete="off" placeholder="電話番号を入力（例：090-1234-5678）" aria-label="電話番号">
<button type="submit">調べる</button>
</form>
<p class="home-links"><a href="/gov">政府機関の番号</a> ・ <a href="/about">このサイトについて</a></p>
</div>`;
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
  const segs = splitNumber(f.digits, f);
  const labels = segmentLabels(f.lineType);
  const segHtml = segs
    .map((s, i) => `<div class="seg"><b>${esc(s)}</b><span>${esc(labels[i] || '')}</span></div>`)
    .join('');
  const crumbs = [{ name: 'ホーム', url: 'https://tel.datumoj.com/' }, { name: f.lineTypeName, url: `https://tel.datumoj.com/type/${f.lineType}` }];
  if (f.areaCode) crumbs.push({ name: `市外局番 ${f.areaCode}`, url: `https://tel.datumoj.com/area/${f.areaCode}` });
  crumbs.push({ name: formatted, url: `https://tel.datumoj.com/${f.digits}` });
  const crumbHtml = crumbs.map((c) => `<a href="${esc(c.url.replace('https://tel.datumoj.com', ''))}">${esc(c.name)}</a>`).join(' › ');

  const rows = [];
  rows.push(`<tr><th>電話番号</th><td>${esc(formatted)}（${esc(f.digits)}）</td></tr>`);
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

  const jsonLd = {
    '@context': 'https://schema.org',
    '@type': 'WebPage',
    name: `${formatted} の電話番号情報`,
    url: `https://tel.datumoj.com/${f.digits}`,
    breadcrumb: {
      '@type': 'BreadcrumbList',
      itemListElement: crumbs.map((c, i) => ({ '@type': 'ListItem', position: i + 1, name: c.name, item: c.url })),
    },
  };
  const body = `
<p class="crumbs">${crumbHtml}</p>
<h1>${esc(formatted)}</h1>
<div class="segs">${segHtml}</div>
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
<div class="card"><h2>番号帯</h2><table>${rows}</table></div>
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
