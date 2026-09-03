---
title: "How to scrape course catalogs with Playwright"
description: "Scrape course catalogs with Playwright: the catalog is a four-level tree, the seat counts live behind a term parameter, and one row per section is the only shape that survives a re-run."
parent: "Scraping with Playwright"
grand_parent: "Guides"
nav_order: 94
---


# How to scrape course catalogs with Playwright

To scrape a course catalog with Playwright, treat it as a four-level tree rather than a
list of courses: walk department to course to section to meeting time, pin the term in
the query string so every request describes the same academic period, read seat counts
from the section endpoint instead of the rendered badge, and emit one row per section so
a course that runs twice does not collapse into a single record.

A catalog looks like a searchable list and behaves like a database with the joins
removed. The page shows courses. What you actually need is sections: the same course
taught by two instructors, at two times, with two separate seat counts and two separate
waitlists. Scrape the course level and you get a tidy table that answers none of the
questions people ask a catalog.

## The four levels, and which one is the row

Departments hold courses. A course holds one or more sections. A section holds one or
more meeting times, because a lab on Thursday is part of the same section as the lecture
on Monday.

A **course** is the catalog entry: a code, a title, a credit value, a description. A
**section** is one scheduled offering of that course, with its own instructor, room,
capacity and enrolment count. A **term** is the academic period that scopes both, and a
**cross-listing** is a single section reachable under more than one course code.

The row is the section. It is the only level that carries a unique identifier, an
instructor, a capacity and an enrolment count. Everything above it is a label and
everything below it is a schedule.

Which level answers the question decides which level you store:

| What someone asks the catalog | Level that answers it |
|---|---|
| Does this course exist, and for how many credits? | course |
| Who teaches it and when does it meet? | section |
| Are there seats left? | section |
| Does it clash with my Tuesday lab? | meeting time |
| Which departments own it? | cross-listing |

```python
row = {
    "term": "2026-FA",
    "department": "CS",
    "course_code": "CS 4820",
    "course_title": "Introduction to Analysis of Algorithms",
    "section_id": "CS-4820-002",
    "instructor": "",
    "credits": 4,
    "capacity": 120,
    "enrolled": 118,
    "waitlist": 14,
    "meetings": [{"days": "MW", "start": "10:10", "end": "11:25", "room": ""}],
}
```

Keeping `meetings` as a nested list is deliberate. Flattening it into `days` and `time`
columns forces you to either drop the lab or duplicate the seat counts, and duplicated
seat counts get summed by whoever reads the file next.

## Pin the term before anything else

Catalogs default to whatever term the registrar considers current, and that default
changes underneath you mid-crawl. A run that starts in the spring term and finishes in
the summer term produces a file where two courses that never coexisted appear side by
side.

Put the term in the URL and never rely on the session:

```python
page.goto(f"{base}/search?term={term}&subject={dept}", wait_until="domcontentloaded")
```

If the catalog keeps the term in a cookie or a POST body rather than the query string,
set it once per context and assert it on every response before parsing. The assertion is
cheap and the alternative is a silently mixed dataset.

For the general problem of an identity that has to stay constant across a long crawl, see
[isolate identities with one browser context per session](isolate-identities-browser-context-per-session.md).

## Read seats from the endpoint, not the badge

The green "Open" pill on the results page is a rendering of a number the page already
fetched. It is rounded, it is cached, and on many catalogs it stops updating once the
section closes. The underlying call carries the real integers.

Capture the response rather than the pixel:

```python
seats = {}

def on_response(resp):
    if "/sections" in resp.url and resp.request.resource_type == "xhr":
        for s in resp.json().get("sections", []):
            seats[s["id"]] = (s["capacity"], s["enrolled"], s.get("waitlist", 0))

page.on("response", on_response)
```

The mechanics of attaching to the right response, and the failure modes when a page
fires several similar calls, are covered in
[how to capture XHR API responses with Playwright](how-to-capture-xhr-api-responses-playwright.md).

## Expand the sections before reading them

Most catalogs collapse sections under the course row and fetch them on click. The
sections are not in the initial HTML, so a parse of the loaded document returns courses
with zero sections and no error.

Click each course, wait for its own container rather than a timeout, then read:

```python
courses = page.locator("[data-course-id]")
ids = [courses.nth(i).get_attribute("data-course-id")
       for i in range(courses.count())]          # read ids first, hold no handles

for cid in ids:
    page.locator(f"[data-course-id='{cid}']").click()   # re-resolved every round
    sections = page.locator(f"[data-sections-for='{cid}'] [data-section-id]")
    sections.first.wait_for()
    for i in range(sections.count()):
        rows.append(parse_section(sections.nth(i), cid))
```

