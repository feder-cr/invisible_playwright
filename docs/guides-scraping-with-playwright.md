---
title: "Scraping with Playwright"
description: "Practical how-tos for real scraping problems - blocked headless browsers, infinite scroll, proxy rotation, sessions behind a login - written against a real patched Firefox driven by stock Playwright."
parent: "Guides"
has_children: true
nav_order: 8
---

# Scraping with Playwright

The rest of the Guides explain how a single surface gives you away. This group is the
other direction: a concrete task, in order, with the code that does it and the specific
thing that breaks it.

Every page here is written against a real browser instead of a generic recipe. The
launch is a two-line change from stock Playwright, the browser returned is a real
Playwright `Browser` with no wrapped subset to learn, and each how-to carries at least one
mistake we made first, measured and fixed, not assumed.

Start with [how to scrape without getting blocked](how-to-scrape-without-getting-blocked.md)
for the model that orders the rest, then pick the task you have.

## Get past blocking

- [How to scrape without getting blocked](how-to-scrape-without-getting-blocked.md) - Blocking is five independent layers; fix them cheapest first, looking real beats rotating agents.
- [How to avoid bot detection with Playwright](how-to-avoid-bot-detection-playwright.md) - navigator.webdriver patches are level one; a patched engine reaches the rest, with runnable code.
- [How to scrape a site that blocks headless browsers](how-to-scrape-headless-blocked.md) - Headless is rarely the block; fix the GPU, font and screen tells instead.
- [How to run Playwright in Docker without getting detected](how-to-run-playwright-docker-undetected.md) - Run in Docker with a real GPU, font set and screen, not six tells.
- [How to rotate proxies when scraping with Playwright](how-to-rotate-proxies-playwright.md) - A proxy per session, SOCKS5 auth, DNS through the proxy, exit IP matching timezone.
- [How to scrape geotargeted content with Playwright](how-to-scrape-geotargeted-content-playwright.md) - Timezone, locale, number format and geolocation must all agree with the proxy exit IP.
- [How to rate limit your own Playwright scraper](how-to-rate-limit-your-scraper-playwright.md) - Request velocity is a scored signal; throttle with a minimum gap, jitter, concurrency caps.
- [Handle 403 and 429 backoff mid-scrape in Playwright](how-to-handle-403-429-backoff-mid-scrape-playwright.md) - Read 403 and 429 off the response event, honor Retry-After, back off, keep the identity.
- [How to retry failed requests when scraping Playwright](how-to-retry-failed-requests-playwright.md) - Retry with a total time-and-attempt budget; aggressive retries raise a velocity signal.

## Page mechanics: waiting, pagination and crawl flow

- [How to wait for content to load in Playwright](how-to-wait-for-page-load-playwright.md) - Why networkidle stalls on long-poll and websockets, and when wait_for_selector or wait_for_function fits.
- [Wait for a specific API response in Playwright](wait-for-specific-api-response-playwright.md) - Use page.expect_response with a URL predicate, read the JSON at source, and waiting's one limit.
- [How to scrape infinite scroll pages with Playwright](how-to-scrape-infinite-scroll-playwright.md) - A loop that waits for content growth, not a fixed sleep; when to stop, how to dedupe.
- [Scrape load-more button pages with Playwright](how-to-scrape-load-more-button-playwright.md) - Re-query the button each round, wait for the count to grow, vary the click timing.
- [How to scrape paginated pages with Playwright](how-to-scrape-paginated-pages-playwright.md) - Numbered and next-page pagination without the context-destroyed crash: re-query after every page turn.
- [Scrape nested pagination with Playwright](how-to-scrape-nested-pagination-playwright.md) - Two cursors: keep the outer position across inner navigations, pace the tree, one identity.
- [Scrape an SPA that changes URL via history API](how-to-scrape-spa-history-url-changes-playwright.md) - history.pushState with no page load: wait on route and DOM markers, capture the view XHR.
- [Crawl list pages to detail pages with Playwright](how-to-crawl-list-to-detail-pages-playwright.md) - Two phases: collect card links, visit each detail URL, re-associate data, pace the fan-out.
- [How to extract links and build a crawl frontier in Playwright](how-to-extract-links-crawl-frontier-playwright.md) - Resolve absolute URLs, strip tracking params, filter same-origin, and dedup with a visited set.
- [How to scrape a sitemap.xml with Playwright](how-to-scrape-a-sitemap-playwright.md) - Walk the index-to-urlset tree, decompress the .xml.gz leaves, recrawl only changed lastmod URLs.

