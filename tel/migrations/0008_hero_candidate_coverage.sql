-- Replace the global rowid sample with up to 48 designated blocks per type.
-- Sparse types keep all their eligible blocks; abundant types stay bounded.
-- Sampling the full allocation table happens only during this migration.
DELETE FROM hero_candidate;
INSERT INTO hero_candidate (prefix, line_type)
  SELECT prefix, line_type FROM (
    SELECT prefix, line_type,
           ROW_NUMBER() OVER (PARTITION BY line_type ORDER BY RANDOM()) AS sample_rank
      FROM number_block_allocation
     WHERE carrier_id IS NOT NULL AND status = 'designated' AND length(prefix) >= 6
       AND line_type IN ('fixed', 'mobile', 'ip050', 'free0120', 'free0800', 'navi0570', 'm2m020')
  ) WHERE sample_rank <= 48;
CREATE INDEX idx_hero_candidate_line_type ON hero_candidate(line_type);
