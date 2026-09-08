---
title: "How to scrape nutrition labels with Playwright"
description: "Scrape nutrition labels with Playwright: map every column to its per 100 g, per serving or per package basis, split the serving size, keep kilojoules and kilocalories apart, and mark image-only panels unextractable."
parent: "Scraping with Playwright"
grand_parent: "Guides"
nav_order: 125
---


# How to scrape nutrition labels with Playwright

To scrape nutrition labels with Playwright, attach a basis to every number before you
store it: map each column header to per 100 g, per serving or per package, split the
serving size into a household measure and a metric one, keep kilojoules and kilocalories
in separate fields, preserve the less-than qualifier instead of parsing it to zero, and
record an image-only panel as unextractable instead of writing empty rows.

A nutrition panel is a small grid, and reading the grid is the easy part. The hard part is
that almost every number on it means nothing on its own. `12` is not a fact. Twelve grams
of fat per 100 grams, or per a 30 gram serving, or per a bag holding two and a half
servings, are three different facts, and the panel prints two of them side by side under
headers you have to read.

So the failures here are not selector failures. The extraction succeeds, the rows look
clean, the types are right, and the dataset is quietly wrong in a way that survives every
check you run on it afterwards. This page is about where that happens and what to store
instead.

## Every number carries a basis, or it carries nothing

Per 100 g, per serving and per package are three different numbers describing the same
nutrient on the same label. A value that arrives without its basis cannot be compared to
any other value, and nothing downstream repairs it. The ratio between those columns is the
serving size, which a scraper that dropped the basis has almost always dropped too.

On a product page the panel is usually collapsed behind a "Nutrition" accordion or a tab,
so it sits in the DOM unrendered, or it does not exist until the header is clicked.
[Scraping accordion and tab content](how-to-scrape-accordion-and-tab-content-playwright.md)
covers opening it and waiting for the panel, not for the click.

The basis lives in the header row and never in the cell. Read the header first, classify
each column, and refuse the panel when nothing classifies. Refusing is the useful
behaviour, because a default of per serving is a guess that will be right often enough to
look like it works.

```python
import re
from invisible_playwright import InvisiblePlaywright

BASIS = [
    ("per_100g",    re.compile(r"per\s*100\s*g|/\s*100\s*g", re.I)),
    ("per_100ml",   re.compile(r"per\s*100\s*ml|/\s*100\s*ml", re.I)),
    ("per_serving", re.compile(r"per\s*serv|per\s*portion|each\s*serving", re.I)),
    ("per_package", re.compile(r"per\s*(pack|package|container|bottle)|whole\s*pack", re.I)),
    ("percent_ri",  re.compile(r"%|\bRI\b|daily\s*value|\bDV\b", re.I)),
]

def basis_of(header):
    for name, pattern in BASIS:
        if pattern.search(header or ""):
            return name
    return None

with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    page.goto("https://example.com/p/12345", wait_until="domcontentloaded")

    panel = page.locator("table").filter(
        has_text=re.compile(r"energy|calorie", re.I)
    ).first
    panel.wait_for(state="visible")

    grid = panel.locator("tr").evaluate_all(
        "rows => rows.map(r => Array.from("
        "r.querySelectorAll('th, td'), c => c.innerText.trim()))"
    )

    columns = [basis_of(h) for h in grid[0][1:]]
    if not any(columns):
        raise ValueError("no basis in the header; do not guess one")
```

The percent column is in that list so it gets recognised and then kept out of the amount
fields. A reference intake column reads `12` beside a cell reading `6.0 g`, and both are
correct: one is a percentage of a reference figure for an average adult, the other is a
mass. Merge them and the grams column is wrong by a factor that changes per nutrient. The
single-call grid read is the same one any table wants, and
[scraping HTML tables](how-to-scrape-html-tables-playwright.md) has the reasoning behind
pulling the whole thing in one `evaluate_all`.

Structured data is worth checking before any of this, with one caveat that decides how far
it takes you. Where a page ships a `NutritionInformation` node, its fields are strings with
the units inside them, and the node is defined per serving. It has no basis field because
it only has one basis, so it can confirm your per-serving column and can never give you the
per 100 g one.
[Extracting JSON-LD structured data](how-to-extract-json-ld-structured-data-playwright.md)
covers reading the node itself.

