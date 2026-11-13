---
title: "How to scrape recipe data with Playwright"
description: "Scrape recipe data with Playwright: read the JSON-LD Recipe node, flatten the three recipeInstructions shapes, split ingredient strings, and parse ISO 8601 times."
parent: "Scraping with Playwright"
grand_parent: "Guides"
nav_order: 122
---


# How to scrape recipe data with Playwright

To scrape recipe data with Playwright, read the JSON-LD `Recipe` node rather than the
rendered article: pull every `script[type="application/ld+json"]` block, keep the nodes
whose `@type` includes `Recipe`, and then normalise the four fields that are never the
shape you expect. Those are `recipeInstructions`, which has three legal forms,
`recipeIngredient`, which is free text, `recipeYield`, which is not a number, and the
duration fields, which are ISO 8601 strings the standard library cannot parse.

Recipe is one of the best-supported schema.org types on the web, because publishers get a
rich search result for emitting it and they know it. That has a convenient consequence for
anyone extracting it. The JSON-LD block on a recipe page is almost always richer and
cleaner than the page it sits on. Check it first and the job is usually done.

What is left is a normalising layer, and it is where the work actually is. The schema is
permissive in exactly the places you need it strict: half the interesting fields are
declared as "Text or something else", so a parser that assumes one shape breaks on the
other two. This page is that layer, field by field, with the rules stated instead of
assumed.

## Read the Recipe node before you touch the DOM

The block is in the initial HTML on nearly every recipe page, so you do not need
`networkidle` and you do not need a selector for anything visible. Load with
`domcontentloaded`, read every `ld+json` script, flatten `@graph`, and filter by type. The
general mechanics of that are covered in
[extracting JSON-LD structured data](how-to-extract-json-ld-structured-data-playwright.md);
the part specific to recipes is at the end of this block.

```python
import json
from invisible_playwright import InvisiblePlaywright

def as_list(value):
    if value is None:
        return []
    return value if isinstance(value, list) else [value]

def read_ld_json(page):
    nodes = []
    for raw in page.locator('script[type="application/ld+json"]').all_text_contents():
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue
        for block in as_list(data):
            if isinstance(block, dict):
                nodes.extend(as_list(block.get("@graph")) or [block])
    return nodes

def find_recipes(nodes):
    # every Recipe node, not the first one: a roundup page carries many
    return [n for n in nodes if "Recipe" in as_list(n.get("@type"))]

with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    page.goto("https://example.com/recipes/123", wait_until="domcontentloaded")
    recipes = find_recipes(read_ld_json(page))
```

`find_recipes` returns a list on purpose. A "30 weeknight dinners" post carries thirty
`Recipe` nodes, and a page whose comment section marks up user submissions carries more
than one too. Taking `[0]` works on a single-recipe page and quietly returns the wrong
recipe everywhere else. Count what you got and decide, rather than indexing.

## The markup is what keeps the story out of your data

Recipe pages are heavy with content that is not the recipe. There is the anecdote about
the author's grandmother, the affiliate block, the "jump to recipe" bar, a related-posts
grid, a nutrition disclaimer, and a comment thread that often contains other people's
variations. Extract from the article body and you inherit all of it, then spend your time
writing classifiers to get back out.

The `Recipe` node contains the recipe and nothing else, which is the second reason to read
it. It is not only cleaner, it is bounded. If you do need the visible prose for some other
purpose, that is a different job with a different tool:
[extracting clean article text](how-to-extract-clean-article-text-playwright.md) is the
readability pass, and it is the wrong instrument for a structured field.

## recipeInstructions has three legal shapes

The field can be a plain string, a list of strings, or a list of `HowToStep` objects, and
those objects are sometimes grouped inside `HowToSection` entries for recipes with a
sauce and a base. Write one recursive walker that accepts all of them and returns
`(section, step)` pairs, so downstream code sees one shape forever.

```python
import re

TAG = re.compile(r"<[^>]+>")

def clean(text):
    return TAG.sub(" ", text or "").replace("\xa0", " ").strip()

def normalise_instructions(value):
    return list(_walk(value, section=""))

def _walk(value, section):
    if value is None:
        return
    if isinstance(value, str):
        # one blob: split on markup breaks and newlines, never on a full stop
        for part in re.split(r"<br\s*/?>|\n", value):
            part = clean(part)
            if part:
                yield section, part
        return
    if isinstance(value, list):
        for entry in value:
            yield from _walk(entry, section)
        return
    if isinstance(value, dict):
        if "HowToSection" in as_list(value.get("@type")):
            name = clean(value.get("name", ""))
            yield from _walk(value.get("itemListElement"), name or section)
            return
        if value.get("itemListElement"):
            yield from _walk(value["itemListElement"], section)
            return
        text = clean(value.get("text") or value.get("name") or "")
        if text:
            yield section, text
```

