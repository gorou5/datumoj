// View counts and anonymous comments for number pages.
// Owner rules for comments: at most 10 characters, anonymous, and no hostile
// language toward people (insults / threats are rejected). Comments are
// reader opinions, never facts — the page labels them as such.

const BAD_WORDS = [
  // Japanese insults / threats
  '死ね', '殺す', 'ころす', '馬鹿', 'バカ', '阿呆', 'アホ', 'カス', 'クズ', 'ゴミ',
  'うざい', 'キモい', 'きもい', 'デブ', 'ハゲ', 'チョン', '氏ね', 'タヒね',
  // Chinese insults
  '傻逼', '傻b', '去死', '操你', '王八蛋', '贱人', '滚蛋',
  // English insults
  'fuck', 'shit', 'bitch', 'idiot', 'asshole', 'stupid', 'kill you',
];

// Returns a problem code, or null when the comment is acceptable.
export function commentProblem(body) {
  if (typeof body !== 'string') return 'empty';
  const chars = [...body.trim()];
  if (chars.length === 0) return 'empty';
  if (chars.length > 10) return 'too_long';
  if (/https?:|www\./i.test(body)) return 'link';
  if (/\d{4,}/.test(body)) return 'digits'; // other numbers / personal info
  const norm = body.normalize('NFKC').toLowerCase();
  if (BAD_WORDS.some((w) => norm.includes(w))) return 'bad_language';
  return null;
}

export const COMMENT_MESSAGES = {
  empty: 'コメントを入力してください。',
  too_long: 'コメントは10文字以内です。',
  link: 'リンクは投稿できません。',
  digits: '数字の羅列は投稿できません。',
  bad_language: '人を傷つける言葉は投稿できません。',
  rate: '投稿が多すぎます。しばらくしてからお試しください。',
};

export async function bumpView(db, number) {
  await db
    .prepare(
      `INSERT INTO number_view (number, views, updated_at) VALUES (?, 1, datetime('now'))
       ON CONFLICT(number) DO UPDATE SET views = views + 1, updated_at = datetime('now')`
    )
    .bind(number)
    .run();
  const row = await db.prepare('SELECT views FROM number_view WHERE number = ?').bind(number).first();
  return row ? row.views : 1;
}

export async function listComments(db, number, limit = 20) {
  const r = await db
    .prepare(
      "SELECT body, created_at FROM number_comment WHERE number = ? AND status = 'visible' ORDER BY id DESC LIMIT ?"
    )
    .bind(number, limit)
    .all();
  return r.results || [];
}

export async function addComment(db, number, body, ipHash) {
  const recent = await db
    .prepare(
      "SELECT COUNT(*) AS n FROM number_comment WHERE ip_hash = ? AND created_at > datetime('now', '-1 hour')"
    )
    .bind(ipHash)
    .first();
  if (recent && recent.n >= 5) return 'rate';
  await db
    .prepare('INSERT INTO number_comment (number, body, ip_hash) VALUES (?, ?, ?)')
    .bind(number, body.trim(), ipHash)
    .run();
  return null;
}

export async function hashIp(ip) {
  const data = new TextEncoder().encode('tel-salt:' + (ip || 'unknown'));
  const digest = await crypto.subtle.digest('SHA-256', data);
  return [...new Uint8Array(digest)].map((b) => b.toString(16).padStart(2, '0')).join('').slice(0, 32);
}
