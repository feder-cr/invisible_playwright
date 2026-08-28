/**
 * The worker's own tests. No dependencies: it is a plain ES module and
 * Request/Response/URL are Node globals, so the real fetch handler runs here
 * with a fake D1 binding instead of a deployed one.
 *
 * The rule most of these exist to defend is the one at the top of worker.js:
 * THE PIXEL MUST NEVER FAIL. It sits in 300+ public pages, so any path that
 * returns something other than an image turns a bookkeeping problem into a
 * broken-image icon on the whole wiki. Missing binding, throwing database,
 * malformed page name, a query that hangs - all of them still return the GIF.
 *
 * The last test is the one that catches the mistake nobody would notice: that
 * the URL shape documented in README.md is the URL shape this worker routes.
 * They are edited months apart, and if they drift the wiki publishes 327 pixels
 * that 404 - and a 404 looks exactly like a page nobody reads.
 */
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import test from "node:test";

import worker, { classify, pageFromPath, utcDay } from "../src/worker.js";

const README = fileURLToPath(new URL("../README.md", import.meta.url));

/** A D1 stand-in that applies the upsert and the aggregate for real, so the
 *  SQL's shape is exercised rather than mocked away. Rows are keyed by a
 *  JSON tuple, not a joined string: a page name can contain the separator
 *  ("odd name"), and the first cut of this truncated it inside the FAKE,
 *  which reads exactly like the worker mangling the name. */
function fakeDB() {
  const rows = new Map();
  const db = {
    rows,
    fail: false,
    prepare(sql) {
      return {
        bind(...args) {
          const guard = () => {
            if (db.fail) throw new Error("D1 is having a day");
          };
          return {
            async run() {
              guard();
              const [page, day, source] = args;
              const k = JSON.stringify([page, day, source]);
              rows.set(k, (rows.get(k) || 0) + 1);
              return { success: true };
            },
            async all() {
              guard();
              const since = args[0];
              const agg = new Map();
              for (const [k, n] of rows) {
                const [page, day, source] = JSON.parse(k);
                if (day < since) continue;
                const cur = agg.get(page) || { page, total: 0, camo: 0 };
                cur.total += n;
                if (source === "camo") cur.camo += n;
                agg.set(page, cur);
              }
              return { results: [...agg.values()].sort((a, b) => b.total - a.total) };
            },
          };
        },
      };
    },
  };
  return db;
}

function ctxSpy() {
  const tasks = [];
  return { waitUntil: (p) => tasks.push(p), settle: () => Promise.allSettled(tasks) };
}

async function hit(env, path, { ua = "github-camo (abc123)", method = "GET" } = {}) {
  const ctx = ctxSpy();
  const res = await worker.fetch(
    new Request("https://count.example" + path, { method, headers: { "user-agent": ua } }),
    env,
    ctx,
  );
  await ctx.settle();
  return res;
}

test("the pixel is a real GIF with cache defeated", async () => {
  const env = { DB: fakeDB() };
  const res = await hit(env, "/w/botd-explained.gif");
  assert.equal(res.status, 200);
  assert.equal(res.headers.get("content-type"), "image/gif");
  const body = new Uint8Array(await res.arrayBuffer());
  assert.equal(body.length, 42);
  assert.deepEqual([...body.slice(0, 6)], [...Buffer.from("GIF89a")]);
  // The only lever this endpoint has over how much traffic it ever sees.
  assert.match(res.headers.get("cache-control"), /no-store/);
  assert.match(res.headers.get("cache-control"), /max-age=0/);
});

test("a hit is recorded against the page, split by source", async () => {
  const env = { DB: fakeDB() };
  await hit(env, "/w/Home.gif");
  await hit(env, "/w/Home.gif");
  await hit(env, "/w/Home.gif", { ua: "curl/8.0" });
  const day = utcDay();
  assert.equal(env.DB.rows.get(JSON.stringify(["Home", day, "camo"])), 2);
  assert.equal(env.DB.rows.get(JSON.stringify(["Home", day, "direct"])), 1);
});

test("camo is recognised, everything else is direct", () => {
  assert.equal(classify("github-camo (5f2b)"), "camo");
  assert.equal(classify("GitHub-Camo/1.0"), "camo");
  assert.equal(classify("Mozilla/5.0"), "direct");
  assert.equal(classify(""), "direct");
  assert.equal(classify(null), "direct");
});

test("no database bound still serves the pixel", async () => {
  const res = await hit({}, "/w/Home.gif");
  assert.equal(res.status, 200);
  assert.equal((await res.arrayBuffer()).byteLength, 42);
});

test("a database that throws still serves the pixel", async () => {
  const db = fakeDB();
  db.fail = true;
  const res = await hit({ DB: db }, "/w/Home.gif");
  assert.equal(res.status, 200);
  assert.equal((await res.arrayBuffer()).byteLength, 42);
});

test("a database that hangs does not hold up the reader", async () => {
  // The write goes to waitUntil, not into the response path. If that ever gets
  // awaited before responding, a slow D1 becomes a slow-loading wiki page.
  let release;
  const stuck = {
    prepare: () => ({
      bind: () => ({ run: () => new Promise((resolve) => { release = resolve; }) }),
    }),
  };
  const ctx = ctxSpy();
  const res = await worker.fetch(
    new Request("https://count.example/w/Home.gif"),
    { DB: stuck },
    ctx,
  );
  assert.equal(res.status, 200);
  assert.equal((await res.arrayBuffer()).byteLength, 42);
  release({ success: true });
  await ctx.settle();
});

