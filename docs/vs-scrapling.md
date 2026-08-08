---
title: "invisible_playwright vs Scrapling"
description: "invisible_playwright vs Scrapling: Scrapling is an adaptive parser, not a stealth engine, so its detectability ceiling is whichever browser it wraps."
parent: "Comparisons"
nav_order: 14
---


# invisible_playwright vs Scrapling

invisible_playwright and Scrapling are not really competitors: invisible_playwright is
a stealth browser engine, while Scrapling is an adaptive HTML parser whose stealth is
only ever as good as the browser it wraps. A comparison of detectability against
Scrapling is therefore a comparison against that underlying engine, not against
Scrapling itself.

Scrapling is the most-starred project in this whole space, with more than 72,000
stars, and that popularity makes it the natural thing to reach for. It is worth being
precise about what it is, because the headline number measures a parser, not a stealth
engine, and those are different problems with different ceilings.

This page is about that distinction: what Scrapling is genuinely excellent at, where
its stealth actually comes from, and why "which one is harder to detect" is a question
about a component Scrapling does not ship.

| Property | invisible_playwright | Scrapling |
|---|---|---|
| What it is | A stealth browser engine (C++-patched Firefox) | An adaptive scraping framework (HTML parser) |
| Layer it works at | The fetch: makes the request look like a real browser | Post-fetch: parses HTML that already arrived |
| Where stealth comes from | Built in, answered below the JavaScript surface | Delegated to a third-party browser it wraps |
| Detectability ceiling | The patched engine itself | Whichever engine the installed version wraps |
| Ships its own fingerprint engine | Yes, every field from one seed | No |
| Survives a site redesign | Not its job | Yes, via a similarity-based adaptive parser |
| Best used | To fetch a page credibly | To keep extraction alive across markup changes |

## What Scrapling actually is

Scrapling is an adaptive web scraping framework. Its standout feature, and the reason
to use it, is a parser that learns the structure of a page and re-locates your elements
after the HTML changes. You mark a selector as adaptive, and when a site ships a
redesign that moves or renames the node you were targeting, the parser finds it again
by similarity instead of returning nothing. That is a real, hard, useful thing, and it
is the differentiator its README leads with.

Everything about that is a post-fetch concern. The parser operates on HTML that already
arrived. It does not decide whether the request that fetched the HTML looked like a real
browser, because by the time the parser runs, the fetch is over.

## Where the stealth comes from

Scrapling exposes a stealthy fetcher, and here is the part that matters for a detection
comparison: that fetcher does not contain a stealth engine of its own. It delegates the
disguise to a third-party browser. Historically that was one open-source engine up to
release 0.3.13, and a different open-source patched-Chromium project after it.

The consequence is direct. Scrapling's detectability ceiling is whichever engine it
happens to wrap in the version you installed. If the underlying engine reports a
software renderer in a container, or a font set that does not match its claimed
platform, or a canvas signal that varies per call, Scrapling reports exactly that,
because it is passing the question through. Upgrading Scrapling can change the engine
under you, and with it the fingerprint, without a line of your own code changing.

Both of the engines it has wrapped are ones this documentation already covers directly:
see [how invisible_playwright compares to the Firefox-based one](vs-camoufox.md) and
[to the patched-Chromium one](vs-patchright.md). The comparison you are on this page to
make is, underneath, one of those two.

## What invisible_playwright does instead

invisible_playwright is the engine layer, not a wrapper around one. It is a Firefox
patched at the C++ level, so the values a detector reads are answered below the
JavaScript surface a page can inspect. It is driven by stock Playwright, so there is no
new automation API to learn, and it derives every fingerprint field from a single seed.

