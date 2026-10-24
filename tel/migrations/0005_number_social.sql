-- Social layer for number pages: how often a number was looked up, and
-- short anonymous comments (owner rules: at most 10 characters, no hostile
-- language toward people — enforced in src/lib/social.js).

CREATE TABLE number_view (
  number     TEXT PRIMARY KEY,
  views      INTEGER NOT NULL DEFAULT 0,
  updated_at TEXT
);

CREATE TABLE number_comment (
  id         INTEGER PRIMARY KEY,
  number     TEXT NOT NULL,
  body       TEXT NOT NULL,
  ip_hash    TEXT,
  status     TEXT NOT NULL DEFAULT 'visible',
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX idx_number_comment_number ON number_comment(number, status, id DESC);