## Forms, widgets and page controls

- [Scrape search results by driving a form in Playwright](how-to-scrape-search-results-form-playwright.md) - Fire the input and change events, get past a JS-gated submit, tell results from zero-results.
- [Scrape autocomplete and typeahead inputs with Playwright](how-to-scrape-autocomplete-typeahead-playwright.md) - These inputs ignore fill(); type per character to fire the debounced XHR, wait for the listbox.
- [Scrape date-picker calendars with Playwright](how-to-scrape-date-picker-calendar-playwright.md) - Page between months, skip disabled cells, click to commit, sweep a range under one identity.
- [Scrape a multi-step wizard flow with Playwright](how-to-scrape-multi-step-wizard-flow-playwright.md) - Complete each step in order, carry the per-step tokens, keep one identity for the whole flow.
- [How to handle cookie consent banners in Playwright](how-to-handle-cookie-consent-banners-playwright.md) - The accept button sits in a cross-origin iframe; use frame_locator, not force=True.
- [How to handle popups and modals in Playwright](how-to-handle-popups-and-modals-playwright.md) - Separate in-page modals, native dialogs and new tabs, and use the right API for each.
- [How to scrape iframe content with Playwright](how-to-scrape-iframe-content-playwright.md) - Use frame_locator for same-origin frames; cross-origin iframes fail for a process-isolation reason.
- [How to scrape shadow DOM content with Playwright](how-to-scrape-shadow-dom-playwright.md) - Locators pierce open shadow roots automatically; closed roots stay unreachable by design.
- [Scrape a map-based search with Playwright](how-to-scrape-map-based-search-playwright.md) - Capture the bounding-box marker XHR, pan and zoom, grid the viewport to cover the area.
- [How to scrape map-based local results with Playwright](how-to-scrape-map-based-local-results-playwright.md) - Drive the viewport, capture the bounds-keyed XHR per step, tile an area through an in-region proxy.

- [Select from dropdowns with Playwright, native and custom](playwright-select-option-dropdowns.md) - select_option() drives a real select element; a custom listbox needs a click, a wait, then a second click.

## Extracting content and structured data

- [How to extract JSON-LD structured data with Playwright](how-to-extract-json-ld-structured-data-playwright.md) - Parse JSON-LD and @graph instead of fragile selectors; an empty result reads as a blocked page.
- [How to extract Open Graph and meta tags with Playwright](how-to-extract-open-graph-metadata-playwright.md) - Read the rendered head after JS, resolve relative og:image URLs, apply og:title fallbacks.
- [How to scrape HTML tables with Playwright](how-to-scrape-html-tables-playwright.md) - Pull the whole table in one evaluate_all call, extract before navigating so no rows drop.
- [How to extract clean article text with Playwright](how-to-extract-clean-article-text-playwright.md) - Wait for the body, run a readability pass over page.content() to drop nav, ads and chrome.
- [How to scrape news article text with Playwright](how-to-scrape-news-article-text-playwright.md) - Pull headline, author and date from JSON-LD, isolate the article node, strip boilerplate.
- [Extract data from canvas charts with Playwright](how-to-extract-data-from-canvas-charts-playwright.md) - Read the data XHR or the chart library's JS state, not the noised pixels.
- [How to capture XHR and API responses in Playwright](how-to-capture-xhr-api-responses-playwright.md) - Capture JSON with page.on('response') and page.route() instead of parsing HTML.
- [How to scrape RSS and Atom feeds with Playwright](how-to-scrape-rss-atom-feeds-playwright.md) - Find the feed URL, fetch the XML through the browser, and parse both schemas.

## Files, images and media

