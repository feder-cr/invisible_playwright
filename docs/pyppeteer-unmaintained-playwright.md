---
title: "pyppeteer's own maintainer says to switch to Playwright"
description: "pyppeteer's own README says it is unmaintained and points to playwright-python. Why the maintainer names Playwright specifically, and what switching does not fix."
parent: "Comparisons"
nav_order: 10
---


# pyppeteer's own maintainer says to switch to Playwright

`pyppeteer` is unmaintained, and its own maintainer recommends switching to
Playwright. The README opens with a plain notice: "this repo is unmaintained and has
been outside of minor changes for a long time. Please consider playwright-python as
an alternative." That is the finding, stated by the source itself, in its own words.

Most maintenance questions in this section need a look at a repository's commit
history to answer. `pyppeteer` doesn't: the notice is the first thing you read. What's
worth unpacking is why the recommendation lands specifically on Playwright rather than
on "a more actively updated Puppeteer port," and what that says about the difference
between the two libraries rather than just about one project's staffing.

## What pyppeteer was, briefly

Before Playwright existed, `pyppeteer` filled a real gap: Puppeteer was Node-only,
and a lot of automation and scraping work happens in Python. Porting it gave Python
users direct CDP-based control over Chromium without leaving their language. For
several years this was a genuinely popular way to drive a headless Chromium browser
from Python.

## Why the fix isn't "find a more active fork"

The maintainer points to Playwright, not to a competing Puppeteer port with fresher
commits, because the recommendation is about architecture, not just activity.
Playwright is a different library built by a different team with a different
underlying design, and that design is the reason the port stopped being where the
energy went.

The two libraries differ on more than staffing:

| | pyppeteer | Playwright |
|---|---|---|
| Maintenance | Unmaintained, per its own README | Actively developed |
| Browser family | Chromium only | Chromium, Firefox, WebKit |
| Automation protocol | CDP only | Per engine (CDP for Chromium, Firefox's own automation layer for Firefox) |
| Design premise | A Python port of Puppeteer | One driver abstraction over three engines |

Puppeteer and pyppeteer are both built around one browser family and one protocol:
Chromium and CDP. Playwright was built afterward with a different premise, one
driver abstraction over [three separate
engines](https://playwright.dev/python/docs/browsers) - Chromium, Firefox, and
WebKit - each through whatever protocol actually works for that engine (CDP for Chromium,
Firefox's own automation layer for Firefox). That's a wider foundation than a CDP
port can be extended into, and it's a reasonable part of why maintaining a
Python-side Puppeteer port stopped being where the energy went: the actively
developed alternative wasn't a faster-moving copy of the same idea, it was a
broader one.

## What this means if pyppeteer is what you're running today

The practical read is straightforward, and it's the same one the maintainer already
gave: functioning code doesn't need fixing today, but a project with no ongoing
development won't track new Chromium releases, new CDP behavior, or anything a site
changes about how it detects automation traffic going forward. That gap only grows.

The part worth adding, if the reason for using pyppeteer in the first place was
detection avoidance rather than just Python-side Chromium control: neither
`pyppeteer` nor Playwright's default configuration were ever solving the property-
level and protocol-level checks a determined detector runs.
[Moving to Playwright](migrate-puppeteer-to-playwright-stealth.md)
because its own upstream is more actively developed fixes a maintenance gap; it
does not, by itself, fix the underlying automation-detection surface, which is a
separate concern from which driver you picked.

## Conclusion

pyppeteer's own README already gives the answer: move to Playwright. The more
useful part is why that recommendation names one specific successor instead of any
actively updated Puppeteer port. A single-browser, single-protocol design has a
structural ceiling that more contributors cannot lift; an abstraction built from the
start to cover three engines does not carry that same ceiling. That difference is a
real reason to move, and it is the one worth remembering. It is not, by itself, a
reason to expect a different result from a detector, because which driver sent the
commands was never the question a detector is asking.

## Short answers to the questions that lead here

**Is pyppeteer still usable?** The existing code still runs. There's no active
development behind it, so it won't track new Chromium behavior or protocol changes
going forward, and no one is currently maintaining it.

**Why does pyppeteer's own maintainer recommend Playwright specifically, and not
another Puppeteer port?** Playwright's driver abstraction covers Chromium, Firefox
and WebKit through a shared interface, rather than being tied to one browser family
and one protocol the way a Puppeteer port structurally is.

**Does switching to Playwright fix bot detection on its own?** No. It fixes having
an actively maintained upstream. [What a detector actually
checks](how-do-websites-detect-bots.md) - properties, protocol artifacts, TLS,
rendering - is a separate question from which driver library you're using.

**Is this specific to Python, or does the same apply elsewhere?** The maintenance
gap is specific to this particular port. The broader engine-abstraction point is
about Playwright generally, independent of which language binding is in use.

## Sources

- The project's own repository and README, read directly rather than assumed from
  its continued popularity in older tutorials and Stack Overflow answers:
  [pyppeteer/pyppeteer](https://github.com/pyppeteer/pyppeteer), retrieved 2026-08-28.

**See also:** [three ways to make Playwright undetected](playwright-stealth-levels.md),
for what switching drivers does and does not solve on its own, and
[invisible_playwright vs playwright-stealth: page vs engine](vs-playwright-stealth.md),
for what changes once you're already on Playwright and asking the next question.

---

*From the notes of [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level, built on the same driver this maintainer points
toward - for the engine-coverage reason above, not for anything about detection on
its own.*
