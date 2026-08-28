/**
 * The endpoint behind WIKI_VIEW_PIXEL: count a wiki page view, return a pixel.
 *
 * GitHub reports no per-page traffic for a wiki (the Traffic API counts the
 * repository), so `scripts/build_wiki.py` ends every published page with an
 * image whose URL carries that page's name. This is what serves that image and
 * does the counting. Cloudflare Worker + D1; no npm dependencies.
 *
 *   GET /w/<page>[.gif]  ->  42-byte transparent GIF, and one recorded hit
 *   GET /stats           ->  the ranking, as HTML
 *   GET /stats.json      ->  the same, as JSON
 *
 * THE PIXEL MUST NEVER FAIL. It is embedded in 300+ public pages, so an error
 * here is not a lost data point, it is a broken-image icon on every page of the
 * wiki. Every failure path below therefore still returns the GIF: a missing D1
 * binding, a broken query, an unparseable page name. Losing a count is cheap;
 * looking broken to readers is not.
 *
 * WHY D1 AND NOT KV. KV's free tier allows 1000 writes/day and a hit is a
 * write - one busy day would silently stop counting partway through, which is
 * the worst failure mode available (numbers that look real and are truncated).
 * D1 allows 100k row writes/day, and one row per page per day per source keeps
 * the write rate to an UPDATE per hit instead of an INSERT, so the table stays
 * small and the daily buckets give trends for free.
 *
 * WHAT THE NUMBERS ARE. A reader's browser never requests this URL: GitHub
 * rewrites every external image in a wiki page to camo.githubusercontent.com,
 * and camo fetches it from GitHub's infrastructure. So a hit is "camo asked for
 * this page's pixel" - no IP, no user agent, no dedup, repeat readers collapsed
 * behind a warm cache entry, crawlers counted as readers. It ranks pages
 * against each other and tracks one page over time. It is not a visitor count,
 * and /stats says so on the page rather than leaving it to be assumed.
 */

import { SQL_RANK, SQL_RECORD } from "./sql.js";

/** 1x1 fully transparent GIF89a. The smallest thing that is unambiguously an
 *  image to camo, which checks the content type of what it proxies. */
const PIXEL = new Uint8Array([
  0x47, 0x49, 0x46, 0x38, 0x39, 0x61, 0x01, 0x00, 0x01, 0x00, 0x80, 0x00, 0x00,
  0x00, 0x00, 0x00, 0xff, 0xff, 0xff, 0x21, 0xf9, 0x04, 0x01, 0x00, 0x00, 0x00,
  0x00, 0x2c, 0x00, 0x00, 0x00, 0x00, 0x01, 0x00, 0x01, 0x00, 0x00, 0x02, 0x01,
  0x44, 0x00, 0x3b,
]);

/**
 * Cache headers are the ONLY lever this endpoint has over how much it sees.
 * The URL in the markdown is static, so cache-busting is impossible; camo and
 * the CDN in front of it decide how often the origin is asked, and they decide
 * it from these. Without them a page's pixel is fetched once and the counter
 * flatlines while the page keeps being read.
 */
const NO_STORE = {
  "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
  "Pragma": "no-cache",
  "Expires": "0",
};

/** A page name longer than this is not one of ours - it is someone probing the
 *  endpoint - and unbounded names are unbounded rows. Truncate rather than
 *  reject: the hit still lands somewhere visible instead of vanishing. */
const MAX_PAGE = 128;

/**
 * Which half of the traffic a request is.
 *
 * Camo identifies itself in its User-Agent, so a request that does not is not a
 * wiki reader: it is someone who found the URL and fetched it directly, or a
 * scanner. Those are NOT discarded - they are recorded as `direct` and shown
 * separately. Discarding them would make a change in camo's user agent look
 * exactly like the wiki suddenly losing all its readers; splitting them means
 * that day shows up as traffic moving from one column to the other, which is
 * a question someone will ask instead of a drop nobody can explain.
 */
export function classify(userAgent) {
  return /camo/i.test(userAgent || "") ? "camo" : "direct";
}

/** The wiki page name from a `/w/<page>[.ext]` path, or "" if it is not one.
 *  The extension is cosmetic - markdown links to something that looks like an
 *  image - so any of them maps to the same page. */
export function pageFromPath(pathname) {
  const m = /^\/w\/(.+)$/.exec(pathname);
  if (!m) return "";
  let page = m[1].replace(/\.(gif|png|jpg|jpeg|svg|webp)$/i, "");
  try {
    page = decodeURIComponent(page);
  } catch {
    // A malformed %-escape is not worth losing the hit over: `build_wiki.py`
    // encodes the name, so this is someone hand-editing a URL. Count it raw.
  }
  return page.slice(0, MAX_PAGE);
}

