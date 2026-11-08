// Block-page existence manifest — the number page's dead-link guard.
//
// Number pages link back to their 区段页 (/tel/<first 7 digits>) only when
// that block page really exists in the bucket. Block coverage is partial
// by design (mobile / 050 / free-dial / navi + metro fixed areas; rural
// fixed blocks are never generated), so an unconditional link would 404 —
// the one outcome the link-integrity pass exists to prevent.
//
// Ground truth: blocks-index/manifest.json in the nbfun bucket, written
// by scripts/gen_blocks_index.py from the same bucket census that builds
// the 区段索引. Format: { v, generated_at, count, keys: ["0909629", ...] }.
//
// Cost shape: the manifest is fetched at most once per isolate per TTL
// and kept as an in-memory Set; snapshot-hit number pages never touch it
// (the decision is baked into the rendered HTML at compose time, and the
// snapshot_version bump in migrations/0011 forces one re-render after
// deploy so the baked decision is fresh). Any failure — missing object,
// bad JSON, R2 unreachable — degrades to "no block link", never to a
// guessed link and never to an error.

import { getR2Object } from './r2.js';

const MANIFEST_KEY = 'blocks-index/manifest.json';
const TTL_MS = 30 * 60 * 1000;

let cache = { at: 0, keys: null };

// True only when the manifest positively lists this 7-digit block key.
export async function blockPageExists(env, blockKey) {
  if (!/^\d{7}$/.test(blockKey || '')) return false;
  const now = Date.now();
  if (!cache.keys || now - cache.at >= TTL_MS) {
    let loaded = null;
    const obj = await getR2Object(env, MANIFEST_KEY);
    if (obj) {
      try {
        const data = JSON.parse(obj.body);
        if (data && Array.isArray(data.keys)) loaded = new Set(data.keys);
      } catch {
        loaded = null;
      }
    }
    if (loaded) {
      cache = { at: now, keys: loaded };
    } else if (!cache.keys) {
      // Nothing cached yet: stamp the attempt so a broken manifest does
      // not cost one R2 fetch per page render; links stay off meanwhile.
      cache = { at: now, keys: null };
    } else {
      // Refresh failed: keep serving the stale set, retry next TTL.
      cache.at = now;
    }
  }
  return cache.keys ? cache.keys.has(blockKey) : false;
}
