---
title: "How to scrape professional directories with Playwright"
description: "Scrape professional directories with Playwright: key each row on the registration number plus the location, read credential badges out of their title attributes, sweep the overlapping letter and specialty partitions, and keep the last-verified date the card hides."
parent: "Scraping with Playwright"
grand_parent: "Guides"
nav_order: 111
---


# How to scrape professional directories with Playwright

To scrape a professional directory with Playwright, make the row a person-at-location pair
keyed on the registration number instead of the name, read the credential badges out of
their `title` attributes, not their text, sweep the letter and specialty partitions
the site offers while expecting them to overlap, and keep the last-verified date the
response carries so a stale record is visibly stale instead of quietly wrong.

A directory of professionals looks like a list of people and is a many-to-many join wearing
a list's clothes. One dentist practises at three clinics. One clinic lists twelve dentists.
The register that issues the licence knows the person; the site that renders the cards knows
the appointment. Neither of those is the row you want, and picking either one as the row
throws away records on the first run.

Two things separate this from an ordinary listing crawl. The identifier is a licence number
instead of a product id, and it is the only field in the record that does not drift. And
the rows are people, which constrains what you should collect before you write the schema.

## The row is a person at a location

The row is the pair. Not the person, not the location.

Collapse to one row per person and every practice address after the first either disappears
or gets crammed into a list column that no query can filter on. Collapse to one row per
location and the same registrant becomes several people you can no longer join back
together, because the only thing linking them was the number you did not make the key.

```python
row = {
    "register": "",                 # which body issued the number
    "registration_number": "",      # the person, stable
    "location_id": "",              # the site's own id for the practice
    "family_name": "",
    "given_name": "",
    "specialty": "",
    "qualification": "",
    "accepting_new_clients": None,  # True / False / None, never a bare False
    "status": "",                   # registered, suspended, lapsed
    "street": "",
    "city": "",
    "postcode": "",
    "last_verified": None,          # the site's date, not your crawl date
    "seen_in_partition": [],        # which letter or specialty query returned it
    "first_seen": None,
    "last_seen": None,
}
```

The primary key is `(register, registration_number, location_id)`. Everything downstream
resolves against that triple: deduplication, incremental runs, and the reconcile step further
down this page.

`accepting_new_clients` is three-valued on purpose. A card that says "not accepting" and a
card that says nothing are different facts, and defaulting the second to `False` turns every
directory that simply does not publish the flag into a register where nobody has room.

## The registration number is the only identifier that survives

Names collide and names change. The number does neither.

In a register with tens of thousands of entries, two people sharing a surname and an initial
in the same city is ordinary, not rare. Names also move: a marriage, a transliteration
that gains or loses a letter, a middle initial the card prints on one page and omits on the
next, a title that is sometimes part of the name field and sometimes its own column. A crawl
keyed on the name silently merges two people, then silently splits one, and both failures
look like clean data.

Keep the issuing body next to the number. A number is unique inside the register that issued
it and nowhere else, so an aggregator that pulls several bodies into one search will hand you
two different people carrying the same digits. `(register, registration_number)` is the pair
that is actually unique.

When the number is not printed on the card it is one step away. Detail URLs are built from
it, so the `href` carries it, and the search response behind the cards nearly always includes
it as a field. What you should not do is manufacture a surrogate key from the name and the
city, because both halves of it move.

## Credential badges hide in the title attribute, not in the text

The specialty, the qualification and the accepting-new-clients flag are often an icon, and
the meaning sits in an attribute, not in a text node. `inner_text()` returns an empty
string, the field looks absent, and you record a null for something the page displayed
plainly to a reader.

Four renderings cover most of it: an `<i>` or `<span>` with a `title`, an `<img>` with the
meaning in `alt`, an element carrying `aria-label` for a screen reader, and a bare CSS class
whose meaning lives in a legend somewhere else on the page.

```python
BADGE_ATTRS = ("title", "aria-label", "alt", "data-tooltip", "data-original-title")

def badge_meaning(el):
    """A badge's meaning is in an attribute, not in its text."""
    for attr in BADGE_ATTRS:
        value = el.get_attribute(attr)
        if value and value.strip():
            return value.strip()
    return None

def read_badges(card):
    out = []
    selector = "[title], [aria-label], img[alt], [data-tooltip], i[class*='icon']"
    for el in card.query_selector_all(selector):
        meaning = badge_meaning(el)
        if meaning:
            out.append({"meaning": meaning, "class": el.get_attribute("class") or ""})
    return out
```

Store the class alongside the meaning. Wording changes between site releases and the class
usually does not, so the class is what maps last quarter's rows onto this quarter's. It is
also the only handle you have on the fourth rendering, where nothing in the element says
anything at all.

That fourth case is where this stops working, and it is worth being blunt about. If the
meaning is carried only by a background image in the stylesheet, the card cannot tell you
what the badge means. Resolve the class once against the page's own legend, keep the mapping
as data, not as a guess, and if there is no legend, write the class and leave the
field null. A null is recoverable. A `False` invented for an unreadable badge is a false
statement about a person that no later run will notice.