## Serving size is a quantity, a unit and a household measure

`1 cup (240ml)` is two measurements, not one. The household measure comes first and it does
not convert: a cup of dense syrup and a cup of puffed cereal are the same volume and
nothing like the same mass, so the conversion factor is a property of the product. The
metric figure in the parentheses is the one that scales, and it is the only route from a
per-serving column to a per 100 g one.

Store four fields, a quantity and a unit for each system. Keeping the raw string is not
enough for arithmetic, and keeping only a converted gram figure throws away the measure the
shopper actually reads.

Per package needs a second value the panel prints somewhere else, usually as
`Servings per container: about 2.5`. That `about` is on the label because packages do not
divide into whole servings, so parse the count as a float and keep its raw text. A per
package column is not always the serving column times the count either, because some labels
compute it and then round again.

```python
GROUPED = re.compile(r"^\d{1,3}(?:,\d{3})+$")
AMOUNT = r"(?P<qty>\d+(?:[.,]\d+)?(?:\s+\d+/\d+)?|\d+/\d+)"
METRIC = re.compile(rf"\(\s*{AMOUNT}\s*(?P<unit>g|ml|kg|l|oz)\s*\)", re.I)
HOUSEHOLD = re.compile(rf"^\s*(?:about\s+)?{AMOUNT}\s*(?P<unit>[A-Za-z][A-Za-z ]*?)\s*(?:\(|$)", re.I)

def to_number(token):
    token = (token or "").strip()
    if not token:
        return None
    # "1,046" is a group separator, "1,5" is a decimal point. One replace ruins one of them.
    token = token.replace(",", "") if GROUPED.match(token) else token.replace(",", ".")
    if "/" in token:
        whole, _, fraction = token.rpartition(" ")
        num, den = fraction.split("/")
        return (float(whole) if whole else 0.0) + float(num) / float(den)
    return float(token)

def parse_serving(text):
    """'1 cup (240ml)' -> household 1 cup, metric 240 ml."""
    raw = (text or "").strip()
    out = {"household_qty": None, "household_unit": None,
           "metric_qty": None, "metric_unit": None, "raw": raw}
    hh = HOUSEHOLD.search(raw)
    if hh:
        out["household_qty"] = to_number(hh.group("qty"))
        out["household_unit"] = (hh.group("unit") or "").strip().lower() or None
    metric = METRIC.search(raw)
    if metric:
        out["metric_qty"] = to_number(metric.group("qty"))
        out["metric_unit"] = metric.group("unit").lower()
    return out
```

The comma rule inside `to_number` is there because one panel prints both kinds of comma.
`1,046 kJ` uses it to group thousands and `1,5 g` uses it as a decimal point, and a blanket
`replace(",", ".")` turns the first into 1.046 kilojoules without raising anything at all.
The general treatment of locale-dependent number text is in
[cleaning scraped prices and dates](how-to-clean-scraped-prices-and-dates-playwright.md).

## Energy arrives twice, in kilojoules and kilocalories

Many regions require both units. They show up in one cell as `1046 kJ / 250 kcal`, or on
two consecutive rows that are both labelled Energy. Take whichever number appears first and
you build a column holding kilojoules for some products and kilocalories for others.

That column never looks broken. The two units differ by a factor of about 4.2, so the mixed
rows read as unusually energy-dense foods rather than as a unit error, and every range check
you write will pass. It surfaces months later, when somebody ranks products by energy and
the ranking is nonsense.

Keep two named fields and fill only what the label printed. Do not derive one from the other
to close a gap. Each unit is rounded independently before publication, so a computed
kilocalorie figure and a printed one disagree, and once both live in the same column nothing
tells them apart again.

```python
ENERGY = re.compile(rf"{AMOUNT}\s*(?P<unit>kJ|kcal|cal|calories)\b", re.I)

def parse_energy(cell):
    raw = (cell or "").strip()
    out = {"energy_kj": None, "energy_kcal": None, "raw": raw}
    for match in ENERGY.finditer(raw):
        unit = match.group("unit").lower()
        value = to_number(match.group("qty"))
        if unit == "kj":
            out["energy_kj"] = value
        else:
            out["energy_kcal"] = value   # "cal" and "calories" on a panel mean kcal
    return out
```

