-- One row per page per day per source. Not one row per hit: at 300+ pages a
-- row-per-hit table grows without bound and burns a D1 row-write on every
-- fetch, while an upsert into a daily bucket costs the same one write and
-- leaves a table small enough to rank with a single GROUP BY.
--
-- `day` is a UTC YYYY-MM-DD string. TEXT rather than an integer epoch so the
-- window queries are readable and `day >= ?` sorts correctly as a string.
--
-- `source` is 'camo' (a real wiki page render, proxied by GitHub) or 'direct'
-- (someone fetching the URL themselves). Kept as a dimension instead of a
-- filter so a change in camo's user agent shows up as traffic moving between
-- the two, not as the wiki losing all its readers overnight.
CREATE TABLE IF NOT EXISTS hits (
  page   TEXT    NOT NULL,
  day    TEXT    NOT NULL,
  source TEXT    NOT NULL,
  n      INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (page, day, source)
);

-- The stats query is always "everything since a day", so this is the index it
-- wants. The primary key leads with `page` and cannot serve that range scan.
CREATE INDEX IF NOT EXISTS hits_by_day ON hits (day);
