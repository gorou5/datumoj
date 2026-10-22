// Composition pipeline (DESIGN.md §3): a number page is assembled at request
// time from the base tables, mostly through the v_* views defined in
// migrations/0002. No per-number rows exist in the main data layer.
// Returns null when the number is unallocated / not composable (-> real 404).

export async function composeNumber(db, digits) {
  // 1) Longest-prefix block match (view: block + line type + carrier).
  const block = await db
    .prepare(
      `SELECT * FROM v_number_block_detail
        WHERE ? LIKE prefix || '%'
        ORDER BY length(prefix) DESC
        LIMIT 1`
    )
    .bind(digits)
    .first();
  if (!block) return null;
  if (block.status === 'unallocated') return null;
  if (digits.length !== block.line_digits) return null;

  const facts = {
    digits,
    lineType: block.line_type,
    lineTypeName: block.line_type_name,
    portable: !!block.portable,
    block: {
      prefix: block.prefix,
      carrierName: block.carrier_name,
      designatedOn: block.designated_on,
      status: block.status,
      note: block.note,
      source: block.source,
    },
  };

  // 2) Geography for fixed lines. The area code comes from the area_code
  // table (longest match), NOT from the matched block: official designation
  // blocks are 6-digit (area+exchange) rows, so block.prefix is not the area
  // code. When the block knows its charge area (MA), the number belongs to
  // exactly that one MA; otherwise fall back to all MAs of the area code.
  if (block.line_type === 'fixed') {
    const acRow = await db
      .prepare(
        `SELECT area_code FROM area_code
          WHERE ? LIKE area_code || '%' ORDER BY length(area_code) DESC LIMIT 1`
      )
      .bind(digits)
      .first();
    facts.areaCode = acRow ? acRow.area_code : null;
    if (facts.areaCode) {
      const sum = await db
        .prepare('SELECT prefecture FROM v_area_summary WHERE area_code = ?')
        .bind(facts.areaCode)
        .first();
      facts.prefecture = sum ? sum.prefecture : null;
    }
    if (block.charge_area_id) {
      const ma = await db
        .prepare('SELECT * FROM v_charge_area_detail WHERE charge_area_id = ?')
        .bind(block.charge_area_id)
        .first();
      facts.mas = ma ? [ma] : [];
    } else if (facts.areaCode) {
      const mas = await db
        .prepare('SELECT * FROM v_charge_area_detail WHERE area_code = ? ORDER BY zone_code')
        .bind(facts.areaCode)
        .all();
      facts.mas = mas.results || [];
    } else {
      facts.mas = [];
    }
  }

  // 3) Government-numbers database: is this number a government agency's?
  // Several agencies can share one switchboard number (e.g. 総務省/消防庁),
  // so keep every matching row, not just the first.
  const govAll = await db
    .prepare('SELECT * FROM v_government_number_detail WHERE number = ? ORDER BY agency_name')
    .bind(digits)
    .all();
  facts.govList = govAll.results || [];
  facts.gov = facts.govList[0] || null;

  // 4) Overlay: business registration.
  const biz = await db
    .prepare('SELECT * FROM business_registration WHERE number = ?')
    .bind(digits)
    .first();
  facts.business = biz || null;

  // 5) Overlay: approved user reports (summary only).
  const rep = await db
    .prepare(
      `SELECT category, COUNT(*) AS n, MAX(created_at) AS latest
         FROM user_report WHERE number = ? AND status = 'approved'
        GROUP BY category ORDER BY n DESC`
    )
    .bind(digits)
    .all();
  facts.reports = rep.results || [];
  facts.reportCount = facts.reports.reduce((a, r) => a + r.n, 0);

  // 6) Third-party flags (mirrored, with their sources — view).
  const flags = await db
    .prepare(
      `SELECT * FROM v_third_party_flag_detail
        WHERE number = ? AND status = 'active'
        ORDER BY source_tier, published_on DESC`
    )
    .bind(digits)
    .all();
  facts.tpFlags = flags.results || [];

  // 7) Indexing tier (DESIGN.md §6). Government numbers are Tier 1 by
  // definition: they carry a verified, sourced fact.
  const hasABFlag = facts.tpFlags.some((f) => f.source_tier === 'A' || f.source_tier === 'B');
  facts.tier = facts.gov || facts.business || facts.reportCount > 0 || hasABFlag ? 1 : 2;

  return facts;
}

export async function listPrefectures(db) {
  const r = await db
    .prepare(
      `SELECT p.name AS prefecture, COUNT(*) AS areas
         FROM v_area_summary s JOIN prefecture p ON p.name = s.prefecture
        GROUP BY p.name ORDER BY MIN(s.area_code)`
    )
    .all();
  return r.results || [];
}

export async function listAreaCodes(db, prefecture) {
  const r = await db
    .prepare('SELECT area_code FROM v_area_summary WHERE prefecture = ? ORDER BY area_code')
    .bind(prefecture)
    .all();
  return (r.results || []).map((x) => x.area_code);
}

