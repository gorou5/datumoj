-- Third-party publication archive (ETL phase 2, 2026-10-27).
--
-- third_party_record stores one row per public announcement/record from a
-- third-party source (FSA warning lists, Consumer Affairs Agency sanction /
-- caution PDFs, National Consumer Affairs Center name publications, Tokyo
-- Metropolitan Police crime-prevention mail open data), whether or not the
-- publication carries a phone number. third_party_flag stays the number-keyed
-- layer that number pages render; it gains the actor/context columns needed
-- to explain a flag, plus a link back to the archived record.
--
-- Column set covers every field observed across the six registered sources
-- (see the ETL phase-1 inventory): actor attributes (商号/法人番号/所在地/
-- 代表者/設立/資本金/サービス名/ウェブサイト/取引類型/取扱商品/国内・海外),
-- publication & sanction attributes (公表日/違反行為/根拠条文/処分の内容・
-- 期間/実施機関/公表理由/備考), and number/method attributes (電話番号の原文
-- 表記と正規化値/手口叙述/かたり名称/地域/警視庁配信用タイトル・本文).
--
-- Indexes are partial on purpose: the Keishicho archive alone is ~55k rows
-- with no actor/phone data, and full indexes would multiply D1 row writes
-- on the free plan for columns that stay NULL on most rows.

CREATE TABLE third_party_record (
  id                  INTEGER PRIMARY KEY,
  source_id           INTEGER NOT NULL REFERENCES third_party_source(id),
  source_format       TEXT,                    -- excel / pdf / html / csv / text
  source_document_url TEXT,                    -- landing page of the publication
  source_file_name    TEXT,                    -- raw file(s) the row was parsed from
  published_on        TEXT,                    -- ISO date (FSA lists: YYYY-MM)
  captured_at         TEXT,                    -- when the raw file was downloaded
  announcing_agency   TEXT,                    -- 公表・処分実施機関
  actor_name          TEXT,                    -- 事業者名称（商号・氏名）
  corporate_number    TEXT,                    -- 法人番号 (13 digits)
  actor_address       TEXT,                    -- 所在地・住所
  representative_name TEXT,                    -- 代表者氏名
  established_on      TEXT,                    -- 設立年月日（原文）
  capital             TEXT,                    -- 資本金（原文）
  service_name        TEXT,                    -- サービス名・屋号
  website_url         TEXT,
  business_type       TEXT,                    -- 取引類型・業務内容
  products            TEXT,                    -- 取扱商品・役務
  domestic_overseas   TEXT,                    -- domestic / overseas (FSA lists)
  violation_summary   TEXT,                    -- 違反行為・手口概要
  legal_basis         TEXT,                    -- 根拠条文
  sanction_content    TEXT,                    -- 処分の内容
  sanction_period     TEXT,                    -- 処分期間（原文）
  publication_reason  TEXT,                    -- 公表理由（国生名称公表）
  remark              TEXT,                    -- 備考（原文）
  method_narrative    TEXT,                    -- 手口種別叙述
  impersonated_name   TEXT,                    -- かたり名称
  region_text         TEXT,                    -- 配信先・発生地域（原文）
  distribution_title  TEXT,                    -- 警視庁メール配信表題
  distribution_body   TEXT,                    -- 警視庁メール配信本文
  phone_number_raw    TEXT,                    -- 電話番号（原文表記）
  phone_digits        TEXT,                    -- 電話番号（正規化・数字のみ）
  has_phone           INTEGER NOT NULL DEFAULT 0,
  status              TEXT NOT NULL DEFAULT 'active',
  created_at          TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX idx_third_party_record_phone_digits
  ON third_party_record(phone_digits) WHERE has_phone = 1;
CREATE INDEX idx_third_party_record_corporate_number
  ON third_party_record(corporate_number) WHERE corporate_number IS NOT NULL;
CREATE INDEX idx_third_party_record_actor_name
  ON third_party_record(actor_name) WHERE actor_name IS NOT NULL;

ALTER TABLE third_party_flag ADD COLUMN actor_name TEXT;
ALTER TABLE third_party_flag ADD COLUMN corporate_number TEXT;
ALTER TABLE third_party_flag ADD COLUMN service_name TEXT;
ALTER TABLE third_party_flag ADD COLUMN website_url TEXT;
ALTER TABLE third_party_flag ADD COLUMN violation_summary TEXT;
ALTER TABLE third_party_flag ADD COLUMN announcing_agency TEXT;
ALTER TABLE third_party_flag ADD COLUMN source_document_url TEXT;
ALTER TABLE third_party_flag ADD COLUMN source_record_id INTEGER REFERENCES third_party_record(id);
ALTER TABLE third_party_flag ADD COLUMN method_summary TEXT;
CREATE INDEX idx_third_party_flag_source_record
  ON third_party_flag(source_record_id) WHERE source_record_id IS NOT NULL;

-- SQLite fixes a view's column list at CREATE time, so recreate the detail
-- view after the ALTERs above for the new flag columns to flow through.
DROP VIEW v_third_party_flag_detail;
CREATE VIEW v_third_party_flag_detail AS
  SELECT f.*, s.name AS source_name, s.publisher, s.tier AS source_tier, s.url AS source_url
    FROM third_party_flag f
    JOIN third_party_source s ON s.id = f.source_id;
