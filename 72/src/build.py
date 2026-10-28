# -*- coding: utf-8 -*-
import json, html, os, shutil
from data import CHARS, GLOBAL_PROMPT, GLOBAL_PROMPT_EN, NEGATIVE_PROMPT, SHEET_LAYOUT

PALETTE = [
 ("#c96f4a","#e9b44c"),("#7a8450","#d9c27a"),("#5d7a8c","#c96f4a"),("#a44a3f","#e0a458"),
 ("#8c6d4f","#b5c48c"),("#6b7f5e","#e9b44c"),("#b3543f","#7a8450"),("#4f6d7a","#d9a05a"),
 ("#946b54","#8fa98f"),("#71563f","#c96f4a"),("#5e7153","#e0a458"),("#845ec2","#d9c27a"),
]
FACTION_STATS = {
 "真爱粉丝": (95,40,15,70), "挑剔观众": (45,55,60,75), "理性分析": (30,95,35,50),
 "立场交锋": (55,70,85,60), "路过乱入": (50,20,10,90), "高阶抬杠": (35,80,90,65),
}
def stats(c):
    base = FACTION_STATS[c["faction"]]
    v = ((c["id"]*37) % 21) - 10
    v2 = ((c["id"]*53) % 17) - 8
    clamp = lambda x: max(5, min(99, x))
    return dict(emotion=clamp(base[0]+v), reason=clamp(base[1]+v2), fire=clamp(base[2]-v2), presence=clamp(base[3]+v))

out = []
for c in CHARS:
    bg, ac = PALETTE[(c["id"]-1) % len(PALETTE)]
    d = dict(c); d["color"]=bg; d["accent"]=ac; d["stats"]=stats(c)
    d["prompt_cn"] = SHEET_LAYOUT + "角色：" + c["visual"] + "。 " + GLOBAL_PROMPT
    d["prompt_en"] = "Claymation character turnaround sheet: front view, side view, back view of the same character standing side by side, same scale, full body, natural standing pose, pitch-black background, no text, no scenery. Character: " + c["visual"] + ". " + GLOBAL_PROMPT_EN
    imgf = f"img/char-{c['id']:02d}.webp"; avf = f"img/avatar-{c['id']:02d}.webp"
    d["img"] = imgf if os.path.exists(imgf) else None
    d["avatar"] = avf if os.path.exists(avf) else None
    out.append(d)
IMG_COUNT = sum(1 for d in out if d["img"])

data_json = json.dumps(out, ensure_ascii=False)
globals_json = json.dumps(dict(cn=GLOBAL_PROMPT, en=GLOBAL_PROMPT_EN, neg=NEGATIVE_PROMPT), ensure_ascii=False)

