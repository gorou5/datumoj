-- Fix government_number key design: a single switchboard number can belong
-- to several agencies (e.g. 03-5253-5111 is published by both 総務省 and its
-- external agency 消防庁), so `number` alone cannot be the primary key.
-- Surrogate key + UNIQUE(number, agency_id).

DROP VIEW IF EXISTS v_government_number_detail;

CREATE TABLE government_number_new (
  id          INTEGER PRIMARY KEY,
  number      TEXT NOT NULL,
  agency_id   INTEGER NOT NULL REFERENCES government_agency(id),
  number_kind TEXT NOT NULL DEFAULT '代表',   -- 代表 | 相談窓口 | その他
  label       TEXT,
  source_url  TEXT NOT NULL,
  verified_on TEXT,
  UNIQUE (number, agency_id)
);
INSERT INTO government_number_new (number, agency_id, number_kind, label, source_url, verified_on)
  SELECT number, agency_id, number_kind, label, source_url, verified_on FROM government_number;
DROP TABLE government_number;
ALTER TABLE government_number_new RENAME TO government_number;
CREATE INDEX idx_government_number_agency ON government_number(agency_id);

CREATE VIEW v_government_number_detail AS
  SELECT n.number, n.number_kind, n.label, n.source_url, n.verified_on,
         a.name AS agency_name, a.name_short AS agency_short, a.kind AS agency_kind
    FROM government_number n
    JOIN government_agency a ON a.id = n.agency_id;