Switching from plain Playwright is two lines, and the returned object is a real
[Playwright `Browser`](https://playwright.dev/python/docs/api/class-browser) with every
method intact:

```python
from invisible_playwright import InvisiblePlaywright

with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    page.goto("https://example.com")
    html = page.content()
    print(html[:200])
```

The seed is the operational difference that a parser cannot provide. Pass one and the
GPU, canvas hash, audio context, fonts and screen come back identical on every run,
which is what turns a flaky failure into a reproducible one:

```python
# same seed, same machine, every run - a failing run replays exactly
with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    page.goto("https://example.com/search")
    print("visitor should be identical across runs")
```

Proxy and timezone are configuration, and the timezone auto-derives from the egress IP
so the browser and the exit tell the same story:

```python
from invisible_playwright import InvisiblePlaywright

proxy = {"server": "socks5://gate.example.com:1080", "username": "u", "password": "p"}

with InvisiblePlaywright(seed=42, proxy=proxy) as browser:
    page = browser.new_page()
    page.goto("https://example.com")
```

The full set of proxy schemes and environment variables is on the
[configuration](configuration.md) page, and forcing an individual field while leaving
the rest seed-derived is covered under [pinning](pinning.md).

## Using them together, because they are not rivals

The honest framing is that these tools sit at different layers, and the interesting
setup uses both. Let invisible_playwright be the engine that fetches a page credibly,
then hand its HTML to Scrapling's adaptive parser to survive the site's next redesign:

```python
from invisible_playwright import InvisiblePlaywright

with InvisiblePlaywright(seed=42, proxy=proxy) as browser:
    page = browser.new_page()
    page.goto("https://example.com/listing")
    html = page.content()

# feed `html` to Scrapling's adaptive parser here - it re-locates your
# elements after the markup changes, which is the job it is genuinely best at
```

Nothing above competes. The engine decides whether the fetch looks human; the parser
decides whether your extraction survives a layout change. Choosing one because it has
more stars is choosing a parser to solve a fingerprint problem, or the reverse.

## How to test the difference yourself

Do not take either project's word, including this one's. The method that settles it is
the same one described in
[how to test whether your browser is detected](how-to-test-bot-detection.md): open a
detection suite in the automated browser and in a stock browser on the same machine,
and diff the report field by field rather than reading a verdict.

Run it against whatever engine Scrapling's stealthy fetcher wraps in your installed
version, then against invisible_playwright, both through the same proxy and on the same
host. The fields that differ from a stock browser are the tells. A parser cannot change
any of those fields, because it never sees the request; it only reads what came back.
For what a green result does and does not prove before you trust it, the
[stealth levels](playwright-stealth-levels.md) page is the honest floor.

## Conclusion

Scrapling deserves its stars for the adaptive parser, which solves a real and annoying
problem that has nothing to do with disguise. But a comparison of detectability against
Scrapling is really a comparison against the engine it delegates to, and both of those
engines already have their own page here. invisible_playwright is that engine layer: a
C++-patched Firefox, stock Playwright, one seed. Use Scrapling's parser on top of it if
the markup keeps moving. Just do not mistake the most-starred parser for the most
undetectable browser, because it is not trying to be one.

## Short answers to the questions that lead here

**Is Scrapling a stealth browser?** No. It is an adaptive scraping framework whose
stealthy fetcher delegates the disguise to a third-party browser engine, so its ceiling
is whichever engine it wraps.

**Which is harder to detect, Scrapling or invisible_playwright?** Whichever engine
Scrapling wraps in your version versus a C++-patched Firefox. Test both against a suite
in the same conditions and diff the fields; the parser plays no part in that answer.

**Should I use Scrapling or invisible_playwright?** Often both. invisible_playwright
fetches the page credibly, Scrapling's parser keeps your extraction alive across
redesigns. They live at different layers.

**Does Scrapling have its own fingerprint engine?** No. It passes fingerprinting to the
browser it wraps, which has changed across releases, so an upgrade can change your
fingerprint without any change on your side.

**What is Scrapling actually best at?** Re-locating elements after a site changes its
HTML, via a similarity-based adaptive parser. That is its genuine differentiator and it
is a good one.

**Can I feed invisible_playwright's HTML to Scrapling?** Yes. Call `page.content()` on
the real Playwright page and pass the string to Scrapling's parser. The engine handles
the fetch, the parser handles the extraction.

## Sources

- Scrapling's own repository README, read for its stated purpose (an adaptive scraping
  framework), its adaptive parser feature, and its stealthy fetcher delegating to a
  third-party browser engine rather than shipping one.
- This project's own comparison pages for the two engines Scrapling has wrapped, and the
  release gates behind the field-by-field test method described above.
- [Playwright's own `Browser` class docs](https://playwright.dev/python/docs/api/class-browser),
  read 2026-08-06, for what the returned object exposes once you switch from plain
  Playwright.

**See also:** [the comparison with the Firefox-based engine](vs-camoufox.md),
[the comparison with the patched-Chromium engine](vs-patchright.md), and
[what the stealth levels actually mean](playwright-stealth-levels.md).

---

*Written while maintaining [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level driven by stock Playwright. The parser-versus-engine
line is the whole point: they solve different problems, and only one of them decides
whether the fetch looked human.*
