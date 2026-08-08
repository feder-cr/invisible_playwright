---
title: "How to export scraped data to Excel with Playwright"
description: "Export scraped data to Excel with Playwright and openpyxl without a spreadsheet turning your SKUs, barcodes and lot codes into numbers, notation or dates."
parent: "Scraping with Playwright"
grand_parent: "Guides"
nav_order: 56
---


# How to export scraped data to Excel with Playwright

To export scraped data to Excel with Playwright without it getting corrupted, read the
values from a rendered browser as exact strings, then write them with openpyxl and set the
text number format (`@`) on every identifier column before saving. Skip either half and the
export looks fine and is wrong: the hard part of exporting scraped data to Excel is not
writing the file, it is that a spreadsheet quietly rewrites some of what you put in it. You
scrape a product code as the exact string `007321`, write it out, open the workbook, and it
says `7321`. The leading zero is gone, the identifier no longer matches the source, and
nothing anywhere reported an error.

This page is about defeating that silent coercion. It covers why the values have to come
from a rendered browser rather than a static parse, how to pull them as exact strings, the
three specific ways a spreadsheet mangles identifiers, the openpyxl formatting that stops
it, and how to split several entity types into separate worksheets in one workbook.

## Why a real browser, and not a static parse

Scrape from a real rendered browser rather than a static HTML parse, because on most modern
pages the numbers you want are not in the HTML that arrives from the server. Before
formatting matters at all, the values have to be correct. A price is updated on the client
after load, a table row expands to reveal the code you need, a quantity is filled in by
script. Fetch the raw markup and you get a placeholder, an empty cell, or last week's
number.

So the extraction runs in a real browser, reading the rendered DOM after the page has
settled. `invisible_playwright` returns a stock Playwright `Browser`, so every selector,
wait and evaluation you already know works unchanged:

```python
from invisible_playwright import InvisiblePlaywright

with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    page.goto("https://example.com/catalog")
    page.wait_for_selector("table.products tbody tr")

    rows = page.eval_on_selector_all(
        "table.products tbody tr",
        """rows => rows.map(tr => {
            const cell = i => tr.children[i].textContent.trim();
            return { sku: cell(0), name: cell(1), barcode: cell(2), price: cell(3) };
        })""",
    )

print(rows[0])
# {'sku': '007321', 'name': 'Bracket', 'barcode': '8801234500012', 'price': '19.90'}
```

Two details carry the whole exercise. The identifiers arrive as strings, `'007321'` and
`'8801234500012'`, and they are still exact strings here. Everything after this point is
about keeping them that way. And the `seed=42` means the same run is reproducible: if a
value comes back wrong you can replay the identical session instead of guessing whether the
page changed. Waiting for the right signal before you read matters as much as the read
itself, which is [its own subject](how-to-wait-for-page-load-playwright.md).

The one stealth caveat worth stating here: the values are only correct if the page treats
the browser as a real one. A session that gets a blocked or stripped-down page renders
empty cells, and empty cells export cleanly into a perfectly formatted, perfectly useless
spreadsheet. Assert that the values are present and plausible before you trust the file.

## The failure mode: the spreadsheet rewrites your identifiers

A spreadsheet corrupts scraped identifiers because, when it imports a value it cannot see a
schema for, it guesses a type. That guesser is tuned for humans typing figures into cells,
and it is exactly wrong for scraped identifiers. Three coercions do almost all the damage:

| Scraped identifier | What the spreadsheet stores | Why it corrupts the data |
|---|---|---|
| `007321` (leading-zero code) | The number `7321` | The leading zero is dropped, so any SKU, ZIP or account number with a significant leading zero becomes a different identifier. |
| `8801234500012` (13-digit barcode) | `8.80123E+12`; past 15 digits, rounded to 15 significant figures | Long numeric codes turn into scientific notation, and beyond 15 digits the trailing digits are destroyed, not just hidden. |
| `3-14` or `1/2024` (date-like code) | An integer serial date (`3-14` becomes a March date of the current year) | Date-like strings are read as dates and stored as a day count, so the original code is lost. |