## The partitions overlap, and that is the useful property

Professional directories paginate by first letter of surname or by specialty far more often
than by page number, and those partitions are not a clean cut of the set.

They overlap in both directions. A practitioner registered in two specialties appears under
both. A double-barrelled surname is filed under either half depending on how the record was
entered. A person practising in two regions comes back from each region filter. Treat a
partition as a query, not as a slice, the same reframing that
[store locator pages](how-to-scrape-store-locator-pages-playwright.md) need for radius search,
and the duplicates stop being a defect to design away.

They also miss, which is the half nobody plans for. A surname with a prefix particle sits
under the particle on one register and under the root on another. A card whose specialty
field was never filled in appears under no specialty filter at all, so a specialty sweep is
structurally incapable of being complete. If the site offers both axes, run both and compare.

```python
seen = {}

def sweep(page, values, kind):
    for value in values:
        tag = f"{kind}:{value}"
        for card in search(page, kind, value):   # your form driver, paginated
            key = (card["register"], card["registration_number"], card["location_id"])
            if key in seen:
                if tag not in seen[key]["seen_in_partition"]:
                    seen[key]["seen_in_partition"].append(tag)
                continue
            card["seen_in_partition"] = [tag]
            seen[key] = card

sweep(page, list("abcdefghijklmnopqrstuvwxyz"), "letter")
after_letters = len(seen)
sweep(page, load_specialties(page), "specialty")
print(f"the specialty axis added {len(seen) - after_letters} rows the letters missed")
```

That last line is the measurement, not a log message. A non-zero number is proof the letter
axis alone was not the register, and it is the only estimate of coverage you can get without
a total the site is under no obligation to publish. Pagination inside each partition is its
own problem, and the loop that survives a filter held in session state is in
[nested pagination](how-to-scrape-nested-pagination-playwright.md).

## The card hides the last-verified date the response carries

Many entries are stale, and the site usually knows how stale.

A register keeps the record after the person moves. The registration is still valid, so
nothing about the entry looks wrong; the address hanging off it is fourteen months old. The
card renders the address and not the date, because a date makes the directory look worse than
it is. The search response behind the card frequently carries that date anyway, as
`lastVerified`, `updatedAt` or `dataAsOf`, because the template dropped the field in place of
the API.

```python
records = {}

def on_response(resp):
    if "/search" in resp.url and resp.request.resource_type in ("xhr", "fetch"):
        try:
            body = resp.json()
        except ValueError:
            return
        for item in body.get("results", []):
            records[item.get("registrationNumber")] = item

page.on("response", on_response)
```

Read the date off `records`, not off the DOM, and keep it next to your crawl date, not
instead of it. A row collected today from a record the register last checked two years ago is
a different fact from one checked last week, and only the pair says which you are holding.
Parse the date once at the edge, since it arrives in whatever format the page prefers:
[cleaning scraped prices and dates](how-to-clean-scraped-prices-and-dates-playwright.md)
covers the ambiguous ones. Attaching to the right call when a page fires several that look
alike is in
[capturing XHR API responses](how-to-capture-xhr-api-responses-playwright.md).

Where this stops: a directory that publishes no date leaves you unable to separate fresh from
stale at all. Re-crawling does not rescue you, because an unchanged record is exactly what a
stale record looks like. Record the absence of the field instead of inferring a freshness you
cannot see.

## A second run reconciles, it does not overwrite

On the second run, a row that does not come back is ambiguous, and the ambiguity has to
survive into the data.

The person may have left that location. The letter partition may have filed them under the
other half of their surname this time. Their specialty field may have been cleared, dropping
them out of a specialty sweep entirely. Overwriting the table with the new run erases the
first possibility and quietly asserts the third.

```python
def reconcile(previous, current, partitions_completed, run_date):
    for key, old in previous.items():
        fresh = current.get(key)
        if fresh is not None:
            fresh["first_seen"] = old["first_seen"]
            fresh["last_seen"] = run_date
            continue
        # The row did not come back. That is evidence, not a deletion.
        old["absent_from"] = sorted(
            partitions_completed.intersection(old["seen_in_partition"])
        )
        old["absent_runs"] = old.get("absent_runs", 0) + 1
        current[key] = old
    return current
```

`absent_from` is the honest version of a delete. If the pair was found under `letter:m` last
time and this run completed `letter:m` without it, that is a real signal about the person. If
this run never finished `letter:m`, the absence says nothing about the person and everything
about the crawl, and an empty `absent_from` list is what tells the two apart. Retire a pair
only after several runs of absence from partitions you completed, and flag it instead of
deleting it, because a practitioner on leave comes back. The incremental run this sits inside
is in [scraping only new items](how-to-scrape-only-new-items-incremental-playwright.md).

## Personal data narrows what you should keep

These rows are about identifiable people, and what is lawful to collect, store and reuse
depends on your jurisdiction and on your purpose. That is not a footnote and it is not legal
advice from a scraping guide. Get an answer from someone qualified before a sweep runs in
production, and treat the answer as a constraint that arrives before the schema.

