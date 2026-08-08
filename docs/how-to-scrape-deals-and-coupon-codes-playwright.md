---
title: "How to scrape deals and coupon codes with Playwright"
description: "Scrape coupon codes cloaked behind a reveal button with Playwright: fire a trusted click, capture the code from a new tab, clipboard or XHR, and read expiry."
parent: "Scraping with Playwright"
grand_parent: "Guides"
nav_order: 52
---


# How to scrape deals and coupon codes with Playwright

To scrape deals and coupon codes with Playwright, drive a real browser that fires a trusted
click on each card's reveal button, then capture the code from wherever the click sends it
(a new tab, the clipboard, or an XHR response) and read expiry from the card's data
attribute rather than the ticking countdown text. A scripted `dispatchEvent` click is
untrusted and a reveal guarded by a bot check returns nothing.

Deal and coupon pages look like the easiest scrape on the web: a grid of cards, each with
a merchant, a discount and a code. Then you read the DOM and the code is not there. The
card shows a "Reveal code" button and nothing else, and the string you came for does not
exist on the page until someone clicks.

That is deliberate. Aggregators cloak the code behind an interaction precisely to defeat
cheap scrapers, and they often put a bot check on the reveal itself. This page is how to
get the reveal to actually fire, follow the code wherever the click sends it, read an
expiry that is computed live in JavaScript, and page a grid that scrolls forever.

## Why the code is not in the DOM

The coupon code is missing from the HTML because aggregators inject, copy or fetch it only
when you click the reveal, specifically to defeat scrapers that read the raw markup. The
merchant, discount and expiry ship in the card; the code does not.

Open a deal card and inspect it. The expiry, the merchant and the discount percentage are
usually right there in the markup. The code is not. The reveal is built to hide it until
an interaction happens, and it does that in one of a few ways:

- **A new tab.** The reveal opens the merchant in a second tab and drops the code onto an
  interstitial or into the URL fragment of the tab it opened.
- **The clipboard.** The click copies the code to the clipboard and shows a "copied"
  toast, so the string never lands in a readable element at all.
- **An XHR on interaction.** The code is fetched from an endpoint only when you click, and
  written into a panel that did not exist a moment earlier.

The common thread: the code is gated behind an event, and often behind a check on that
event. A scripted `dispatchEvent` click fires an untrusted event, and a reveal guarded by
a bot check reads that boolean and returns nothing. This is the whole reason a real
browser firing a real click matters here, and it is worth understanding
[why a Playwright click carries isTrusted=true](playwright-clicks-istrusted.md) while a
JavaScript-synthesised one never can.

## Launch a browser whose reveal click is trusted

The wrapper is a two-line change from stock Playwright, and the `browser` it hands back is
a real Playwright `Browser` with every method intact. A [seed makes the run reproducible](reproducible-agent-browser-identity-seed.md),
which matters when a reveal fails and you need the same identity to debug it.

```python
from invisible_playwright import InvisiblePlaywright

with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    page.goto("https://deals.example.com/category/electronics")

    cards = page.locator(".deal-card")
    cards.first.wait_for()
    print("cards on first page:", cards.count())
```

`page.click()` here drives Firefox through its native input path, so the reveal receives
an event with `isTrusted` set to true and the correct pointer sequence in front of it. The
mouse also arcs to the button on a curve rather than teleporting, which is the difference
between a reveal that fires and one that silently no-ops. Nothing about the code below is
wrapper-specific API: it is ordinary Playwright, which is the point.

## Capture the code from wherever the reveal sends it

You do not know in advance which of the three paths a given card uses, so handle all three
around a single click. Register a tab listener and a
[response listener that captures the XHR the click fires](how-to-capture-xhr-api-responses-playwright.md)
before clicking, then read the clipboard after, and take whichever one produced a value.

