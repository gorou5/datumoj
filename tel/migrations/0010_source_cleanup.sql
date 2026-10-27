-- Source registry cleanup (2026-10-27).
--
-- 1. Remove the UGC review-platform source row (id=6). Verified before
--    writing this migration: no third_party_record or third_party_flag
--    row references it. The project no longer lists that platform
--    anywhere (registry, docs, or logs).
-- 2. Split the National Consumer Affairs Center (国民生活センター)
--    "事業者の名称等を公表" publication type out of the combined
--    Consumer Affairs Agency row (id=5) into its own source row. The new
--    row uses the explicit id 7 so the id is stable and this file stays
--    replay-safe (INSERT OR IGNORE + idempotent UPDATEs). The archived
--    record and the flag that came from that publication type are
--    re-pointed to the new row.
-- 3. Narrow row id=5 back to the Consumer Affairs Agency alone
--    (publisher / landing URL / has_numbers), matching the split.

DELETE FROM third_party_source WHERE id = 6;

INSERT OR IGNORE INTO third_party_source
  (id, name, publisher, url, tier, form, has_numbers, access, note, updated_at)
VALUES
  (7, '事業者の名称等を公表', '国民生活センター', 'https://www.kokusen.go.jp/',
   'C', '公表ページ（事業者の情報として名称・所在地・電話番号等を掲載）',
   1, 'public',
   '事業者名と電話番号をセットで公表する公的定型。消費者庁の注意喚起と合算されていた来源登記から2026-10-27に分離した。番号のある公表から third_party_flag を生成する。',
   '2026-10-27');

UPDATE third_party_record
   SET source_id = 7
 WHERE announcing_agency = '国民生活センター';

UPDATE third_party_flag
   SET source_id = 7
 WHERE id = 1;

UPDATE third_party_source
   SET publisher = '消費者庁',
       url = 'https://www.caa.go.jp/',
       has_numbers = 0
 WHERE id = 5;