HTML = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>72 · 评论区人物资产库 — CHARACTER SELECT | datumoj</title>
<meta name="description" content="自媒体评论区 69 个角色的人物资产库：游戏开场选人式展示，每人一名、一套人设、三句台词、一份粘土三视图生成 Prompt。69 实装 + 3 锁定 = 72 格。">
<style>
:root{--ink:#3b2d20;--paper:#efe3c8;--paper2:#e8d9b8;--mud:#8a6d4f;--rust:#b3543f;--moss:#7a8450;--slate:#5d7a8c;--gold:#e9b44c;--card:#f7efdb}
*{box-sizing:border-box;margin:0;padding:0}
body{background:var(--paper);color:var(--ink);font-family:"PingFang SC","Hiragino Sans GB","Microsoft YaHei",system-ui,sans-serif;
 background-image:radial-gradient(rgba(139,109,79,.14) 1px,transparent 1.4px);background-size:22px 22px}
.wrap{max-width:1180px;margin:0 auto;padding:20px 16px 56px}
header.hero{border:4px solid var(--ink);background:var(--card);box-shadow:8px 8px 0 var(--ink);padding:20px 22px;position:relative;overflow:hidden}
header.hero:after{content:"";position:absolute;inset:0;pointer-events:none;opacity:.5;
 background:radial-gradient(circle at 12% 30%,transparent 26px,rgba(139,109,79,.08) 27px,transparent 30px),radial-gradient(circle at 82% 70%,transparent 20px,rgba(139,109,79,.08) 21px,transparent 24px)}
.kicker{font-family:ui-monospace,Menlo,Consolas,monospace;letter-spacing:.28em;font-size:12px;color:var(--rust);font-weight:700}
h1{font-size:clamp(26px,4.6vw,44px);line-height:1.15;margin:8px 0 6px;font-weight:900}
h1 .n72{color:var(--rust);text-shadow:3px 3px 0 var(--gold)}
.sub{font-size:14.5px;line-height:1.75;color:#5c4a36;max-width:760px}
.progressRow{display:flex;gap:12px;align-items:center;margin-top:14px;flex-wrap:wrap}
.progressBar{flex:1;min-width:200px;height:18px;border:3px solid var(--ink);background:#fff8;border-radius:99px;overflow:hidden}
.progressFill{height:100%;width:95.8%;background:repeating-linear-gradient(45deg,var(--moss) 0 12px,#67743f 12px 24px)}
.progressTxt{font-family:ui-monospace,Menlo,monospace;font-weight:700;font-size:13px}
.blink{animation:blink 1.1s steps(2) infinite;color:var(--rust);font-weight:800;font-family:ui-monospace,Menlo,monospace;font-size:12.5px;letter-spacing:.14em}
@keyframes blink{50%{opacity:0}}
.controls{display:flex;gap:10px;flex-wrap:wrap;margin:22px 0 14px;align-items:center}
.controls input{flex:1;min-width:180px;padding:11px 14px;border:3px solid var(--ink);border-radius:10px;background:#fffdf6;font-size:15px;box-shadow:4px 4px 0 rgba(59,45,32,.85)}
.chips{display:flex;gap:8px;flex-wrap:wrap}
.chip{border:3px solid var(--ink);background:var(--card);padding:6px 12px;border-radius:99px;font-size:13px;font-weight:700;cursor:pointer;box-shadow:3px 3px 0 rgba(59,45,32,.8)}
.chip.on{background:var(--ink);color:var(--paper)}
.chip small{opacity:.65;font-weight:600}
.main{display:grid;grid-template-columns:minmax(0,1.55fr) minmax(320px,1fr);gap:20px;align-items:start}
@media(max-width:900px){.main{grid-template-columns:1fr}}
.grid{display:grid;grid-template-columns:repeat(3,1fr);gap:14px}
@media(max-width:640px){.grid{grid-template-columns:repeat(2,1fr);gap:9px}}
.slot{border:4px solid var(--ink);border-radius:16px;background:var(--card);box-shadow:5px 5px 0 rgba(59,45,32,.85);padding:12px;text-align:left;cursor:pointer;position:relative;transition:transform .08s}
.slot:hover{transform:translate(-1px,-2px)}
.slot.sel{background:#fff3cf;outline:4px solid var(--gold);box-shadow:5px 5px 0 var(--rust)}
.slotHead{display:flex;gap:12px;align-items:center}
.slot .no{font-family:ui-monospace,Menlo,monospace;font-size:11.5px;font-weight:700;opacity:.65;display:block}
.avatarImg{width:78px;height:96px;object-fit:cover;flex:0 0 auto;border:3px solid rgba(59,45,32,.8);border-radius:14px;box-shadow:3px 4px 0 rgba(59,45,32,.35);background:#000}
.bigAvatarImg{width:118px;height:146px;object-fit:cover;flex:0 0 auto;border:3.5px solid var(--ink);border-radius:16px;box-shadow:4px 5px 0 rgba(59,45,32,.4);background:#000}
.turnImg{width:100%;border:3.5px solid var(--ink);border-radius:14px;margin-top:6px;background:#000;display:block}
.turnWait{border:3px dashed var(--mud);border-radius:14px;padding:20px 14px;text-align:center;color:#77644c;font-weight:700;font-size:13.5px;margin-top:6px;background:#00000008}
.detailHead{display:flex;gap:16px;align-items:center}
.avatar{width:78px;height:96px;flex:0 0 auto;margin:0;display:flex;align-items:center;justify-content:center;font-size:40px;position:relative;
 border:3px solid rgba(59,45,32,.75);
 border-radius:47% 53% 55% 45%/52% 46% 54% 48%;
 box-shadow:inset -8px -10px 0 rgba(0,0,0,.13),inset 6px 8px 0 rgba(255,255,255,.22),3px 4px 0 rgba(59,45,32,.35)}
.avatar:after{content:"";position:absolute;inset:0;border-radius:inherit;opacity:.35;
 background:radial-gradient(circle at 30% 28%,transparent 9px,rgba(59,45,32,.14) 10px,transparent 12px),radial-gradient(circle at 68% 62%,transparent 7px,rgba(59,45,32,.12) 8px,transparent 10px),radial-gradient(circle at 52% 82%,transparent 5px,rgba(59,45,32,.12) 6px,transparent 8px)}
.slot .nm{font-size:19px;font-weight:900;line-height:1.2}
.slot .tt{font-size:12.5px;color:#6d5741;margin-top:3px;line-height:1.35}
.slot .fc{font-size:11px;font-weight:700;display:inline-block;margin-top:4px;padding:2px 8px;border-radius:99px;border:2px solid var(--ink)}
.slot.locked{cursor:not-allowed;background:#d9cbaa;color:#8a7a5f;box-shadow:5px 5px 0 rgba(59,45,32,.4)}
.slot.locked .avatar{background:#c9b98f!important;font-size:36px;color:#8a7a5f}
.pager{display:flex;align-items:center;gap:10px;margin-top:16px;flex-wrap:wrap}
.pgbtn{border:3px solid var(--ink);background:var(--card);min-width:42px;height:42px;padding:0 13px;border-radius:11px;font-weight:900;font-size:15px;cursor:pointer;box-shadow:3px 3px 0 rgba(59,45,32,.85)}
.pgbtn.on{background:var(--rust);color:#fff}
.pgbtn:disabled{opacity:.4;cursor:not-allowed}
.pageInfo{font-family:ui-monospace,Menlo,monospace;font-size:13px;font-weight:700;margin-left:auto}
.detail{border:4px solid var(--ink);border-radius:18px;background:var(--card);box-shadow:7px 7px 0 var(--ink);padding:20px;position:sticky;top:16px}
@media(max-width:900px){.detail{position:static}}
.detail .bigAvatar{width:118px;height:146px;font-size:58px;margin:0;border-radius:16px}
.detail h2{text-align:left;font-size:30px;font-weight:900}
.detail .dtitle{text-align:left;color:var(--rust);font-weight:800;margin-top:2px}
.detail .dnote{text-align:left;font-size:12.5px;color:#77644c;margin-top:5px}
.detail .dfc{text-align:left;margin-top:8px}
.detail h3{font-size:13px;letter-spacing:.18em;color:var(--rust);margin:18px 0 7px;font-family:ui-monospace,Menlo,monospace}
.detail p.desc{font-size:14.5px;line-height:1.8}
.bubble{background:#fffdf6;border:2.5px solid var(--ink);border-radius:13px;padding:8px 12px;font-size:13.5px;line-height:1.6;margin:7px 0;position:relative}
.bubble:before{content:"💬";margin-right:6px}
.stat{display:grid;grid-template-columns:64px 1fr 30px;gap:8px;align-items:center;font-size:12.5px;font-weight:700;margin:5px 0}
.statBar{height:11px;border:2.5px solid var(--ink);border-radius:99px;background:#fff8;overflow:hidden}
.statFill{height:100%;background:var(--rust)}
.promptBox{background:#2f2718;color:#f2e4bd;border-radius:12px;padding:12px 13px;font-size:12.5px;line-height:1.75;word-break:break-all;max-height:190px;overflow:auto}
.copyBtn{margin-top:8px;border:3px solid var(--ink);background:var(--gold);padding:8px 14px;border-radius:10px;font-weight:900;cursor:pointer;box-shadow:3px 3px 0 rgba(59,45,32,.9);font-size:13.5px}
.copyBtn:active{transform:translate(2px,2px);box-shadow:1px 1px 0 rgba(59,45,32,.9)}
.bible{margin-top:30px;border:4px solid var(--ink);border-radius:18px;background:var(--card);box-shadow:7px 7px 0 var(--ink);padding:22px}
.bible h2{font-size:21px;margin-bottom:6px}
.bible p{font-size:13.5px;line-height:1.8;color:#5c4a36}
.bible h3{font-size:12.5px;letter-spacing:.16em;color:var(--rust);margin:16px 0 6px;font-family:ui-monospace,Menlo,monospace}
footer{margin-top:26px;text-align:center;font-size:12.5px;color:#77644c;line-height:1.9}
.empty{grid-column:1/-1;text-align:center;padding:44px 10px;font-weight:800;color:#77644c;border:3px dashed var(--mud);border-radius:14px}
.toast{position:fixed;left:50%;bottom:26px;transform:translateX(-50%) translateY(80px);background:var(--ink);color:var(--paper);padding:10px 20px;border-radius:99px;font-weight:800;font-size:14px;transition:transform .25s;z-index:50}
.toast.show{transform:translateX(-50%) translateY(0)}
</style>
</head>
<body>
<div class="wrap">
<header class="hero">
  <div class="kicker">DATUMOJ ASSET LIBRARY · CHARACTER SELECT</div>
  <h1><span class="n72">72</span> 号选人画面 · 评论区人物资产库</h1>
  <p class="sub">自媒体评论区的 <b>69 个角色</b>，全部起了人名，排成游戏开场的选人网格：<b>每页 6 个、共 12 页</b>。点开任意角色，右边就是他的完整资产卡——人设、招牌台词、手感参数（示意），一套粘土三视图生成 Prompt（新版全局风格 + 本角色视觉设定，一键复制），以及 Prompt 下方生成的三视图本体。三视图已生成 <b>__IMG_COUNT__ / 69</b>，分批补齐中。</p>
  <div class="progressRow">
    <span class="progressTxt">UNLOCKED 69 / 72</span>
    <div class="progressBar"><div class="progressFill"></div></div>
    <span class="blink">▶ PRESS A CHARACTER</span>
  </div>
</header>

<div class="controls">
  <input id="q" type="search" placeholder="搜人名 / 角色 / 台词，如：半瓶、抬杠、收藏了…">
  <div class="chips" id="chips"></div>
</div>

<div class="main">
  <div>
    <div class="grid" id="grid"></div>
    <div class="pager" id="pager"></div>
  </div>
  <aside class="detail" id="detail"></aside>
</div>

<section class="bible">
  <h2>🎨 视觉圣经 · 粘土世界全局风格</h2>
  <p>全部 69 个角色共用这一套全局 Prompt（The Neverhood 设计哲学版：捏出来的世界、糖果色派对悬浮于漆黑虚空），生成时拼上角色卡里的「视觉设定」即可。三视图正在按此 Prompt 逐个生成、回填到每个角色卡的 Prompt 下方，头像取自三视图的正面；未生成者暂用按标志性道具做的粘土色占位脸。</p>
  <h3>GLOBAL STYLE · 中文</h3>
  <div class="promptBox" id="gCn"></div>
  <button class="copyBtn" data-copy="cn">📋 复制中文全局 Prompt</button>
  <h3>GLOBAL STYLE · ENGLISH</h3>
  <div class="promptBox" id="gEn"></div>
  <button class="copyBtn" data-copy="en">📋 复制英文全局 Prompt</button>
  <h3>NEGATIVE PROMPT</h3>
  <div class="promptBox" id="gNeg"></div>
  <button class="copyBtn" data-copy="neg">📋 复制 Negative Prompt</button>
</section>

<footer>
  72 = 8 页 × 9 格 · 69 实装 + 3 锁定（70 / 71 / 72 号位留给下一批评论区新物种）<br>
  72.datumoj.com · datumoj 人物资产库 · 角色人设与台词为评论生态观察记录，手感参数为趣味示意
</footer>
</div>
<div class="toast" id="toast">已复制 ✅</div>

<script>
const CHARS = __DATA__;
const GLOBALS = __GLOBALS__;
const PAGE_SIZE = 6;
const FACTIONS = ["全部", ...new Set(CHARS.map(c=>c.faction))];
let faction = "全部", query = "", page = 0, selectedId = 1;

const $ = s => document.querySelector(s);
function filtered(){
  return CHARS.filter(c=>{
    if(faction!=="全部" && c.faction!==faction) return false;
    if(!query) return true;
    const hay = c.name+" "+c.title+" "+c.desc+" "+c.visual+" "+c.lines.join(" ");
    return hay.toLowerCase().includes(query.toLowerCase());
  });
}
function renderChips(){
  $("#chips").innerHTML = FACTIONS.map(f=>{
    const n = f==="全部" ? CHARS.length : CHARS.filter(c=>c.faction===f).length;
    return `<button class="chip ${f===faction?'on':''}" data-f="${f}">${f} <small>${n}</small></button>`;
  }).join("");
  document.querySelectorAll(".chip").forEach(b=>b.onclick=()=>{faction=b.dataset.f;page=0;render();});
}
function avatarHTML(c, big){
  if(c.avatar) return `<img class="${big?'bigAvatarImg':'avatarImg'}" src="${c.avatar}" alt="${c.name}头像">`;
  return `<div class="avatar ${big?'bigAvatar':''}" style="background:radial-gradient(circle at 32% 28%, ${c.accent}, ${c.color} 72%)">${c.icon}</div>`;
}
function renderGrid(){
  const list = filtered();
  const pages = Math.max(1, Math.ceil(list.length/PAGE_SIZE));
  if(page>=pages) page = pages-1;
  const slice = list.slice(page*PAGE_SIZE, page*PAGE_SIZE+PAGE_SIZE);
  let cells = slice.map(c=>`
    <div class="slot ${c.id===selectedId?'sel':''}" data-id="${c.id}" role="button" tabindex="0" aria-label="${c.name} ${c.title}">
      <div class="slotHead">
        ${avatarHTML(c,false)}
        <div><span class="no">No.${String(c.id).padStart(2,'0')}</span>
        <div class="nm">${c.name}</div>
        <div class="tt">${c.title}</div>
        <span class="fc" style="background:${c.accent}55">${c.faction}</span></div>
      </div>
    </div>`).join("");
  // 未筛选、最后一页：补 3 个锁定格凑满 72
  if(faction==="全部" && !query && page===pages-1){
    for(let n=70;n<=72;n++){
      cells += `<div class="slot locked"><div class="slotHead"><div class="avatar">🔒</div><div><span class="no">No.${n}</span><div class="nm">？？？</div><div class="tt">未解锁角色</div><span class="fc">COMING SOON</span></div></div></div>`;
    }
  }
  $("#grid").innerHTML = cells || `<div class="empty">没有找到这个角色——换个关键词试试？</div>`;
  document.querySelectorAll(".slot[data-id]").forEach(el=>{
    const pick=()=>{selectedId=+el.dataset.id; renderGrid(); renderDetail();};
    el.onclick=pick;
    el.onkeydown=e=>{if(e.key==="Enter"||e.key===" "){e.preventDefault();pick();}};
  });
  // pager
  let pg = `<button class="pgbtn" id="prev" ${page===0?'disabled':''}>◀</button>`;
  for(let i=0;i<pages;i++) pg += `<button class="pgbtn ${i===page?'on':''}" data-p="${i}">${i+1}</button>`;
  pg += `<button class="pgbtn" id="next" ${page>=pages-1?'disabled':''}>▶</button>`;
  pg += `<span class="pageInfo">PAGE ${page+1} / ${pages} · ${list.length} 人</span>`;
  $("#pager").innerHTML = pg;
  const pv=$("#prev"), nx=$("#next");
  if(pv) pv.onclick=()=>{page--;renderGrid();};
  if(nx) nx.onclick=()=>{page++;renderGrid();};
  document.querySelectorAll(".pgbtn[data-p]").forEach(b=>b.onclick=()=>{page=+b.dataset.p;renderGrid();});
}
function statRow(label, v){
  return `<div class="stat"><span>${label}</span><div class="statBar"><div class="statFill" style="width:${v}%"></div></div><span>${v}</span></div>`;
}
function renderDetail(){
  const c = CHARS.find(x=>x.id===selectedId) || CHARS[0];
  $("#detail").innerHTML = `
    <div class="detailHead">
      ${avatarHTML(c,true)}
      <div><h2>${c.name}</h2>
      <div class="dtitle">No.${String(c.id).padStart(2,'0')} · ${c.title}</div>
      <div class="dnote">名字由来：${c.note}</div>
      <div class="dfc"><span class="fc" style="background:${c.accent}55;border:2px solid var(--ink);padding:2px 10px;border-radius:99px;font-size:12px;font-weight:700">${c.faction}</span></div></div>
    </div>
    <h3>人设 PERSONA</h3>
    <p class="desc">${c.desc}</p>
    <h3>招牌台词 LINES</h3>
    ${c.lines.map(l=>`<div class="bubble">${l}</div>`).join("")}
    <h3>手感参数 STATS（示意）</h3>
    ${statRow("情感",c.stats.emotion)}${statRow("理性",c.stats.reason)}${statRow("火力",c.stats.fire)}${statRow("出没",c.stats.presence)}
    <h3>视觉设定 VISUAL</h3>
    <p class="desc">${c.visual}</p>
    <h3>生成 PROMPT（全局风格 + 本角色）</h3>
    <div class="promptBox">${c.prompt_cn}</div>
    <button class="copyBtn" id="cpCn">📋 复制中文 Prompt</button>
    <button class="copyBtn" id="cpEn">📋 复制英文 Prompt</button>
    <button class="copyBtn" id="cpNeg">📋 复制 Negative</button>
    <h3>三视图 TURNAROUND</h3>
    ${c.img ? `<img class="turnImg" src="${c.img}" alt="${c.name} 三视图">` : `<div class="turnWait">⏳ 三视图待生成 · 已完成 ${CHARS.filter(x=>x.img).length} / 69，分批补齐中</div>`}`;
  $("#cpCn").onclick=()=>copyText(c.prompt_cn);
  $("#cpEn").onclick=()=>copyText(c.prompt_en);
  $("#cpNeg").onclick=()=>copyText(GLOBALS.neg);
}
async function copyText(t){
  try{ await navigator.clipboard.writeText(t); }
  catch(e){ const ta=document.createElement("textarea"); ta.value=t; document.body.appendChild(ta); ta.select(); document.execCommand("copy"); ta.remove(); }
  const el=$("#toast"); el.classList.add("show"); setTimeout(()=>el.classList.remove("show"),1400);
}
function render(){ renderChips(); renderGrid(); renderDetail(); }
$("#q").addEventListener("input", e=>{query=e.target.value.trim(); page=0; renderGrid();});
document.addEventListener("keydown", e=>{
  if(e.target.tagName==="INPUT") return;
  const pages = Math.max(1, Math.ceil(filtered().length/PAGE_SIZE));
  if(e.key==="ArrowRight" && page<pages-1){page++;renderGrid();}
  if(e.key==="ArrowLeft" && page>0){page--;renderGrid();}
});
$("#gCn").textContent = GLOBALS.cn;
$("#gEn").textContent = GLOBALS.en;
$("#gNeg").textContent = GLOBALS.neg;
document.querySelectorAll("[data-copy]").forEach(b=>b.onclick=()=>copyText(GLOBALS[b.dataset.copy]));
render();
</script>
</body>
</html>
"""

html_out = HTML.replace("__DATA__", data_json).replace("__GLOBALS__", globals_json).replace("__IMG_COUNT__", str(IMG_COUNT))
with open("dist/index.html","w",encoding="utf-8") as f: f.write(html_out)
with open("dist/characters.json","w",encoding="utf-8") as f: json.dump(out, f, ensure_ascii=False, indent=1)
if os.path.isdir("img"):
    os.makedirs("dist/img", exist_ok=True)
    for fn in os.listdir("img"):
        shutil.copy(os.path.join("img",fn), os.path.join("dist/img",fn))
print("built", len(html_out), "bytes,", len(out), "chars,", IMG_COUNT, "turnarounds")