None of these throw. The write succeeds, the file opens, and the corruption is only visible
if you compare a sample against the source by eye. It is the same shape of bug as a
[test that passes because it checked nothing](how-to-test-bot-detection.md): a silent pass
is more dangerous than a loud failure.

## Force every code column to text with openpyxl

The fix is to tell the spreadsheet, per column, that these cells are text and must not be
reinterpreted. In openpyxl that is the number format `@`, the text format code. Set it on
every cell in an identifier column and the guesser is disabled for that column only, so your
genuine numbers (price, quantity) still behave as numbers and sort correctly.

```python
from openpyxl import Workbook

# columns that hold identifiers, not quantities: keep them exact text
TEXT_COLUMNS = {"sku", "barcode"}
HEADERS = ["sku", "name", "barcode", "price"]

wb = Workbook()
ws = wb.active
ws.title = "products"
ws.append(HEADERS)

for row in rows:
    ws.append([row[h] for h in HEADERS])

# force the text format on the identifier columns, header row excluded
for col_index, header in enumerate(HEADERS, start=1):
    if header in TEXT_COLUMNS:
        for cell in ws.iter_cols(min_col=col_index, max_col=col_index, min_row=2):
            for c in cell:
                c.value = str(c.value)   # ensure a string, not an inferred number
                c.number_format = "@"    # text: the spreadsheet stops guessing

# real numbers stay numeric so they sum and sort
price_col = HEADERS.index("price") + 1
for c in next(ws.iter_cols(min_col=price_col, max_col=price_col, min_row=2)):
    c.value = float(c.value)

wb.save("catalog.xlsx")
```

Two things make this reliable rather than hopeful. Assigning `str(c.value)` guarantees the
cell holds a string even if the scraped value was already numeric-looking, and setting
`number_format = "@"` makes the spreadsheet display and re-save it verbatim, so a later
manual edit does not silently re-coerce it. Do this for every code and ID column, not just
the ones that happen to look risky in your first sample. The next batch will contain the
leading-zero SKU that your first batch did not.

## Split entity types across worksheets

Scrapes usually pull more than one kind of record: products and the sellers that list them,
listings and their reviews, orders and their line items. Flattening those into one sheet
forces a type onto columns that hold different things in different rows. A workbook is the
natural fit, because openpyxl gives you one worksheet per entity in a single file, each with
its own text-column rules.

```python
def write_sheet(wb, title, headers, records, text_columns):
    ws = wb.create_sheet(title=title)
    ws.append(headers)
    for rec in records:
        ws.append([rec[h] for h in headers])
    for col_index, header in enumerate(headers, start=1):
        if header in text_columns:
            for col in ws.iter_cols(min_col=col_index, max_col=col_index, min_row=2):
                for c in col:
                    c.value = str(c.value)
                    c.number_format = "@"
    return ws

wb = Workbook()
wb.remove(wb.active)  # drop the default empty sheet

write_sheet(wb, "products", ["sku", "name", "barcode", "price"],
            products, text_columns={"sku", "barcode"})
write_sheet(wb, "sellers", ["seller_id", "display_name", "rating"],
            sellers, text_columns={"seller_id"})

wb.save("export.xlsx")
```

The `seller_id` on the second sheet gets the same text treatment as `sku` on the first, for
the same reason: it is an identifier, not a quantity, and an identifier that a spreadsheet
is free to reformat is an identifier you will eventually fail to join on. When the records
span [many paginated pages](how-to-scrape-paginated-pages-playwright.md) or arrive from a
[table you extracted row by row](how-to-scrape-html-tables-playwright.md), collect them into
lists first and hand each list to `write_sheet` once at the end.

## Measure it: what coercion does to real identifiers

The demonstration is deterministic, which is why it is worth running rather than trusting.
Take the same records and write them twice, once with the text format and once without, then
read both files back and compare each identifier to what you scraped.

