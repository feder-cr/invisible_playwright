---
title: "How to scrape accordion and tab content with Playwright"
description: "Scrape accordion and tab content with Playwright: tell panels already in the DOM from panels fetched on first expand, drive aria-expanded and aria-controls, and wait on a selector keyed to the panel id."
parent: "Scraping with Playwright"
grand_parent: "Guides"
nav_order: 97
---


# How to scrape accordion and tab content with Playwright

To scrape accordion and tab content with Playwright, work out first whether the panels
already sit in the DOM hidden by CSS or arrive over the network on first expand, then read
the hidden ones with `text_content()` and, for the fetched ones, click the control, wait on
a selector keyed to that panel's own id, and extract each panel before opening the next.

The two cases look identical from the outside. A heading, a click, content appears.
Underneath they are different problems, and a scraper written for one returns blank rows on
the other without raising a single error.

## The two cases behind every accordion

An accordion either ships its content collapsed or fetches it on demand, and that choice
decides everything else you write.

In the first case the server sends the full text in the initial HTML and CSS hides it.
`display: none`, `height: 0`, the `hidden` attribute. The text is in the document from the
first paint, so there is nothing to wait for and nothing to click.

In the second case the panel is an empty shell. The first expand fires an XHR or a `fetch`,
and a script writes the response into that shell. Before the click there is no text
anywhere in the DOM.

| What the panel does | Where the text sits before any click | What the scrape needs |
|---|---|---|
| Ships collapsed in the initial HTML | Already in the DOM, hidden by CSS | Read it, no clicking at all |
| Fetches on first expand | Nowhere yet, the panel is an empty shell | Click, wait keyed to that panel, then read |

The first case is faster and quieter, because a run that never clicks produces no
interaction pattern to model. Finding out which one you have is worth the two minutes it
takes.

## How to tell which case you have

Load the page, click nothing, and measure how much text each panel already holds.

```python
from invisible_playwright import InvisiblePlaywright

with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    page.goto("https://example.com/support", wait_until="domcontentloaded")

    sizes = page.locator("[role='region'], .accordion-panel").evaluate_all(
        "nodes => nodes.map(n => (n.textContent || '').trim().length)"
    )
    print(sizes)
```

A list of real numbers means the content is already there. A list of zeros means it arrives
on click. A mix is common and it is not an error: plenty of pages render the first panel
open and server-side, then load the rest lazily.

The second check is the network. Attach a response listener, expand one panel by hand, and
watch whether a request fires. If one does, the JSON it returns is usually cleaner and more
stable than the HTML that gets painted from it, and
[capturing XHR and API responses](how-to-capture-xhr-api-responses-playwright.md) may
replace the DOM work entirely.

## Reading panels that are already in the DOM

When the text ships with the page, read it straight out and skip the interaction.
`text_content()` returns the node's `textContent`, and `textContent` does not care whether
the element is visible.

```python
panels = page.locator("[role='region']")

for i in range(panels.count()):
    text = (panels.nth(i).text_content() or "").strip()
    if not text:
        raise RuntimeError(f"panel {i} is empty: this page is the lazy kind")
    print(text)
```

`inner_text()` is the wrong tool here. It is defined in terms of rendered text, so a
collapsed subtree gives you results that depend on how the widget hides itself. Use
`text_content()` for anything that is not currently on screen.

Now the trap. On a lazily loaded accordion the exact same call returns an empty string and
raises nothing, because an empty string is a legal value for an empty element. A run over
forty panels finishes green, writes forty rows, and every row is blank.

That is why the guard above is not decoration. An empty panel means your assumption about
the page was wrong, and the run should stop and say so, not fill a file with nothing.

## aria-expanded and aria-controls beat class names

The button carries both the widget's state and a pointer to the panel it opens, in two
attributes that outlast any redesign.

`aria-expanded` is `"true"` or `"false"` and reflects what the widget itself believes.
`aria-controls` holds the id of the panel the button opens, which pairs the control to its
content without guessing at DOM adjacency. That pairing matters more than it looks, because
plenty of layouts put the panels in a separate container from the headings, and every
scraper built on "the div after the button" breaks on those.

