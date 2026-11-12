---
title: "How to scrape size charts with Playwright"
description: "Scrape size charts with Playwright: open the modal that holds the table, record which unit the toggle had active, detect row-versus-column orientation, and mark image-only charts instead of emitting empty rows."
parent: "Scraping with Playwright"
grand_parent: "Guides"
nav_order: 98
---


# How to scrape size charts with Playwright

To scrape a size chart with Playwright, click the trigger that opens the modal or tab
holding it, wait for the table instead of the trigger, record which unit the toggle had
active at the moment you read the cells, detect whether measurements run along the rows or
down the columns, parse ranges and fractional inches into numbers, and mark image-only
charts as unextractable rather than emitting empty rows.

A size chart looks like the easiest table on a retail page. It is a small grid of numbers
with a header, and the extraction itself is about five lines. Almost every failure happens
before or after that extraction: the grid is not in the HTML you fetched, or the numbers
you read belong to a unit nobody wrote down.

## The chart is not in the document you fetched

Fetch the product URL, parse the HTML, search for a `table`, and you get nothing. That is
the normal case, not a broken selector. The chart lives behind a "Size guide" link, a
"Fit" tab or an accordion, and the markup for it either sits hidden in the DOM or does not
exist until the click happens.

Three variants cover nearly everything. The panel is already in the DOM with
`display: none` and the click only unhides it. The panel is empty and the click fires an
XHR that returns the chart as JSON or as an HTML fragment. Or the panel is an iframe, and
the chart is a separate document with its own styling and its own load timing.

The second variant is the one worth catching deliberately, because the response body is
usually cleaner than anything the rendered grid gives back. If the panel populates from a
request, take the request:
[capturing XHR and API responses](how-to-capture-xhr-api-responses-playwright.md) covers
the response hooks. The panel itself follows the ordinary overlay rules from
[handling popups and modals](how-to-handle-popups-and-modals-playwright.md).

## Open the panel, then wait for the table itself

Waiting for the click to resolve proves nothing. The click resolves the instant the button
accepts it, and the chart can arrive several hundred milliseconds later, or never, if the
trigger opened an empty shell. Wait for the thing you want to read.

Every method below is stock Playwright, used as documented upstream. The library returns a
real `Browser`, so there is no special API to learn for this.

```python
import re
from invisible_playwright import InvisiblePlaywright

WANTED = re.compile(r"size\s*(guide|chart)|fit\s*guide", re.I)

with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    page.goto("https://example.com/p/12345", wait_until="domcontentloaded")

    trigger = page.get_by_role("button", name=WANTED)
    if trigger.count() == 0:
        trigger = page.get_by_role("link", name=WANTED)
    trigger.first.click()

    panel = page.get_by_role("dialog")
    panel.wait_for(state="visible")

    # some retailers render the chart in its own frame
    if panel.locator("iframe").count():
        root = page.frame_locator("[role=dialog] iframe")
    else:
        root = panel

    root.locator("table").first.wait_for(state="visible")
```

`root` is now either the dialog or the frame, and the rest of the code does not care which.
That indirection is worth the three lines, because the iframe case is common enough to hit
you on the second retailer and it fails in a confusing way: the selector is right, the
element is real, and it is in another document. The general treatment is in
[scraping iframe content](how-to-scrape-iframe-content-playwright.md).

## Capture the unit system, or the numbers mean nothing

A size chart is a table with a unit system attached, and the unit is not in the table. It
sits in a toggle above it, usually a pair of buttons marked cm and in, and clicking the
toggle rewrites the same cells in place. Both unit systems are almost never in the DOM at
once. So a cell reading `91` is 91 centimetres or 91 inches depending entirely on a piece
of state you did not capture, and 91 inches is not a chest measurement of any human being.

Read the toggle state first, then read the grid, then label the grid with the unit you
just read. If you want both systems, click the other button and wait for a known cell to
actually change before the second read.