Two details in there earn their place. The string branch splits on `<br>` and newlines and
never on a full stop, because "Bake at 350 F. for 20 minutes" and "cut into 1.5 cm cubes"
both contain a period that is not a step boundary. And a `HowToStep` can carry both `name`
and `text`, where `name` is a short label and `text` is the actual instruction, so the
fallback order matters: prefer `text`, accept `name` only when `text` is absent.

## Fractions, ranges, and two units on one line

Normalise the numbers before you try to parse them, because recipe pages use characters
that a plain regex on digits and hyphens will miss. Unicode vulgar fractions appear
constantly. So do en dashes standing in for ranges, and the fraction slash U+2044 in place
of an ordinary one.

```python
VULGAR = {
    "¼": "1/4", "½": "1/2", "¾": "3/4",
    "⅓": "1/3", "⅔": "2/3",
    "⅛": "1/8", "⅜": "3/8", "⅝": "5/8", "⅞": "7/8",
}
DASHES = "\u2010\u2011\u2012\u2013\u2014-\u2212"   # hyphen variants, en/em dash, minus

def normalise(text):
    text = text.replace("\xa0", " ").replace("⁄", "/")
    for ch in DASHES:
        text = text.replace(ch, "-")
    for ch, fraction in VULGAR.items():
        text = text.replace(ch, " " + fraction)   # keep the space: 1 1/2 stays mixed
    text = re.sub(r"\s*/\s*", "/", text)
    return re.sub(r"\s+", " ", text).strip()

NUM = r"\d+/\d+|\d+(?:\.\d+)?(?:\s+\d+/\d+)?"
QTY = re.compile(r"^(" + NUM + r")(?:(?:\s*-\s*|\s+to\s+)(" + NUM + r"))?")

def to_float(token):
    total = 0.0
    for part in token.split():
        if "/" in part:
            num, den = part.split("/")
            total += float(num) / float(den)
        else:
            total += float(part)
    return total
```

The dash loop is the line that pays for itself. A range written "2-3 cloves" uses an
en dash, and `re.match(r"(\d+)-(\d+)")` does not match it, so the quantity silently
becomes 2 and the upper bound is gone with no error anywhere. The same applies to a
quantity written with a non-breaking space before its unit: it survives every slice you
take by index.

Then there is the doubled measure, "250g (1 cup) flour", which is two units for one
ingredient. Pick which one you keep and record that you picked, because whichever you drop
is information somebody will later ask for. The same rule applies to any parsed number you
store next to a raw one, and the general form of it is in
[cleaning scraped prices and dates](how-to-clean-scraped-prices-and-dates-playwright.md).

## Ingredients are strings, not structures

`recipeIngredient` is a list of free text. "2 cups flour, sifted" carries a quantity, a
unit, an item and a preparation note in one field, and there is no delimiter that
separates them reliably. So state the split rules rather than assuming them, and put them
where the next reader will find them.

```python
UNITS = {
    "g", "kg", "mg", "ml", "l", "oz", "lb", "lbs",
    "tsp", "teaspoon", "teaspoons", "tbsp", "tablespoon", "tablespoons",
    "cup", "cups", "clove", "cloves", "can", "cans", "pinch", "slice", "slices",
}
PAREN = re.compile(r"\(([^)]*)\)")

def split_ingredient(raw):
    """Rules: the leading numeric run is the quantity, the next token is the unit
    if the vocabulary knows it, text up to the first comma is the item, and text
    after that comma is the preparation note."""
    text = normalise(raw)
    row = {"raw": raw, "quantity_low": None, "quantity_high": None,
           "unit": "", "item": "", "note": "", "alt_measure": ""}

    match = QTY.match(text)
    if match:
        row["quantity_low"] = to_float(match.group(1))
        row["quantity_high"] = to_float(match.group(2)) if match.group(2) \
            else row["quantity_low"]
        text = text[match.end():].strip()

    bracket = PAREN.search(text)
    if bracket and any(c.isdigit() for c in bracket.group(1)):
        row["alt_measure"] = bracket.group(1)      # the measure you chose to drop
        text = PAREN.sub(" ", text, count=1)

    head, _, note = text.partition(",")
    words = head.split()
    if words and words[0].lower().rstrip(".") in UNITS:
        row["unit"] = words.pop(0).lower().rstrip(".")
    row["item"] = " ".join(words).strip()
    row["note"] = note.strip()
    return row
```

