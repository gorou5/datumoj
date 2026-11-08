#!/usr/bin/env python3
"""
tel 区段索引页生成器 （区段索引）: nbfun bucket `tel` の blocks/ 前缀に
实际在位する区段页を普查し、全部の区段页にクリックで到達できる
静的索引页を生成して同 bucket の blocks-index/ 前缀へ上書きする。

生成する页 (最大で三级):
  blocks-index/index.html            /tel/blocks
      号种 （携帯電話/IP電話/フリーダイヤル/ナビダイヤル/固定電話） ごとに
      前缀ファミリへの入口と区段数を列挙。
  blocks-index/<family>.html         /tel/blocks/<family>
      そのファミリの全区段 key を真リンクで列挙。区段数が
      CHUNK_THRESHOLD を超えるファミリは第 4 桁で子页に分割し、
      ファミリ页は子页へのリンクと範囲・件数だけを列挙する。
  blocks-index/<family>/<chunk>.html /tel/blocks/<family>/<chunk4>
      子页： その 4 桁前缀で始まる全区段 key を真リンクで列挙
      （目視で探しやすいよう 5 桁前缀ごとに小见出しを付ける）。

設計判断:
- 在位判定の真実は bucket の blocks/ オブジェクト一覧（S3 List）。
  D1 number_block_allocation は参考カウントの照合にだけ使う
  （割当行は 6 桁前缀単位などで、区段页の枚数とは一致しない）。
- ファミリは key の前缀だけで分類する （分類順は下の PREFIX_ORDER:
  0120/0800/0570 の 4 桁 → 060/070/080/090/050 → 市外局番 3 桁 →
  03/06。"06" より先に "060" を見ること、"080" より先に "0800" を
  見ることが衝突回避の要）。
- 冪等： 何度実行しても在位オブジェクトから同じ集合を再生成して
  上書きするだけ。固定電話（都会区）などの追加分が着荷したら
  再実行すれば索引に自動で現れる。逆に消えた区段は索引から消える。
- 索引页の様式は区段页（gen_blocks 系）と同一の inline CSS・
  ヘッダ・フッタに揃える。
- salt（2026-11-07 内链网完整化で追加）: 総索引页とファミリ页に、
  D1 由来の事実だけの小さな統計ブロックを付ける（種別・区段数・
  対象番号数・信号区段数・主な割当事業者、ファミリ页は登録の多い
  区段の例示も）。文章の創作はしない。D1 が引けない場合は salt
  無しで従来どおり生成する。
- manifest（同）: 普查した全区段 key を blocks-index/manifest.json
  として同梱する。Worker（src/lib/block_manifest.js）は番号页の
  「所属区段页」リンクを、この manifest に載っている区段にだけ
  描画する（未生成区段への死链防止）。

凭证： ~/.cloudflare.env の R2_* 字段（nbfun 账户、S3 互換）を
读入するだけで、出力しない。D1 照合は ~/.cloudflare-2.env
（Datumoj 账户）+ wrangler 経由（gen_blocks_prod.py と同じ流儀）。

用法:
  python3 gen_blocks_index.py             # 普查 → 生成 → 上传
  python3 gen_blocks_index.py --dry-run   # 生成と検算だけ（上传しない）
  python3 gen_blocks_index.py --no-d1     # D1 参考照合を省略
出产物（生成 HTML のローカル控え）: --out DIR（既定 /tmp/tel-blocks-index-out）
"""
import argparse, json, os, re, subprocess, sys, tempfile
from collections import Counter, OrderedDict
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
TEL_DIR = HERE.parent
WRANGLER = os.path.expanduser("~/.npm-global/bin/wrangler")
BUCKET = "tel"
CHUNK_THRESHOLD = 1500   # これを超えるファミリは第4桁で子页分割
GROUP_HEADING_MIN = 120  # これを超える一覧は5桁前缀ごとに小见出し

# ------------------------------------------------------------- families
# (family id = key の前缀そのもの, 表示ラベル, ラベル内のハイフン位置)
def fam(fid, label):
    return {"id": fid, "label": label, "plen": len(fid)}

