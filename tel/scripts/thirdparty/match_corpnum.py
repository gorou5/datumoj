#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
match_corpnum.py — third_party_record の企業記録に法人番号を名寄せする。

入力:
  targets.json : D1 から書き出した対象行 (id, source_id, actor_name,
                 actor_address, service_name, domestic_overseas, corporate_number)
  zenken.csv   : 国税庁法人番号公表サイト「全件データ」CSV (Unicode, 前月末時点)
                 列(実測): 0=一連番号 1=法人番号 6=商号 9=都道府県 10=市区町村
                 11=街区・番地等

方針（推測で埋めない）:
  - 名称に法人格（株式会社等）を含む候補名だけを照合対象にする。
    屋号・個人名・「〜を詐称」行は照合しない（詐称行は被害企業への誤付番防止）。
  - 名称は NFKC 正規化＋空白除去。法人格の前置/後置ゆれは
    「法人格クラス＋核名称」でも索引し、核一致でも住所一致を必須とする。
  - 住所は少なくとも都道府県＋市区町村の一致を必須（政令市は市名で府県を補完）。
  - 候補が複数番号に割れる場合は番地（丁目・番）まで見て一意化を試み、
    それでも割れれば ambiguous として埋めない。
  - 実装は 2 パス: 対象側の候補キー集合を作ってから登記簿を 1 回だけ流し、
    該当行だけ保持する（全件をメモリに載せない）。
出力:
  <prefix>_result.json  : id ごとの判定 (fill/ambiguous/no_match + 理由 + 候補詳細)
  <prefix>_updates.sql  : fill 分の UPDATE 文