Here is where it stops, stated plainly. "Juice of 1 lemon" gets no quantity, because the
number is not at the front and digging it out of the middle is how you turn "preheat to
350" into a quantity somewhere else. "2 cups flour, plus more for dusting" puts a second
quantity into the note, where the parser cannot see it. "3 large eggs" glues the adjective
to the item, since "large" is not a unit. And `recipeIngredient` sometimes contains
"For the sauce:" as an entry, which is a section header wearing an ingredient's clothes: a
row with no quantity and a trailing colon should be dropped or promoted to a label, never
shopped for.

## recipeYield is not a number

Sometimes it is an integer. Sometimes the string "4". Sometimes "4-6 servings", sometimes
"1 loaf", sometimes a `QuantitativeValue` object, and quite often a list holding two of
those at once because the publisher emits both a bare count and a labelled one. Scaling
arithmetic on that raises nothing and produces nonsense.

```python
YIELD = re.compile(r"^(\d+)(?:\s*-\s*(\d+))?\s*(.*)$")
SERVING_WORDS = {"", "serving", "servings", "portion", "portions", "people"}

def _texts(value):
    for v in as_list(value):
        if isinstance(v, dict):                     # QuantitativeValue
            v = f"{v.get('value', '')} {v.get('unitText', '')}".strip()
        if v not in (None, ""):
            yield str(v)

def parse_yield(value):
    """(low, high, unit, raw). low is None when nothing parsed."""
    candidates = list(_texts(value))
    raw = " | ".join(candidates)
    for text in candidates:
        match = YIELD.match(normalise(text))
        if match:
            low = int(match.group(1))
            high = int(match.group(2)) if match.group(2) else low
            return low, high, match.group(3).strip().lower(), raw
    return None, None, "", raw

def scale_factor(value, wanted_servings):
    low, high, unit, _ = parse_yield(value)
    if low is None or low != high or unit not in SERVING_WORDS:
        return None          # refuse: store the raw yield and do not scale
    return wanted_servings / low
```

`scale_factor` returns `None` more often than it returns a number, and that is the point.
A range refuses, because scaling to the low end and the high end give answers that differ
by fifty percent. A unit the vocabulary does not recognise refuses, because "1 loaf" is not
one serving. A refusal you can see beats a quantity nobody can trace back.

## Times are ISO 8601 in the markup and prose on the page

`prepTime`, `cookTime` and `totalTime` are ISO 8601 durations in the markup, `PT1H30M`,
and human strings in the rendered page, "1 hr 30 mins". Only the first is parseable
without guessing, which is one more reason the markup is the source. Python has no
duration parser in the standard library, so write the regex or take the dependency.

```python
ISO = re.compile(r"^P(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?)?$")

def parse_duration(value):
    """Whole minutes, or None. None means 'absent or non-conforming'. Not zero."""
    if not isinstance(value, str):
        return None
    match = ISO.match(value.strip().upper())
    if not match or not any(match.groups()):
        return None
    days, hours, minutes, seconds = (int(g or 0) for g in match.groups())
    return days * 1440 + hours * 60 + minutes + seconds // 60
```

The `any(match.groups())` test catches the empty forms `P` and `PT`, which are valid
against the pattern and carry no duration at all. The day component matters more than it
looks: anything cured, proved or marinated overnight arrives as `P1DT2H` and a
time-only regex drops the day. And keep all three fields separately without recomputing
one from the others, because `totalTime` frequently includes resting time that appears in
no other field, so prep plus cook does not add up and was never meant to.

## One row per ingredient, and what the row has to carry

Flatten to one row per ingredient, with the recipe identity repeated and the raw string
kept beside the parse. That shape survives a second run and loads into anything, which is
the same reason a menu or a product table gets flattened before it gets stored:
[writing rows to CSV](how-to-scrape-to-csv-playwright.md) covers the escaping this shape
needs.

| Column | What it holds | Why it is its own column |
|---|---|---|
| `recipe_id` | the page URL, or the node's `@id` | one recipe spans many rows |
| `position` | the index in `recipeIngredient` | order is meaningful and rows do not stay sorted |
| `quantity_low`, `quantity_high` | parsed numbers, equal when not a range | collapsing a range to one number is a silent edit |
| `unit` | the vocabulary token, lowercased | free text here defeats every later grouping |
| `item` | text up to the first comma, unit removed | the thing you actually shop for |
| `note` | text after the first comma | "sifted", "at room temperature", "divided" |
| `alt_measure` | the bracketed second measure, verbatim | you kept one unit; this records the one you dropped |
| `raw` | the original string, untouched | the only column that can prove a parse wrong |

The `raw` column is not padding. Every rule above is a decision made under uncertainty,
and without the original string you cannot audit any of them, or fix a whole table later
when the unit vocabulary grows.