AREA_CITY = [
    ("011", "札幌"), ("022", "仙台"), ("048", "さいたま"), ("043", "千葉"),
    ("03", "東京"), ("045", "横浜"), ("044", "川崎"), ("042", "相模原"),
    ("025", "新潟"), ("054", "静岡"), ("053", "浜松"), ("052", "名古屋"),
    ("075", "京都"), ("06", "大阪"), ("072", "堺"), ("078", "神戸"),
    ("086", "岡山"), ("082", "広島"), ("093", "北九州"), ("092", "福岡"),
    ("096", "熊本"),
]

SECTIONS = [
    ("携帯電話", [fam("060", "携帯電話 060"), fam("070", "携帯電話 070"),
                  fam("080", "携帯電話 080"), fam("090", "携帯電話 090")]),
    ("IP電話", [fam("050", "IP電話 050")]),
    ("フリーダイヤル", [fam("0120", "フリーダイヤル 0120"),
                         fam("0800", "フリーダイヤル 0800")]),
    ("ナビダイヤル", [fam("0570", "ナビダイヤル 0570")]),
    ("固定電話", [fam(code, f"固定電話 {code}（{city}）") for code, city in AREA_CITY]),
]
FAMILIES = OrderedDict()
for _sec, _fams in SECTIONS:
    for _f in _fams:
        FAMILIES[_f["id"]] = _f

# 分類順： 長い・特殊な前缀を先に見る
# （"0800"→フリーダイヤル を "080"→携帯 より先、"060"→携帯 を "06"→大阪 より先）
PREFIX_ORDER = (["0120", "0800", "0570", "060", "070", "080", "090", "050"]
                + [c for c, _ in AREA_CITY if len(c) == 3]
                + [c for c, _ in AREA_CITY if len(c) == 2])


def classify(key):
    for p in PREFIX_ORDER:
        if key.startswith(p):
            return p
    return None


# ------------------------------------------------------------- formatting
def fmt_key(key, plen):
    return f"{key[:plen]}-{key[plen:]}"


def fmt_partial(prefix, plen):
    if len(prefix) > plen:
        return f"{prefix[:plen]}-{prefix[plen:]}"
    return prefix


def esc(s):
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
             .replace('"', "&quot;"))


CSS = (":root{color-scheme:light dark}*{box-sizing:border-box}"
       "body{font-family:system-ui,-apple-system,\"Hiragino Kaku Gothic ProN\",\"Noto Sans JP\",sans-serif;"
       "margin:0;line-height:1.7;background:#fafafa;color:#1a1a1a}"
       "a{color:#0b57d0;text-decoration:none}a:hover{text-decoration:underline}"
       "header{border-bottom:1px solid #ddd;padding:.8rem 1rem}"
       "header .logo{font-weight:700;font-size:1.15rem;color:inherit}"
       "main{max-width:860px;margin:0 auto;padding:1rem}"
       ".card{background:#fff;border:1px solid #e2e2e2;border-radius:10px;padding:1rem 1.1rem;margin:1rem 0}"
       "h1{font-size:1.5rem;margin:.4rem 0;word-break:break-all}"
       "h2{font-size:1.05rem;margin:1.2rem 0 .3rem}"
       "table{border-collapse:collapse;width:100%}"
       "th,td{border-bottom:1px solid #e5e5e5;padding:.45rem .5rem;text-align:left;vertical-align:top}"
       "th{width:11rem;color:#555;font-weight:600}"
       ".meta{color:#666;font-size:.9rem}"
       ".crumbs{font-size:.9rem;color:#666;margin:.5rem 0}"
       ".grid{line-height:2;word-break:break-all}"
       ".grid a{margin-right:.5em}.grid a.on{font-weight:700}"
       ".grid i{font-style:normal;color:#0b57d0;cursor:pointer;margin-right:.5em}"
       ".hl{background:#ffe58a;border-radius:3px;padding:0 .15em}"
       "footer{border-top:1px solid #ddd;padding:.8rem 1rem;color:#666;font-size:.85rem}")


def page(title, canonical, crumbs, inner):
    return ("<!DOCTYPE html><html lang=\"ja\"><head><meta charset=\"utf-8\">"
            "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
            f"<title>{esc(title)}</title>"
            f"<link rel=\"canonical\" href=\"{esc(canonical)}\">"
            "<base href=\"/tel/\">"
            f"<style>{CSS}</style></head><body>"
            "<header><a class=\"logo\" href=\"https://datumoj.com/tel/\">電話番号情報</a></header>"
            f"<main><p class=\"crumbs\">{crumbs}</p>{inner}</main>"
            "<footer>データ出典：総務省 電気通信番号計画（指定区段）</footer>"
            "</body></html>")


