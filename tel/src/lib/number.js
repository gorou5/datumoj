// Number normalization, decomposition and display formatting.
// Pure functions — no I/O. Decomposition rules come from the numbering plan,
// not from per-number data (see DESIGN.md §2).

// Normalize user input to domestic digit string, or null if not dialable-looking.
// Accepts: hyphens/spaces (half/full width), +81 / 0081 prefixes, parentheses.
export function normalize(raw) {
  if (!raw) return null;
  let s = String(raw)
    .replace(/[０-９]/g, (c) => String.fromCharCode(c.charCodeAt(0) - 0xfee0))
    .replace(/[\s\-ー−–—()（）]/g, '');
  if (s.startsWith('+81')) s = '0' + s.slice(3);
  else if (s.startsWith('0081')) s = '0' + s.slice(4);
  else if (s.startsWith('+')) return null; // non-JP international number: out of scope
  if (!/^0\d{9,13}$/.test(s)) return null;
  return s;
}

// Split a number into display segments given its composed facts.
// facts: { lineType: code, areaCode?: string }
export function splitNumber(digits, facts) {
  const t = facts.lineType;
  if (t === 'mobile' || t === 'ip050') {
    return [digits.slice(0, 3), digits.slice(3, 7), digits.slice(7)];
  }
  if (t === 'free0120' || t === 'navi0570') {
    return [digits.slice(0, 4), digits.slice(4, 7), digits.slice(7)];
  }
  if (t === 'free0800') {
    return [digits.slice(0, 4), digits.slice(4, 7), digits.slice(7)];
  }
  if (t === 'fixed' && facts.areaCode) {
    const a = facts.areaCode.length;
    const exchLen = digits.length - a - 4;
    return [digits.slice(0, a), digits.slice(a, a + exchLen), digits.slice(a + exchLen)];
  }
  return [digits];
}

export function formatNumber(digits, facts) {
  return splitNumber(digits, facts).join('-');
}

const SEG_LABELS = {
  fixed: ['市外局番', '市内局番', '加入者番号'],
  mobile: ['番号帯', '局番', '加入者番号'],
  ip050: ['番号帯', '局番', '加入者番号'],
  free0120: ['番号帯', '局番', '加入者番号'],
  free0800: ['番号帯', '局番', '加入者番号'],
  navi0570: ['番号帯', '局番', '加入者番号'],
  m2m020: ['番号'],
};
export function segmentLabels(lineType) {
  return SEG_LABELS[lineType] || ['番号'];
}

// Neighboring numbers inside the same block (for onward browsing links).
export function neighbors(digits, n = 1) {
  const v = BigInt(digits);
  const out = { prev: [], next: [] };
  for (let i = n; i >= 1; i--) {
    const p = (v - BigInt(i)).toString().padStart(digits.length, '0');
    if (p.length === digits.length && p.startsWith('0')) out.prev.push(p);
    const x = (v + BigInt(i)).toString().padStart(digits.length, '0');
    if (x.length === digits.length) out.next.push(x);
  }
  return out;
}