The bare word `Calories` on a nutrition panel means kilocalories, which is why it maps to
the kcal field. That is a labelling convention rather than a physics one, and reading
`250 Calories` as 250 calories puts the value off by a thousand.

## Less than 0.5 g and 0 g are different statements

`<0.5 g` says the manufacturer measured something and it landed below the level the rules
allow to be declared. `0 g` says there is none. `float()` on the visible text flattens both
to 0.0, and a regex that strips non-numeric characters turns the first into 0.5, which is
worse than flattening it.

Keep the qualifier in its own field and let the consumer pick a policy. A record of 0.5 with
qualifier `less_than` is honest. A record of 0.0 with no qualifier is a claim the label did
not make. `Trace`, `Nil` and a bare dash belong in that same field.

Units need the same care, and one of them is a byte problem. Micrograms appear on labels as
`mcg`, as U+00B5 MICRO SIGN followed by g, and as U+03BC GREEK SMALL LETTER MU followed by
g. The last two render identically and are different codepoints, so a unit table keyed on
one silently fails to match the other and the row loses its unit. NFKC normalisation folds
the micro sign onto the Greek letter, which takes three spellings down to two.

```python
import unicodedata

UNITS = {"g", "mg", "μg", "mcg", "ml", "kj", "kcal"}
QUALIFIED = re.compile(r"^\s*(?:<|less\s+than|under)\s*", re.I)
TRACE = re.compile(r"^\s*traces?\b", re.I)
ABSENT = re.compile(r"^\s*(?:n/?a|nil|-|not\s+detected)?\s*$", re.I)

def parse_amount(cell):
    raw = (cell or "").strip()
    text = unicodedata.normalize("NFKC", raw)      # U+00B5 folds to U+03BC here
    out = {"value": None, "unit": None, "qualifier": None, "known_unit": False, "raw": raw}
    if ABSENT.match(text):
        return out
    if TRACE.match(text):
        out["qualifier"] = "trace"
        return out
    if QUALIFIED.match(text):
        out["qualifier"] = "less_than"
        text = QUALIFIED.sub("", text)
    match = re.match(rf"{AMOUNT}\s*(?P<unit>[a-zμ]+)?", text, re.I)
    if not match:
        return out
    out["value"] = to_number(match.group("qty"))
    out["unit"] = (match.group("unit") or "").lower() or None
    out["known_unit"] = out["unit"] in UNITS
    return out
```

Feed this from `inner_text`, not `inner_html`. The less-than character is written as `&lt;`
in the source of plenty of panels, and the text call has already decoded it for you. Reading
the HTML for amounts means writing an entity decoder you do not need.

## The macros will not add up to the stated energy, and that is correct

Multiply the printed grams by 4, 4 and 9 for protein, carbohydrate and fat and the total
does not match the printed energy. This is not a scraping bug and it is not a bad label.
Labelling rules in most regions require the published values to be rounded, sometimes to
steps as coarse as the nearest gram, and the energy figure is rounded separately from the
components it was computed from.

Rounding is proportionally largest on small servings. A validator demanding agreement
therefore rejects correct data with a bias: single-serve items and low-calorie products fail
most often. That is the worst possible shape for a filter, because the rows it throws away
are not a random sample of the rows you collected.

A second reason survives even without rounding. Regions disagree about which components
carry energy and at what factor. Fibre counts at a low factor under some rules and not at
all under others, and polyols and organic acids carry factors of their own. One arithmetic
does not describe every label.

So compute the gap, store it, and flag rather than reject. Set the tolerance wide, around 25
percent, and treat the check as a detector for column mix-ups instead of a nutrition audit.
The errors worth catching are a per 100 g value landing in a per-serving field, or a
kilojoule figure in a kilocalorie one, and those open gaps of a factor of three or four.
Rounding opens gaps of a few percent. At that threshold the two never get confused.

## When the panel is an image, record that instead of empty rows

A large share of labels are pictures. The manufacturer supplies a photograph or a rendered
graphic of its own panel, the page drops it in, and there is no table element anywhere. The
table parser runs, finds nothing and writes nothing, and downstream that is
indistinguishable from a product carrying no nutrition information at all.

Detect the case and write one record that says so. It carries the format, an extractable
flag and the image URL, and it carries zero nutrient rows. Someone can queue those URLs for
a separate pass later, which is possible only because the record exists.