Waiting for the container keyed to that specific course matters. A generic wait on
`[data-section-id]` passes immediately because the previous course's sections are still
in the DOM, and you parse the same section twice under two different course codes.

Holding the element handles from a first pass and clicking them one by one looks
equivalent and is not. A catalog that re-renders its course list when a row expands
detaches every handle taken before that click, and the loop dies on the second course
with a detached-node error. Reading the identifiers first and re-resolving the locator on
each round costs one extra query and removes the failure.

The same trap in its general form is described in
[how to scrape a load-more button with Playwright](how-to-scrape-load-more-button-playwright.md).

## The faceted filters lie about totals

Catalog search pages carry filters for level, credits, days, and delivery mode, and they
display a result count. That count is frequently the number of **courses** while the
filter applies to **sections**, so a filter for "Monday" returns courses that have any
Monday section, then shows you all their sections including the Tuesday ones.

Do not use the filters to partition a crawl. Partition by department and term, which are
the two dimensions the catalog actually indexes, and filter locally after extraction.
The result is slower per request and correct, which is the better trade when the
alternative is a file whose row count you cannot explain.

## Cross-listed courses appear twice and are not duplicates

The same section often lists under two departments with two course codes and one shared
section identifier. Deduping on course code keeps both. Deduping on section identifier
keeps one and silently discards the second department, which is the field somebody
wanted.

Keep both rows and mark them:

```python
seen = {}
for r in rows:
    key = r["section_id"]
    if key in seen:
        seen[key]["cross_listed_as"].append(r["course_code"])
    else:
        r["cross_listed_as"] = []
        seen[key] = r
```

## Pace the crawl to the catalog, not to your patience

A registrar's catalog is a small application in front of a student information system,
and the section endpoint is usually the slowest thing it serves. Requesting departments
in parallel is what turns a working scraper into a blocked one.

Walk departments sequentially, keep one context for the whole term, and let the natural
latency of the section calls set the pace. If a run has to be faster, split it by term
across separate days rather than by department across parallel workers.

The reasoning behind rate limits that are set by the target rather than by the client is
in [how to rate limit your scraper with Playwright](how-to-rate-limit-your-scraper-playwright.md),
and the retry side is in
[how to handle 403 and 429 backoff mid-scrape](how-to-handle-403-429-backoff-mid-scrape-playwright.md).

## Re-running the same term should produce the same rows

Seat counts change; the identity of a section does not. A second run of the same term
should match the first on `section_id`, `course_code` and `meetings`, and differ only on
`enrolled` and `waitlist`.

Assert that. If the section identifiers move between runs, the catalog is generating them
per session and you need a composite key from term, course code and section number
instead. Discovering this on the first run costs one comparison. Discovering it after six
weeks of collection costs the collection.

For keeping a long crawl resumable rather than restarting it, see
[how to resume an interrupted scrape with Playwright](how-to-resume-an-interrupted-scrape-playwright.md).

## Conclusion

Course catalogs punish the obvious shape. The list you see is courses, the data you need
is sections, and the difference shows up as soon as somebody asks which of two identical
course codes still has seats.

Pin the term in the URL. Read capacity and enrolment from the section response rather
than the badge. Wait on the container belonging to the course you just clicked. Keep
cross-listings as two rows with one shared identifier. Then re-run the term and check
that only the seat counts moved.

## Short answers to the questions that lead here

**Should one row be a course or a section?** A section. A course with two sections has two instructors, two schedules and two seat
counts, and a course-level row cannot hold either pair without duplicating the other.

**Why do the seat numbers differ from the page?** The badge on the results page is usually a cached rendering that stops updating once a
section closes. The section endpoint carries the current integers, so read the response
rather than the element.

**Why does the filtered result count not match the rows I extract?** Catalog filters commonly count courses while returning sections. Partition the crawl by
department and term, then filter locally.

**Is a cross-listed course a duplicate?** No. It is one section reachable under two course codes. Keep one row per section and
record the additional codes alongside it.

## Sources

- Playwright documentation, [Events and response handling](https://playwright.dev/python/docs/events), retrieved 2026-08-28
- Playwright documentation, [Auto-waiting](https://playwright.dev/python/docs/actionability), retrieved 2026-08-28

**See also:** [capturing XHR API responses](how-to-capture-xhr-api-responses-playwright.md)
for reading the section endpoint instead of the badge, [scraping a load-more button](how-to-scrape-load-more-button-playwright.md)
for the wait that passes on the previous panel, and [resuming an interrupted scrape](how-to-resume-an-interrupted-scrape-playwright.md)
for picking a long term back up.

---

*Written while maintaining [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level driven by stock Playwright. The generic wait on `[data-section-id]` is a mistake that shipped here first: it
passes on the previous course's sections and doubles them under a new code.*
