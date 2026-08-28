# wiki view counter

The endpoint behind `WIKI_VIEW_PIXEL`. `scripts/build_wiki.py` ends every
published wiki page with an image whose URL carries that page's name; this
serves that image and counts the fetch.

Cloudflare Worker + D1, no npm dependencies. It is not part of the Python
package and is not shipped in the wheel or the sdist.

## Deploy

```bash
cd tools/wiki-counter
npx wrangler d1 create wiki_counter          # paste the printed id into wrangler.toml
npx wrangler d1 execute wiki_counter --remote --file=schema.sql
npx wrangler deploy
npx wrangler secret put STATS_KEY            # optional, see "Stats" below
```

Then set the repository variable (Settings -> Secrets and variables -> Actions
-> Variables). This exact line is what the tests check the two halves against:

    WIKI_VIEW_PIXEL = https://wiki-counter.example.workers.dev/w/{page}.gif

Replace `example` with your workers.dev subdomain, or use a custom domain. The
`{page}` placeholder is required - without it every page would fetch the same
URL and the whole wiki would be one number, so `build_wiki.py` fails the build
rather than publish that.

The pixel only reaches readers after `publish-wiki.yml` runs (a published
release, or a manual `workflow_dispatch`), which rewrites all 327 pages.

## Routes

| route          | what it does                                          |
| -------------- | ----------------------------------------------------- |
| `/w/<page>.gif`| records a hit, returns a 42-byte transparent GIF       |
| `/stats`       | the ranking, as HTML                                   |
| `/stats.json`  | the same, as JSON                                      |

`?days=N` sets the window on either stats route (default 30, max 365). The
extension on `/w/` is cosmetic - markdown needs the URL to look like an image -
and any of `.gif .png .jpg .svg .webp` maps to the same page.

## Stats

Public unless the `STATS_KEY` secret is set; set it and `/stats?key=...` is
required. Public is a reasonable default here because the only thing on that
page is the names of pages that are already public - but it is a choice, so it
is written down instead of left to be discovered.

## What the numbers are

A reader's browser never requests this URL. GitHub rewrites every external
image in a wiki page to `camo.githubusercontent.com` and fetches it from its own
infrastructure, so a hit is "camo asked for this page's pixel":

- **no dedup** - one reader visiting ten times and ten readers look identical;
- **cache** - repeat views collapse behind a warm entry, so this under-counts;
- **crawlers** - anything that renders the page counts, so this over-counts.

What survives all three is the **comparison**: every page sits behind the same
cache with the same policy, so page A having 10x page B is real, and one page
tracked week over week is real. Treat it as a ranking, never as visitors.
`/stats` says this on the page so nobody has to remember it.

The `via camo` column is the honest half. `total - camo` is direct traffic:
someone who found this URL and fetched it, or a scanner. It is shown rather
than filtered out, so that if camo's user agent ever changes it looks like
traffic moving between columns instead of the wiki losing all its readers.

The only lever over how much of the real traffic reaches here is the cache
headers: the URL in the markdown is static, so cache-busting is impossible.
The worker sends `no-store, no-cache, must-revalidate, max-age=0`.

## Calibrating it

Once it has run for a couple of weeks, compare its totals against the
repository's own Insights -> Traffic over the same window. If the two curves
move together, the ranking underneath is trustworthy.

## Tests

`node --test test/` - no dependencies, the worker is a plain ES module and
`Request`/`Response` are Node globals. `tests/test_wiki_counter.py` runs them
as part of the normal `pytest` run, and checks the `WIKI_VIEW_PIXEL` line above
against what `scripts/build_wiki.py` actually accepts and produces.