```python
def panel_source(container):
    rows = container.locator("table tr")
    if rows.count() > 1 and re.search(r"energy|calorie", container.inner_text(), re.I):
        return {"panel_format": "table", "extractable": True, "image_url": None}
    images = container.locator("img, picture img, canvas")
    if images.count():
        first = images.first
        return {
            "panel_format": "image",
            "extractable": False,
            "image_url": (first.get_attribute("src")
                          or first.get_attribute("data-src")
                          or first.get_attribute("srcset")),
        }
    return {"panel_format": "absent", "extractable": False, "image_url": None}
```

The `data-src` fallback matters more here than on most images. A panel image sits low on a
product page and is nearly always lazy loaded, so `src` holds a placeholder until it scrolls
into view, and the URL you store is the placeholder.
[Scraping lazy loaded images](how-to-scrape-lazy-loaded-images-playwright.md) covers forcing
the real one out. The same record shape works for any spec panel shipped as a picture, and
[scraping size charts](how-to-scrape-size-charts-playwright.md) uses it for the size grid.

This is also where the approach stops, and the honest thing is to say so. Nothing about a
real browser or a clean session reads pixels, and no stealth layer has an opinion about a
JPEG. If a later pass does run OCR over these, mark those rows with their own source value
and never merge them with parsed ones. A misread decimal point on a fat value is a factor of
ten, and it looks entirely plausible in a spreadsheet.

## Allergens are their own block, sometimes only a bold run

Allergen information is legally distinct from the ingredient list, and it turns up in two
shapes. Sometimes it is a separate statement, `Contains: milk, soy`, in an element of its
own. Sometimes it exists only as emphasis inside the ingredient string, where the
allergenic words are bold and nothing else marks them.

The second shape is the one that vanishes. `inner_text()` returns the ingredient sentence
with the bold runs flattened into ordinary words, so the declaration is gone and the
extraction reports no allergens for a product that declares four. Read `inner_html()`, or
query `b, strong` inside the ingredients element, and treat the emphasised runs as the
declaration they legally are.

Then keep two fields rather than one. `Contains` is a statement about what is in the
product. `May contain` and `produced in a facility that also handles` are precautionary
statements about what might have got in. Folding them together makes a product look like it
contains something it does not, and that is the direction of error that matters most for
anyone reading this data because of an allergy.

## Ingredient order is data, not noise

The ingredient list is ordered by descending weight. Position is therefore a measurement,
a coarse one about composition, and the first entry is the largest component by mass. Store
the list as a set, alphabetise it for readability, or dedupe it, and that measurement is
gone with no way back.

Parentheses carry a second ordering. `chocolate chips (sugar, cocoa butter, milk solids)` is
one top-level ingredient with three sub-ingredients, themselves in descending order within
their parent. Split the string on every comma and you get four top-level entries, one of
which claims the product contains sugar directly at position two. The label does not say
that.

Split at depth zero only, keep the parent link, and number both levels.

```python
CHILDREN = re.compile(r"^(?P<name>[^(\[]+)[(\[](?P<inner>.*)[)\]]\s*$")

def split_at_depth_zero(text):
    parts, buffer, depth = [], [], 0
    for char in text:
        if char in "([":
            depth += 1
        elif char in ")]":
            depth = max(0, depth - 1)
        if char == "," and depth == 0:
            parts.append("".join(buffer))
            buffer = []
        else:
            buffer.append(char)
    parts.append("".join(buffer))
    return [p.strip() for p in parts if p.strip()]

def ingredient_rows(container):
    node = container.locator(".ingredients").first
    declared = [t.strip(" ,.;") for t in node.locator("b, strong").all_inner_texts()]
    rows = []
    for position, entry in enumerate(split_at_depth_zero(node.inner_text()), start=1):
        match = CHILDREN.match(entry)
        name = (match.group("name") if match else entry).strip()
        rows.append({"name": name, "position": position, "parent": None})
        if match:
            children = split_at_depth_zero(match.group("inner"))
            for child_position, child in enumerate(children, start=1):
                rows.append({"name": child, "position": child_position, "parent": name})
    return rows, declared
```

One caveat keeps those positions honest. Some regions allow a clause such as
`contains 2 percent or less of` partway down the list, and strict ordering stops applying
after it. Record the position of that marker when it appears, so a consumer knows which part
of the list the ordering guarantee covers and which part it does not.

