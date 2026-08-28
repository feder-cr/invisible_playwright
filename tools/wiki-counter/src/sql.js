/**
 * The two statements the worker sends, in a module of their own.
 *
 * SEPARATE FILE, NOT AN EXPORT FROM worker.js. workerd accepts only functions
 * and handlers as named exports of a worker's entry module - a string export
 * is a startup error, not a warning:
 *
 *   Uncaught TypeError: Incorrect type for map entry 'SQL_RANK':
 *   the provided value is not of type 'function or ExportedHandler'
 *
 * which is what the first version of this did, and what neither test suite
 * could see: node imports a string export happily, and the fake D1 never
 * parses the SQL. It was caught by running the thing under `wrangler dev`,
 * and `worker.test.js` now has a test for the rule itself.
 *
 * They are exported at all so they can be RUN rather than described:
 * `tests/test_wiki_counter.py` executes these exact strings against Python's
 * sqlite3 with schema.sql loaded. D1 is SQLite, so that is the only thing that
 * proves the upsert increments instead of erroring and that the ranking groups
 * the way /stats assumes - the JavaScript fake reimplements both in JavaScript,
 * so on its own it would keep passing against SQL no database would accept.
 */

/** One write per hit: the second hit on a page increments the day's row. */
export const SQL_RECORD =
  "INSERT INTO hits (page, day, source, n) VALUES (?, ?, ?, 1) " +
  "ON CONFLICT(page, day, source) DO UPDATE SET n = n + 1";

/** What /stats shows: pages ranked over a window, with the camo share. */
export const SQL_RANK =
  "SELECT page, SUM(n) AS total, " +
  "SUM(CASE WHEN source = 'camo' THEN n ELSE 0 END) AS camo " +
  "FROM hits WHERE day >= ? GROUP BY page ORDER BY total DESC LIMIT 500";
