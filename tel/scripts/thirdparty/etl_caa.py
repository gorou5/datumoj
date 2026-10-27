#!/usr/bin/env python3
"""Consumer Affairs Agency publications -> third_party_record.

Covers the two publication forms downloaded in phase 1 (source_id=5,
tier C; the kokusen name-publication files in the same raw folder are
handled by etl_kokusen.py):

- 特定商取引法に基づく行政処分 (disposition, 62 entries): actor block
  (名称/法人番号/本店所在地/代表者/設立/資本金/取引類型/取扱商品),
  violation section, sanction (業務停止命令 etc. + period), legal basis
  (特定商取引法 articles), announcing agency (消費者庁 or a regional
  経済産業局 acting on delegation).
- 消費者安全法第38条第1項に基づく注意喚起 (caution38, 19 entries):
  事業者の概要 block (名称/法人番号/所在地/代表者), method narrative
  (具体的な事例の内容), impersonated name for かたり-type cautions.

One record per (entry, actor); actor-less entries yield one record with
actor_name NULL.

IMPORTANT (phase-1 verified rule): the only phone numbers appearing in
these PDFs are agency consultation-desk numbers in the footer (経済産業局
消費者相談室) — they are the *inquiry contact*, never the sanctioned
actor. Per the task rule this script therefore never extracts phone
numbers from CAA PDFs: has_phone=0, phone_digits stays NULL.
"""

import re
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from common import collapse, norm_digits, record_insert, sql_str, wareki_date

RAW = Path.home() / "workspace/tel-datumoj/thirdparty/raw/caa"
CAPTURED_AT = "2026-10-27"
SOURCE_ID = 5

AGENCIES = ["北海道経済産業局", "東北経済産業局", "関東経済産業局", "中部経済産業局",
            "近畿経済産業局", "中国経済産業局", "四国経済産業局", "九州経済産業局",
            "沖縄総合事務局", "消費者庁"]


def pdf_text(path):
    r = subprocess.run(["pdftotext", str(path), "-"], capture_output=True,
                       text=True, timeout=120)
    return r.stdout or ""


def compact(s):
    return re.sub(r"\s+", "", s or "")


def field_after(text_c, label, stops, limit=120):
    """Value following a label in compact text, up to the next stop token."""
    i = text_c.find(label)
    if i < 0:
        return None
    rest = text_c[i + len(label):]
    rest = rest.lstrip("：:・")
    end = len(rest)
    for st in stops:
        j = rest.find(st)
        if 0 <= j < end:
            end = j
    v = rest[:end].strip("、,")
    return v[:limit] if v else None


def parse_actors(text, text_c):
    """Return list of actor dicts found in a publication."""
    actors = []
    # (a) corporate-number pattern: 名称（法人番号 1234567890123）
    for m in re.finditer(r"([^\s（）()、。「」]{2,30}(?:株式会社|有限会社|合同会社|会社|[^\s（）()、。「」]{0,10}))[（(]法人番号[：: ]?(\d{13})[）)]",
                         norm_digits(text_c)):
        name = m.group(1)
        # trim leading section junk glued by compacting
        name = re.split(r"[０-９0-9]+処分対象事業者|名称", name)[-1]
        actors.append({"actor_name": name.strip("：:"),
                       "corporate_number": m.group(2)})
    # (b) individual proprietors: （１）山田太郎（個人事業主）
    for m in re.finditer(r"[（(][０-９0-9]+[）)]([^\s（）()]{2,15})[（(]個人事業主[）)]",
                         norm_digits(text_c)):
        actors.append({"actor_name": m.group(1), "corporate_number": None})
    # (c) caution intro: 、X（以下「本件事業者」といいます。）が、消費者
    if not actors:
        m = re.search(r"、([^\s、。]{2,30})[（(]以下「本件事業者」といいます", text_c)
        if m:
            actors.append({"actor_name": m.group(1), "corporate_number": None})
    # dedupe by name
    seen, out = set(), []
    for a in actors:
        if a["actor_name"] and a["actor_name"] not in seen:
            seen.add(a["actor_name"])
            out.append(a)
    return out


