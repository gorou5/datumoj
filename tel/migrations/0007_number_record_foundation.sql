-- DESIGN v2.0 (owner decision 2026-10-25): the number foundation becomes a
-- set of per-year record tables. A number that has been queried once is
-- "known": its composed fact snapshot lives in number_record_<year of first
-- query> together with its view/comment counters, and later lookups are
-- served from that row instead of re-composing from the 30k block table.
-- Only facts are stored — template content (FAQ, explanations) is rendered
-- live and never persisted. Comments stay in their own table
-- (number_comment, 0005); only their count is denormalized onto the record.

CREATE TABLE IF NOT EXISTS site_meta (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

-- Bump this whenever the official designation data or the compose rules
-- change; records with an older snapshot_version are re-composed and
-- refreshed the next time they are queried.
INSERT OR IGNORE INTO site_meta (key, value) VALUES ('snapshot_version', '1');

CREATE TABLE IF NOT EXISTS number_record_2025 (
  digits TEXT PRIMARY KEY,
  line_type TEXT,
  line_type_name TEXT,
  portable INTEGER NOT NULL DEFAULT 0,
  block_prefix TEXT,
  carrier_name TEXT,
  designated_on TEXT,
  block_status TEXT,
  block_note TEXT,
  block_source TEXT,
  area_code TEXT,
  prefecture TEXT,
  mas_json TEXT,
  government_agency TEXT,
  designated INTEGER NOT NULL DEFAULT 0,
  tier INTEGER NOT NULL,
  view_count INTEGER NOT NULL DEFAULT 0,
  comment_count INTEGER NOT NULL DEFAULT 0,
  snapshot_version INTEGER NOT NULL DEFAULT 1,
  first_queried_at TEXT NOT NULL DEFAULT (datetime('now')),
  last_queried_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_number_record_2025_views
  ON number_record_2025 (view_count DESC);
