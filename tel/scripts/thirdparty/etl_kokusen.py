#!/usr/bin/env python3
"""National Consumer Affairs Center (国民生活センター) "name publication"
releases -> third_party_record + third_party_flag.

This is the one public source in the registry that publishes a business
name together with its phone number: releases whose text states
「本件事業者の名称等を公表し、消費者に注意を呼びかけます」 carry a
事業者の情報 block (名称/法人番号/サービスの名称/所在地/電話番号/
代表者の氏名/ウェブサイト). The release list pages do NOT carry the
publication phrase in their titles, so every detail page of the current
archive (news.html = FY2026, news25..news21 = FY2025..FY2021, plus
news20..news16 = FY2020..FY2016, which are still reachable) is scanned
and matched on the phrase in the body.

Phone numbers are taken ONLY from inside the 事業者の情報 block of a
matched release (phase-1 parsing rule ②: numbers elsewhere in the page —
hotline 188, contact desks — are not the actor's number).

Fetches are polite (1.1 s interval, official site only) and cached under
raw/kokusen/, so the script is resumable. Records are emitted for every
matched release (has_phone as found); a third_party_flag (source_id=7,
tier C) is emitted per published phone number, linked to its record via a
subquery on (source_id, source_file_name, phone_digits).
"""

import json
import re
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from common import (collapse, norm_digits, phone_digits, record_insert,
                    sql_str, wareki_date)

BASE = "https://www.kokusen.go.jp"
RAW = Path.home() / "workspace/tel-datumoj/thirdparty/raw/kokusen"
CAPTURED_AT = "2026-10-27"
# Registry id of the 国民生活センター「事業者の名称等を公表」 source row.
# It was 5 while the row was combined with 消費者庁; migration
# 0010_source_cleanup.sql split it out as id=7.
SOURCE_ID = 7
UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) tel-datumoj-etl/1.0"}
LIST_PAGES = ["news.html"] + [f"news{y}.html" for y in range(25, 15, -1)]
LABELS = {"名称", "サービスの名称", "サービス名", "所在地", "本店所在地",
          "電話番号", "電話", "代表者の氏名", "代表者", "ウェブサイト",
          "ウェブサイトURL", "URL", "屋号"}
BLOCK_END = {"相談事例", "相談事例からみる問題点", "消費者へのアドバイス",
             "事業者への申入れ", "申入れの内容", "情報提供先"}


def fetch(url, cache_path, binary=False):
    if cache_path.exists() and cache_path.stat().st_size > 0:
        return cache_path.read_bytes() if binary else cache_path.read_text(
            encoding="utf-8", errors="replace")
    req = urllib.request.Request(url, headers=UA)
    last_err = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                data = r.read()
            break
        except Exception as e:  # transient network errors: retry, then give up
            last_err = e
            time.sleep(5 * (attempt + 1))
    else:
        raise last_err
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_bytes(data)
    time.sleep(1.1)
    return data if binary else data.decode("utf-8", errors="replace")


def _strip(fragment):
    t = re.sub(r"<[^>]+>", " ", fragment)
    return collapse(t.replace("　", " "))