export async function getAreaHub(db, areaCode) {
  const ac = await db
    .prepare('SELECT * FROM v_area_summary WHERE area_code = ?')
    .bind(areaCode)
    .first();
  if (!ac) return null;
  const mas = await db
    .prepare('SELECT * FROM v_charge_area_detail WHERE area_code = ? ORDER BY zone_code')
    .bind(areaCode)
    .all();
  return { areaCode, prefecture: ac.prefecture, mas: mas.results || [] };
}

export async function listLineTypes(db) {
  const r = await db.prepare('SELECT * FROM line_type ORDER BY digits, code').all();
  return r.results || [];
}

export async function getLineTypeHub(db, code) {
  const lt = await db.prepare('SELECT * FROM line_type WHERE code = ?').bind(code).first();
  if (!lt) return null;
  // Hub pages list band-level blocks only; the tens of thousands of official
  // 6-digit designation blocks are counted, not listed.
  const blocks = await db
    .prepare('SELECT * FROM number_block_allocation WHERE line_type = ? AND length(prefix) < 6 ORDER BY prefix')
    .bind(code)
    .all();
  const dc = await db
    .prepare('SELECT COUNT(*) AS n FROM number_block_allocation WHERE line_type = ? AND length(prefix) >= 6')
    .bind(code)
    .first();
  return { lineType: lt, blocks: blocks.results || [], detailCount: dc ? dc.n : 0 };
}

export async function listTpSources(db) {
  const r = await db.prepare('SELECT * FROM third_party_source ORDER BY tier, id').all();
  return r.results || [];
}

export async function allAreaCodes(db) {
  const r = await db.prepare('SELECT area_code, prefecture FROM v_area_summary ORDER BY area_code').all();
  return r.results || [];
}

export async function listGovAgencies(db) {
  const r = await db
    .prepare(
      `SELECT a.id, a.name, a.name_short, a.kind, COUNT(n.id) AS numbers
         FROM government_agency a LEFT JOIN government_number n ON n.agency_id = a.id
        GROUP BY a.id ORDER BY a.id`
    )
    .all();
  return r.results || [];
}

export async function listGovNumbers(db) {
  const r = await db.prepare('SELECT number FROM government_number ORDER BY number').all();
  return (r.results || []).map((x) => x.number);
}

export async function govNumbersByAgency(db) {
  const r = await db.prepare('SELECT * FROM v_government_number_detail ORDER BY agency_name, number').all();
  return r.results || [];
}

// Fallback for numbers that do not compose. The catch rule for 11-digit
// 0-leading inputs: the band is identified by the FIRST THREE digits, and
// every such input lands on an explanatory page (served as a real 404 with
// full content — never a bare error, never a fake 200).
const BAND_BY_P3 = {
  '050': 'IP電話（050）',
  '060': '携帯電話（060）',
  '070': '携帯電話（070）',
  '080': '携帯電話（080）',
  '090': '携帯電話（090）',
  '020': 'M2M等専用番号（020）',
};

export async function analyzeFallback(db, digits) {
  const p3 = digits.slice(0, 3);
  const base = { digits, p3, bandName: BAND_BY_P3[p3] || null };
  if (p3 === '020') {
    return { ...base, reason: 'length', expected: '12〜14桁',
      message: '020番号はM2M等専用で、電話番号は12〜14桁です。11桁の020番号は割り当てられていません。' };
  }
  if (digits.startsWith('0120')) {
    return { ...base, bandName: 'フリーダイヤル（0120）', reason: 'length', expected: '10桁',
      suggestion: digits.slice(0, 10),
      message: '0120のフリーダイヤルは10桁です。11桁の番号は割り当てられていません。末尾の1桁が多い可能性があります。' };
  }
  if (digits.startsWith('0570')) {
    return { ...base, bandName: 'ナビダイヤル（0570）', reason: 'length', expected: '10桁',
      suggestion: digits.slice(0, 10),
      message: '0570のナビダイヤルは10桁です。11桁の番号は割り当てられていません。末尾の1桁が多い可能性があります。' };
  }
  // Fixed-line family: longest matching area code (2-5 digits).
  const ac = await db
    .prepare(
      `SELECT area_code FROM area_code
        WHERE ? LIKE area_code || '%'
        ORDER BY length(area_code) DESC LIMIT 1`
    )
    .bind(digits)
    .first();
  if (ac) {
    return { ...base, bandName: '固定電話（0AB-J）', reason: 'length', expected: '10桁',
      areaCode: ac.area_code, suggestion: digits.length > 10 ? digits.slice(0, 10) : null,
      message: `市外局番 ${ac.area_code} の固定電話は10桁です。11桁の番号は割り当てられていません。桁数が1つ多い可能性があります。` };
  }
  return { ...base, reason: 'unallocated', expected: null, suggestion: null,
    message: `先頭3桁「${p3}」の番号帯に、11桁の電話番号の割り当ては確認できませんでした。この番号帯は未指定か、特殊な用途のものです。` };
}
