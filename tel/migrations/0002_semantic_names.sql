-- Semantic table/view names (user requirement 2026-10-22): every table name in
-- tel_datumoj must be meaningful on its own — no abbreviations (ma, alloc, tp,
-- gov) and no bare generic nouns (report, business). Data-preserving renames;
-- views are dropped and recreated because SQLite cannot rename views.

DROP VIEW IF EXISTS v_ma_detail;
DROP VIEW IF EXISTS v_area_summary;
DROP VIEW IF EXISTS v_block_detail;
DROP VIEW IF EXISTS v_flag_detail;
DROP VIEW IF EXISTS v_gov_number_detail;

ALTER TABLE ma RENAME TO charge_area;                    -- 単位料金区域
ALTER TABLE alloc_block RENAME TO number_block_allocation;
ALTER TABLE business RENAME TO business_registration;
ALTER TABLE report RENAME TO user_report;
ALTER TABLE tp_source RENAME TO third_party_source;
ALTER TABLE tp_flag RENAME TO third_party_flag;
ALTER TABLE gov_agency RENAME TO government_agency;
ALTER TABLE gov_number RENAME TO government_number;

DROP INDEX IF EXISTS idx_ma_area;
DROP INDEX IF EXISTS idx_alloc_prefix;
DROP INDEX IF EXISTS idx_report_number;
DROP INDEX IF EXISTS idx_tp_flag_number;
DROP INDEX IF EXISTS idx_gov_number_agency;
CREATE INDEX idx_charge_area_area_code ON charge_area(area_code);
CREATE INDEX idx_number_block_allocation_prefix ON number_block_allocation(prefix);
CREATE INDEX idx_user_report_number ON user_report(number);
CREATE INDEX idx_third_party_flag_number ON third_party_flag(number);
CREATE INDEX idx_government_number_agency ON government_number(agency_id);

CREATE VIEW v_charge_area_detail AS
  SELECT m.id AS charge_area_id, m.name AS charge_area_name, m.zone_code, m.region_text AS region,
         m.area_code, p.name AS prefecture
    FROM charge_area m
    JOIN prefecture p ON p.code = m.prefecture_code;

CREATE VIEW v_area_summary AS
  SELECT a.area_code, p.name AS prefecture,
         (SELECT COUNT(*) FROM charge_area m WHERE m.area_code = a.area_code) AS charge_area_count
    FROM area_code a
    JOIN prefecture p ON p.code = a.primary_prefecture_code;

CREATE VIEW v_number_block_detail AS
  SELECT b.id AS block_id, b.line_type, b.prefix, b.designated_on, b.status,
         b.note, b.source, lt.name_ja AS line_type_name, lt.digits AS line_digits,
         lt.portable AS portable, c.name AS carrier_name
    FROM number_block_allocation b
    JOIN line_type lt ON lt.code = b.line_type
    LEFT JOIN carrier c ON c.id = b.carrier_id;

CREATE VIEW v_third_party_flag_detail AS
  SELECT f.*, s.name AS source_name, s.publisher, s.tier AS source_tier, s.url AS source_url
    FROM third_party_flag f
    JOIN third_party_source s ON s.id = f.source_id;

CREATE VIEW v_government_number_detail AS
  SELECT n.number, n.number_kind, n.label, n.source_url, n.verified_on,
         a.name AS agency_name, a.name_short AS agency_short, a.kind AS agency_kind
    FROM government_number n
    JOIN government_agency a ON a.id = n.agency_id;