```python
from openpyxl import Workbook, load_workbook

records = [
    {"sku": "007321", "barcode": "8801234500012", "lot": "3-14"},
    {"sku": "000090", "barcode": "4006381333931", "lot": "1/2024"},
]
HEADERS = ["sku", "barcode", "lot"]

def dump(path, force_text):
    wb = Workbook(); ws = wb.active; ws.append(HEADERS)
    for r in records:
        ws.append([r[h] for h in HEADERS])
    if force_text:
        for col in ws.iter_cols(min_row=2):
            for c in col:
                c.value = str(c.value); c.number_format = "@"
    wb.save(path)

def read_back(path):
    ws = load_workbook(path).active
    return [[c.value for c in row] for row in ws.iter_rows(min_row=2)]

dump("guessed.xlsx", force_text=False)
dump("text.xlsx", force_text=True)
print("guessed:", read_back("guessed.xlsx"))
print("text:   ", read_back("text.xlsx"))
```

With the guesser left on, `007321` reads back as `7321`, `8801234500012` as a float that has
lost its exact value, and `3-14` as a date object: three identifiers out of three per row,
corrupted. With the text format on, all three come back byte-for-byte what you scraped. The
point is not the count, it is that every mangled value passed through your pipeline without a
single exception being raised. The only thing standing between a correct scrape and a
corrupt export is the format code on the column.

## Conclusion

Exporting to Excel is two problems wearing one coat. The first is getting the values right,
which needs a real rendered browser because the numbers are written on the client and a
static parse reads placeholders. The second is keeping them right, which needs an explicit
text format on every identifier column because a spreadsheet's type guesser will otherwise
strip leading zeros, collapse long codes to scientific notation, and turn size and lot codes
into dates. Set `number_format = "@"` on the code columns, write each entity to its own
worksheet, and read a sample back to confirm. The failure here is silent, so the verification
is not optional.

## Short answers to the questions that lead here

**Why does my SKU lose its leading zero in Excel?** Because the spreadsheet read `007321`
as the number 7321. Store it as a string and set the column's number format to `@`, the text
format, so it is never reinterpreted.

**Why does a long barcode show as scientific notation?** The value was imported as a number,
and numbers over about 15 digits are shown in scientific notation and rounded to 15
significant figures, which destroys the trailing digits. Force the column to text.

**How do I write multiple tables into one Excel file?** Create one worksheet per entity with
`wb.create_sheet(...)` in openpyxl and apply the text-column rules to each sheet separately.

**Can I just export CSV instead?** CSV avoids openpyxl but not the problem: whatever opens
the CSV runs the same type guesser, so the leading zero is lost at open time rather than at
write time. The text-format fix only exists inside a real spreadsheet file.

**Do I need a browser at all, or can I parse the HTML?** If the values are written or updated
by client-side script, a static parse reads the pre-update placeholder. Read them from the
rendered DOM after the page settles.

**Why is my exported spreadsheet full of empty cells?** Usually the page never rendered the
values for this session, not a formatting bug. Confirm the values are present in the browser
before you write the file, the same way you would [check a page is not being blocked](playwright-detected-as-bot.md).

## Sources

- The [openpyxl documentation](https://openpyxl.readthedocs.io/en/stable/) for cell number
  formats, in particular the `@` text format code.
- The published behaviour of common spreadsheet software on numeric-looking text: leading-zero
  loss, scientific-notation display past 15 significant figures, and date inference on
  slash- and hyphen-separated codes, all reproducible with the snippet above.
- This project's own gates, whose lesson that a silent pass is worse than a loud failure is
  the same one that governs a data export you never eyeball.

**See also:** [how to scrape HTML tables](how-to-scrape-html-tables-playwright.md) for
getting the rows out in the first place, and [scraping e-commerce product pages](how-to-scrape-ecommerce-product-pages-playwright.md)
for the client-updated prices that make a static parse insufficient.

---

*Written while maintaining [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level driven by stock Playwright. The export is only as good as
the values going into it, and the values are only correct if the browser was treated as
real.*
