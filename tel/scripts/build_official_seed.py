# Derive the curated D1 seed from the SOUMU foundation library
# (knowledge/soumu_foundation.db, built by import_soumu_foundation.py):
#
#   * carriers — legal names from the designation files, width-normalized;
#     the majors are aliased onto the existing short-name carrier rows (1-7),
#     everyone else gets a new row with id >= 100.
#   * fixed blocks — every 使用中 row of the 9 fixed files becomes a 6-digit
#     designation block carrying its carrier AND its charge area (MA), which
#     the foundation file gives as 番号区画コード.
#   * matrix blocks — the mobile / 050 / 0120 / 0800 / 0570 / 020 designation
#     matrices are unpivoted: cell (base row, digit column) -> prefix block.
#
# Output: seed/official_seed_N.sql parts (idempotent: the parts first delete
# the block class they own, length(prefix) >= 6, and carriers id >= 100).
#
# Usage: python3 scripts/build_official_seed.py
import json
import sqlite3
import unicodedata
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent  # tel/
FOUNDATION = HERE / 'knowledge' / 'soumu_foundation.db'
SOURCE = '総務省「電気通信番号指定状況」'

# normalized legal name -> existing carrier id (seed.sql rows 1-7)
ALIAS = {
    '東日本電信電話株式会社': 1,
    '西日本電信電話株式会社': 2,
    '株式会社NTTドコモ': 3,
    'KDDI株式会社': 4,
    'ソフトバンク株式会社': 5,
    '楽天モバイル株式会社': 6,
    'エヌ・ティ・ティ・コミュニケーションズ株式会社': 7,
}

MATRICES = {
    'designation_mobile_060': 'mobile',
    'designation_mobile_070': 'mobile',
    'designation_mobile_080': 'mobile',
    'designation_mobile_090': 'mobile',
    'designation_ip_phone_050': 'ip050',
    'designation_toll_free_0120': 'free0120',
    'designation_toll_free_0800': 'free0800',
    'designation_navi_dial_0570': 'navi0570',
    'designation_m2m_020_11digit': 'm2m020',
    'designation_m2m_020_14digit': 'm2m020',
}


def norm(name):
    return unicodedata.normalize('NFKC', (name or '')).strip()


def q(s):
    if s is None:
        return 'NULL'
    return "'" + str(s).replace("'", "''") + "'"


def main():
    con = sqlite3.connect(FOUNDATION)

    # zone_code -> charge_area id (seed.sql inserts area_ma.json in order, id from 1)
    ma_rows = json.loads((HERE / 'seed' / 'area_ma.json').read_text(encoding='utf-8'))
    zone_to_id = {}
    zone_to_area = {}
    for i, r in enumerate(ma_rows, 1):
        zone_to_id[r['zone_code']] = i
        zone_to_area[r['zone_code']] = r['area_code']

    carriers = {}  # normalized name -> id
    next_id = [100]

    def carrier_id(raw):
        name = norm(raw)
        if not name:
            return None
        if name in ALIAS:
            return ALIAS[name]
        if name not in carriers:
            carriers[name] = next_id[0]
            next_id[0] += 1
        return carriers[name]

    blocks = {}  # (line_type, prefix) -> (carrier_id, charge_area_id)

    # --- fixed blocks ---
    zone_area_mismatch = 0
    for i in range(1, 10):
        t = f'designation_fixed_area_leading_{i}'
        for zone, bango, area, carrier in con.execute(
            f"SELECT 番号区画コード, 番号, 市外局番, 事業者 FROM {t} WHERE 使用状況='使用中'"
        ):
            if not carrier or not bango:
                continue
            cid = carrier_id(carrier)
            maid = zone_to_id.get(str(zone).zfill(3))
            if maid and zone_to_area.get(str(zone).zfill(3)) != area:
                zone_area_mismatch += 1
            blocks[('fixed', bango)] = (cid, maid)
    print(f'fixed blocks: {sum(1 for k in blocks if k[0] == "fixed")}, zone/area mismatches vs seed: {zone_area_mismatch}')

    # --- matrix blocks (unpivot) ---
    for table, line_type in MATRICES.items():
        cols = [r[1] for r in con.execute(f'PRAGMA table_info({table})')]
        digit_cols = [(j, unicodedata.normalize('NFKC', c)) for j, c in enumerate(cols) if unicodedata.normalize('NFKC', c).isdigit()]
        n0 = len(blocks)
        for row in con.execute(f'SELECT * FROM {table}'):
            base = row[0]
            if not base:
                continue
            for j, d in digit_cols:
                cell = row[j]
                if cell:
                    blocks[(line_type, str(base) + d)] = (carrier_id(cell), None)
        print(f'{table}: +{len(blocks) - n0} blocks')

    # --- emit SQL parts ---
    stmts = [
        'DELETE FROM number_block_allocation WHERE length(prefix) >= 6;',
        'DELETE FROM carrier WHERE id >= 100;',
    ]
    for name, cid in sorted(carriers.items(), key=lambda kv: kv[1]):
        stmts.append(f'INSERT INTO carrier (id, name, kind) VALUES ({cid},{q(name)},NULL);')
    items = sorted(blocks.items())
    for k in range(0, len(items), 400):
        chunk = items[k : k + 400]
        vals = ',\n'.join(
            f'({q(lt)},{q(px)},{cid},NULL,\'designated\',NULL,{q(SOURCE)},{maid if maid else "NULL"})'
            for (lt, px), (cid, maid) in chunk
        )
        stmts.append(
            'INSERT INTO number_block_allocation (line_type, prefix, carrier_id, designated_on, status, note, source, charge_area_id) VALUES\n'
            + vals
            + ';'
        )
    parts, cur, part_no = [], [], 1
    size = 0
    for s in stmts:
        if size + len(s) > 1_100_000 and cur:
            parts.append(cur)
            cur, size = [], 0
        cur.append(s)
        size += len(s)
    if cur:
        parts.append(cur)
    for old in (HERE / 'seed').glob('official_seed*.sql'):
        old.unlink()
    for idx, part in enumerate(parts, 1):
        p = HERE / 'seed' / f'official_seed_{idx}.sql'
        p.write_text('\n'.join(part) + '\n', encoding='utf-8')
        print(f'wrote {p.name}: {len(part)} statements')
    print(f'carriers: {len(carriers)} new, blocks total: {len(blocks)}')


if __name__ == '__main__':
    main()