def key_listing(keys, plen):
    """区段 key の真リンク一覧。多い場合は5桁前缀ごとに小见出し。"""
    links = [f'<a href="/tel/{k}">{esc(fmt_key(k, plen))}</a>' for k in keys]
    if len(keys) <= GROUP_HEADING_MIN:
        return f'<p class="grid">{" ".join(links)}</p>'
    out, cur, buf = [], None, []
    for k, link in zip(keys, links):
        g = k[:5]
        if g != cur:
            if buf:
                out.append(f'<p class="grid">{" ".join(buf)}</p>')
            out.append(f'<h2 id="g{g}">{esc(fmt_partial(g, plen))}</h2>')
            cur, buf = g, []
        buf.append(link)
    if buf:
        out.append(f'<p class="grid">{" ".join(buf)}</p>')
    return "\n".join(out)


# ------------------------------------------------------------- data sources
def load_env_file(path):
    env = {}
    for line in Path(os.path.expanduser(path)).read_text().splitlines():
        if "=" in line and not line.strip().startswith("#"):
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def s3_client():
    import boto3
    from botocore.config import Config
    env = load_env_file("~/.cloudflare.env")
    return boto3.client(
        "s3", endpoint_url=env["R2_ENDPOINT"],
        aws_access_key_id=env["R2_ACCESS_KEY_ID"],
        aws_secret_access_key=env["R2_SECRET_ACCESS_KEY"],
        config=Config(max_pool_connections=16))


def census_block_keys(s3):
    keys, token = [], None
    while True:
        kw = dict(Bucket=BUCKET, Prefix="blocks/")
        if token:
            kw["ContinuationToken"] = token
        resp = s3.list_objects_v2(**kw)
        for o in resp.get("Contents", []):
            k = o["Key"]
            if k.endswith(".html"):
                keys.append(k[len("blocks/"):-len(".html")])
        if resp.get("IsTruncated"):
            token = resp["NextContinuationToken"]
        else:
            break
    return sorted(set(keys))


# ------------------------------------------------------------- salt (D1 facts)
# ファミリ → 総務省割当上の種別 (line_type)。区段1枚が含む番号数は
# 種別で決まる (万号页=10,000 / 千号页=1,000、gen_blocks 系の LT と同一)。
FAMILY_LINE_TYPE = {"060": "mobile", "070": "mobile", "080": "mobile",
                    "090": "mobile", "050": "ip050", "0120": "free0120",
                    "0800": "free0800", "0570": "navi0570"}
for _code, _city in AREA_CITY:
    FAMILY_LINE_TYPE[_code] = "fixed"
LINE_TYPE_NAME = {"mobile": "携帯電話", "ip050": "IP電話",
                  "free0120": "フリーダイヤル", "free0800": "フリーダイヤル",
                  "navi0570": "ナビダイヤル", "fixed": "固定電話"}
LINE_TYPE_SPAN = {"mobile": 10000, "ip050": 10000, "free0800": 10000,
                  "free0120": 1000, "navi0570": 1000, "fixed": 1000}
# 割当前缀の長さ (gen_blocks 系の指定行の取り方と同一)
ALLOC_PLEN = {"mobile": 6, "ip050": 7, "free0800": 7, "free0120": 7,
              "navi0570": 7, "fixed": 6}


def d1(sql):
    env = dict(os.environ)
    env.update(load_env_file("~/.cloudflare-2.env"))
    out = subprocess.run([WRANGLER, "d1", "execute", "tel_datumoj", "--remote",
                          "--command", sql, "--json"],
                         capture_output=True, text=True, cwd=str(TEL_DIR), env=env)
    if out.returncode != 0:
        raise RuntimeError("wrangler d1 failed: " + out.stderr[-300:])
    return json.loads(out.stdout)[0]["results"]


