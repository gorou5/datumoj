-- Tier index for the homepage hero draw (src/lib/record.js
-- drawRandomRecord). The draw probes a year table by (tier, rowid);
-- without an index on tier the probe degrades to a forward rowid scan,
-- and the previous ORDER BY RANDOM() draw read the whole table on every
-- request. Year tables created at runtime get the same index from
-- ensureYearTable in record.js (which also backfills it, via IF NOT
-- EXISTS, on the first write to a table created before this change).
-- number_record_2025 is the only year table the migrations themselves
-- create (0007), so it is the only one named here.
CREATE INDEX IF NOT EXISTS idx_number_record_2025_tier
  ON number_record_2025 (tier);
