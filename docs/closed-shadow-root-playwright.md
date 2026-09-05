---
title: "Closed shadow roots: what Playwright cannot see"
description: "Stock Playwright finds nothing inside a closed shadow root, in Firefox and Chromium alike. Measured, with the page that reproduces it and what this engine does instead."
parent: "The Automation Layer"
grand_parent: "Guides"
nav_order: 45
---


# Closed shadow roots: what Playwright cannot see

A closed shadow root makes `element.shadowRoot` return `null`, and stock
Playwright locators stop at that boundary: measured on 5 September 2026
against a page with one open and one closed root, stock Firefox and stock
Chromium both matched the open content and returned zero matches for the
closed content. The engine this wiki documents returns one match for the
same closed content, and the page still reads `null`.

For the ordinary case, open shadow roots, Playwright crosses them by itself
and there is nothing to configure: see
[how to scrape shadow DOM content with Playwright](how-to-scrape-shadow-dom-playwright.md).
This page is about the other case, and it starts with a number rather than
an opinion.

## The measurement, and the page that reproduces it

The test page defines two custom elements. One attaches its shadow root with
`mode: "open"`, the other with `mode: "closed"`, and each puts a unique
string inside. Served from `127.0.0.1`, so no network variability, and the
same file for every arm.

```html
<open-widget></open-widget>
<closed-widget></closed-widget>
<script>
class OpenWidget extends HTMLElement {
  connectedCallback() {
    const r = this.attachShadow({mode: "open"});
    r.innerHTML = '<p id="inner-open">OPEN_SECRET_VALUE_7788</p>';
  }
}
class ClosedWidget extends HTMLElement {
  connectedCallback() {
    const r = this.attachShadow({mode: "closed"});
    r.innerHTML = '<p id="inner-closed">CLOSED_SECRET_VALUE_9911</p>';
    this.readIt = () => r.querySelector("#inner-closed").textContent;
  }
}
customElements.define("open-widget", OpenWidget);
customElements.define("closed-widget", ClosedWidget);
</script>
```

Three arms, one instrument, same page:

| arm | `#inner-open` | `#inner-closed` | text inside the closed root | `closed.shadowRoot` |
|---|---|---|---|---|
| Playwright, stock Firefox | 1 match | 0 matches | 0 matches | `null` |
| Playwright, stock Chromium | 1 match | 0 matches | 0 matches | `null` |
| Playwright, this engine | 1 match | **1 match** | **1 match** | `null` |

The stock rows are exactly what Playwright's own documentation promises:
"Closed-mode shadow roots are not supported." The third row is this engine,
and the last column is the part that matters most: from the page's point of
view nothing changed. `element.shadowRoot` still reads `null`, because the
resolution happens beneath the page's JavaScript rather than by rewriting
what the page can see.

## Why the boundary exists at all

`attachShadow({ mode: "closed" })` is defined to hide the subtree from
outside code. MDN puts it plainly: elements inside the shadow root "cannot
be accessed from JavaScript via the `shadowRoot` property, which is set to
`null`". The mode does not hide anything from the component itself, because
`attachShadow()` returns the root to the code that called it, and that code
keeps the reference for the life of the page.

So a closed root is an encapsulation choice by the component author, not a
protection against automation, and it is not a detection surface either.
Nothing about a closed root tells a site who is visiting.

## How to recognise one in ten seconds

Three signals together, never one alone. The element renders visible
content. A selector aimed inside it returns nothing. And the host's
`.shadowRoot`, read from the console, comes back `null`.

That last signal does not separate a closed root from an element with no
shadow root at all. Pair it with the host's own `textContent`: on our test
page the closed host returns an empty string while text is plainly on
screen, which is the tell. Content you can see, text you cannot read from
the host, and a `null` shadow root is a closed root every time.

