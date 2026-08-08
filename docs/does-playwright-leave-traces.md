---
title: "Does Playwright Leave Traces a Website Can See?"
description: "Playwright leaves some traces a page can read, some it cannot. Which are page-visible, why the control WebSocket is not, and how patched Firefox reads ordinary."
parent: "The Automation Layer"
grand_parent: "Guides"
nav_order: 22
---


# Does Playwright Leave Traces a Website Can See?

Short version: Playwright leaves traces, but only some of them are readable by the
page you are visiting. The distinction matters more than the list, because most of the
famous "Playwright tells" are things the page can see in its own document, and the part
people worry about most - the connection the driver uses to control the browser - is not
one of them.

This page splits automation traces into two piles, page-visible and off-page, shows a
DOM check you can run yourself, explains why Chromium-based tooling and Firefox differ on
one specific tell, and states plainly where a clean document still is not enough.

## Two kinds of trace: what the page can read, and what it cannot

A running automation session has two very different surfaces.

**Off-page.** The driver talks to the browser over a control channel: a local
WebSocket or pipe that carries the commands your script issues (navigate, click, read
this element). That channel lives between your process and the browser process. The page
being automated has no handle to it, cannot open it, and cannot enumerate it. It is
server-side relative to the document, in the same sense a debugger attached to a process
is invisible to the code running inside.

**Page-visible.** Everything the automation writes into the document or the global
scope. If a tool sets a property on `navigator`, defines a global, or injects a helper
object into `window`, then any script the page runs can read it back with one line. This
is the pile that detectors actually query, because it is the only pile they can reach
from JavaScript.

The useful question is therefore never "does Playwright leave traces" but "which pile is
this trace in". A trace in the first pile is not something a page can test for. A trace
in the second pile is a one-line check, and that is where the work is.

## The control channel: a trace, but not one the page can see

The WebSocket (or pipe) is real. If you inspect your own machine while a session runs,
it is right there. But it is off-page, and that has a concrete consequence: a website
cannot detect automation by looking for the driver connection, because nothing in the
document reaches across that boundary.

This is why "close the DevTools port" style advice misses the point for in-page
detection. Whether the transport is a Chrome DevTools Protocol socket, a WebDriver BiDi
session, or Firefox's own Juggler protocol, the page sees the same thing either way:
nothing. The differences between those transports show up in other places -
[the protocol a tool speaks changes what artifacts land in the page](bidi-vs-cdp-detection.md) -
but the raw existence of a control socket is not a page-readable signal.

What the page reads is the residue the driver leaves behind in the document. That is the
next pile.

## The traces a page CAN see

Three families, in rough order of how often they appear:

- **A webdriver flag.** The [WebDriver specification](https://www.w3.org/TR/webdriver2/)
  requires a conforming browser to expose `navigator.webdriver` as `true` while a session
  is under automation control. That is a page-visible boolean by design, and a stock
  automated browser reports it honestly. Patching it in a page script trades one tell for
  another, because a clean browser reports `undefined` and not `false` - the
  [full reason setting it to false is worse than leaving it alone](navigator-webdriver-explained.md)
  is its own page.
- **Automation globals.** Some stacks leave a named object on `window` so their in-page
  code has somewhere to keep state. Anything on the global object is enumerable, so a
  named global is a named tell.
- **Injected document properties.** On Chromium-based tooling driven through
  ChromeDriver, the driver keeps working state on the document under `cdc_` / `$cdc`
  property names. Their presence is a one-line automation test, and
  [renaming them in the binary does not remove the surface](cdc-variable-explained.md),
  it only changes its shape.

All three are page-visible because they live in the document or the global scope. None of
them is the control channel. Fixing them is a matter of not writing them into the page in
the first place, which is a design decision, not a runtime patch.

## Chromium injects cdc_ into the document; Firefox through Juggler does not

Chromium-based tooling writes `cdc_`-prefixed properties into the page; Firefox driven
through Juggler writes none, because its driver never has to touch the document at all.

ChromeDriver needs to run its own JavaScript inside the page to do its job, and it keeps
state on the page's own objects under `cdc_`-prefixed names. That is a page-visible
artifact of how that particular driver works.

Firefox driven through its own Juggler protocol does not use ChromeDriver, so `cdc_`
simply does not exist there. That is a fact about the protocol, not a claim of virtue:
Firefox's automation state lives in privileged execution contexts the page cannot
enumerate, so the driver does not have to write into the document to function. The
`cdc_` family of checks returns nothing on a Firefox session for a structural reason.

That structural point is only half the story, though, because a stock Firefox under
automation still sets the webdriver flag to `true`. So "no `cdc_`" does not mean "looks
like a person" on its own. What closes the remaining gap is where the value is decided.
On the patched Firefox this project ships, the webdriver flag is cleared and the
fingerprint is normalized inside the engine, before it ships, so the document a site
inspects reports the same values a normal build reports, through the same native code
path. There is no in-page override for a detector to catch, because there is no override.
The wider comparison of the two engines lives in
[Firefox versus Chromium for anti-detect work](firefox-vs-chromium-antidetect.md).

## Check the document yourself

Do not take any of this on trust. Enumerate the document and the global scope in your own
session and read what is there. With the real API the launch is two lines and the check
is one `page.evaluate`:

```python
from invisible_playwright import InvisiblePlaywright

with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    page.goto("https://example.com")

    report = page.evaluate("""() => ({
        webdriver: navigator.webdriver,
        cdc_on_document: Object.getOwnPropertyNames(document)
                              .filter(k => k.startsWith('cdc_') || k.startsWith('$cdc')),
        suspicious_globals: Object.getOwnPropertyNames(window)
                              .filter(k => /^(cdc_|\\$cdc|__playwright|__driver|__webdriver)/.test(k)),
    })""")

    print(report)
```

On this patched Firefox, `webdriver` comes back `undefined` (not `false`), and both
lists come back empty: no `cdc_` on the document, no automation global on `window`. The
same check on a stock ChromeDriver session is where you see the `cdc_` entries appear.
Passing `seed=42` just means the run is reproducible, so you can re-run the exact same
identity and compare - the
[wider method for testing detection](how-to-test-bot-detection.md) is to compare against
a stock browser field by field rather than reading a single verdict.

Note what this check does not include: the control-channel WebSocket. There is no
expression you can write in `page.evaluate` that finds it, because it is off-page. That
absence is the point of this whole page.

## What this fixes, and what it honestly does not

Clearing the page-visible traces is what makes invisible_playwright pass most in-document
detection: the automation flag, the injected globals, and the fingerprint (GPU, audio,
fonts, screen, roughly 400 fields) all read as a genuine Firefox, because they are
produced by a genuine, if patched, Firefox. That is the demonstrable part, and the DOM
check above is how you confirm it for yourself.

"No page-visible traces" is not the same as "invisible", and it is worth being blunt
about the difference:

- **The network layer.** The TLS handshake and HTTP/2 settings are decided before any
  page script runs, and [an in-page test cannot see them](ja3-ja4-tls-fingerprint.md),
  which cuts both ways: a clean document does not fix a handshake that contradicts the
  user agent. The patched engine's handshake is a real Firefox handshake, so this
  particular mismatch is not introduced, but the point stands that this surface is
  outside the document.
- **IP reputation.** A consistent browser on a datacenter address is still on a
  datacenter address. That is not a browser property, and no amount of fingerprint work
  changes it. You supply a clean exit, and the browser stays consistent with it.
- **Per-account quotas and rate limits.** If an account or a source address makes more
  requests than a person plausibly would, that is measured server-side and is invisible
  to any document check.
- **Behaviour and timing.** Pointer motion, typing rhythm, and pacing are watched by
  some sites regardless of how clean the document is. This project drives real mouse
  motion, but the cadence of your script is yours to make human.

So the honest framing: invisible_playwright is built to look like a real browser driven
by a real person, which is why it clears the page-visible driver and fingerprint layer.
It does not, on its own, fix your IP, your account limits, or your pacing. Those are the
reader's to supply - a clean proxy and human timing - and a page that reads as ordinary
plus a session that behaves ordinarily is the combination that actually holds.

## Conclusion

Playwright leaves traces, but sort them before you worry about them. The control channel
is a real trace and the page cannot see it, so it is not a detection vector from the
document. The webdriver flag, automation globals, and Chromium's `cdc_` injection are
page-visible, and those are the ones detectors query. Firefox through Juggler carries no
`cdc_` for a structural reason, and a Firefox patched in the engine additionally clears
the webdriver flag and normalizes the fingerprint, so the document reads as an ordinary
browser. Confirm that with the one-line DOM check rather than believing the paragraph.
Then remember that a clean document is a necessary condition, not a sufficient one: the
network, the address, the account, and the behaviour are still yours to get right.

## Short answers to the questions that lead here

**Does Playwright leave traces a website can detect?** Some yes, some no. Automation
globals, a webdriver flag, and (on Chromium tooling) `cdc_` properties are page-visible.
The driver's control WebSocket is off-page and a document cannot see it.

**Can a website see the WebSocket Playwright uses to control the browser?** No. That
channel is between your process and the browser process. Nothing in the page has a handle
to it, so it is not an in-page detection signal.

**Does Firefox have the `cdc_` variables Chrome does?** No. Those come from ChromeDriver.
Firefox driven through its Juggler protocol keeps driver state off the page, so a `cdc_`
scan returns nothing on a Firefox session.

**Is `navigator.webdriver` always true under automation?** On a stock automated browser,
yes, because the spec requires it. On the patched Firefox this project ships it is cleared
in the engine, so it reports `undefined` like a normal browser, not `false`.

**If the document is clean, am I undetectable?** No. A clean document does nothing about
IP reputation, per-account quotas, rate limits, the TLS handshake, or your timing. Those
are separate surfaces you have to handle yourself.

**How do I check what my own session exposes?** Enumerate `window` and `document` in a
live session with `page.evaluate` and read `navigator.webdriver`, as in the example
above, then compare the result against a stock browser on the same machine.

## Sources

- This project's own sessions, enumerating `window` and `document` on a live
  Playwright-driven Firefox rather than trusting a description of one.
- The public detection suites (CreepJS, BotD, sannysoft, FingerprintJS, BrowserLeaks),
  each read from its own source for what it collects from the document.
- The Playwright, [WebDriver BiDi](https://www.w3.org/TR/webdriver-bidi/), and Juggler
  protocol behaviour for where driver state lives relative to the page.

**See also:** [the ChromeDriver cdc_ variable](cdc-variable-explained.md),
[why navigator.webdriver is not the tell you think it is](navigator-webdriver-explained.md),
and [how to test whether your browser is detected](how-to-test-bot-detection.md).

---

*Written while maintaining [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level driven by stock Playwright. The DOM check above is one
I run against a real session before repeating any claim about what a page can see.*