"""
import csv, json, re, sys, unicodedata
from collections import defaultdict

LEGAL_FORMS = [
    "特定非営利活動法人", "一般社団法人", "一般財団法人", "公益社団法人",
    "公益財団法人", "株式会社", "有限会社", "合同会社", "合名会社", "合資会社",
    "医療法人", "学校法人", "社会福祉法人", "宗教法人", "協同組合",
    "信用金庫", "信用組合", "労働金庫",
]
LEGAL_RE = re.compile("|".join(map(re.escape, LEGAL_FORMS)))

def norm(s):
    if not s:
        return ""
    s = unicodedata.normalize("NFKC", s)
    s = re.sub(r"[\s　]+", "", s)
    return s

KANJI_NUM = {"一": "1", "二": "2", "三": "3", "四": "4", "五": "5",
             "六": "6", "七": "7", "八": "8", "九": "9", "十": "10"}
def norm_addr(s):
    s = norm(s)
    def repl(m):
        t = m.group(0)
        if t == "十":
            return "10"
        if "十" in t:
            a, _, b = t.partition("十")
            return str((int(KANJI_NUM[a]) if a else 1) * 10 + (int(KANJI_NUM[b]) if b else 0))
        return "".join(KANJI_NUM[c] for c in t)
    s = re.sub(r"[一二三四五六七八九十]+(?=丁目|番地|番|号)", repl, s)
    s = s.replace("丁目", "-").replace("番地", "-").replace("番", "-").replace("号", "")
    s = re.sub(r"-+", "-", s)
    return s

SEIREI = {  # 政令指定都市 市名→都道府県
    "札幌市": "北海道", "仙台市": "宮城県", "さいたま市": "埼玉県", "千葉市": "千葉県",
    "横浜市": "神奈川県", "川崎市": "神奈川県", "相模原市": "神奈川県", "新潟市": "新潟県",
    "静岡市": "静岡県", "浜松市": "静岡県", "名古屋市": "愛知県", "京都市": "京都府",
    "大阪市": "大阪府", "堺市": "大阪府", "神戸市": "兵庫県", "岡山市": "岡山県",
    "広島市": "広島県", "北九州市": "福岡県", "福岡市": "福岡県", "熊本市": "熊本県",
}
PREF2SEIREI = defaultdict(list)
for _c, _p in SEIREI.items():
    PREF2SEIREI[_p].append(_c)

def split_legal(name):
    """(法人格, 核名称) を返す。法人格が無ければ (None, name)。"""
    for lf in LEGAL_FORMS:
        if name.startswith(lf):
            return lf, name[len(lf):]
        if name.endswith(lf):
            return lf, name[:-len(lf)]
    return None, name

def candidate_names(actor_name):
    """actor_name から商号候補を抽出する。"""
    n = norm(actor_name)
    cands = []
    if LEGAL_RE.search(n):
        cands.append(n)
    for seg in re.findall(r"[(（]([^)）]+)[)）]", n):
        if LEGAL_RE.search(seg):
            cands.append(seg)
    head = re.split(r"[(（]", n)[0]
    if LEGAL_RE.search(head):
        cands.append(head)
    seen, out = set(), []
    for c in cands:
        if c and c not in seen:
            seen.add(c)
            out.append(c)
    return out

def main():
    targets_path, zenken_path, out_prefix = sys.argv[1], sys.argv[2], sys.argv[3]
    targets = json.load(open(targets_path, encoding="utf-8"))

    # --- pass 0: 対象側の候補キー集合 ---
    want_full = set()
    want_core = set()
    per_target = {}
    for t in targets:
        if t.get("corporate_number"):
            continue
        raw_name = t.get("actor_name") or ""
        if "詐称" in raw_name:
            continue
        cands = candidate_names(raw_name)
        per_target[t["id"]] = cands
        for c in cands:
            want_full.add(c)
            lf, core = split_legal(c)
            if lf:
                want_core.add((lf, core))
    print(f"wanted names: {len(want_full)} / cores: {len(want_core)}", file=sys.stderr)

    # --- pass 1: 登記簿を流して該当行だけ保持 ---
    by_full = defaultdict(list)
    by_core = defaultdict(list)
    n_reg = 0
    with open(zenken_path, encoding="utf-8", newline="") as f:
        for row in csv.reader(f):
            if len(row) < 12 or not re.fullmatch(r"\d{13}", row[1] or ""):
                continue
            n_reg += 1
            nn = norm(row[6])
            lf, core = split_legal(nn)
            if nn not in want_full and (not lf or (lf, core) not in want_core):
                continue
            rec = (row[1], row[6], row[9], row[10], row[11])  # 番号,商号,都道府県,市区町村,街区番地
            if nn in want_full:
                by_full[nn].append(rec)
            if lf and (lf, core) in want_core:
                by_core[(lf, core)].append(rec)
    print(f"registry rows scanned: {n_reg}; kept full={sum(len(v) for v in by_full.values())} "
          f"core={sum(len(v) for v in by_core.values())}", file=sys.stderr)

    results = {}
    stats = defaultdict(int)
    for t in targets:
        rid = t["id"]
        if t.get("corporate_number"):
            results[rid] = {"verdict": "already_filled", "corp": t["corporate_number"]}
            stats["already_filled"] += 1
            continue
        raw_name = t.get("actor_name") or ""
        if "詐称" in raw_name:
            results[rid] = {"verdict": "no_match", "reason": "impersonation_entry"}
            stats["no_match:impersonation_entry"] += 1
            continue
        cands = per_target.get(rid, [])
        if not cands:
            results[rid] = {"verdict": "no_match", "reason": "no_legal_name"}
            stats["no_match:no_legal_name"] += 1
            continue
        addr_n = norm_addr(t.get("actor_address") or "")
        if not addr_n or addr_n == "不明":
            results[rid] = {"verdict": "no_match", "reason": "address_unknown",
                            "candidates_tried": cands}
            stats["no_match:address_unknown"] += 1
            continue

        pool = {}
        for c in cands:
            for rec in by_full.get(c, []):
                pool[rec[0]] = rec
            lf, core = split_legal(c)
            if lf:
                for rec in by_core.get((lf, core), []):
                    if norm(rec[1]) != c:
                        pool[rec[0]] = rec
        if not pool:
            results[rid] = {"verdict": "no_match", "reason": "name_not_in_registry",
                            "candidates_tried": cands}
            stats["no_match:name_not_in_registry"] += 1
            continue

        def addr_ok(rec):
            pref, city = rec[2], rec[3]
            if not city or norm(city) not in addr_n:
                return False
            if pref and norm(pref) in addr_n:
                return True
            return any(sc in addr_n for sc in PREF2SEIREI.get(pref, ()))

        ok = {c: r for c, r in pool.items() if addr_ok(r)}
        if not ok:
            results[rid] = {"verdict": "no_match", "reason": "address_mismatch",
                            "candidates_tried": cands,
                            "registry_hits": [r[0] for r in pool.values()][:5]}
            stats["no_match:address_mismatch"] += 1
            continue
        if len(ok) > 1:
            deep = {}
            for c, r in ok.items():
                street_n = norm_addr(r[2] + r[3] + r[4])
                tail = street_n.split(norm(r[3]))[-1] if norm(r[3]) in street_n else street_n
                if tail and len(tail) >= 3 and tail in addr_n:
                    deep[c] = r
            if len(deep) == 1:
                ok = deep
            else:
                results[rid] = {"verdict": "ambiguous",
                                "corps": sorted(ok.keys()),
                                "candidates_tried": cands}
                stats["ambiguous"] += 1
                continue
        corp, rec = next(iter(ok.items()))
        results[rid] = {"verdict": "fill", "corp": corp,
                        "registry_name": rec[1],
                        "registry_addr": rec[2] + rec[3] + rec[4],
                        "candidates_tried": cands}
        stats["fill"] += 1

    json.dump({"stats": dict(stats), "results": results},
              open(out_prefix + "_result.json", "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    with open(out_prefix + "_updates.sql", "w", encoding="utf-8") as f:
        for t in targets:
            r = results[t["id"]]
            if r["verdict"] == "fill":
                f.write(f"UPDATE third_party_record SET corporate_number='{r['corp']}' "
                        f"WHERE id={t['id']} AND (corporate_number IS NULL OR corporate_number='');\n")
    print(json.dumps(dict(stats), ensure_ascii=False), file=sys.stderr)

if __name__ == "__main__":
    main()