```python
import re

CODE_RE = re.compile(r"[A-Z0-9]{4,20}")

def reveal_code(page, card):
    context = page.context
    captured = {"code": None}

    # Path 3: the code arrives over XHR fired by the click.
    def on_response(response):
        if "coupon" in response.url or "code" in response.url:
            try:
                data = response.json()
            except Exception:
                return
            for key in ("code", "coupon", "voucher"):
                if isinstance(data, dict) and data.get(key):
                    captured["code"] = data[key]
    page.on("response", on_response)

    # Path 1: the reveal opens a new tab carrying the code. Fire the trusted
    # click and catch the tab if one opens; otherwise the click stays on-page.
    try:
        with context.expect_page(timeout=4000) as new_tab_info:
            card.locator(".reveal-code").click()
        new_tab = new_tab_info.value
        new_tab.wait_for_load_state()
        # code often sits in an interstitial element or the URL fragment
        text = new_tab.locator("[data-clipboard-text], .coupon-code").first
        if text.count():
            captured["code"] = text.get_attribute("data-clipboard-text") or text.inner_text()
        else:
            m = CODE_RE.search(new_tab.url)
            if m:
                captured["code"] = m.group(0)
        new_tab.close()
    except Exception:
        # no new tab opened; the click stayed on this page
        card.locator(".reveal-code").click()

    # Path 2: the code was copied to the clipboard.
    if not captured["code"]:
        try:
            clip = page.evaluate("navigator.clipboard.readText()")
            if clip and CODE_RE.fullmatch(clip.strip()):
                captured["code"] = clip.strip()
        except Exception:
            pass

    # Fallback: a panel appeared in place after the click.
    if not captured["code"]:
        panel = card.locator(".revealed-code")
        if panel.count():
            captured["code"] = panel.inner_text().strip()

    page.remove_listener("response", on_response)
    return captured["code"]
```