The mechanical part is field selection. A licensing body publishes registration status so the
public can check a credential, which is a purpose. It is not a general permission to assemble
a contact database, and the fields that serve the first purpose are a small subset of the
fields on the page. Take the minimum the purpose needs and drop the rest at extraction time
rather than storing everything and filtering downstream.

```python
# Decide the fields for the purpose, then enforce the decision at write time.
ALLOWED = {
    "register", "registration_number", "location_id",
    "status", "specialty", "city", "last_verified",
}

def project(row):
    return {k: v for k, v in row.items() if k in ALLOWED}
```

One allowlist in one place is reviewable in a way a scraper is not. Someone can read seven
field names and tell you whether they match what you said you were doing. There is a retention
edge too: a record that was accurate on collection becomes an inaccurate statement about a
person as it ages, which is the second reason `last_verified` earns its slot. Many registers
also publish an explicit statement of what the data may be used for.

## Pace it like a lookup, because that is what the endpoint is

A register search is built to answer one question at a time, and an alphabet sweep is a
completely different traffic shape.

The endpoint behind the form exists so someone can check one name before an appointment. It
sees one query and a detail page. Twenty-six letter queries with pagination under each, then a
specialty pass over the same set, is thousands of requests through a form sized for one. Walk
the partitions in sequence in a single browser context and hold the identity constant: a
register watching one session read through the alphabet sees a researcher, while a fleet of
fresh sessions each pulling one letter is a much cheaper thing to spot.

The two halves are not substitutes. A stable identity makes the sweep coherent, so it reads as
one visitor rather than hundreds of one-query strangers. It does nothing about volume, which
is measured outside the page and does not care how real each request looks. The pacing comes
from your loop, and its shape is in
[rate limiting your scraper](how-to-rate-limit-your-scraper-playwright.md).

## Conclusion

A professional directory is a join, not a list. Make the row the person-at-location pair and
key it on the issuing body plus the registration number, because names collide, names change,
and every other field on the card drifts. Pull the credential fields out of the `title` and
`aria-label` attributes where the icons keep them, and leave a null rather than inventing a
`False` for a badge you could not read. Sweep both partition axes and use the rows one finds
that the other missed as your coverage estimate. Keep the register's own last-verified date so
staleness is visible, reconcile the second run instead of overwriting it, and keep only the
fields your purpose needs.

## Short answers to the questions that lead here

**Should one row be a person or a location?** Neither. One person practises at several
locations and one location lists several people, so the row is the pair, keyed on
`(register, registration_number, location_id)`. Collapsing either way loses records on the
first run.

**Two entries have the same name. How do I tell them apart?** By the registration number, the
only identifier in the record that does not change. Names collide within a single city and
change over time, so a name-derived key merges two people and splits one, and neither failure
shows in the output.

**The specialty column is empty for every card. Where is it?** In an attribute. Credential
fields are usually icons carrying their meaning in `title`, `aria-label` or `alt`, so
`inner_text()` correctly returns nothing. Read the attributes, and store the CSS class next to
the meaning so a wording change later does not orphan the old rows.

**Why do the same people keep coming back under different letters?** Because letter and
specialty partitions overlap by design: two specialties, a double-barrelled surname, a second
region. Deduplicate on the key and record which partitions returned each pair, then use the
partition list when a row goes missing later.

**The address is wrong. Is my parser broken?** Probably not. Directories keep a record after
the person moves, and the response often carries a last-verified date that the card does not
render. Capture it from the search response and store it beside your own crawl date.

**Can I collect everything now and decide what to keep later?** That is the one decision this
data does not let you postpone. These are records about identifiable people, the lawful basis
depends on your jurisdiction and purpose, and the practical control is an allowlist applied at
write time rather than a filter applied downstream.

## Sources

- Playwright documentation, [Events and response handling](https://playwright.dev/python/docs/events), retrieved 2026-08-28
- Playwright documentation, [`Locator.get_attribute`](https://playwright.dev/python/docs/api/class-locator#locator-get-attribute), retrieved 2026-08-28
- Playwright documentation, [Browser contexts](https://playwright.dev/python/docs/browser-contexts), retrieved 2026-08-28

**See also:** [scraping business directory listings](how-to-scrape-business-directory-listings-playwright.md)
for the form-driven crawl and the obfuscated contact fields,
[scraping store locator pages](how-to-scrape-store-locator-pages-playwright.md) for the same
overlap problem when the axis is geography rather than credentials,
[capturing XHR API responses](how-to-capture-xhr-api-responses-playwright.md) for reading the
search call instead of the cards, and
[scraping only new items](how-to-scrape-only-new-items-incremental-playwright.md) for the
incremental run the reconcile step sits inside.

---

*Written while maintaining [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level driven by stock Playwright. The first version keyed rows on
name plus city, and it merged two registrants who shared a surname in one town. Nobody caught
it until a single letter partition returned more cards than the whole table had rows.*
