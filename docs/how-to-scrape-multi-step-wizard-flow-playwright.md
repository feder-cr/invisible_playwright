---
title: "Scrape a multi-step wizard flow with Playwright"
description: "Scrape a multi-step wizard or checkout flow with Playwright: complete each step in order, carry the per-step tokens, and keep one identity for the whole flow."
parent: "Scraping with Playwright"
grand_parent: "Guides"
nav_order: 73
---


# Scrape a multi-step wizard flow with Playwright

A wizard is not a set of pages you can visit. It is a state machine wearing a set of
URLs. Each step is gated behind the previous one's server-side state, so the address bar
alone does not get you in, and the thing that trips most scrapers is treating step three
as a page instead of as a position you have to arrive at.

This page is the order to do it in: why a deep link fails, how to carry the state the URL
does not hold, why you re-read the DOM after every transition, and the one stealth reason
a long stateful flow is exactly where a single stable identity earns its keep.

## Why you cannot deep-link to step three

You cannot deep-link to step three because each step validates server-side state the
previous step set, so a direct visit to the final URL bounces you back to step one or to an
error. The instinct is to read the URL of the final step and go straight there; on a real
wizard that returns you to step one, or an error, and it does so on purpose.

Each step sets state on the server that the next step validates: a token in a hidden
field, a session cookie updated on submit, a per-step nonce, a flag that says step two was
actually completed. Step three checks for that state before it renders anything useful. No
state, no step. The URL is a label, not a key.

```python
from invisible_playwright import InvisiblePlaywright

with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()

    # Jumping straight to step three with no prior state.
    page.goto("https://example.com/wizard/step-3")

    # The server has nothing on file for this session, so it bounces you.
    # The page you get is step one, or an error, never step three.
    print(page.url)   # -> .../wizard/step-1
```

So the whole flow is sequential by construction. You complete step one, let the server
record it, and only then does step two exist for you. This is the same shape as
[completing a login before you can reuse the session](automating-login-vs-session-reuse.md):
the state is created by the act, not by the address.

## Set up one identity for the whole flow

Before touching the steps, fix the identity. A wizard is a long session, and long
sessions are where a shifting fingerprint gets noticed.

Pass a seed. Every field it implies - GPU, canvas hash, audio context, fonts, screen, and
the hundreds of others that make up a fingerprint - is derived from that seed and held
constant for the life of the context. One browser, one page, all steps inside the same
`with` block:

```python
from invisible_playwright import InvisiblePlaywright

proxy = {"server": "socks5://gate.example.com:1080", "username": "u", "password": "p"}

with InvisiblePlaywright(seed=42, proxy=proxy) as browser:
    page = browser.new_page()
    # every step of the wizard runs on THIS page, in THIS context,
    # with one identity that does not change between steps
    ...
```

Two things matter here. The whole flow lives in one context, so cookies and the identity
persist across steps without you doing anything. And the seed makes a failed run
reproducible: if step four breaks, the same seed gives the same machine, so you can replay
the exact session instead of hoping the next random draw looks the same. The proxy and
timezone default to a matched pair, which the [configuration page](configuration.md)
covers, and a mismatched pair is [a category of failure of its own](timezone-proxy-mismatch.md).

## Complete the steps in order and carry the state

Now walk the steps, and at each one grab the state the next step will demand. The most
common piece is a token the server writes into the DOM after you submit, which you never
see in the URL.

```python
with InvisiblePlaywright(seed=42, proxy=proxy) as browser:
    page = browser.new_page()

    # --- Step 1 ---
    page.goto("https://example.com/wizard/step-1")
    page.fill("#full-name", "Jordan Rivera")
    page.fill("#email", "jordan@example.com")

    # The token that step 2 validates is already in the page, put there
    # by the server for THIS session. Read it before you leave.
    step1_token = page.get_attribute("input[name=flow_token]", "value")

    page.click("#continue")

    # --- Step 2 ---
    # Do not assume step 2 is loaded just because you clicked.
    # Wait for a marker that only step 2 renders.
    page.wait_for_selector("#step-2")

    # Step 2 often carries a fresh token, not the same one. Re-read it.
    step2_token = page.get_attribute("input[name=flow_token]", "value")
    page.fill("#address-line-1", "500 Example Ave")
    page.click("#continue")

    # --- Step 3 ---
    page.wait_for_selector("#step-3")
    # ... and so on, each step gated behind the last
```

Notice what the code carries between steps that the URL does not: a token per step, read
straight from the DOM, refreshed each time because the server usually rotates it. If you
cache the step-one token and post it at step three, validation fails, because it was never
a step-three token. Carry the state the flow gives you, at the moment it gives it.

Use ordinary Playwright interaction methods (`fill`, `click`, `select_option`, `check`) so
the events look like a person did them. On this engine those events arrive as
[trusted events the way a real click does](playwright-clicks-istrusted.md), which matters
because a form that only ever advances on synthetic, untrusted events is its own tell.

## Re-read the DOM after every transition

The single most reliable way to break a wizard scraper is to hold a reference across a
transition. After each step, the DOM you had is gone.

A step transition can be a full navigation or an in-place re-render, and in both cases the
elements, tokens and even the selectors you were using can be replaced. Re-query
everything after the transition completes, and wait for a marker that belongs to the new
step before you read anything:

```python
def advance(page, next_marker):
    page.click("#continue")
    # Block until the NEXT step's own element is attached, not just until
    # the click resolves. This is the line people skip.
    page.wait_for_selector(next_marker, state="attached")
    # Re-read anything you need; the previous step's handles are stale now.
    return page.get_attribute("input[name=flow_token]", "value")

with InvisiblePlaywright(seed=42, proxy=proxy) as browser:
    page = browser.new_page()
    page.goto("https://example.com/wizard/step-1")
    page.fill("#full-name", "Jordan Rivera")

    token2 = advance(page, "#step-2")
    page.fill("#address-line-1", "500 Example Ave")

    token3 = advance(page, "#step-3")
    # token2 and token3 are different values, read fresh each time
```

If a step re-renders in place while a site script is watching, an action fired against the
old tree can surface as an
["execution context was destroyed" error](execution-context-destroyed.md), which reads
like a navigation race but is really you reaching for a DOM that no longer exists. Waiting
for the new step's marker before you touch anything is what removes it.

## Keep the identity stable, and do not rush the steps

Keep the seed-derived identity and the pace between steps constant for the whole flow: a
wizard is precisely where a shifting disguise falls apart, because five sequential steps
in one session build a story that has to stay consistent, unlike a single request that can
hide almost anything. This is the stealth part, and it is specific to long flows rather
than single page loads. Two things break that story:

- **An identity that shifts mid-flow.** If the canvas hash, the GPU string or the screen
  changes between step two and step four, no human sits behind that. One person's machine
  does not become a different machine while filling in a form. Holding every seed-derived
  field constant for the life of the context is what keeps step four's fingerprint equal
  to step one's, and you can check it: read a value like the FingerprintJS visitor ID on
  step one and again on the last step and they match, because nothing underneath moved.
- **Instant, uniform transitions.** A form completed in eighty milliseconds, steps that
  advance at a perfectly even interval, a pointer that teleports between fields. A flow
  that a real person spends a minute on, cleared in two seconds, is a behavioural signal
  no fingerprint fix touches. Let each step take a plausible moment, and let the mouse arc
  to the button rather than jumping to it, which the default cursor motion already does.

The measurement that makes this concrete: derive the identity from one seed, walk the
whole flow in one context, and diff the fingerprint reported on the first step against the
last. On this engine the seed-derived fields come back identical across every transition,
so the delta is zero, which is what "one human moved through the form" is supposed to look
like. A scraper that re-launches per step, or randomises per request, produces a non-zero
delta at exactly the moment a stateful flow is watching for it.

## Conclusion

Treat a wizard as a state machine, not a set of URLs. You cannot deep-link past the gates,
so complete the steps in order, and at each one carry the token or hidden field the server
just handed you, because the URL does not hold it. Re-read the DOM after every transition,
waiting for the new step's own marker before you touch anything. And run the entire flow in
one context on one seeded identity, because a long stateful session is exactly where a
fingerprint that shifts, or a set of instant transitions, stops looking like a person.

Get the sequencing right and most wizards are ordinary automation. Get it wrong and you
will spend a day debugging a step-three redirect that was never a step-three problem.

## Short answers to the questions that lead here

**Can I skip to the last step of a wizard by its URL?** No. Each step validates state the
previous step set on the server, so a direct visit to the final URL bounces you back to
the start or to an error. The URL is a label, not a key.

**What state do I have to carry between steps?** Whatever the server hands you at each
step and expects back at the next: a token in a hidden field, a per-step nonce, an updated
session cookie. Read it from the DOM after each transition, and expect it to change each
step rather than reusing the first one.

**Why does my scraper break on step two even though step one worked?** Usually because you
held a reference across the transition. The step-one DOM, and its tokens, are stale once
step two renders. Re-query after waiting for a step-two marker.

**Do I need a new browser or context for each step?** No, the opposite. Run the whole flow
in one context so cookies and the identity persist, and so the fingerprint stays constant
across every step.

**How do I make the transitions look human?** Do not fire everything instantly. Let each
step take a plausible interval, use real interaction methods so events are trusted, and let
the pointer travel to controls rather than teleporting.

**How do I debug a flow that fails intermittently?** Pin the identity with a seed so a
failing run is reproducible, then replay it. Same seed, same machine, so you can tell the
site changing from your session changing.

## Sources

- The real product API as documented on the [quickstart](quickstart.md) and
  [configuration](configuration.md) pages: `InvisiblePlaywright(seed=...)` returns a stock
  Playwright `Browser`, and every method used above is standard Playwright.
- This project's own gates on identity stability across a session, which assert that
  seed-derived fingerprint fields are equal at the start and end of a flow rather than
  merely present.

**See also:** [automating login versus reusing a session](automating-login-vs-session-reuse.md)
for the same state-before-access shape, [why clicks on this engine are trusted events](playwright-clicks-istrusted.md),
and [the "execution context was destroyed" error](execution-context-destroyed.md) that a
mid-step re-render can produce.

---

*Written while maintaining [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level driven by stock Playwright. The step-three redirect that
is really a state problem is a mistake I have made more than once.*