## Conclusion

Nutrition panels break in ways that leave the parser looking healthy. The number extracts,
the type is right, and the value is unusable because the basis it belonged to sat in a
header nobody read. Map every column to per 100 g, per serving or per package, and refuse
the panel when none of them match. Split the serving size into a household measure and a
metric one, because only the metric one scales. Keep kilojoules and kilocalories apart and
never derive one from the other. Keep the less-than qualifier instead of a zero. Flag the
energy gap at a wide tolerance rather than rejecting rounded labels that are perfectly
correct. Record an image panel as an image. And leave the ingredient list in the order the
label printed it, because that order is the only quantity information the list carries.

## Short answers to the questions that lead here

**Why can I not compare two nutrition rows I scraped?** Because one is probably per 100 g
and the other per serving, and the basis was in a column header that never made it into the
row. Store the basis on every row, and refuse a panel whose header does not name one.

**What does "1 cup (240ml)" mean for my data model?** Four fields: a household quantity and
unit, and a metric quantity and unit. The household measure does not convert across products,
so the metric figure in the parentheses is the only one you can scale a column with.

**Should I convert kilojoules to kilocalories and keep one column?** No. Each unit is
rounded independently before it is printed, so a derived value and a printed value disagree.
Keep two named fields and leave the one the label omitted as null.

**How do I store "less than 0.5 g"?** As a value of 0.5 with a qualifier of `less_than` and
the raw text beside it. It is a different statement from `0 g`, and a float parse flattens
both into a zero that nothing downstream can tell apart.

**My validator says the macronutrients do not match the calories.** They will not. Published
values are rounded before printing and the energy figure is rounded separately, and regions
disagree on which components carry energy at all. Flag a gap above roughly 25 percent, and
never reject on a small one.

**The nutrition label is an image. What now?** Write one record with the format, an
extractable flag of false and the image URL, and no nutrient rows. Empty rows read as a
product with no label, which hides the gap permanently.

**Where are the allergens if the page has no allergen block?** Bolded inside the ingredient
string. `inner_text()` flattens that emphasis away, so read `inner_html()` or query
`b, strong` in the ingredients element, and keep `Contains` and `May contain` in separate
fields.

## Sources

- Playwright Python API, read from the upstream documentation and retrieved 2026-08-28:
  [`locator.filter`](https://playwright.dev/python/docs/api/class-locator#locator-filter),
  [`locator.evaluate_all`](https://playwright.dev/python/docs/api/class-locator#locator-evaluate-all),
  [`locator.wait_for`](https://playwright.dev/python/docs/api/class-locator#locator-wait-for),
  [`locator.inner_text`](https://playwright.dev/python/docs/api/class-locator#locator-inner-text),
  [`locator.inner_html`](https://playwright.dev/python/docs/api/class-locator#locator-inner-html),
  [`locator.all_inner_texts`](https://playwright.dev/python/docs/api/class-locator#locator-all-inner-texts),
  [`locator.get_attribute`](https://playwright.dev/python/docs/api/class-locator#locator-get-attribute).
  Retrieved 2026-08-28.
- Playwright's [locator guide](https://playwright.dev/python/docs/locators), for the
  re-resolving semantics the panel and container locators above rely on. Retrieved
  2026-08-28.
- Python's `unicodedata` normalisation forms, for the compatibility decomposition that maps
  U+00B5 MICRO SIGN onto U+03BC GREEK SMALL LETTER MU under NFKC.
- The schema.org `NutritionInformation` type, whose fields are unit-bearing strings and
  whose values are defined per serving, which is why it cannot supply a per 100 g column.

**See also:** [scraping HTML tables](how-to-scrape-html-tables-playwright.md) for the
one-call grid read every parser here depends on,
[scraping size charts](how-to-scrape-size-charts-playwright.md) for the same unit and
image-only problems on a spec grid,
[cleaning scraped prices and dates](how-to-clean-scraped-prices-and-dates-playwright.md) for
locale-dependent number text, and
[scraping ecommerce product pages](how-to-scrape-ecommerce-product-pages-playwright.md) for
the product fields the panel has to be keyed to.

---

*Written while maintaining [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level driven by stock Playwright. The basis column is the one I
got wrong first: every number parsed cleanly, and half of them were per 100 g sitting in a
per-serving field.*
