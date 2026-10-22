-- tel_datumoj database — schema for tel.datumoj.com (D1).
-- One dedicated database for this project; table names carry no prefix.
-- DBA-standard normalization; views provide the standard compositions.
-- Design: DESIGN.md (tel project). Seed: seed/seed.sql + seed/gov_seed.sql.

-- Reference: 47 prefectures (JIS X 0401 codes).
CREATE TABLE prefecture (
  code  TEXT PRIMARY KEY,              -- '01'..'47'
  name  TEXT NOT NULL UNIQUE           -- 北海道 .. 沖縄県
);

CREATE TABLE line_type (
  code      TEXT PRIMARY KEY,          -- fixed / mobile / ip050 / free0120 / free0800 / navi0570 / m2m020
  name_ja   TEXT NOT NULL,
  digits    INTEGER NOT NULL,
  portable  INTEGER NOT NULL DEFAULT 0,
  note      TEXT,
  source    TEXT
);

CREATE TABLE carrier (
  id    INTEGER PRIMARY KEY,
  name  TEXT NOT NULL,
  kind  TEXT,
  url   TEXT
);

-- An area code's primary prefecture is a presentation/grouping attribute
-- (a few area codes span prefecture borders; each MA carries its own exact
-- prefecture in ma, which is the authoritative one).
CREATE TABLE area_code (
  area_code              TEXT PRIMARY KEY,
  primary_prefecture_code TEXT NOT NULL REFERENCES prefecture(code)
);

-- One MA belongs to exactly one area code (verified on the seed data:
-- 561 MA rows, 561 distinct (name, zone_code)), so the v1 ma_area link table
-- is folded into ma — no many-to-many exists in reality.
CREATE TABLE ma (
  id              INTEGER PRIMARY KEY,
  name            TEXT NOT NULL,                       -- e.g. 札幌
  zone_code       TEXT NOT NULL UNIQUE,                -- 番号区画コード e.g. 001, 496-2
  area_code       TEXT NOT NULL REFERENCES area_code(area_code),
  prefecture_code TEXT NOT NULL REFERENCES prefecture(code),
  region_text     TEXT                                 -- municipalities covered
);
CREATE INDEX idx_ma_area ON ma(area_code);

-- Core allocation table: a number belongs to the block with the LONGEST
-- matching prefix. (line_type, prefix) is the natural key.
CREATE TABLE alloc_block (
  id            INTEGER PRIMARY KEY,
  line_type     TEXT NOT NULL REFERENCES line_type(code),
  prefix        TEXT NOT NULL,
  carrier_id    INTEGER REFERENCES carrier(id),
  designated_on TEXT,
  status        TEXT NOT NULL DEFAULT 'designated',
  note          TEXT,
  source        TEXT,
  UNIQUE (line_type, prefix)
);
CREATE INDEX idx_alloc_prefix ON alloc_block(prefix);

-- Overlay: business registered to a number (operator-curated).
CREATE TABLE business (
  number     TEXT PRIMARY KEY,
  name       TEXT NOT NULL,
  address    TEXT,
  category   TEXT,
  source     TEXT,
  verified   INTEGER NOT NULL DEFAULT 0,
  updated_at TEXT
);

-- Overlay: user reports (only approved ones are aggregated onto pages).
CREATE TABLE report (
  id         INTEGER PRIMARY KEY,
  number     TEXT NOT NULL,
  category   TEXT NOT NULL,
  body       TEXT,
  status     TEXT NOT NULL DEFAULT 'pending',
  created_at TEXT
);
CREATE INDEX idx_report_number ON report(number);

-- Third-party harm layer: source registry + mirrored flags (never self-judged).
CREATE TABLE tp_source (
  id          INTEGER PRIMARY KEY,
  name        TEXT NOT NULL,
  publisher   TEXT NOT NULL,
  url         TEXT,
  tier        TEXT NOT NULL,           -- A / B / C
  form        TEXT,
  has_numbers INTEGER NOT NULL DEFAULT 0,
  access      TEXT,                    -- public / licensed / closed
  note        TEXT,
  updated_at  TEXT
);

CREATE TABLE tp_flag (
  id            INTEGER PRIMARY KEY,
  number        TEXT NOT NULL,
  source_id     INTEGER NOT NULL REFERENCES tp_source(id),
  flag_type     TEXT NOT NULL,
  label         TEXT NOT NULL,
  evidence_url  TEXT,
  published_on  TEXT,
  first_seen    TEXT,
  last_verified TEXT,
  status        TEXT NOT NULL DEFAULT 'active'
);
CREATE INDEX idx_tp_flag_number ON tp_flag(number);

-- Government-numbers database: agencies and the phone numbers they publish.
CREATE TABLE gov_agency (
  id         INTEGER PRIMARY KEY,
  name       TEXT NOT NULL,            -- 正式名称 e.g. 総務省
  name_short TEXT,
  kind       TEXT,                     -- 省 / 庁 / 委員会 / 府 / その他
  url        TEXT                      -- official top page
);

CREATE TABLE gov_number (
  id          INTEGER PRIMARY KEY,
  number      TEXT NOT NULL UNIQUE,    -- normalized digits
  agency_id   INTEGER NOT NULL REFERENCES gov_agency(id),
  number_kind TEXT NOT NULL DEFAULT '代表',  -- 代表 / 相談窓口 / その他
  label       TEXT,                    -- what the number is for, as published
  source_url  TEXT NOT NULL,           -- official page publishing this number
  verified_on TEXT                     -- date we last saw it on the official page
);
CREATE INDEX idx_gov_number_agency ON gov_number(agency_id);

-- Views: the standard compositions, defined once in the database (DBA practice)
-- instead of being re-joined by hand in application code.
CREATE VIEW v_ma_detail AS
  SELECT m.id AS ma_id, m.name AS ma_name, m.zone_code, m.region_text AS region,
         m.area_code, p.name AS prefecture
    FROM ma m
    JOIN prefecture p ON p.code = m.prefecture_code;

CREATE VIEW v_area_summary AS
  SELECT a.area_code, p.name AS prefecture,
         (SELECT COUNT(*) FROM ma m WHERE m.area_code = a.area_code) AS ma_count
    FROM area_code a
    JOIN prefecture p ON p.code = a.primary_prefecture_code;

CREATE VIEW v_block_detail AS
  SELECT b.id AS block_id, b.line_type, b.prefix, b.designated_on, b.status,
         b.note, b.source, lt.name_ja AS line_type_name, lt.digits AS line_digits,
         lt.portable AS portable, c.name AS carrier_name
    FROM alloc_block b
    JOIN line_type lt ON lt.code = b.line_type
    LEFT JOIN carrier c ON c.id = b.carrier_id;

CREATE VIEW v_flag_detail AS
  SELECT f.*, s.name AS source_name, s.publisher, s.tier AS source_tier, s.url AS source_url
    FROM tp_flag f
    JOIN tp_source s ON s.id = f.source_id;

CREATE VIEW v_gov_number_detail AS
  SELECT n.number, n.number_kind, n.label, n.source_url, n.verified_on,
         a.name AS agency_name, a.name_short AS agency_short, a.kind AS agency_kind
    FROM gov_number n
    JOIN gov_agency a ON a.id = n.agency_id;
