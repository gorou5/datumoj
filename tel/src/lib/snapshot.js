// SSG snapshots (保命/效率层): the rendered HTML of a landed Tier 1 number
// page, stored in R2 (bucket `datumoj`, binding SNAPSHOTS). Serving order
// for GET /<digits> stays record-first; the snapshot only replaces the
// *render* step for a record that is already known fresh, never the
// routing, tiering or 404 logic. Any R2 failure degrades silently to the
// plain SSR path — the snapshot layer must never become a new SPOF.
//
// Validity contract (checked on every candidate hit, all cheap reads):
//   1. the D1 record row exists and row.snapshot_version >= the current
//      site snapshot_version (the pre-existing v2.0 freshness rule), and
//   2. the R2 object's metadata `sv` equals the record row's
//      snapshot_version (snapshot was rendered from this exact record
//      state), and
//   3. the R2 object's metadata `fp` equals the number's current
//      third-party-flag fingerprint (see flagFingerprint below).
// Overlay facts that are neither in the record row nor in the flags table
// (government detail, business registration, approved user reports) follow
// the site's existing convention: whoever reloads that data bumps
// site_meta.snapshot_version, which invalidates every snapshot through
// rule 1 (stale records are re-composed and re-rendered on next query).
// Dynamic counters are NOT frozen by the snapshot: the view counter span
// is refreshed by the page's own /view beacon script on every real view
// (and the beacon keeps incrementing the record row), and the comment
// list is loaded by script from /comments.json — same as the SSR page.

const PREFIX = 'tel/snapshots/';

export function snapshotKey(digits) {
  return `${PREFIX}${digits}.html`;
}

// Fingerprint of a number's active third-party flags. Flags are the one
// overlay expected to change per-number outside a data reload (ETL adds /
// retires individual flags), so they get a dedicated invalidation signal
// instead of relying on the global snapshot_version bump. One indexed
// lookup (idx_third_party_flag_number); a number normally has 0 flags.
export async function flagFingerprint(db, digits) {
  try {
    const r = await db
      .prepare(
        `SELECT id, source_id, flag_type, label, evidence_url, published_on,
                last_verified, actor_name, service_name, violation_summary
           FROM third_party_flag
          WHERE number = ? AND status = 'active'
          ORDER BY id`
      )
      .bind(digits)
      .all();
    const rows = r.results || [];
    if (!rows.length) return 'none';
    // FNV-1a over the rendering-relevant fields.
    const s = JSON.stringify(rows);
    let h = 0x811c9dc5;
    for (let i = 0; i < s.length; i++) {
      h ^= s.charCodeAt(i);
      h = Math.imul(h, 0x01000193);
    }
    return (h >>> 0).toString(16);
  } catch {
    return null; // unknown → caller treats snapshots as invalid, uses SSR
  }
}

// Returns { body, meta } or null (miss / R2 unavailable / bad object).
export async function getSnapshot(env, digits) {
  if (!env || !env.SNAPSHOTS) return null;
  try {
    const obj = await env.SNAPSHOTS.get(snapshotKey(digits));
    if (!obj) return null;
    const meta = obj.customMetadata || {};
    if (!meta.sv || !meta.fp) return null;
    return { body: await obj.text(), meta };
  } catch {
    return null;
  }
}

// Fire-and-forget friendly: never throws.
export async function putSnapshot(env, digits, bodyHtml, { sv, fp }) {
  if (!env || !env.SNAPSHOTS) return;
  try {
    await env.SNAPSHOTS.put(snapshotKey(digits), bodyHtml, {
      httpMetadata: { contentType: 'text/html; charset=utf-8' },
      customMetadata: {
        sv: String(sv),
        fp: String(fp),
        gen: new Date().toISOString(),
      },
    });
  } catch {
    /* snapshot write failure must never break the page */
  }
}
