-- Signal block index for the staged block-sitemap rollout (2026-11-07).
--
-- signal_block holds the 7-digit keys of blocks that contain at least one
-- signal number, where "signal" is the block generators' definition:
-- every landed number in the current-year record table, every number in
-- the seed list_v1, every list_v2 number with search_count >= 10, and
-- every number appearing in number_comment / user_report /
-- third_party_flag — intersected with the generated block plan (main
-- scope plus the fixed-line metro scope).
--
-- The table is a materialized copy, on purpose: the seed lists live as
-- files in the generation workspace, not in D1, so the Worker cannot
-- recompute the set at request time. scripts/sync_signal_blocks.py
-- refreshes this table by re-running the generators' own signal
-- computation (imported, not re-implemented), which keeps the sitemap's
-- stage-1 shard identical by construction to the set of blocks whose
-- pages render signal numbers as real links. The Worker only reads it:
-- SELECT block_key FROM signal_block is the whole contract.
--
-- signal_count is the number of signal numbers inside the block at sync
-- time (kept for rollout reporting; the sitemap does not use it).

CREATE TABLE signal_block (
  block_key    TEXT PRIMARY KEY,
  signal_count INTEGER NOT NULL DEFAULT 0,
  updated_at   TEXT NOT NULL DEFAULT ''
);
