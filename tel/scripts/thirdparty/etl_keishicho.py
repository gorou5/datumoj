#!/usr/bin/env python3
"""Tokyo Metropolitan Police "Mail Keishicho" open data, topic 304
(scam-call reports) -> third_party_record.

Source: Mail Keishicho open data (CC BY 4.0), downloaded in phase 1
(source_id=4, tier C). One record per distribution mail: distribution
datetime -> published_on, title/body -> distribution_title/body, sender
(police station) -> announcing_agency, region -> region_text. The
impersonation phrase in the body is lifted into impersonated_name, and
the "content of the call" section into method_narrative.

Phase-1 verified: perpetrators' numbers are NEVER published in full in
these mails (described by type only); the only complete numbers in the
text are police-station contact numbers in the inquiry footer. Those are
not the actor's number, so has_phone=0 for every row and no digits are
extracted.

Output is chunked (2,000 records per file) so the load can stop at the
D1 free-plan daily write budget and resume the next day; the chunk set
is idempotent as a whole (the first chunk deletes the previous load of
this source file).
"""

import csv
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from common import collapse, record_insert, sql_str

RAW = (Path.home() / "workspace/tel-datumoj/thirdparty/raw/keishicho/"
       "20261027_keishicho_mail_topic304_scam_calls.csv")
FNAME = RAW.name
CAPTURED_AT = "2026-10-27"
SOURCE_ID = 4
DOC_URL = "https://mail.keishicho.metro.tokyo.lg.jp/opendata/download"
CHUNK = 2000

IMPERSONATE_RE = re.compile(r"([^\s、。・]{2,20})をかたる")
METHOD_RE = re.compile(r"■電話の内容\s*(.*?)(?:■|【問合せ先】|$)", re.S)

COL_DT = "配信日時"
COL_TITLE = "配信表題"
COL_BODY = "配信本文"
COL_FROM = "配信元"
COL_TO = "配信先"


def main():
    outdir = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).parent / "out"
    outdir.mkdir(parents=True, exist_ok=True)
    for old in outdir.glob("keishicho_*.sql"):
        old.unlink()
    state = {"total": 0, "chunk": 0, "buf": []}

    def flush():
        if not state["buf"]:
            return
        state["chunk"] += 1
        path = outdir / f"keishicho_{state['chunk']:02d}.sql"
        path.write_text("\n".join(state["buf"]) + "\n", encoding="utf-8")
        print(f"  chunk {state['chunk']}: {len(state['buf'])} stmts -> {path.name}")
        state["buf"] = []

    with open(RAW, encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        state["buf"].append(
            f"DELETE FROM third_party_record WHERE source_id={SOURCE_ID} "
            f"AND source_file_name={sql_str(FNAME)};")
        for row in reader:
            dt = (row.get(COL_DT) or "").strip()
            body = (row.get(COL_BODY) or "").strip()
            imp = IMPERSONATE_RE.search(body)
            meth = METHOD_RE.search(body)
            rec = {
                "source_id": SOURCE_ID,
                "source_format": "csv",
                "source_document_url": DOC_URL,
                "source_file_name": FNAME,
                "published_on": dt[:10] if dt else None,
                "captured_at": CAPTURED_AT,
                "announcing_agency": collapse(row.get(COL_FROM)) or "警視庁",
                "method_narrative": (collapse(meth.group(1), 1000)
                                     if meth else None),
                "impersonated_name": imp.group(1) if imp else None,
                "region_text": collapse(row.get(COL_TO)),
                "distribution_title": collapse(row.get(COL_TITLE)),
                "distribution_body": body or None,
                "has_phone": 0,
                "status": "active",
            }
            state["buf"].append(record_insert(rec))
            state["total"] += 1
            if len(state["buf"]) >= CHUNK + 1:
                flush()
    flush()
    print(f"keishicho: {state['total']} records in {state['chunk']} chunk files")


if __name__ == "__main__":
    main()