```python
def active_unit(panel):
    for label in ("cm", "in"):
        btn = panel.get_by_role("button", name=re.compile(rf"^\s*{label}\b", re.I))
        if btn.count() and btn.first.get_attribute("aria-pressed") == "true":
            return label
    return "cm" if "cm" in panel.inner_text().lower() else "in"

def read_grid(root):
    return root.locator("table tr").evaluate_all(
        "rows => rows.map(r => Array.from("
        "r.querySelectorAll('th, td'), c => c.innerText.trim()))"
    )

unit = active_unit(panel)
grids = {unit: read_grid(root)}

other = "in" if unit == "cm" else "cm"
toggle = panel.get_by_role("button", name=re.compile(rf"^\s*{other}\b", re.I))
if toggle.count():
    probe = root.locator("table tr").nth(1).locator("td").first
    before = probe.inner_text()
    toggle.first.click()
    page.wait_for_function(
        "([el, old]) => el.innerText.trim() !== old",
        arg=[probe.element_handle(), before],
    )
    grids[other] = read_grid(root)
```

Do not substitute a fixed sleep for that `wait_for_function`. The toggle mutates text
nodes in place, so a read that lands too early returns the old numbers under the new label,
which is worse than a crash: it is a silent unit swap that survives every downstream check
you have. Also resist converting one system into the other and storing only the result.
Retailers round each system independently, and a converted value stops matching the number
printed on the page.

## Detect the orientation instead of assuming it

Two layouts are both normal. Measurement types down the first column with sizes across the
header, or sizes down the first column with measurement names across the header. Same data,
transposed, and no attribute tells you which one you are looking at. Guess, and half your
retailers produce a table where the chest measurement is called "M".

Classify both candidate axes and pick the one that looks more like a set of size labels.
Size labels are short and highly patterned: letters from a fixed set, plain integers, an
integer with a half, a slash pair. Measurement names are words.

```python
SIZE_TOKEN = re.compile(
    r"^(xx?s|s|m|l|xx?l|[2-6]xl|one size|\d{1,3}([.,]5)?|\d{1,3}/\d{1,3})$", re.I
)

def size_score(cells):
    values = [c.strip() for c in cells if c and c.strip()]
    if not values:
        return 0.0
    return sum(1 for c in values if SIZE_TOKEN.match(c)) / len(values)

def orient(grid):
    """Return a grid whose header row is sizes and whose first column is measurements."""
    header = grid[0][1:]
    first_col = [row[0] for row in grid[1:] if row]
    if size_score(header) >= size_score(first_col):
        return grid
    return [list(col) for col in zip(*grid)]
```

Store the decision alongside the data. When a row later looks wrong, the recorded
orientation tells you in one glance whether the parser transposed a grid it should have
left alone. The one-call read that feeds this is the same pattern used for any grid, and
[scraping HTML tables](how-to-scrape-html-tables-playwright.md) has the reasoning behind
pulling the whole thing in a single `evaluate_all`.

## Parse ranges and fractional inches with stated rules

Cells are not numbers. Centimetre charts commonly give a range, `86-91`, because a size
covers a band. Inch charts commonly give a fraction, `34 1/2` or `34` with a vulgar
fraction glyph. Some locales use a comma decimal separator. Some use a unicode dash that
looks identical to a hyphen and is not one.

Two rules, stated so the data has a contract. First: a range keeps both ends, never a
midpoint, and a single value stores as low equal to high. Second: keep the raw cell text
next to the parsed numbers, so any disagreement is checkable without a re-crawl.