def parse_disposition(text, text_c):
    d = {}
    m = re.search(r"([^\s]{2,12}経済産業局|消費者庁)が特定商取引法に基づく行政処分を実施", text_c)
    if m:
        d["announcing_agency"] = m.group(1)
    else:
        d["announcing_agency"] = "消費者庁"
    d["actor_address"] = field_after(text_c, "本店所在地",
                                     ["代表者", "設立", "資本金", "取引類型"], 150)
    if not d["actor_address"]:
        addr = field_after(text_c, "事業所所在地", ["※", "特定商取引法"], 300)
        d["actor_address"] = addr
    d["representative_name"] = field_after(text_c, "代表者",
                                           ["設立", "資本金", "取引類型", "取扱商品"], 60)
    if d["representative_name"]:
        d["representative_name"] = re.split(r"[（(]", d["representative_name"])[0]
    d["established_on"] = field_after(text_c, "設立", ["資本金", "取引類型"], 40)
    d["capital"] = field_after(text_c, "資本金", ["取引類型", "取扱商品"], 40)
    d["business_type"] = field_after(text_c, "取引類型",
                                      ["取扱商品", "特定商取引法", "処分対象事業者"], 60)
    d["products"] = field_after(text_c, "取扱商品",
                                 ["特定商取引法", "処分対象事業者"], 100)
    vm = re.search(r"特定商取引法の規定に(?:違反又は)?該当する行為(.{10,600}?)(?:別紙|処分対象事業者|\d処分)", text_c)
    if vm:
        d["violation_summary"] = collapse(vm.group(1), 500)
    arts = []
    for m in re.finditer(r"特定商取引法第\d+条(?:の\d+)?(?:第\d+項)?", norm_digits(text_c)):
        if m.group(0) not in arts:
            arts.append(m.group(0))
    d["legal_basis"] = "、".join(arts[:5]) or None
    sanc = []
    for m in re.finditer(r"(業務停止命令|取引等停止命令|業務禁止命令|指示)[（(]?([０-９0-9]+か月)?", text_c):
        s = m.group(1) + (f"（{norm_digits(m.group(2))}）" if m.group(2) else "")
        if s not in sanc:
            sanc.append(s)
    d["sanction_content"] = "、".join(sanc[:4]) or None
    pm = re.search(r"((?:令和|平成)(?:元|\d+)年\d+月\d+日から(?:令和|平成)(?:元|\d+)年\d+月\d+日までの[０-９0-9]+か月間)",
                   text_c)
    d["sanction_period"] = norm_digits(pm.group(1)) if pm else None
    return d


def parse_caution(text, text_c):
    d = {"announcing_agency": "消費者庁",
         "legal_basis": "消費者安全法第38条第1項"}
    d["actor_address"] = field_after(text_c, "所在地",
                                     ["代表者", "（注）", "なお、本件事業者"], 150)
    d["representative_name"] = field_after(text_c, "代表者",
                                           ["（注）", "なお、本件事業者"], 40)
    mm = re.search(r"具体的な事例の(?:内容|概要)(.{10,900}?)(?:\d．|以上、)", text_c)
    if mm:
        d["method_narrative"] = collapse(mm.group(1), 800)
    im = re.search(r"([^\s、。]{2,25})の名称をかたり", text_c)
    if im:
        d["impersonated_name"] = im.group(1)
    return d


def main():
    outdir = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).parent / "out"
    outdir.mkdir(parents=True, exist_ok=True)
    entries = defaultdict(list)
    for f in sorted(RAW.glob("*_caa_*.pdf")):
        m = re.match(r"\d+_caa_(disposition|caution38)_(\d+)_\d+\.pdf", f.name)
        if m:
            entries[(m.group(1), m.group(2))].append(f)
    stmts, stats = [], {"entries": 0, "records": 0, "with_actor": 0,
                        "with_corpno": 0, "with_addr": 0}
    for (kind, eid), files in sorted(entries.items()):
        text = "\n".join(pdf_text(f) for f in files)
        text_c = compact(text)
        entry_url = f"https://www.caa.go.jp/notice/entry/{eid}/"
        scope = files[0].name.rsplit("_", 1)[0]  # delete scope stem
        stmts.append(
            f"DELETE FROM third_party_record WHERE source_id={SOURCE_ID} "
            f"AND source_file_name LIKE {sql_str(scope + '%')};")
        actors = parse_actors(text, text_c)
        parsed = (parse_disposition(text, text_c) if kind == "disposition"
                  else parse_caution(text, text_c))
        if not actors:
            actors = [{"actor_name": None, "corporate_number": None}]
        for a in actors:
            rec = {
                "source_id": SOURCE_ID,
                "source_format": "pdf",
                "source_document_url": entry_url,
                "source_file_name": ";".join(f.name for f in files),
                "published_on": wareki_date(text[:800]),
                "captured_at": CAPTURED_AT,
                "has_phone": 0,  # rule: CAA PDFs carry no actor phone (see docstring)
                "status": "active",
                **parsed,
                **a,
            }
            stmts.append(record_insert(rec))
            stats["records"] += 1
            stats["with_actor"] += bool(a["actor_name"])
            stats["with_corpno"] += bool(a["corporate_number"])
            stats["with_addr"] += bool(parsed.get("actor_address"))
        stats["entries"] += 1
    path = outdir / "caa.sql"
    path.write_text("\n".join(stmts) + "\n", encoding="utf-8")
    print(f"caa: {stats} -> {path}")


if __name__ == "__main__":
    main()