def parse_block(html):
    """Parse the 事業者の情報 definition list(s) into per-actor dicts.

    The block is a <dl> following the 事業者の情報 heading: <dt> label /
    <dd> value pairs (所在地 may repeat its <dd>). A new actor starts at
    each 名称 <dt> after the first.
    """
    groups = []
    for hm in re.finditer(
            r"(?:事業者の情報|事業者の概要|本件事業者の概要|本件事業者の情報)</h2>",
            html):
        dlm = re.search(r"<dl>(.*?)</dl>", html[hm.end():], re.S)
        if not dlm:
            continue
        cur, label = None, None
        for m in re.finditer(r"<dt>(.*?)</dt>|<dd>(.*?)</dd>", dlm.group(1),
                             re.S):
            if m.group(1) is not None:
                label = _strip(m.group(1))
                if label == "名称":
                    cur = {}
                    groups.append(cur)
                continue
            if cur is None or label is None:
                continue
            raw_dd = m.group(2)
            href = re.search(r'href="([^"]+)"', raw_dd)
            val = _strip(raw_dd)
            cur.setdefault(label, []).append(val)
            if href and label in ("ウェブサイト", "ウェブサイトURL", "URL"):
                cur.setdefault("_hrefs", []).append(href.group(1))
    actors = []
    for g in groups:
        a = {}
        nm = " ".join(g.get("名称") or [])
        m = re.search(r"^(.*?)[（(]法人番号\s*(\d{13})[）)]", nm)
        if m:
            a["actor_name"] = collapse(m.group(1))
            a["corporate_number"] = m.group(2)
        else:
            a["actor_name"] = collapse(nm) or None
        a["service_name"] = collapse(
            " ".join(g.get("サービスの名称") or g.get("サービス名") or [])) or None
        addr_cells = g.get("所在地") or g.get("本店所在地") or []
        addrs = []
        for c in addr_cells:
            # dd text may glue a 種別 prefix ending in 所在地/住所 onto the
            # address itself; keep only the address part.
            c2 = re.sub(r"^.*(?:所在地|住所)", "", c)
            if re.search(r"[都道府県市区町村]", c2) and len(c2) >= 6:
                addrs.append(c2)
        a["actor_address"] = "／".join(addrs) if addrs else (
            collapse(" ".join(addr_cells)) or None)
        tel = g.get("電話番号") or g.get("電話") or []
        a["phone_number_raw"] = tel[0] if tel else None
        a["phone_digits"] = phone_digits(a["phone_number_raw"])
        rep = g.get("代表者の氏名") or g.get("代表者") or []
        a["representative_name"] = collapse(" ".join(rep)) or None
        a["_hrefs"] = g.get("_hrefs") or []
        actors.append(a)
    return actors