```python
VULGAR = {"½": 0.5, "¼": 0.25, "¾": 0.75, "⅓": 1/3, "⅛": 0.125}
# hyphen plus U+2012, U+2013, U+2014: separators that all render like a hyphen
DASHES = "".join(chr(c) for c in (0x2D, 0x2012, 0x2013, 0x2014))
RANGE = re.compile(rf"\s*(?:[{DASHES}]|\bto\b)\s*")

def to_number(token):
    token = token.strip().replace(",", ".")
    for glyph, value in VULGAR.items():
        if token.endswith(glyph):
            whole = token[: -len(glyph)].strip()
            return (float(whole) if whole else 0.0) + value
    parts = token.split()
    if len(parts) == 2 and "/" in parts[1]:          # "34 1/2"
        num, den = parts[1].split("/")
        return float(parts[0]) + float(num) / float(den)
    if "/" in token:                                  # "1/2"
        num, den = token.split("/")
        return float(num) / float(den)
    return float(token)

def parse_cell(text):
    """'86-91' -> (86.0, 91.0). '34 1/2' -> (34.5, 34.5). '' -> (None, None)."""
    text = (text or "").strip()
    if not text:
        return (None, None)
    try:
        numbers = [to_number(p) for p in RANGE.split(text) if p]
    except (ValueError, ZeroDivisionError):
        return (None, None)
    return (min(numbers), max(numbers)) if numbers else (None, None)
```

An unparseable cell returns nulls and keeps its raw text. It does not raise, and it does
not become a zero. A zero in a measurement column is indistinguishable from a real reading
once it reaches storage.

## When the chart is an image, say so instead of emitting empty rows

A large share of charts are pictures. A brand ships a PNG or a JPEG of its own grid, drops
it in the panel, and there is no table element anywhere. The table scraper runs, finds
zero rows, and writes nothing. Downstream, "no rows" reads as "this product has no chart",
which is false and unrecoverable later.

Detect the case and record it as what it is.

```python
def chart_source(root):
    if root.locator("table tr").count() > 1:
        return {"chart_format": "table", "extractable": True, "image_url": None}
    images = root.locator("img, picture img, canvas")
    if images.count():
        first = images.first
        return {
            "chart_format": "image",
            "extractable": False,
            "image_url": first.get_attribute("src") or first.get_attribute("srcset"),
        }
    return {"chart_format": "unknown", "extractable": False, "image_url": None}
```

Write one record carrying `chart_format: "image"`, `extractable: false` and the image URL,
with no measurement rows at all. That record is honest and it is actionable: someone can
queue those URLs for a separate pass. If a later pass does read the pixels, mark the rows
`source: "ocr"` and keep them distinguishable from parsed cells forever. OCR on a
low-resolution chart misreads a 6 as an 8 often enough that mixing the two sources
quietly poisons the dataset.

## One chart per product, never one chart per crawl

The chart is keyed to the product, not to the site. A retailer ships different grids for
shirts, trousers, footwear and outerwear, and the trouser grid carries an inseam row the
shirt grid has never heard of. On a marketplace it goes further, because each seller or
brand supplies its own chart, and two listings in the same category on the same domain
disagree by several centimetres for the same letter size.

So caching one chart for the whole crawl is not an optimisation. It is a mislabelling
step that runs at full speed. Cache on the chart's own identity instead: the trigger's
`href` or `data-chart-id` when the page exposes one, otherwise a hash of the header row
plus the first measurement column, scoped to the brand and category you already extract on
the product page. That key is cheap, it survives a redesign that renames the CSS classes,
and it collapses correctly when two products genuinely share a grid.

The brand and category fields come from the product page itself, which you are already
parsing:
[scraping ecommerce product pages](how-to-scrape-ecommerce-product-pages-playwright.md)
covers where those live and how to keep them stable.

## The row shape that survives all four variations

One row per size and measurement, with the unit and the provenance attached to every row
rather than to the file. Fields written once at the top of a CSV get separated from their
data the first time somebody merges two exports.