Class names are the fragile alternative. `.is-active`, `.open`, `.accordion__panel--expanded`
are styling decisions, and a theme update renames them with no warning. The ARIA attributes
are what assistive technology consumes, so they tend to be maintained, and the
[WAI-ARIA accordion pattern](https://www.w3.org/WAI/ARIA/apg/patterns/accordion/) specifies
exactly these two.

```python
pairs = page.locator("[aria-controls][aria-expanded]").evaluate_all(
    "nodes => nodes.map(n => ({"
    "  panel_id: n.getAttribute('aria-controls'),"
    "  label: n.innerText.trim(),"
    "  expanded: n.getAttribute('aria-expanded') === 'true',"
    "}))"
)

for pair in pairs:
    print(pair["panel_id"], pair["expanded"], pair["label"])
```

Read `expanded` before you click, and skip the ones that are already open. A loop that
clicks every control it finds will close the panel the page opened for you, and the row it
writes for that heading comes back empty.

If the accordion is a web component and the panel lives inside its shadow root, the
attributes work the same way, but the selector needs the behaviour described in
[scraping shadow DOM content](how-to-scrape-shadow-dom-playwright.md).

## The generic wait that passes immediately

Here is the bug that fills a dataset with duplicates, and it survives review because the
code looks correct.

```python
# WRONG: the previous panel still matches, so this returns instantly
page.click(f"[aria-controls='{panel_id}']")
page.wait_for_selector(".accordion-panel .content")

# RIGHT: keyed to this control, and to text actually landing in this panel
page.click(f"[aria-controls='{panel_id}']")
page.wait_for_selector(f"[aria-controls='{panel_id}'][aria-expanded='true']")
page.wait_for_function(
    "id => { const el = document.getElementById(id);"
    " return el && el.textContent.trim().length > 0; }",
    arg=panel_id,
    timeout=15000,
)
```

`.accordion-panel .content` matches something the moment you ask, because the panel you
opened a second ago is still sitting in the DOM and still matches that selector. The wait
returns true, your next line reads the panel, and the text you file under the new heading
belongs to the old one. Nothing fails. The run is green and the data is wrong.

Two conditions fix it, and you need both. The `aria-expanded` flip on the specific control
proves the widget accepted the click. The text-length check on the specific panel id proves
the fetch landed. On its own the first passes before any content arrives, and on its own the
second passes instantly against a panel that was never empty. This is the same shape as
[waiting for a load-more click to actually add rows](how-to-scrape-load-more-button-playwright.md):
wait for the thing you asked for, not for something that was already true.

## Extract each panel before you open the next

Some accordions remove the previous panel from the DOM when a new one opens, so the
tempting shape, expand everything and then read everything, comes back with one panel of
content and a stack of empty nodes.

Single-open accordions are common, and frameworks that render conditionally tend to unmount
the closed panel rather than hide it. You cannot tell by looking. Read while the panel is
open and the question never comes up.

```python
def read_panel(page, panel_id):
    return (page.locator(f"#{panel_id}").text_content() or "").strip()

rows = []

for pair in pairs:
    panel_id = pair["panel_id"]
    control = page.locator(f"[aria-controls='{panel_id}']").first

    if not pair["expanded"]:
        control.click()
        page.wait_for_selector(f"[aria-controls='{panel_id}'][aria-expanded='true']")
        page.wait_for_function(
            "id => { const el = document.getElementById(id);"
            " return el && el.textContent.trim().length > 0; }",
            arg=panel_id,
            timeout=15000,
        )

    rows.append({"heading": pair["label"], "text": read_panel(page, panel_id)})
```

Two details carry the weight. The control is re-resolved from a locator on every round
rather than held as an element handle, so a widget that re-renders its buttons does not
produce a detached-node error. And the text crosses into plain Python inside the loop, which
is the same discipline that keeps
[a multi-page table scrape from losing rows](how-to-scrape-html-tables-playwright.md): once
a value is a Python string it survives whatever the page does next.

## Nested accordions do not open with their parent

Expanding a parent reveals its children's headings. It does not expand the children.

A two-level specification page or a layered help centre returns the top-level sections and
nothing underneath if you enumerate the controls once at load and loop over that list. The
nested controls were either absent from the DOM at that moment or not clickable yet, so they
were never in the list at all.

Re-query after every expand and stop when a full pass finds nothing new.

```python
seen = set()

while True:
    todo = [
        pid
        for pid in page.locator("[aria-controls][aria-expanded='false']").evaluate_all(
            "nodes => nodes.map(n => n.getAttribute('aria-controls'))"
        )
        if pid and pid not in seen
    ]
    if not todo:
        break

    for panel_id in todo:
        seen.add(panel_id)
        control = page.locator(f"[aria-controls='{panel_id}']").first
        if control.count() == 0 or not control.is_visible():
            continue
        control.click()
        page.wait_for_selector(f"[aria-controls='{panel_id}'][aria-expanded='true']")
        rows.append({"panel_id": panel_id, "text": read_panel(page, panel_id)})
```

The loop ends on evidence rather than on a depth you guessed in advance. A fixed two-pass
version handles two levels and silently drops the third. The `seen` set is what keeps a
control that reappears in a later pass from being clicked twice, which on a toggle would
close it again.

## Tabs are the same widget with one panel visible

A tab strip is an accordion whose panels are mutually exclusive, so both cases and every
trap above apply unchanged. Only the attribute names move.

The control is `[role='tab']`, its state attribute is `aria-selected` rather than
`aria-expanded`, and `aria-controls` still names the `[role='tabpanel']` it reveals. Swap
those three strings into the loops above and the code works as written.

Tab sets lean towards the first case more than accordions do. Sending every panel and
setting `hidden` on the inactive ones is the cheapest implementation, which means
`page.locator("[role='tabpanel']")` plus `text_content()` frequently collects the whole
strip without a single click.

When tabs do load lazily, the generic-wait trap is sharper than on an accordion, because the
previous tabpanel almost always stays in the DOM with `hidden` set. A wait on
`.tab-content` is satisfied by a panel that has been sitting there since page load.

Two shortcuts are worth checking before writing any of this. Some tab strips write the
active tab into the URL hash or a query parameter, which turns the whole job into loading
one URL per tab, no clicking and no waiting. And a tabpanel that hosts an embedded document
puts its text in another frame, which needs
[the frame-scoped approach to iframe content](how-to-scrape-iframe-content-playwright.md)
rather than a page-level locator.

## Conclusion

Accordions and tabs punish one assumption harder than any other: that content behaves the
same way before and after a click. Measure first. A page whose panels already hold their
text needs no interaction at all, and that is both the faster path and the quieter one.

When the content really is fetched on demand, drive the widget through `aria-expanded` and
`aria-controls` instead of class names, wait on a selector keyed to the specific panel id
plus a check that text arrived in it, and pull each panel into Python before opening the
next. The failures here are quiet ones. A blank string and a duplicated paragraph both look
like a successful run until somebody reads the output.

## Short answers to the questions that lead here

**Can I scrape accordion content without clicking anything?** Often yes. If the panels ship
collapsed in the initial HTML, `text_content()` reads them while they are hidden, and a run
that never clicks is both faster and less distinctive. Measure the text length of every
panel before the first click to find out.

**Why does text_content() return an empty string on my accordion?** Because that panel is an
empty shell and its content is fetched on first expand. The call is working correctly, there
is genuinely no text yet. Click the control, wait for content to land in that specific
panel, then read.

**Why does my scraper record the same text under several headings?** Because the wait after
the click is on a generic panel selector, and the previously opened panel still matches it.
The wait returns instantly and you read stale content. Key the wait to the panel id from
`aria-controls`, and add a check that its text is non-empty.

**Should I expand every panel first and then read them all?** No. Some accordions unmount
the previous panel when the next one opens, so that order leaves you with one populated
panel and a set of empty nodes. Extract each panel while it is open.

**Are aria attributes really better than class names?** Yes, for two reasons. They express
state and control-to-panel pairing directly, and they are maintained because assistive
technology depends on them, while class names change whenever the styling does.

**Why do nested accordions come back empty below the first level?** Because expanding a
parent does not expand its children, and the child controls were not in the DOM when you
enumerated the list. Re-query for unexpanded controls after every click and stop when a full
pass finds none.

## Sources

- Playwright documentation, [Locators](https://playwright.dev/python/docs/locators), retrieved 2026-08-28
- Playwright documentation, [Auto-waiting](https://playwright.dev/python/docs/actionability), retrieved 2026-08-28
- Playwright documentation, [locator.text_content()](https://playwright.dev/python/docs/api/class-locator#locator-text-content), retrieved 2026-08-28
- Playwright documentation, [Events and response handling](https://playwright.dev/python/docs/events), retrieved 2026-08-28
- W3C, [WAI-ARIA Authoring Practices: Accordion Pattern](https://www.w3.org/WAI/ARIA/apg/patterns/accordion/), retrieved 2026-08-28

**See also:** [scraping shadow DOM](how-to-scrape-shadow-dom-playwright.md)
for panels behind a closed root, [scraping a load-more button](how-to-scrape-load-more-button-playwright.md)
for the same wait that passes too early, and [capturing XHR API responses](how-to-capture-xhr-api-responses-playwright.md)
for panels that fetch on first expand.

---

*Written while maintaining [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level driven by stock Playwright. Expanding every panel first and reading afterwards is the approach that failed
silently on the accordions that unmount the panel you just left.*