- [How to download files with Playwright](how-to-download-files-playwright.md) - Use expect_download and save_as, keeping the transfer on the browser's proxied exit.
- [How to upload files with Playwright](how-to-upload-files-playwright.md) - Use set_input_files or expect_file_chooser; the driver fires trusted events, then verify it landed.
- [How to download images in bulk with Playwright](how-to-download-images-in-bulk-playwright.md) - Resolve lazy srcset and data-src to full-res URLs, then fetch the bytes inside the page session.
- [How to scrape image galleries with Playwright](how-to-scrape-image-galleries-playwright.md) - Scroll to trigger lazy tiles, take the widest srcset, open the lightbox for full-res.
- [Scrape lazy-loaded images with Playwright](how-to-scrape-lazy-loaded-images-playwright.md) - Read the data-src attribute off the DOM: no scrolling, no downloads. Scroll fallback included.
- [How to scrape video listings and metadata with Playwright](how-to-scrape-video-listings-and-metadata-playwright.md) - Pull exact duration, views and upload date from JSON-LD, fall back to overlays, page the grid.
- [How to take full-page screenshots with Playwright](how-to-take-full-page-screenshots-playwright.md) - Captured pixels are the true rendered image while the same page's canvas fingerprint is substituted.
- [How to generate a PDF with Playwright and Firefox](how-to-generate-pdf-with-playwright-firefox.md) - page.pdf() is Chromium-only; why it is unavailable, plus two capture routes that run on Firefox.

## Products, prices and shopping

- [How to scrape e-commerce product pages with Playwright](how-to-scrape-ecommerce-product-pages-playwright.md) - Price and stock live in a variant XHR; select each variant and cross-check the Product JSON-LD.
- [How to scrape product reviews with Playwright](how-to-scrape-product-reviews-playwright.md) - Reviews load from a separate endpoint behind a tab; drive the widget, dedupe on review id.
- [How to scrape reviews and ratings with Playwright](how-to-scrape-reviews-and-ratings-playwright.md) - Read star ratings from aria-label or CSS width, expand Read more, page the load more XHR.
- [How to track product prices with Playwright](how-to-track-product-prices-playwright.md) - Wait for the async price widget, keep one fingerprint per item, diff a saved daily time series.
- [How to track product stock and restocks with Playwright](how-to-track-product-stock-playwright.md) - Poll the per-variant availability XHR, diff the in-stock boolean, pace to read as one shopper.
- [How to scrape deals and coupon codes with Playwright](how-to-scrape-deals-and-coupon-codes-playwright.md) - Codes cloaked behind a reveal button: fire a trusted click, capture from tab, clipboard or XHR.
- [How to scrape location-based store prices with Playwright](how-to-scrape-local-store-prices-playwright.md) - Drive the store or ZIP picker, verify the location cookie stuck, then align proxy and timezone.
- [How to scrape restaurant menu data with Playwright](how-to-scrape-restaurant-menu-data-playwright.md) - Read JSON-LD first, fall back to the menu tab XHR, flatten category, size and variant prices.

## Listings, travel and live data by vertical