Two practical notes. Clipboard reads need the [`clipboard-read`
permission](https://playwright.dev/python/docs/api/class-browsercontext#browser-context-grant-permissions)
granted on the context (`browser.new_context(permissions=["clipboard-read"])` and open the
page from that context), or [`navigator.clipboard.readText()`](https://developer.mozilla.org/en-US/docs/Web/API/Clipboard/readText)
rejects. And a reveal that opens a tab is a
popup: if the site also throws a confirmation dialog in the middle, Playwright's default is
to silently dismiss it, which stalls the reveal. See
[dialog and popup handling without a tell](playwright-dialog-popup-handling.md) for the
listener that keeps that path moving.

## Read expiry from the data, not the ticking text

Every card carries a countdown ("expires in 02:14:57") and the temptation is to scrape that
string. Do not. It is computed live in JavaScript from a target timestamp, it is different
every second, and two reads a second apart disagree, which makes your dataset
non-reproducible for no reason.

The value you want is the target the timer counts down to, and it almost always lives in a
data attribute on the element the script reads on load.

```python
def read_expiry(card):
    timer = card.locator("[data-expires], [data-end-ts], .countdown").first
    if not timer.count():
        return None
    # prefer a stable attribute over the rendered text
    for attr in ("data-expires", "data-end-ts", "data-expiry"):
        val = timer.get_attribute(attr)
        if val:
            return val
    return None  # only fall back to parsing text if no attribute exists
```

Reading the attribute rather than the rendered text gives you the same value on every run
of the same seed, which is what makes a scrape you can diff against yesterday's.

## Page the grid without a mechanical scroll

Deal grids scroll infinitely: more cards load as you reach the bottom, and there is no page
2 to request. Scroll, wait for the card count to actually grow rather than sleeping a fixed
interval, and stop when it stops growing.

```python
def load_all_cards(page, max_rounds=40):
    cards = page.locator(".deal-card")
    seen = 0
    for _ in range(max_rounds):
        count = cards.count()
        if count == seen:
            break  # no new cards arrived; the grid is exhausted
        seen = count
        cards.nth(count - 1).scroll_into_view_if_needed()
        try:
            page.wait_for_function(
                "(n) => document.querySelectorAll('.deal-card').length > n",
                arg=count,
                timeout=6000,
            )
        except Exception:
            break  # growth stalled within the timeout
    return cards.count()
```

A perfectly uniform scroll every N milliseconds is itself something a page can watch for,
and the block that follows arrives minutes into a session rather than at the first request.
The full treatment, including how to vary the rhythm, is in
[how to scrape infinite scroll pages with Playwright](how-to-scrape-infinite-scroll-playwright.md).
Put the three pieces together and one card becomes a record:

```python
with InvisiblePlaywright(seed=42) as browser:
    context = browser.new_context(permissions=["clipboard-read"])
    page = context.new_page()
    page.goto("https://deals.example.com/category/electronics")
    page.locator(".deal-card").first.wait_for()

    load_all_cards(page)

    rows = []
    cards = page.locator(".deal-card")
    for i in range(cards.count()):
        card = cards.nth(i)
        rows.append({
            "merchant": card.locator(".merchant").inner_text().strip(),
            "discount": card.locator(".discount").inner_text().strip(),
            "expires": read_expiry(card),
            "code": reveal_code(page, card),
        })
    print(f"{len([r for r in rows if r['code']])}/{len(rows)} codes captured")
```

## What a trusted click cannot do

Be honest about the boundary. The trusted click and the real fingerprint get an
interaction-gated reveal to fire where a scripted click returns nothing, and that is a real
and measurable difference: on a card whose reveal reads `event.isTrusted`, the same code
that returns `None` under a dispatched event returns the string under this one.

That is where it stops. A code gated behind an actual logged-in account is beyond a click:
no fingerprint substitutes for a session you do not have. A reveal that hands off to a
separate challenge is a separate problem, not a reveal problem. And a browser that looks
real still fails on a bad exit IP, because the address is not a browser property. This page
solves the interaction gate. It does not solve authentication, and it does not solve the
network. Before blaming the reveal, confirm the click is the actual barrier by walking
[the detection checklist](playwright-detected-as-bot.md) in order.

## Conclusion

Coupon codes are cloaked on purpose, and the cloak is an interaction: the code is absent
from the DOM until a reveal fires, and the reveal is often guarded by a check that a
scripted click fails. A real browser firing a trusted click, with the new-tab, clipboard
and XHR paths all handled around it, is what surfaces the string. Read expiry from the data
attribute rather than the ticking text so your run is reproducible, page the grid by
waiting for growth rather than sleeping, and know that the method ends at the
interaction-gated reveal, not at an account or a challenge behind it.

## Short answers to the questions that lead here

**Why is the coupon code missing from the HTML?** Because it is cloaked behind the reveal
interaction on purpose. The card ships with the merchant, discount and expiry, but the code
is only injected, copied or fetched when you click, specifically to defeat scrapers that
only read the DOM.

**Why does my scripted click return nothing?** Because a JavaScript-dispatched event carries
`isTrusted=false`, and a reveal guarded by a bot check reads that boolean and refuses. A
click driven through the browser's native input path is trusted and the reveal fires.

**Where does the code go after the click?** One of three places: a new tab, the clipboard,
or a panel filled by an XHR the click triggered. Handle all three around a single click and
take whichever produced a value.

**How do I read the expiry reliably?** From the timer's data attribute (`data-expires` or
similar), not the counting-down text. The text is recomputed every second and changes
between two reads; the attribute is stable.

**How do I get past the infinite scroll?** Scroll the last card into view, wait for the card
count to actually increase rather than sleeping a fixed time, and stop when it stops
growing.

**Can this get codes behind a login?** No. A trusted click surfaces an interaction-gated
reveal. A code behind an actual account or a separate challenge is beyond what a click and a
real fingerprint can do.

## Sources

- This project's own measurements of interaction-gated reveals: the same extraction that
  returns `None` under a dispatched event returns the code under a trusted click on a reveal
  that reads `event.isTrusted`.
- Playwright's documented [context permissions](https://playwright.dev/python/docs/api/class-browsercontext#browser-context-grant-permissions)
  (including `clipboard-read`) and the [`Clipboard.readText()`](https://developer.mozilla.org/en-US/docs/Web/API/Clipboard/readText)
  method it unlocks, plus its tab, dialog and response handling, read from the documented
  behaviour rather than from a tutorial.

**See also:** [Playwright isTrusted: are automated clicks real?](playwright-clicks-istrusted.md)
for why the reveal fires at all, and
[how to test whether your browser is detected](how-to-test-bot-detection.md) before you
assume the click is the problem.

---

*Written while maintaining [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level driven by stock Playwright. The reveal-path handling
above is the part every generic coupon-scraper tutorial skips, and it is the part that
actually returns a code.*
