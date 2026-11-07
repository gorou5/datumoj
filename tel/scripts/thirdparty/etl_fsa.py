#!/usr/bin/env python3
"""FSA unregistered-operator warning lists -> third_party_record.

Source: 金融庁 (Financial Services Agency) public Excel lists of operators
that received warning letters for unregistered business (source_id=3,
tier B). Phase-1 inventory verified by full-cell scan: there is NO phone
number column and no complete phone number anywhere in these files, so
every record is emitted with has_phone=0 and no digits are mined from the
free-text 備考 (note) field — digit fragments there are association
membership numbers, impersonated registration numbers and foreign ZIP
codes, not phone numbers.

Fields mapped: 商号 -> actor_name (with a trailing 代表者… segment split
out into representative_name), 所在地 -> actor_address, 業務の内容等 ->
business_type, 備考 -> remark (verbatim), 掲載時期 -> published_on
(YYYY-MM). Additionally the service name is lifted out of the note
(「サービス名称は「X」」/「「X」を運営」 patterns) into service_name, the
first domain-like token in name/note into website_url, and a 金融商品取引
法/資金決済法 article citation in the note into legal_basis. Domestic /
overseas follows the list file split.
"""

import re
import sys
from pathlib import Path

import openpyxl

sys.path.insert(0, str(Path(__file__).parent))
from common import collapse, norm_digits, record_insert, sql_str, wareki_date

RAW = Path.home() / "workspace/tel-datumoj/thirdparty/raw/fsa"
CAPTURED_AT = "2026-10-27"
SOURCE_ID = 3

LISTS = [
    # (file, list landing page, business label, domestic_overseas)
    ("20261027_fsa_kinsho_domestic.xlsx",
     "https://www.fsa.go.jp/ordinary/chuui/mutouroku/01.xlsx",
     "金融商品取引業（無登録）", "domestic"),
    ("20261027_fsa_kinsho_overseas.xlsx",
     "https://www.fsa.go.jp/ordinary/chuui/mutouroku/03.xlsx",
     "金融商品取引業（無登録）", "overseas"),
    ("20261027_fsa_kinsho_tokurei.xlsx",
     "https://www.fsa.go.jp/ordinary/chuui/mutouroku/02.xlsx",
     "適格機関投資家等特例業務（無登録・警告書発出済）", None),
    ("20261027_fsa_crypto_domestic.xlsx",
     "https://www.fsa.go.jp/policy/virtual_currency/angoushisan_mutouroku_j.xlsx",
     "暗号資産交換業（無登録）", "domestic"),
    ("20261027_fsa_crypto_overseas.xlsx",
     "https://www.fsa.go.jp/policy/virtual_currency/angoushisan_mutouroku.xlsx",
     "暗号資産交換業（無登録）", "overseas"),
]

DOMAIN_RE = re.compile(
    r"\b(?:[a-zA-Z0-9-]+\.)+(?:com|net|org|io|co|jp|info|xyz|site|online|trade|finance|markets?|exchange|vip|app)\b")
SERVICE_RES = [
    re.compile(r"サービス名称は「([^」]{1,60})」"),
    re.compile(r"サービス名は「([^」]{1,60})」"),
    re.compile(r"「([^」]{1,60})」を運営"),
    re.compile(r"「([^」]{1,60})」等を運営"),
]
LEGAL_RE = re.compile(
    r"((?:金融商品取引法|資金決済に関する法律|資金決済法)第[0-9]+条(?:の[0-9]+)?)")


def split_representative(name_cell):
    """Name cells sometimes carry a 代表者 segment on a later line, or (in
    the kinsho lists) a representative's title/name mixed in. Split only the
    explicit 代表者 segment; keep everything else verbatim in actor_name."""
    if not name_cell:
        return None, None
    parts = [p.strip() for p in str(name_cell).split("\n")]
    name_lines, rep = [], None
    for p in parts:
        if not p:
            continue
        m = re.match(r"代表者[\s　]*(.+)", p)
        if m:
            rep = m.group(1).strip()
        else:
            name_lines.append(p)
    return collapse(" ".join(name_lines)), (collapse(rep) if rep else None)


def parse_workbook(path):
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    try:
        sheet = None
        for sn in wb.sheetnames:
            if sn == "留意事項":
                continue
            sheet = wb[sn]
            break
        rows = list(sheet.iter_rows(values_only=True))
    finally:
        wb.close()
    hdr = None
    for i, r in enumerate(rows):
        if r and r[0] and "商号" in str(r[0]):
            hdr = i
            break
    if hdr is None:
        raise RuntimeError(f"header row not found in {path}")
    out = []
    for r in rows[hdr + 1:]:
        if not r or not r[0] or not str(r[0]).strip():
            continue
        out.append(r)
    return out


def main():
    outdir = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).parent / "out"
    outdir.mkdir(parents=True, exist_ok=True)
    stmts, total = [], 0
    per_file = {}
    for fname, url, biz_label, dom in LISTS:
        rows = parse_workbook(RAW / fname)
        per_file[fname] = len(rows)
        stmts.append(
            f"DELETE FROM third_party_record WHERE source_id={SOURCE_ID} "
            f"AND source_file_name={sql_str(fname)};")
        for r in rows:
            name_raw, addr, content, note, period = (list(r) + [None] * 5)[:5]
            actor, rep = split_representative(name_raw)
            remark = collapse(note)
            service = None
            if remark:
                for rx in SERVICE_RES:
                    m = rx.search(remark)
                    if m:
                        service = m.group(1)
                        break
            website = None
            for hay in (str(name_raw or ""), remark or ""):
                m = DOMAIN_RE.search(hay)
                if m:
                    website = m.group(0).lower()
                    break
            legal = None
            if remark:
                m = LEGAL_RE.search(norm_digits(remark))
                if m:
                    legal = m.group(1)
            rec = {
                "source_id": SOURCE_ID,
                "source_format": "excel",
                "source_document_url": url,
                "source_file_name": fname,
                "published_on": wareki_date(period),
                "captured_at": CAPTURED_AT,
                "announcing_agency": "金融庁",
                "actor_name": actor,
                "actor_address": collapse(addr),
                "representative_name": rep,
                "service_name": service,
                "website_url": website,
                "business_type": collapse(content),
                "domestic_overseas": dom,
                "legal_basis": legal,
                "remark": remark,
                "has_phone": 0,
                "status": "active",
            }
            # biz_label is context, kept in remark? No: business_type already
            # holds the list's own 内容等 text; the list category is recorded
            # via the file/URL. Nothing extra to store.
            stmts.append(record_insert(rec))
            total += 1
    path = outdir / "fsa.sql"
    path.write_text("\n".join(stmts) + "\n", encoding="utf-8")
    print(f"fsa: {total} records -> {path}")
    for k, v in per_file.items():
        print(f"  {k}: {v}")


if __name__ == "__main__":
    main()