- [How to scrape real estate listings with Playwright](how-to-scrape-real-estate-listings-playwright.md) - Portals re-fetch from a map-bounds XHR and hide price and beds in detail-page JSON-LD.
- [How to scrape apartment rentals with Playwright](how-to-scrape-apartment-rentals-playwright.md) - Trigger the unit-availability XHR, read the per-floorplan price table, key rows on move-in date.
- [How to scrape vacation rental listings with Playwright](how-to-scrape-vacation-rental-listings-playwright.md) - Drive the date and guest inputs, read the fee-inclusive total from the pricing XHR, iterate windows.
- [How to scrape hotel room prices with Playwright](how-to-scrape-hotel-prices-playwright.md) - Drive the date and occupancy form, read the rate XHR: rates exist only when you query.
- [How to scrape flight prices with Playwright](how-to-scrape-flight-prices-playwright.md) - Wait for the results-complete signal instead of networkidle, then read the settled fare matrix.
- [How to scrape flight status boards with Playwright](how-to-scrape-flight-status-boards-playwright.md) - Snapshot a board that rewrites itself in one evaluation, keep scheduled and estimated apart, key rows on the service day.
- [How to scrape ferry schedules with Playwright](how-to-scrape-ferry-schedules-playwright.md) - Query date by date because sailings are seasonal, keep direction in the key, record cancellations as events.
- [How to scrape flexible-date fare calendars with Playwright](how-to-scrape-fare-calendars-playwright.md) - Wait for each month's price XHR before reading the grid, page forward across a seed-stable sweep.
- [How to scrape event and ticket listings with Playwright](how-to-scrape-event-and-ticket-listings-playwright.md) - Wait for the availability XHR, normalise event times to the source timezone, step the calendar.
- [How to scrape car listings with Playwright](how-to-scrape-car-listings-playwright.md) - Drive the faceted filter sidebar, wait on the results XHR, pull VIN and specs, then dedupe.
- [How to scrape classifieds listings with Playwright](how-to-scrape-classifieds-listings-playwright.md) - Align proxy region and timezone, harvest cards from the rotating feed, drive a contact-reveal click.
- [How to scrape business directory listings with Playwright](how-to-scrape-business-directory-listings-playwright.md) - Drive the search form, reveal click-gated phone and email, walk filtered pagination as one visitor.
- [How to scrape pet adoption listings with Playwright](how-to-scrape-pet-adoption-listings-playwright.md) - Reconcile each run against the last so a disappearance is data, and key animals on the shelter's own identifier.
- [How to scrape job postings with Playwright](how-to-scrape-job-postings-playwright.md) - Drive the faceted search filters, wait for the results XHR, and read the JobPosting JSON-LD.
- [How to scrape cryptocurrency prices with Playwright](how-to-scrape-cryptocurrency-prices-playwright.md) - Read WebSocket frames, not the flickering DOM node, and hold the feed open with a stable identity.
- [How to scrape EV charging availability with Playwright](how-to-scrape-ev-charging-availability-playwright.md) - Capture the status feed behind the map, keep the operator's timestamp apart from yours, store transitions not snapshots.
- [How to scrape fuel prices with Playwright](how-to-scrape-fuel-prices-playwright.md) - Set the location explicitly, keep grade and unit with every price, and record whether the number was reported or observed.
- [How to scrape snow reports with Playwright](how-to-scrape-snow-reports-playwright.md) - Pin the unit before reading, keep base and summit as separate measurements, and take the resort's own report time.
- [How to scrape parking rates with Playwright](how-to-scrape-parking-rates-playwright.md) - Capture the tariff table as duration bands instead of a price, and keep the small print that outranks the table.
- [How to scrape energy tariff comparisons with Playwright](how-to-scrape-energy-tariffs-playwright.md) - Drive the consumption form, keep standing charge and unit rate apart, and copy the inputs into every quoted row.
- [How to scrape bank interest rates with Playwright](how-to-scrape-bank-interest-rates-playwright.md) - Resolve the footnote markers while you have the DOM, keep the balance tiers as rows, and take the effective date.
- [How to scrape stock and financial data with Playwright](how-to-scrape-stock-and-financial-data-playwright.md) - Scrape fundamentals from tables, capture the live quote stream at the WebSocket or polling XHR.
- [How to scrape sports scores and stats with Playwright](how-to-scrape-sports-scores-and-stats-playwright.md) - Read the score feed over a WebSocket, click each stat tab to trigger its XHR, one long session.
- [How to scrape forum and community threads with Playwright](how-to-scrape-forum-and-community-threads-playwright.md) - Walk the reply tree depth-first, expand collapsed branches over XHR, strip quoted text.
- [How to scrape social media profiles with Playwright](how-to-scrape-social-media-profiles-playwright.md) - Extract each virtualized batch before it scrolls out, parse abbreviated counts, reuse a session.

## Output, storage and data cleaning

- [How to scrape to CSV with Playwright](how-to-scrape-to-csv-playwright.md) - Correct delimiter and newline escaping, a UTF-8 BOM spreadsheets read, crash-safe incremental appends.
- [How to scrape to JSON Lines with Playwright](how-to-scrape-to-json-lines-playwright.md) - Why NDJSON, not one big array, is crash-safe for a long crawl, plus the flush rule.
- [How to export scraped data to Excel with Playwright](how-to-export-scraped-data-to-excel-playwright.md) - Export with openpyxl without turning SKUs, barcodes and lot codes into numbers or dates.
- [How to scrape into a SQLite database with Playwright](how-to-scrape-into-a-database-playwright.md) - Upsert on a natural key, one transaction per page, idempotent instead of piling up duplicates.
- [How to scrape into a pandas DataFrame with Playwright](how-to-scrape-into-a-pandas-dataframe-playwright.md) - Feed read_html page.content() for a rendered logged-in table, then fix the dtypes it guesses wrong.
- [How to clean scraped prices and dates with Playwright](how-to-clean-scraped-prices-and-dates-playwright.md) - Parse locale prices and relative dates into typed numbers and UTC timestamps.

## Sessions, scheduling and running at scale