```python
def rows_for_chart(product_key, brand, category, unit, grid, meta):
    grid = orient(grid)
    sizes = [s.strip() for s in grid[0][1:]]
    rows = []
    for raw_row in grid[1:]:
        measurement = raw_row[0].strip().lower()
        for size, cell in zip(sizes, raw_row[1:]):
            low, high = parse_cell(cell)
            rows.append({
                "product_key": product_key,
                "brand": brand,
                "category": category,
                "size": size,
                "measurement": measurement,
                "unit": unit,                       # captured, never inferred later
                "value_low": low,
                "value_high": high,
                "raw": (cell or "").strip(),
                "chart_format": meta["chart_format"],
            })
    return rows
```

Every row now answers the four questions that break size data: which product, which unit,
which measurement, and whether the number came from a parsed cell or from somewhere less
trustworthy. A row that cannot answer them is not a measurement, it is a number.

## Conclusion

Size charts fail in ways that look like parser bugs and are not. The grid is behind a
modal or a tab, so wait for the table rather than the click. The unit lives in a toggle
that rewrites the same cells, so capture it before the read and keep both ends of every
range. Orientation flips between retailers, so detect it. Charts are sometimes images, so
record that fact instead of writing zero rows that read as an absent chart. And the grid
belongs to the product, not the domain, which makes a crawl-wide cache a mislabelling
machine. Get those five right and the extraction really is five lines.

## Short answers to the questions that lead here

**Why does my scraper find no size chart in the HTML?** Because it is not there yet. The
chart sits behind a "Size guide" trigger, and the panel is either hidden, populated by an
XHR on first open, or rendered inside an iframe. Click the trigger, then wait for the
table.

**Which unit are the scraped numbers in?** Read the toggle before you read the cells,
usually via `aria-pressed` on the cm and in buttons, and store the unit on every row. The
toggle rewrites the same cells, so the DOM alone will not tell you afterwards.

**Should I convert inches to centimetres and store one column?** No. Retailers round each
system separately, so a converted value stops matching the printed page. Capture each
system by toggling and reading twice, and label both.

**How should ranges like 86-91 be stored?** As two numbers, a low and a high, with the raw
text kept beside them. A single value stores as low equal to high. Collapsing a range to a
midpoint destroys information that nothing downstream can rebuild.

**What if the size chart is an image?** Record `chart_format: "image"`, `extractable:
false` and the image URL, and emit no measurement rows. Empty rows are indistinguishable
from a product that has no chart, which makes the gap invisible.

**Is one chart enough for a whole site?** Only if you enjoy wrong data. Charts vary by
category and, on marketplaces, by brand. Key the cache to the chart's own id or to a hash
of its header row, scoped to brand and category.

## Sources

- Playwright Python API, read from the upstream documentation and retrieved 2026-08-28:
  [`page.get_by_role`](https://playwright.dev/python/docs/api/class-page#page-get-by-role),
  [`page.frame_locator`](https://playwright.dev/python/docs/api/class-page#page-frame-locator),
  [`locator.evaluate_all`](https://playwright.dev/python/docs/api/class-locator#locator-evaluate-all),
  [`locator.wait_for`](https://playwright.dev/python/docs/api/class-locator#locator-wait-for),
  [`locator.get_attribute`](https://playwright.dev/python/docs/api/class-locator#locator-get-attribute),
  [`page.wait_for_function`](https://playwright.dev/python/docs/api/class-page#page-wait-for-function).
- Playwright's [other locators and ARIA roles](https://playwright.dev/python/docs/other-locators)
  guide, for the `dialog` role used to scope the panel. Retrieved 2026-08-28.

**See also:** [handling popups and modals](how-to-handle-popups-and-modals-playwright.md)
for the panel itself,
[capturing XHR and API responses](how-to-capture-xhr-api-responses-playwright.md) for the
chart that arrives as JSON, and
[scraping HTML tables](how-to-scrape-html-tables-playwright.md) for the one-call grid read
the parsers above depend on.

---

*Written while maintaining [invisible_playwright](https://github.com/oss-invisible/playwright),
a Firefox patched at the C++ level driven by stock Playwright. The unit toggle is the one
I got wrong first: the numbers looked fine, and half of them were inches.*