def pull_salt_data():
    """salt の原料を D1 から引く。全て実データ由来で、創作はしない。
    戻り値: {prefix_carrier: {line_type: {prefix: carrier}},
             landed_by_block: {key7: 件数}, flags_by_block: {key7: 件数}}"""
    prefix_carrier = {}
    for lt, plen in ALLOC_PLEN.items():
        rows = d1(
            "SELECT a.prefix, c.name AS carrier FROM number_block_allocation a "
            "LEFT JOIN carrier c ON c.id = a.carrier_id "
            f"WHERE a.line_type = '{lt}' AND a.prefix_length = {plen} "
            "AND a.status = 'designated'")
        prefix_carrier[lt] = {r["prefix"]: (r["carrier"] or "") for r in rows}
    landed_by_block = {}
    tables = [r["name"] for r in d1(
        "SELECT name FROM sqlite_master WHERE type = 'table' "
        "AND name GLOB 'number_record_[0-9]*' ORDER BY name")]
    for t in tables:
        for r in d1(f"SELECT substr(digits, 1, 7) AS b, COUNT(*) AS n "
                    f"FROM {t} GROUP BY b"):
            landed_by_block[r["b"]] = landed_by_block.get(r["b"], 0) + r["n"]
    flags_by_block = {}
    for r in d1("SELECT substr(number, 1, 7) AS b, COUNT(*) AS n "
                "FROM third_party_flag WHERE status = 'active' GROUP BY b"):
        flags_by_block[r["b"]] = r["n"]
    return {"prefix_carrier": prefix_carrier,
            "landed_by_block": landed_by_block,
            "flags_by_block": flags_by_block}


def family_salt(fid, keys, salt):
    """1ファミリ分の salt を計算する。信号区段 = 落表レコードか
    第三者標識のある番号を含む区段 (D1 で数えられる信号のみ)。"""
    lt = FAMILY_LINE_TYPE[fid]
    pmap = salt["prefix_carrier"].get(lt, {})
    carriers = Counter()
    for k in keys:
        c = pmap.get(k) or pmap.get(k[:6]) or ""
        if c:
            carriers[c] += 1
    landed = salt["landed_by_block"]
    flags = salt["flags_by_block"]
    sig_keys = [k for k in keys
                if landed.get(k, 0) + flags.get(k, 0) > 0]
    examples = sorted((k for k in keys if landed.get(k, 0) > 0),
                      key=lambda k: (-landed[k], k))[:10]
    return {"line_type": lt, "blocks": len(keys),
            "numbers": len(keys) * LINE_TYPE_SPAN[lt],
            "signal_blocks": len(sig_keys),
            "carriers": carriers,
            "top_carriers": carriers.most_common(3),
            "examples": [(k, landed[k], flags.get(k, 0)) for k in examples]}


def salt_table_html(rows):
    trs = "".join(f"<tr><th>{esc(k)}</th><td>{v}</td></tr>" for k, v in rows)
    return f'<div class="card"><table>{trs}</table></div>'


def family_salt_html(fid, f, fs):
    rows = [("番号の種類", esc(LINE_TYPE_NAME[fs["line_type"]])),
            ("区段数", f"{fs['blocks']:,} 区段"),
            ("対象番号数", f"{fs['numbers']:,} 件"),
            ("信号区段数", f"{fs['signal_blocks']:,} 区段（登録・標識のある番号を含む）")]
    if fs["top_carriers"]:
        rows.append(("主な割当事業者", "、".join(
            f"{esc(c)}（{n:,} 区段）" for c, n in fs["top_carriers"])))
    html = salt_table_html(rows)
    if fs["examples"]:
        plen = f["plen"]
        items = "".join(
            f'<li><a href="/tel/{k}">{esc(fmt_key(k, plen))}</a> '
            f'<span class="meta">登録 {n:,} 件'
            f'{"・標識 " + str(fl) + " 件" if fl else ""}</span></li>'
            for k, n, fl in fs["examples"])
        html += f"<h2>登録の多い区段</h2><ul>{items}</ul>"
    return html


def d1_reference_counts():
    """D1 number_block_allocation の参考カウント（照合用、生成は驱动しない）。"""
    env = dict(os.environ)
    env.update(load_env_file("~/.cloudflare-2.env"))
    sql = ("SELECT line_type, COUNT(*) AS n FROM number_block_allocation "
           "WHERE status='designated' GROUP BY line_type ORDER BY line_type")
    out = subprocess.run([WRANGLER, "d1", "execute", "tel_datumoj", "--remote",
                          "--command", sql, "--json"],
                         capture_output=True, text=True, cwd=str(TEL_DIR), env=env)
    if out.returncode != 0:
        raise RuntimeError(out.stderr[-300:])
    return {r["line_type"]: r["n"] for r in json.loads(out.stdout)[0]["results"]}