- [How to scrape data behind a login with Playwright](how-to-scrape-behind-login-playwright.md) - Log in once with a fixed seed, save storage_state, and reuse it every run.
- [Resume an interrupted scrape with Playwright](how-to-resume-an-interrupted-scrape-playwright.md) - Write a durable checkpoint, skip completed work, re-validate the boundary, reload the seeded identity.
- [Incremental scraping: only new items since last run](how-to-scrape-only-new-items-incremental-playwright.md) - A high-water mark that stops at the first already-seen id, handling out-of-order inserts and edits.
- [How to use invisible_playwright in Docker](how-to-use-invisible-playwright-in-docker.md) - Install in Docker, fetch the patched Firefox at build time, and verify the fingerprint survives.
- [Can you run invisible_playwright serverless?](can-you-run-invisible-playwright-serverless.md) - Why the patched-Firefox download weight is the real blocker, and the container or worker pattern.
- [Run invisible_playwright headful on a server with Xvfb](run-invisible-playwright-headful-server-xvfb.md) - Run a real headful window on a headless Linux server with Xvfb, when headless already suffices.
- [Run invisible_playwright in GitHub Actions CI](run-invisible-playwright-in-github-actions.md) - A headless CI recipe caching the engine, and why a datacenter runner IP is the limit.
- [Run invisible_playwright in a Jupyter notebook](run-invisible-playwright-in-a-jupyter-notebook.md) - Why the sync API raises inside a kernel, and the await-in-a-cell fix for interactive sessions.
- [Run invisible_playwright in Celery task workers](run-invisible-playwright-in-celery-workers.md) - One browser per worker reused across tasks, the seed passed as a task argument for retries.
- [Use invisible_playwright in an Airflow DAG](use-invisible-playwright-in-an-airflow-dag.md) - One browser per task run, the seed stored as a param for reproducible retries.
- [Schedule invisible_playwright scrapes with cron](schedule-invisible-playwright-scrapes-with-cron.md) - A scrape firing at the same clock minute daily is detectable; jitter the schedule and pacing.
- [Wrap invisible_playwright in a FastAPI service](wrap-invisible-playwright-in-a-fastapi-service.md) - One browser kept alive by lifespan, a semaphore to bound concurrency, the cost of one server IP.
- [How to scrape pages in parallel with Playwright](how-to-scrape-multiple-pages-in-parallel-playwright.md) - Run many workers with asyncio.gather, one identity per worker; a shared fingerprint is the tell.
- [Run invisible_playwright concurrently with asyncio](run-invisible-playwright-concurrently-asyncio.md) - Bound parallel pages with a Semaphore, give each worker its own seed; concurrency is a speed lever.
- [Combine invisible_playwright with httpx for speed](combine-invisible-playwright-with-httpx-for-speed.md) - Clear the fingerprint-gated entry, export storage_state, hand cookies to httpx for cheap follow-up.
- [Use BeautifulSoup with invisible_playwright](use-beautifulsoup-with-invisible-playwright.md) - The browser does the fingerprint-real fetch, BeautifulSoup parses page.content(); BS4 does not evade detection.
- [Block images to speed up scraping (and when not to)](block-images-speed-up-playwright-scraping-page-route.md) - Blocking images with route.abort() cuts bandwidth, but a no-image waterfall is itself a tell.
- [Run stealth Playwright tests with pytest fixtures](stealth-playwright-tests-with-pytest-fixtures.md) - Wire pytest-asyncio fixtures: a session-scoped browser, a per-test seeded context, realness assertions.

## Specific objects and data shapes