```python
from invisible_playwright import InvisiblePlaywright

with InvisiblePlaywright(seed=42, headless=True) as browser:
    page = browser.new_page()
    page.goto("http://127.0.0.1:8731/page.html")

    host = page.query_selector("closed-widget")
    print(host.evaluate("el => el.shadowRoot !== null"))   # False: null, as the spec says
    print(host.evaluate("el => el.textContent"))           # '' while text is on screen
    print(page.locator("#inner-closed").count())           # 1 on this engine, 0 on stock
```

## What still works when a selector will not reach

On stock Playwright the closed subtree is off limits to selectors, and
three routes remain, each for a different reason.

The component's own script keeps full access, so any interface the author
deliberately exposed on the host still answers. On the test page above the
component sets a `readIt()` method on its host, and `page.evaluate` calling
that method returned the closed string on every arm, stock included. A well
built component publishes properties, methods, or events dispatched with
`composed: true`, and that public surface is the door the author intended.

Input reaches the component regardless. The host sits in ordinary light DOM,
so it has a real bounding box, it can be clicked, and keyboard focus moves
into the controls inside it. Neither route lets you name what you are
touching, but both reach it.

And the data usually exists before the component renders it. Most closed
components are filled from a network response, and
[waiting for that response](wait-for-specific-api-response-playwright.md)
sidesteps the rendered tree entirely.

## The workaround that is worse than the problem

You will find snippets that patch `Element.prototype.attachShadow` before
the page's scripts run so every later call becomes `mode: "open"`. It has
two problems. It only wins a timing race you rarely control, and it changes
what the page itself can observe: after the patch, `element.shadowRoot`
stops being `null` on components whose authors asked for `null`. That is a
difference a consistency check can read, which is the opposite of the goal
on a wiki like this one.

The measurement above is the argument against it. Reaching the content while
leaving `shadowRoot` at `null` is strictly better than reaching it by making
the page lie about itself.

## Short answers to the questions that lead here

**Why does my Playwright locator find nothing when the element is visible?**
If the host's `.shadowRoot` reads `null` while text renders on screen, the
content is inside a closed shadow root. Stock Playwright locators stop
there, in Firefox and Chromium alike.

**Does Playwright support closed shadow roots?** No. Its documentation says
so directly, and the measurement above confirms it on both stock engines:
zero matches for content that is plainly rendered.

**How do I tell an open root from a closed one?** Read `element.shadowRoot`.
An object means open. `null` means closed, or no shadow root at all, and the
host's empty `textContent` next to visible text separates those two.

**Can I force a closed root open?** You can patch `attachShadow` before the
component runs, and you should not. It depends on a race you do not control
and it changes what the page can observe about itself.

**What if the data only exists inside a closed root?** Call whatever the
component exposes on its host, or read the network response that filled it.
On this engine, a locator also reaches the content directly.

**See also:** [scraping open shadow DOM with Playwright](how-to-scrape-shadow-dom-playwright.md),
[scraping iframe content with Playwright](how-to-scrape-iframe-content-playwright.md),
and [waiting for a specific API response](wait-for-specific-api-response-playwright.md).

## Sources

- MDN, `Element.attachShadow()`,
  https://developer.mozilla.org/en-US/docs/Web/API/Element/attachShadow -
  the `closed` mode definition and the quoted sentence about `shadowRoot`
  being set to `null`. Read 5 September 2026.
- Playwright, Locators,
  https://playwright.dev/python/docs/locators - "All locators in Playwright
  by default work with elements in Shadow DOM" and "Closed-mode shadow roots
  are not supported". Read 5 September 2026.
- Our own measurement, 5 September 2026: the page above served from
  `127.0.0.1`, run against stock Playwright Firefox, stock Playwright
  Chromium and this engine, same script for all three arms.

---

*From [invisible_playwright](https://github.com/feder-cr/invisible_playwright).
The three-arm table is our own run and the test page is printed above so you
can reproduce it rather than take our word for it.*