Two cases the markup does not cover. Some pages ship a `Recipe` node with an empty
`recipeIngredient` and paint the list from a client-side store, so you fall back to the
DOM and inherit the noise this page exists to avoid. Others emit markup that has drifted
from what is rendered, usually after an edit that touched the visible list only. Both are
caught the same way: count the entries in the node against the list items in the
ingredients section, and flag the disagreement rather than silently preferring one. Then,
since a recipe dataset is usually a whole category rather than one page, pace the sweep and
hold one identity across it, which is
[rate limiting your own scraper](how-to-rate-limit-your-scraper-playwright.md) more than
it is anything else.

## Conclusion

Recipe pages are the friendly case and the parsing is still where projects lose their
afternoons. The block is right there, well maintained, and richer than the article around
it, so read it first and the extraction is mostly finished. What remains is a set of
fields the schema declines to constrain: instructions in three shapes, ingredients as
prose, a yield that looks like a number and is not, durations in a format the standard
library will not touch. None of those is hard on its own. Each of them fails quietly, and
quietly is the expensive way to fail, which is why the rules belong in the code where
somebody can read them and the raw string belongs in the row where somebody can check
them.

## Short answers to the questions that lead here

**Where is the cleanest recipe data on a page?** In the `application/ld+json` block, as a
schema.org `Recipe` node. Recipe is one of the best-supported types on the web, so that
block is usually richer and tidier than the rendered article, and it excludes the anecdote,
the ads and the comments by construction.

**Why does my instruction parser work on one site and break on the next?**
`recipeInstructions` has three legal shapes: a plain string, a list of strings, and a list
of `HowToStep` objects that are sometimes grouped inside `HowToSection`. Write one
recursive walker that handles all three and return a single shape downstream.

**How do I split "2 cups flour, sifted" into fields?** Leading numeric run is the quantity,
next token is the unit when a vocabulary you control recognises it, text up to the first
comma is the item, text after it is the note. State those rules in the docstring, because
every one of them is a choice and none is obvious.

**My quantities are wrong on ranges. What did I miss?** Probably the en dash. Recipe pages
write "2-3 cloves" with U+2013, not a hyphen, so a regex on `-` matches nothing and
keeps only the first number. Normalise every dash variant, the fraction slash U+2044 and
the vulgar fractions before parsing.

**Can I scale a recipe from recipeYield?** Only when it parses to a single count of
servings. "4-6 servings" and "1 loaf" must refuse, since scaling either one invents a
number, and the arithmetic raises no error while doing it. Store the raw yield and skip
the scaling.

**Why parse PT1H30M instead of the time shown on the page?** Because the ISO 8601 string
is unambiguous and the visible "1 hr 30 mins" is a locale-dependent guess. Also keep
`prepTime`, `cookTime` and `totalTime` separately: totals often include resting time, so
they do not add up.

## Sources

- Playwright's [`locator.all_text_contents()`](https://playwright.dev/python/docs/api/class-locator#locator-all-text-contents),
  used to read every matching `ld+json` script in one call. Retrieved 2026-08-28.
- Playwright's [`page.goto()` and its `wait_until` states](https://playwright.dev/python/docs/api/class-page#page-goto),
  which is why `domcontentloaded` is enough for markup that ships in the initial HTML.
  Retrieved 2026-08-28.
- The schema.org `Recipe` type and its `recipeIngredient`, `recipeInstructions`,
  `recipeYield`, `prepTime`, `cookTime` and `totalTime` fields, plus the `HowToStep` and
  `HowToSection` types the instruction walker above descends into.
- ISO 8601 duration syntax, which is the format those three time fields carry and which
  the Python standard library does not parse.

**See also:** [extracting JSON-LD structured data](how-to-extract-json-ld-structured-data-playwright.md)
for the block-reading mechanics in full,
[scraping restaurant menu data](how-to-scrape-restaurant-menu-data-playwright.md) for the
same markup-first approach on a nested menu tree,
[cleaning scraped prices and dates](how-to-clean-scraped-prices-and-dates-playwright.md)
for the number and unit normalising this page borrows, and
[extracting clean article text](how-to-extract-clean-article-text-playwright.md) for the
prose around the recipe when you do want it.

---

*Written while maintaining [invisible_playwright](https://github.com/oss-invisible/playwright),
a Firefox patched at the C++ level driven by stock Playwright. The yield bug is the one
that actually shipped here: a scaler took the first integer it found, read "1 loaf, 12
slices" as one serving, and wrote per-serving quantities twelve times too large across a
whole batch before anything looked wrong enough to check.*
