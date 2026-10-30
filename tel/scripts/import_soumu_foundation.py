# Build the SOUMU foundation library: every Excel file published on the MIC
# "電気通信番号指定状況" page becomes ONE table in a local SQLite database
# (tel/knowledge/soumu_foundation.db). Faithful staging: the file's own grid
# (title rows skipped, header row detected, all values stored as TEXT).
# Curated D1 tables (tel_datumoj) are derived from this foundation later.
#
# Usage: python3 scripts/import_soumu_foundation.py
import re
import sqlite3
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent  # tel/
EXCEL_DIR = HERE / 'knowledge' / 'soumu_excel'
DB_PATH = HERE / 'knowledge' / 'soumu_foundation.db'
PAGE_URL = 'https://www.soumu.go.jp/main_sosiki/joho_tsusin/top/tel_number/number_shitei.html'
FETCHED_ON = '2026-10-22'

# file name -> (table name, category in Japanese)
FILES = {
    '000697543.xls': ('designation_fixed_area_leading_1', '固定電話番号（市外局番1始まり）'),
    '000697544.xls': ('designation_fixed_area_leading_2', '固定電話番号（市外局番2始まり）'),
    '000697545.xls': ('designation_fixed_area_leading_3', '固定電話番号（市外局番3始まり）'),
    '000697546.xls': ('designation_fixed_area_leading_4', '固定電話番号（市外局番4始まり）'),
    '000697548.xls': ('designation_fixed_area_leading_5', '固定電話番号（市外局番5始まり）'),
    '000697549.xls': ('designation_fixed_area_leading_6', '固定電話番号（市外局番6始まり）'),
    '000697550.xls': ('designation_fixed_area_leading_7', '固定電話番号（市外局番7始まり）'),
    '000697551.xls': ('designation_fixed_area_leading_8', '固定電話番号（市外局番8始まり）'),
    '001090880.xls': ('designation_fixed_area_leading_9', '固定電話番号（市外局番9始まり）'),
    '000697577.xlsx': ('designation_toll_free_0120', '着信課金機能（0120）'),
    '001045206.xlsx': ('designation_toll_free_0800', '着信課金機能（0800）'),
    '001076964.xlsx': ('designation_navi_dial_0570', '統一番号機能（0570）'),
    '000697581.xlsx': ('designation_charge_billing_0990', '情報料代理徴収機能（0990）'),
    '001055867.xlsx': ('designation_m2m_020_11digit', 'データ伝送携帯電話番号（020・11桁）'),
    '001080766.xlsx': ('designation_m2m_020_14digit', 'データ伝送携帯電話番号（020・14桁）'),
    '001019693.xlsx': ('designation_mobile_060', '音声伝送携帯電話番号（060）'),
    '001090878.xlsx': ('designation_mobile_070', '音声伝送携帯電話番号（070）'),
    '000697565.xlsx': ('designation_mobile_080', '音声伝送携帯電話番号（080）'),
    '000697567.xlsx': ('designation_mobile_090', '音声伝送携帯電話番号（090）'),
    '001076968.xlsx': ('designation_ip_phone_050', '特定IP電話番号（050）'),
    '000748700.xlsx': ('designation_special_connection', '特定接続電話番号'),
    '001090876.xlsx': ('designation_imsi', 'IMSI'),
    '001012643.xlsx': ('designation_carrier_equipment_id', '事業者設備識別番号（00XY等）'),
    '001080770.xlsx': ('designation_supplementary_service_id', '付加的役務識別番号'),
    '001019698.xlsx': ('designation_emergency_numbers', '緊急通報番号（110／118／119）'),
    '001080774.xlsx': ('designation_international_signalling_id', '国際信号局識別番号'),
    '000748708.xlsx': ('designation_data_equipment_id', 'データ通信設備識別番号'),
}


def cell_text(v):
    if v is None:
        return None
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v).strip()


def read_grid(path):
    """Return list of rows (lists of cell_text) from the first sheet."""
    if path.suffix == '.xlsx':
        import openpyxl

        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
        ws = wb[wb.sheetnames[0]]
        return [[cell_text(c) for c in row] for row in ws.iter_rows(values_only=True)], wb.sheetnames[0]
    import xlrd

    wb = xlrd.open_workbook(str(path))
    ws = wb.sheet_by_index(0)
    return [
        [cell_text(ws.cell_value(r, c)) for c in range(ws.ncols)] for r in range(ws.nrows)
    ], wb.sheet_names()[0]


def find_header(grid):
    """Header row = first row within 12 that has >=2 non-empty cells and whose
    first cell is a plausible label (番号/番号区画コード/識別番号/名称 ...)."""
    for i, row in enumerate(grid[:12]):
        cells = [c for c in row if c]
        if len(cells) >= 2 and row[0] and not row[0].startswith('・') and '現在' not in row[0]:
            return i
    return 0


def sanitize(name, used):
    base = re.sub(r'[\s　/／・（）()「」]+', '_', (name or '').strip()).strip('_') or 'col'
    out, n = base, 2
    while out in used:
        out = f'{base}_{n}'
        n += 1
    used.add(out)
    return out


def main():
    if DB_PATH.exists():
        DB_PATH.unlink()
    con = sqlite3.connect(DB_PATH)
    con.execute(
        """CREATE TABLE source_file (
             table_name TEXT PRIMARY KEY, source_file TEXT NOT NULL, category_ja TEXT,
             sheet_name TEXT, data_asof TEXT, row_count INTEGER,
             page_url TEXT NOT NULL, fetched_on TEXT NOT NULL)"""
    )
    total = 0
    for fname, (table, category) in FILES.items():
        path = EXCEL_DIR / fname
        if not path.exists():
            print(f'MISSING {fname}', file=sys.stderr)
            continue
        grid, sheet = read_grid(path)
        hi = find_header(grid)
        header = grid[hi]
        width = max(len(r) for r in grid)
        used = set()
        cols = [sanitize(header[j] if j < len(header) else None, used) for j in range(width)]
        asof = None
        for row in grid[:6]:
            for c in row:
                if c and '現在' in c and '令和' in c:
                    asof = c.strip('()（） ')
        con.execute(f'CREATE TABLE "{table}" (' + ', '.join(f'"{c}" TEXT' for c in cols) + ')')
        rows = []
        for row in grid[hi + 1 :]:
            vals = [(row[j] if j < len(row) else None) for j in range(width)]
            if any(vals):
                rows.append(vals)
        con.executemany(f'INSERT INTO "{table}" VALUES (' + ','.join('?' * width) + ')', rows)
        con.execute(
            'INSERT INTO source_file VALUES (?,?,?,?,?,?,?,?)',
            (table, fname, category, sheet, asof, len(rows), PAGE_URL, FETCHED_ON),
        )
        total += len(rows)
        print(f'{table:45s} {len(rows):6d} rows  ({category}, {asof or "-"})')
    con.commit()
    con.close()
    print(f'\nfoundation db: {DB_PATH}  tables={len(FILES)}  total rows={total}')


if __name__ == '__main__':
    main()