- [How to handle A/B test variants when scraping with Playwright](how-to-handle-ab-test-variants-playwright.md) - read the assignment marker from a cookie, data attribute or global object, pin it with add_cookies or storage_state, and record variant a...
- [How to scrape accordion and tab content with Playwright](how-to-scrape-accordion-and-tab-content-playwright.md) - tell panels already in the DOM from panels fetched on first expand, drive aria-expanded and aria-controls, and wait on a selector keyed t...
- [How to scrape appointment availability with Playwright](how-to-scrape-appointment-availability-playwright.md) - key each slot to a provider, service type and location, capture the per-period calendar response, and read the grid without clicking a sl...
- [How to scrape auction listings with Playwright](how-to-scrape-auction-listings-playwright.md) - stamp every read in UTC, take the server end timestamp instead of the rendered countdown, and read bid history from its own paginated call.
- [How to scrape book metadata with Playwright](how-to-scrape-book-metadata-playwright.md) - read the Book and workExample nodes, normalise every ISBN to ISBN-13, keep one row per edition, and split contributor roles out of the by...
- [How to scrape breadcrumb hierarchies with Playwright](how-to-scrape-breadcrumb-hierarchies-playwright.md) - read the trail from the markup instead of the truncated text, drop the self crumb, keep the category id from the href, and merge trails o...
- [How to scrape fitness class schedules with Playwright](how-to-scrape-class-schedules-playwright.md) - key each row by studio, instructor, room and start time instead of the occurrence id, fetch spots remaining and waitlist state from their...
- [How to scrape clinical trial listings with Playwright](how-to-scrape-clinical-trial-listings-playwright.md) - timestamp every status read, carry both trial identifiers, keep eligibility criteria as text, and split target enrollment from site-level...
- [How to scrape conference agendas with Playwright](how-to-scrape-conference-agendas-playwright.md) - read the grid as tracks over time so a session keeps its column, resolve the event timezone once, and follow the session pages for speakers.
- [How to scrape construction permit records with Playwright](how-to-scrape-construction-permits-playwright.md) - drive the postback search, assert the result count the portal prints, and capture the status history rather than the current stage.
- [How to scrape course catalogs with Playwright](how-to-scrape-course-catalogs-playwright.md) - the catalog is a four-level tree, the seat counts live behind a term parameter, and one row per section is the only shape that survives a...
- [How to scrape court docket listings with Playwright](how-to-scrape-court-dockets-playwright.md) - work from case numbers you can name rather than enumerating, keep the court's own sequence numbers, and pace it with a written daily cap.
- [How to scrape CSS pseudo-element content with Playwright](how-to-scrape-css-pseudo-element-content-playwright.md) - text injected by ::before and ::after is not in the DOM, so read it from getComputedStyle and unwrap the quoted CSS value.
- [How to scrape cursor-based pagination with Playwright](how-to-scrape-cursor-pagination-playwright.md) - read endCursor and hasNextPage out of the JSON payload, pass the token back untouched, and checkpoint it with the last item id.
- [How to scrape delivery slots with Playwright](how-to-scrape-delivery-slots-playwright.md) - a slot row is keyed to a postcode, a basket and a UTC moment, the grid arrives one week per request, and full is not the same as not offe...
- [How to scrape domain WHOIS records with Playwright](how-to-scrape-domain-whois-records-playwright.md) - Scrape WHOIS records with Playwright by parsing labeled fields instead of positions, treating REDACTED FOR PRIVACY as a real value, keepi...
- [How to scrape currency exchange rates with Playwright](how-to-scrape-exchange-rates-playwright.md) - capture the quote response instead of the animating node, and store base, quote, side, amount and a UTC arrival time in every row.
- [How to scrape museum and gallery exhibition dates with Playwright](how-to-scrape-exhibition-dates-playwright.md) - read the run's start and end dates from the page's own Event node, key each run by exhibition plus venue, and track a last-checked timest...
- [How to scrape GraphQL endpoints with Playwright](how-to-scrape-graphql-endpoints-playwright.md) - filter captured calls by the operationName in the POST body, replay persisted queries by hash, check the errors array on a 200, and page ...
- [How to scrape infinite carousels with Playwright](how-to-scrape-infinite-carousels-playwright.md) - mark the cloned wrap slides by their own attribute, pause autoplay through reduced motion, track the real slide index, and stop when an i...
- [How to scrape insurance quotes with Playwright](how-to-scrape-insurance-quotes-playwright.md) - fill the whole multi-step form fresh per profile, read only the final-step response, and capture every tier a tabbed result renders in on...
- [How to scrape library catalog availability with Playwright](how-to-scrape-library-catalog-availability-playwright.md) - read holdings per copy and per branch instead of the record badge, stay inside the session the catalogue issued, and keep its status vocabulary.
- [How to scrape live sports scores with Playwright](how-to-scrape-live-sports-scores-playwright.md) - read match state alongside the score, timestamp every update against its own clock, and accept that a score can go down after a VAR review.
- [How to scrape currency and locale switchers with Playwright](how-to-scrape-locale-switchers-playwright.md) - find which signal the site obeys by moving one at a time, record the final URL, and separate a display conversion from a real regional pr...
- [How to scrape microdata and RDFa markup with Playwright](how-to-scrape-microdata-markup-playwright.md) - walk [itemscope] and [itemprop] with page.evaluate, apply the nine-case value rule instead of textContent, resolve itemref, and read RDFa...
- [How to scrape multi-select facet filters with Playwright](how-to-scrape-multi-select-facets-playwright.md) - expand show-more first, wait for the facet response before reading counts, store the state URL on every row, and sweep one group at a time.
- [How to scrape newsletter archives with Playwright](how-to-scrape-newsletter-archives-playwright.md) - page until the archive repeats or runs dry, read the hosted rendering instead of the sent email, and resolve tracking-redirect links once...
- [How to scrape numbers rendered as images with Playwright](how-to-scrape-numbers-rendered-as-images-playwright.md) - look in the accessible layer and the sprite offsets before reaching for pixels, and compare decoded pixels rather than PNG bytes.
- [How to scrape nutrition labels with Playwright](how-to-scrape-nutrition-labels-playwright.md) - map every column to its per 100 g, per serving or per package basis, split the serving size, keep kilojoules and kilocalories apart, and ...
- [How to scrape open data portals with Playwright](how-to-scrape-open-data-portals-playwright.md) - name the catalogue product from its JSON response shape, page its documented listing API, and open a browser only where the search UI hid...
- [How to scrape package download statistics with Playwright](how-to-scrape-package-download-stats-playwright.md) - take the series the chart draws from, keep the version breakdown, and carry the mirror and CI caveats next to the number.
- [How to scrape package tracking status with Playwright](how-to-scrape-package-tracking-playwright.md) - capture the event timeline rather than the headline status, merge revisions instead of appending, and widen the poll when nothing moves.
- [How to scrape patent listings with Playwright](how-to-scrape-patent-listings-playwright.md) - bind each date to its labeled field, keep family members and claim dependencies intact instead of flattening them, and stamp legal status...
- [How to scrape podcast episode listings with Playwright](how-to-scrape-podcast-episodes-playwright.md) - find the RSS feed behind the player, key episodes on the guid, and parse durations and episode numbers that arrive in several shapes.
- [How to scrape podcast transcripts with Playwright](how-to-scrape-podcast-transcripts-playwright.md) - fetch the underlying VTT or SRT file instead of scrolling the panel, keep cue timing and text as rows, and detect speaker labels and auto...
- [How to scrape printer-friendly pages with Playwright](how-to-scrape-printer-friendly-pages-playwright.md) - find the print URL, emulate print media on the same DOM, diff one selector under both media types, and read the print stylesheet as the s...
- [How to scrape professional directories with Playwright](how-to-scrape-professional-directories-playwright.md) - key each row on the registration number plus the location, read credential badges out of their title attributes, sweep the overlapping le...
- [How to scrape range slider filters with Playwright](how-to-scrape-range-slider-filters-playwright.md) - read aria-valuenow instead of calling fill, prefer the URL parameter, wait for the debounced response, and sweep the field in half-open b...
- [How to scrape recipe data with Playwright](how-to-scrape-recipe-data-playwright.md) - read the JSON-LD Recipe node, flatten the three recipeInstructions shapes, split ingredient strings, and parse ISO 8601 times.
- [How to scrape salary and pay scale data with Playwright](how-to-scrape-salary-data-playwright.md) - expand the hidden pay breakdown, keep the range and period intact, tag self-reported figures apart from wage statistics, and carry sample...
- [How to scrape scholarship listings with Playwright](how-to-scrape-scholarship-listings-playwright.md) - parse the deadline into a shape that admits it is often not a date, keep eligibility as prose, and follow through to the provider page.
- [How to scrape school term calendars with Playwright](how-to-scrape-school-term-calendars-playwright.md) - prefer the calendar feed the page links to, expand ranges with an inclusive end, and keep a closure distinct from a staff day.
- [How to scrape flight seat maps with Playwright](how-to-scrape-seat-maps-playwright.md) - Scrape flight seat maps with Playwright by reading each seat's own data attribute instead of its position, joining status and pricing fro...
- [How to scrape server-sent events with Playwright](how-to-scrape-server-sent-events-playwright.md) - the response event fires but body() never returns, so replace window.EventSource in an init script, wrap fetch, and parse text/event-stre...
- [How to scrape shipping rates with Playwright](how-to-scrape-shipping-rates-playwright.md) - a rate is keyed to destination, weight, dimensions and service level, the quote response carries surcharge lines the total hides, and fre...
- [How to scrape size charts with Playwright](how-to-scrape-size-charts-playwright.md) - open the modal that holds the table, record which unit the toggle had active, detect row-versus-column orientation, and mark image-only c...
- [How to scrape software changelogs and release notes with Playwright](how-to-scrape-software-changelogs-playwright.md) - detect the page shape, parse versions into sortable tuples, read the change type from the heading above each bullet, and stop at the vers...
- [How to scrape sortable data tables with Playwright](how-to-scrape-sortable-tables-playwright.md) - read rows once keyed on an identifier, trust aria-sort over a click count, and sort the data yourself instead of the page's own string co...
- [How to scrape spare part compatibility with Playwright](how-to-scrape-spare-part-compatibility-playwright.md) - drive the cascading dropdowns waiting on each level to repopulate, key results on the whole path, and keep the fitment notes.
- [How to scrape stock levels with Playwright](how-to-scrape-stock-levels-playwright.md) - the badge is a bucket, the number lives in the variant endpoint, and a reading without a timestamp and a variant id is not a stock level.
- [How to scrape store locator pages with Playwright](how-to-scrape-store-locator-pages-playwright.md) - seed the search with a point grid instead of postcodes, read the radius endpoint rather than the cards, and dedupe on the store id becaus...
- [How to scrape public tender notices with Playwright](how-to-scrape-tender-notices-playwright.md) - one row per lot, deadlines kept with the offset the response carries, classification-code filters, and a re-scrape that reconciles revisi...
- [How to scrape concert and tour dates with Playwright](how-to-scrape-tour-dates-playwright.md) - key rows on the event URL so a postponement does not vanish, timestamp every status read, and treat a cancelled badge as its own state ra...
- [How to scrape public transport timetables with Playwright](how-to-scrape-transport-timetables-playwright.md) - key every row by route, direction, stop and service day, pin the date, keep 24:15 times and request-stop flags, and store the trip id wit...
- [How to scrape vehicle recall notices with Playwright](how-to-scrape-vehicle-recalls-playwright.md) - key each row to the campaign identifier and its VIN or build-date range, carry the notice revision so amendments update, and budget the V...
- [How to scrape video game prices with Playwright](how-to-scrape-video-game-prices-playwright.md) - key rows on the edition or SKU instead of the title, record region, currency and read time with every price, and keep physical, digital a...
- [How to scrape virtual scrolling tables with Playwright](how-to-scrape-virtual-scrolling-tables-playwright.md) - measure the row pitch, step by less than one window, extract on sight because the nodes are recycled, dedupe on the row id, and stop on t...
- [How to scrape warranty terms with Playwright](how-to-scrape-warranty-terms-playwright.md) - Scrape warranty terms with Playwright by keeping parts, labor, powertrain and battery durations as separate fields, pairing each with its...
- [How to scrape weather station data with Playwright](how-to-scrape-weather-station-data-playwright.md) - Scrape weather station data with Playwright by capturing the observation feed instead of the rendered table, converting timestamps to UTC...
- [How to scrape WebSocket streams with Playwright](how-to-scrape-websocket-streams-playwright.md) - subscribe with page.on('websocket'), classify each frame before parsing, apply the deltas to a snapshot, and stamp every row with a UTC r...
- [How to scrape wine and spirits catalogs with Playwright](how-to-scrape-wine-catalog-playwright.md) - key rows by name, vintage and bottle size, pass the age gate once per context, and normalize critic scores before averaging them.
- [How to Handle a PDF That Opens in a New Tab with Playwright](how-to-handle-pdf-opens-new-tab-playwright.md) - Catch the new page with `context.expect_page()`, then pull the real bytes instead of screenshotting the viewer.
- [Does Playwright Support Firefox Extensions?](does-playwright-support-firefox-extensions.md) - The documented extension API is Chromium-only; loading an .xpi into Firefox means a persistent profile, not a launch flag.
- [Automating an Email OTP / Verification-Link Login with Playwright](automating-email-otp-verification-login-playwright.md) - Poll your own inbox over IMAP mid-login and feed the code back into the same session that requested it.
- [Automating TOTP-Based 2FA Login with Playwright](automating-totp-2fa-login-playwright.md) - Generate a time-based code with pyotp from the same shared secret an authenticator app would scan; no inbox, no polling.
- [How to Scrape a Sandboxed Iframe with Playwright](how-to-scrape-sandboxed-iframe-content-playwright.md) - A `sandbox` attribute can block Playwright's own injected scripts even on a same-origin frame; what the tokens restrict and how to detect it first.
- [Puppeteer scraping examples, and what a real defense does to them](puppeteer-scraping-examples.md) - Six standard patterns as code, then run against an actual detector to show what changes and what does not.
