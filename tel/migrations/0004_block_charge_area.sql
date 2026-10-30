-- Blocks learn their charge area (MA). Fixed-line designation blocks are
-- 6-digit (area code + exchange) rows published per numbering zone
-- (番号区画 = MA), so a block can name its MA directly and a number page can
-- show the ONE correct MA instead of every MA in the area code.

ALTER TABLE number_block_allocation ADD COLUMN charge_area_id INTEGER REFERENCES charge_area(id);
CREATE INDEX idx_number_block_allocation_charge_area ON number_block_allocation(charge_area_id);

DROP VIEW IF EXISTS v_number_block_detail;
CREATE VIEW v_number_block_detail AS
  SELECT b.id AS block_id, b.line_type, b.prefix, b.designated_on, b.status,
         b.note, b.source, b.charge_area_id,
         lt.name_ja AS line_type_name, lt.digits AS line_digits,
         lt.portable AS portable, c.name AS carrier_name,
         ca.name AS charge_area_name, ca.zone_code AS charge_area_zone_code
    FROM number_block_allocation b
    JOIN line_type lt ON lt.code = b.line_type
    LEFT JOIN carrier c ON c.id = b.carrier_id
    LEFT JOIN charge_area ca ON ca.id = b.charge_area_id;
