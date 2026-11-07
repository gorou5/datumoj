-- 0006: D1 read reduction (2026-10-25).
-- Why: the number-page block match (v_number_block_detail reverse LIKE)
-- scanned all ~30k blocks per render and burned 14.64M rows read on
-- 2026-10-24 (free-tier daily read quota is 5M). The Worker now probes
-- exact prefixes via idx_number_block_allocation_prefix; this migration
-- adds the supporting column/indexes and the hero sample table.
ALTER TABLE number_block_allocation ADD COLUMN prefix_length INTEGER;
UPDATE number_block_allocation SET prefix_length = length(prefix);
CREATE INDEX idx_number_block_allocation_type_length
  ON number_block_allocation(line_type, prefix_length, prefix);
-- Keep prefix_length maintained for future seed inserts.
CREATE TRIGGER trg_number_block_prefix_length
AFTER INSERT ON number_block_allocation
BEGIN
  UPDATE number_block_allocation SET prefix_length = length(NEW.prefix) WHERE id = NEW.id;
END;
-- Homepage hero draws: a small evenly-spaced sample of designated blocks,
-- so ORDER BY RANDOM() touches hundreds of rows instead of ~30k.
CREATE TABLE hero_candidate (
  prefix TEXT NOT NULL,
  line_type TEXT NOT NULL
);
INSERT INTO hero_candidate (prefix, line_type)
  SELECT prefix, line_type FROM number_block_allocation
   WHERE carrier_id IS NOT NULL AND status = 'designated' AND length(prefix) >= 6
     AND rowid % 97 = 0;