def main():
    outdir = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).parent / "out"
    outdir.mkdir(parents=True, exist_ok=True)
    items = {}
    for lp in LIST_PAGES:
        try:
            html = fetch(f"{BASE}/news/{lp}", RAW / "lists" / lp)
        except Exception as e:
            print(f"list {lp}: FETCH FAILED {e}", file=sys.stderr)
            continue
        for m in re.finditer(
                r'href="data/(n-(\d{8})_(\d+))\.html"[^>]*>([^<]+)</a>', html):
            fid, ymd = m.group(1), m.group(2)
            items[fid] = {
                "file_id": fid,
                "title": collapse(m.group(4)),
                "published_on": f"{ymd[:4]}-{ymd[4:6]}-{ymd[6:]}",
                "detail_url": f"{BASE}/news/data/{fid}.html",
                "pdf_url": f"{BASE}/pdf/{fid}.pdf",
                "pdf_name": f"{fid}.pdf",
            }
    print(f"kokusen: {len(items)} releases listed")
    rec_stmts = [
        f"DELETE FROM third_party_record WHERE source_id={SOURCE_ID} "
        "AND source_file_name LIKE 'n-%';"]
    flag_stmts = [
        f"DELETE FROM third_party_flag WHERE source_id={SOURCE_ID} "
        "AND flag_type='名称等公表';"]
    stats = {"listed": len(items), "matched": 0, "records": 0,
             "with_phone": 0, "flags": 0, "multi_actor": 0}
    for fid, it in sorted(items.items()):
        try:
            html = fetch(it["detail_url"], RAW / "html" / f"{fid}.html")
        except Exception as e:
            print(f"{fid}: detail fetch failed, skipped: {e}",
                  file=sys.stderr)
            stats["fetch_failed"] = stats.get("fetch_failed", 0) + 1
            continue
        text = collapse(re.sub(r"<[^>]+>", " ", html))
        if "名称等を公表" not in text:
            continue
        stats["matched"] += 1
        # evidence PDF (also the source of website URLs)
        pdf_bytes = None
        try:
            pdf_bytes = fetch(it["pdf_url"], RAW / "pdf" / it["pdf_name"],
                              binary=True)
        except Exception as e:
            print(f"{fid}: PDF fetch failed {e}", file=sys.stderr)
        pdf_block_url = None
        if pdf_bytes:
            r = subprocess.run(["pdftotext", "-layout",
                                str(RAW / "pdf" / it["pdf_name"]), "-"],
                               capture_output=True, text=True, timeout=120)
            m = re.search(r"https?://[^\s」]+", r.stdout or "")
            pdf_block_url = m.group(0) if m else None
        actors = parse_block(html)
        if not actors:
            actors = [{"actor_name": None}]
        if len(actors) > 1:
            stats["multi_actor"] += 1
        reason = None
        rm = re.search(r"([^。]{10,200}名称等を公表し、消費者に注意を呼びかけます)",
                       text)
        if rm:
            reason = rm.group(1) + "。"
        intro = None
        im = re.search(r"(.{40,500}?)事業者の情報", text)
        if im:
            intro = im.group(1)[-500:]
        for a in actors:
            hrefs = a.pop("_hrefs", []) or []
            web = hrefs[0] if hrefs else None
            if not web and pdf_block_url and a.get("service_name"):
                web = pdf_block_url
            has_phone = 1 if a.get("phone_digits") else 0
            rec = {
                "source_id": SOURCE_ID,
                "source_format": "pdf",
                "source_document_url": it["detail_url"],
                "source_file_name": it["pdf_name"],
                "published_on": it["published_on"],
                "captured_at": CAPTURED_AT,
                "announcing_agency": "国民生活センター",
                "actor_name": a.get("actor_name"),
                "corporate_number": a.get("corporate_number"),
                "actor_address": a.get("actor_address"),
                "representative_name": a.get("representative_name"),
                "service_name": a.get("service_name"),
                "website_url": web,
                "publication_reason": reason,
                "violation_summary": intro,
                "phone_number_raw": a.get("phone_number_raw"),
                "phone_digits": a.get("phone_digits"),
                "has_phone": has_phone,
                "status": "active",
            }
            rec_stmts.append(record_insert(rec))
            stats["records"] += 1
            stats["with_phone"] += has_phone
            if has_phone:
                sub = (f"(SELECT id FROM third_party_record WHERE source_id={SOURCE_ID} "
                       f"AND source_file_name={sql_str(it['pdf_name'])} "
                       f"AND phone_digits={sql_str(a['phone_digits'])} LIMIT 1)")
                flag_stmts.append(
                    "INSERT INTO third_party_flag (number, source_id, "
                    "flag_type, label, evidence_url, published_on, first_seen, "
                    "last_verified, status, actor_name, corporate_number, "
                    "service_name, website_url, violation_summary, "
                    "announcing_agency, source_document_url, source_record_id, "
                    "method_summary) VALUES ("
                    f"{sql_str(a['phone_digits'])}, {SOURCE_ID}, '名称等公表', "
                    f"{sql_str('国民生活センターが事業者の名称等を公表した際に併せて公表された電話番号')}, "
                    f"{sql_str(it['detail_url'])}, "
                    f"{sql_str(it['published_on'])}, '{CAPTURED_AT}', "
                    f"'{CAPTURED_AT}', 'active', "
                    f"{sql_str(a.get('actor_name'))}, "
                    f"{sql_str(a.get('corporate_number'))}, "
                    f"{sql_str(a.get('service_name'))}, {sql_str(web)}, "
                    f"{sql_str(intro)}, '国民生活センター', "
                    f"{sql_str(it['pdf_url'])}, {sub}, "
                    f"{sql_str(reason)});")
                stats["flags"] += 1
    (outdir / "kokusen_records.sql").write_text(
        "\n".join(rec_stmts) + "\n", encoding="utf-8")
    (outdir / "kokusen_flags.sql").write_text(
        "\n".join(flag_stmts) + "\n", encoding="utf-8")
    print(f"kokusen: {json.dumps(stats, ensure_ascii=False)}")


if __name__ == "__main__":
    main()
