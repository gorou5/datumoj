"""Shared helpers for the third-party publication ETL (phase 2).

All scripts are re-runnable: each emits a DELETE for its own
(source_id, source_file_name) scope before its INSERTs, so re-running a
script replaces exactly the rows it owns and never duplicates.

Evidence chain (repo AGENTS.md §3.3): every record keeps the official
landing-page URL (source_document_url), the raw file it was parsed from
(source_file_name), the publication date (published_on) and the capture
date (captured_at) as separate fields.
"""

import re
import unicodedata

ZEN2HAN = str.maketrans("０１２３４５６７８９", "0123456789")


def norm_digits(s):
    if s is None:
        return None
    return str(s).translate(ZEN2HAN)


def wareki_to_year(era, n):
    n = 1 if n in ("元", "1") else int(n)
    return {"令和": 2018, "平成": 1988, "昭和": 1925}[era] + n


WAREKI_RE = re.compile(r"(令和|平成|昭和)(元|\d+)年(?:(\d+)月)?(?:(\d+)日)?")


def wareki_date(text, default_day="01"):
    """First Japanese-era date in text -> 'YYYY-MM-DD' (or 'YYYY-MM')."""
    if not text:
        return None
    m = WAREKI_RE.search(norm_digits(text))
    if not m:
        return None
    y = wareki_to_year(m.group(1), m.group(2))
    if not m.group(3):
        return f"{y:04d}"
    if not m.group(4):
        return f"{y:04d}-{int(m.group(3)):02d}"
    return f"{y:04d}-{int(m.group(3)):02d}-{int(m.group(4)):02d}"


def collapse(s, limit=None):
    if s is None:
        return None
    s = re.sub(r"\s+", " ", str(s)).strip()
    if limit and len(s) > limit:
        s = s[:limit]
    return s or None


def phone_digits(raw):
    """Normalize a phone number as printed in a publication to digits only.

    Returns None unless the result is a plausible Japanese number
    (0 + 9 or 10 further digits).
    """
    if not raw:
        return None
    d = re.sub(r"\D", "", norm_digits(raw))
    if re.fullmatch(r"0\d{9,10}", d):
        return d
    return None


def sql_str(v):
    if v is None:
        return "NULL"
    if isinstance(v, int):
        return str(v)
    return "'" + str(v).replace("'", "''") + "'"


RECORD_COLUMNS = [
    "source_id", "source_format", "source_document_url", "source_file_name",
    "published_on", "captured_at", "announcing_agency", "actor_name",
    "corporate_number", "actor_address", "representative_name",
    "established_on", "capital", "service_name", "website_url",
    "business_type", "products", "domestic_overseas", "violation_summary",
    "legal_basis", "sanction_content", "sanction_period",
    "publication_reason", "remark", "method_narrative", "impersonated_name",
    "region_text", "distribution_title", "distribution_body",
    "phone_number_raw", "phone_digits", "has_phone", "status",
]


def record_insert(rec):
    vals = ",".join(sql_str(rec.get(c)) for c in RECORD_COLUMNS)
    cols = ",".join(RECORD_COLUMNS)
    return f"INSERT INTO third_party_record ({cols}) VALUES ({vals});"


def batched(stmts, size=200):
    for i in range(0, len(stmts), size):
        yield stmts[i:i + size]