test("the extension is cosmetic and the name is decoded and capped", () => {
  assert.equal(pageFromPath("/w/Home.gif"), "Home");
  assert.equal(pageFromPath("/w/Home.svg"), "Home");
  assert.equal(pageFromPath("/w/Home"), "Home");
  assert.equal(pageFromPath("/w/odd%20name.gif"), "odd name");
  assert.equal(pageFromPath("/w/broken%2.gif"), "broken%2"); // not worth a lost hit
  assert.equal(pageFromPath("/w/" + "x".repeat(500)).length, 128);
  assert.equal(pageFromPath("/stats"), "");
  assert.equal(pageFromPath("/"), "");
});

test("HEAD gets the headers and no body", async () => {
  const env = { DB: fakeDB() };
  const res = await hit(env, "/w/Home.gif", { method: "HEAD" });
  assert.equal(res.status, 200);
  assert.equal(res.headers.get("content-type"), "image/gif");
  assert.equal(env.DB.rows.size, 1); // a HEAD is still a render
});

test("other methods and unknown paths are refused, not counted", async () => {
  const env = { DB: fakeDB() };
  assert.equal((await hit(env, "/w/Home.gif", { method: "POST" })).status, 405);
  assert.equal((await hit(env, "/nope")).status, 404);
  assert.equal((await hit(env, "/")).status, 200);
  assert.equal(env.DB.rows.size, 0);
});

test("stats ranks pages and reports the camo split", async () => {
  const env = { DB: fakeDB() };
  for (let i = 0; i < 5; i++) await hit(env, "/w/popular.gif");
  await hit(env, "/w/quiet.gif");
  await hit(env, "/w/quiet.gif", { ua: "curl/8.0" });
  const res = await hit(env, "/stats.json");
  assert.equal(res.status, 200);
  const data = await res.json();
  assert.equal(data.days, 30);
  assert.deepEqual(
    data.rows.map((r) => [r.page, r.total, r.camo]),
    [
      ["popular", 5, 5],
      ["quiet", 2, 1],
    ],
  );
});

test("the stats window is clamped, not trusted", async () => {
  const env = { DB: fakeDB() };
  assert.equal((await (await hit(env, "/stats.json?days=99999")).json()).days, 365);
  assert.equal((await (await hit(env, "/stats.json?days=0")).json()).days, 30);
  assert.equal((await (await hit(env, "/stats.json?days=banana")).json()).days, 30);
});

test("the html page states what the number is not", async () => {
  const env = { DB: fakeDB() };
  await hit(env, "/w/Home.gif");
  const html = await (await hit(env, "/stats")).text();
  // If this ever stops being said on the page, the ranking starts being read
  // as a visitor count by whoever opens it next.
  assert.match(html, /not a visitor count/i);
  assert.match(html, /camo/i);
});

test("a page name cannot inject markup into the stats page", async () => {
  const env = { DB: fakeDB() };
  await hit(env, "/w/" + encodeURIComponent("<script>alert(1)</script>"));
  const html = await (await hit(env, "/stats")).text();
  assert.ok(!html.includes("<script>alert(1)</script>"));
  assert.match(html, /&lt;script&gt;/);
});

test("STATS_KEY, when set, is required", async () => {
  const env = { DB: fakeDB(), STATS_KEY: "s3cret" };
  assert.equal((await hit(env, "/stats")).status, 401);
  assert.equal((await hit(env, "/stats?key=wrong")).status, 401);
  assert.equal((await hit(env, "/stats?key=s3cret")).status, 200);
  // The pixel is never gated: gating it would blank the wiki.
  assert.equal((await hit(env, "/w/Home.gif")).status, 200);
});

test("the entry module exports only what workerd accepts", async () => {
  // workerd refuses to START a worker whose entry module has a named export
  // that is not a function or a handler:
  //
  //   Uncaught TypeError: Incorrect type for map entry 'SQL_RANK': the
  //   provided value is not of type 'function or ExportedHandler'
  //
  // which is what `export const SQL_RANK = "SELECT ..."` here did, until it was
  // moved into src/sql.js. Node imports a string export perfectly happily, and
  // the fake D1 never parses SQL, so NEITHER suite could see it - it took a
  // `wrangler dev`. This is the cheap standing guard for that rule.
  const mod = await import("../src/worker.js");
  for (const [name, value] of Object.entries(mod)) {
    if (name === "default") {
      assert.equal(typeof value.fetch, "function");
      continue;
    }
    assert.equal(typeof value, "function", `export ${name} must be a function`);
  }
});

test("the URL documented in README.md is the URL this worker routes", async () => {
  const readme = readFileSync(README, "utf8");
  const m = /WIKI_VIEW_PIXEL\s*=\s*(\S+)/.exec(readme);
  assert.ok(m, "README.md must document the WIKI_VIEW_PIXEL template");
  const template = m[1];
  assert.ok(template.startsWith("https://"), template);
  assert.ok(template.includes("{page}"), template);

  const env = { DB: fakeDB() };
  for (const page of ["Home", "botd-explained", "odd%20name"]) {
    const url = new URL(template.replace("{page}", page));
    const res = await hit(env, url.pathname);
    assert.equal(res.status, 200, page);
    assert.equal(res.headers.get("content-type"), "image/gif", page);
  }
  assert.deepEqual([...env.DB.rows.keys()].map((k) => JSON.parse(k)[0]).sort(), [
    "Home",
    "botd-explained",
    "odd name",
  ]);
});