# ------------------------------------------------------------- generation
def build_pages(keys_by_family, salt_by_family=None):
    """{bucket_key: html} を返す。あわせて統計も返す。
    salt_by_family があれば総索引页・ファミリ页に salt を差し込む。"""
    pages = {}
    total = sum(len(v) for v in keys_by_family.values())

    # ---- 総索引页
    sec_html = []
    for sec_name, fams in SECTIONS:
        items = []
        for f in fams:
            ks = keys_by_family.get(f["id"])
            if not ks:
                continue
            items.append(
                f'<li><a href="/tel/blocks/{f["id"]}">{esc(f["label"])}</a> '
                f'<span class="meta">{len(ks):,} 区段</span></li>')
        if items:
            sec_html.append(f'<h2>{esc(sec_name)}</h2>\n<ul>{"".join(items)}</ul>')
    hub_salt = ""
    if salt_by_family:
        sec_rows = []
        for sec_name, fams in SECTIONS:
            agg = [salt_by_family[f["id"]] for f in fams
                   if f["id"] in salt_by_family]
            if not agg:
                continue
            carr = Counter()
            for a in agg:
                carr.update(a["carriers"])
            top = "、".join(f"{esc(c)}（{n:,} 区段）"
                             for c, n in carr.most_common(3))
            sec_rows.append((
                sec_name,
                f"{sum(a['blocks'] for a in agg):,} 区段・"
                f"対象番号 {sum(a['numbers'] for a in agg):,} 件・"
                f"信号区段 {sum(a['signal_blocks'] for a in agg):,} 区段"
                + (f"<br>主な割当事業者：{top}" if top else "")))
        if sec_rows:
            hub_salt = salt_table_html(sec_rows)
    inner = (f"<h1>電話番号の区段インデックス</h1>"
             f'<p class="meta">総務省の指定区段 {total:,} 件を、番号の種類と'
             f"区段ごとに一覧できます。各区段のページでは、その区段に含まれる"
             f"全番号を確認できます。</p>" + hub_salt + "\n".join(sec_html))
    pages["blocks-index/index.html"] = page(
        "電話番号の区段インデックス｜datumoj.com/tel",
        "https://datumoj.com/tel/blocks", "区段インデックス", inner)

    # ---- ファミリ页 / 子页
    chunked = {}
    for fid, f in FAMILIES.items():
        ks = keys_by_family.get(fid)
        if not ks:
            continue
        plen = f["plen"]
        crumbs = f'<a href="/tel/blocks">区段インデックス</a> ＞ {esc(f["label"])}'
        if len(ks) > CHUNK_THRESHOLD:
            chunks = OrderedDict()
            for k in ks:
                chunks.setdefault(k[:4], []).append(k)
            chunked[fid] = len(chunks)
            items = []
            for c, cks in chunks.items():
                items.append(
                    f'<li><a href="/tel/blocks/{fid}/{c}">{esc(c)}</a> '
                    f'<span class="meta">{esc(fmt_key(cks[0], plen))} 〜 '
                    f'{esc(fmt_key(cks[-1], plen))}・{len(cks):,} 区段</span></li>')
            inner = (f"<h1>{esc(f['label'])} の区段一覧</h1>"
                     f'<p class="meta">全 {len(ks):,} 区段。第4桁までの前缀ごとに'
                     f"分けてあります。</p>"
                     + (family_salt_html(fid, f, salt_by_family[fid])
                        if salt_by_family and fid in salt_by_family else "")
                     + f"<ul>{''.join(items)}</ul>")
            pages[f"blocks-index/{fid}.html"] = page(
                f"{f['label']} の区段一覧｜datumoj.com/tel",
                f"https://datumoj.com/tel/blocks/{fid}", crumbs, inner)
            for c, cks in chunks.items():
                ccrumbs = (f'<a href="/tel/blocks">区段インデックス</a> ＞ '
                           f'<a href="/tel/blocks/{fid}">{esc(f["label"])}</a> ＞ {esc(c)}')
                cinner = (f"<h1>{esc(f['label'])} {esc(fmt_partial(c, plen))} の区段一覧</h1>"
                          f'<p class="meta">{esc(fmt_key(cks[0], plen))} 〜 '
                          f'{esc(fmt_key(cks[-1], plen))} の {len(cks):,} 区段。</p>'
                          + key_listing(cks, plen))
                pages[f"blocks-index/{fid}/{c}.html"] = page(
                    f"{f['label']} {fmt_partial(c, plen)} の区段一覧｜datumoj.com/tel",
                    f"https://datumoj.com/tel/blocks/{fid}/{c}", ccrumbs, cinner)
        else:
            inner = (f"<h1>{esc(f['label'])} の区段一覧</h1>"
                     f'<p class="meta">全 {len(ks):,} 区段。</p>'
                     + (family_salt_html(fid, f, salt_by_family[fid])
                        if salt_by_family and fid in salt_by_family else "")
                     + key_listing(ks, plen))
            pages[f"blocks-index/{fid}.html"] = page(
                f"{f['label']} の区段一覧｜datumoj.com/tel",
                f"https://datumoj.com/tel/blocks/{fid}", crumbs, inner)
    return pages, {"total_blocks": total, "chunked_families": chunked}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="生成と検算のみ、上传しない")
    ap.add_argument("--no-d1", action="store_true", help="D1 参考照合を省略")
    ap.add_argument("--out", default="/tmp/tel-blocks-index-out",
                    help="生成 HTML のローカル控えの出力先")
    args = ap.parse_args()

    s3 = s3_client()
    keys = census_block_keys(s3)
    print(f"census: blocks/ に {len(keys):,} 区段页")

    keys_by_family, unknown = {}, []
    for k in keys:
        fid = classify(k)
        if fid is None:
            unknown.append(k)
        else:
            keys_by_family.setdefault(fid, []).append(k)
    if unknown:
        print(f"WARNING: 分類できない key が {len(unknown)} 件（索引に入れない）: "
              f"{unknown[:10]}")
    for fid in FAMILIES:
        if fid in keys_by_family:
            print(f"  family {fid}: {len(keys_by_family[fid]):,} 区段")

    salt_by_family = None
    if not args.no_d1:
        try:
            ref = d1_reference_counts()
            print(f"参考: D1 number_block_allocation designated 行数 {ref} "
                  f"（割当行は前缀単位で、区段页数とは一致しない）")
        except Exception as e:  # 照合は任意。失敗しても生成は続ける
            print(f"参考: D1 照合をスキップ（{e}）")
        try:
            salt = pull_salt_data()
            salt_by_family = {fid: family_salt(fid, ks, salt)
                              for fid, ks in keys_by_family.items()}
            print(f"salt: D1 事実を取得（落表のある区段 "
                  f"{len(salt['landed_by_block']):,} 件・標識のある区段 "
                  f"{len(salt['flags_by_block']):,} 件）")
        except Exception as e:  # salt は任意。失敗しても生成は続ける
            print(f"salt: D1 取得に失敗、salt 無しで生成（{e}）")

    pages, stats = build_pages(keys_by_family, salt_by_family)
    print(f"生成: 索引页 {len(pages):,} 页（対象区段 {stats['total_blocks']:,}、"
          f"子页分割したファミリ {stats['chunked_families']}）")

    out_dir = Path(args.out)
    for bk, html in pages.items():
        dest = out_dir / bk[len("blocks-index/"):]
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(html, encoding="utf-8")

    # manifest: 在位区段 key の全量清单。Worker が番号页の所属区段
    # リンクの描画可否をこれで判定する (src/lib/block_manifest.js)。
    manifest = {"v": 1,
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "count": len(keys), "keys": keys}
    manifest_json = json.dumps(manifest, ensure_ascii=False,
                               separators=(",", ":"))
    (out_dir / "manifest.json").write_text(manifest_json, encoding="utf-8")
    print(f"ローカル控え: {out_dir}（manifest: {len(keys):,} key）")

    if args.dry_run:
        print("dry-run: 上传しません")
        return

    def put(item):
        bk, html = item
        s3.put_object(Bucket=BUCKET, Key=bk, Body=html.encode("utf-8"),
                      ContentType="text/html; charset=utf-8")
        return bk

    with ThreadPoolExecutor(max_workers=8) as ex:
        done = list(ex.map(put, pages.items()))
    s3.put_object(Bucket=BUCKET, Key="blocks-index/manifest.json",
                  Body=manifest_json.encode("utf-8"),
                  ContentType="application/json")
    print(f"上传: {len(done):,} 页 + manifest.json → bucket {BUCKET} の blocks-index/")


if __name__ == "__main__":
    main()