/** UTC day bucket. UTC and not a local zone on purpose: the worker runs in
 *  whichever datacentre is nearest the reader, so a local day would slice the
 *  same hour into different buckets depending on where camo fetched from. */
export function utcDay(now = new Date()) {
  return now.toISOString().slice(0, 10);
}

async function record(env, page, source) {
  if (!env || !env.DB) return; // unconfigured: serve the pixel, count nothing
  await env.DB.prepare(SQL_RECORD).bind(page, utcDay(), source).run();
}

function pixelResponse(status = 200, body = PIXEL) {
  return new Response(body, {
    status,
    headers: {
      "Content-Type": "image/gif",
      "Content-Length": String(PIXEL.length),
      ...NO_STORE,
    },
  });
}

async function stats(env, url) {
  // A key is optional. Set the STATS_KEY secret and the ranking needs it;
  // leave it unset and /stats is public, which is a fine default for a public
  // wiki's public page names. It is stated in the README rather than left to
  // be discovered, because a default that silently exposes something is only
  // acceptable when the thing exposed is already public - and this is.
  if (env && env.STATS_KEY && url.searchParams.get("key") !== env.STATS_KEY) {
    return { error: new Response("stats key required\n", { status: 401 }) };
  }
  if (!env || !env.DB) {
    return { error: new Response("no database bound\n", { status: 503 }) };
  }
  const days = Math.min(Math.max(parseInt(url.searchParams.get("days") || "30", 10) || 30, 1), 365);
  const since = utcDay(new Date(Date.now() - days * 86400000));
  const { results } = await env.DB.prepare(SQL_RANK).bind(since).all();
  return { days, since, rows: results || [] };
}

function statsHtml({ days, since, rows }) {
  const esc = (s) =>
    String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);
  const body = rows
    .map(
      (r, i) =>
        `<tr><td>${i + 1}</td><td>${esc(r.page)}</td><td>${r.total}</td><td>${r.camo}</td></tr>`,
    )
    .join("\n");
  return `<!doctype html>
<meta charset="utf-8"><title>wiki views</title>
<style>
 body{font:14px/1.5 system-ui,sans-serif;margin:2rem auto;max-width:52rem;padding:0 1rem}
 table{border-collapse:collapse;width:100%}
 th,td{text-align:left;padding:.3rem .6rem;border-bottom:1px solid #8883}
 td:first-child,td:nth-child(3),td:nth-child(4){text-align:right;font-variant-numeric:tabular-nums}
 p.note{color:#777;font-size:13px}
</style>
<h1>wiki views - last ${days} days</h1>
<p class="note">Since ${esc(since)} (UTC). <strong>This is not a visitor count.</strong>
GitHub proxies every wiki image through camo, so a hit is "camo fetched this
page's pixel": no dedup, repeat readers hidden behind a warm cache, crawlers
counted as readers. Compare pages against each other, or one page over time.
The <em>direct</em> share is total minus camo - requests that did not come from
GitHub at all.</p>
<table><thead><tr><th>#</th><th>page</th><th>hits</th><th>via camo</th></tr></thead>
<tbody>
${body}
</tbody></table>
<p class="note">?days=N to change the window, max 365.</p>
`;
}

export default {
  async fetch(request, env, ctx) {
    const url = new URL(request.url);

    if (request.method !== "GET" && request.method !== "HEAD") {
      return new Response("method not allowed\n", { status: 405 });
    }

    const page = pageFromPath(url.pathname);
    if (page) {
      const source = classify(request.headers.get("user-agent"));
      // The write is deliberately NOT awaited before responding: the reader is
      // waiting on an image, not on our bookkeeping. waitUntil keeps the worker
      // alive until it finishes, and a rejection is swallowed for the reason at
      // the top of this file - a database problem must not become a broken
      // image on 300+ pages.
      const write = record(env, page, source).catch(() => {});
      if (ctx && typeof ctx.waitUntil === "function") ctx.waitUntil(write);
      return pixelResponse(200, request.method === "HEAD" ? null : PIXEL);
    }

    if (url.pathname === "/stats" || url.pathname === "/stats.json") {
      const data = await stats(env, url);
      if (data.error) return data.error;
      if (url.pathname === "/stats.json") {
        return new Response(JSON.stringify(data, null, 2), {
          headers: { "Content-Type": "application/json; charset=utf-8", ...NO_STORE },
        });
      }
      return new Response(statsHtml(data), {
        headers: { "Content-Type": "text/html; charset=utf-8", ...NO_STORE },
      });
    }

    if (url.pathname === "/") {
      return new Response(
        "wiki view counter. GET /w/<page>.gif to count, /stats for the ranking.\n",
        { status: 200, headers: { "Content-Type": "text/plain; charset=utf-8" } },
      );
    }

    return new Response("not found\n", { status: 404 });
  },
};
