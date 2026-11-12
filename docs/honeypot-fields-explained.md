---
title: "Honeypot Fields and Hidden Links: How Sites Trap Scrapers"
description: "A honeypot is a form field or link a real visitor never sees and a script that processes the raw DOM will trigger. The CSS that hides it, how a trip gets detected, and why a real browser engine does not avoid this on its own."
parent: "Detectors, Explained"
grand_parent: "Guides"
nav_order: 40
---


# Honeypot Fields and Hidden Links: How Sites Trap Scrapers

A honeypot is a form field or a link that exists in a page's HTML, is hidden from a
human visitor by CSS, and does something a real person never does when a script
touches it: fills it in, or follows it. It is one of the oldest anti-scraping tricks
still in wide use, cheap to add, and effective against exactly the class of script it
targets, one that reads raw HTML or the DOM and interacts with everything it finds
rather than what a person would actually see.

This is a different kind of check from most of this corpus. It is not a fingerprint
of the browser engine. It is a trap for how your own crawling code behaves, and that
distinction decides most of what follows.

## The classic form: a field only a bot fills in

The pattern, well documented across scraping-infrastructure writeups, is a form field
added to the markup with a name that looks legitimate, `email2`, `phone_confirm`,
`website`, styled so a human never sees it and never has a reason to type into it. A
script that fills every input it finds on a form, which is a common and reasonable
default for a naive scraper or a bulk form-fill tool, fills that field too. The
server checks on submit: if the honeypot field is non-empty, the submission did not
come from a person looking at the rendered page, because a person looking at the
rendered page cannot see a field that is not there.

The link version works the same way at the level of navigation instead of form
submission: an anchor tag hidden from view, sometimes leading to a dedicated trap
page. A crawler that walks every `<a href>` it finds in the markup follows it; a
person browsing the rendered page has nothing to click. Hitting the trap page at all
is the tell, independent of anything filled in.

## The CSS, and why it is not one trick

Real writeups on this technique describe several distinct hiding methods, and they
matter individually because they defeat different levels of scraper sophistication:

- **`display: none`** removes the element from layout entirely. Cheap, common, and
  the first thing a slightly more careful scraper checks for.
- **`visibility: hidden`** keeps the element's layout space but removes it from view
  and from the accessibility tree. A scraper checking only for presence in the DOM,
  not computed style, still misses this distinction.
- **Zero width or height**, sometimes combined with `overflow: hidden`, makes an
  element technically present and even technically "displayed" by some narrow
  definitions, while occupying no visible space.
- **Off-screen positioning**, `position: absolute` with a large negative offset,
  keeps normal display and visibility values while placing the element outside the
  visible viewport entirely, which specifically defeats a check that only reads
  `display` and `visibility` and nothing about actual position.
- **Color-matching**, text or a link styled the same color as its background,
  produces an element that is technically visible by every layout-level definition
  and simply cannot be read by a human eye.

The reason a site layers several of these rather than picking one is exactly the
reason a naive detection-avoidance check fails: each individual method is cheap to
detect once you know to look for it, and a scraper hardened against `display: none`
alone still walks straight into a color-matched link or an off-screen field.

## A real browser engine does not dodge this by itself

This is the point worth being precise about, because it cuts against an assumption
that runs through a lot of this corpus in the other direction. Most of what
`invisible_playwright` answers is a question the *browser engine* gets asked: does
canvas render like a real GPU, does the JavaScript engine behave like the one it
claims to be, does the TLS handshake match a real Firefox. A honeypot is not that
kind of question. It is asked of your *automation code*: did the script that is
driving this browser, real engine or not, interact with an element a human would
never have seen? A perfectly genuine Firefox, patched at the C++ level or not, walks
into a color-matched honeypot link exactly as readily as a bare HTTP client does, if
the code driving it calls `.click()` on every anchor tag it finds without checking
whether a human could ever have seen it.

Playwright's own actionability checks help here, but only partially, and knowing
the boundary matters more than knowing the feature exists. Per Playwright's own
documentation on auto-waiting, a standard action like `locator.click()` or
`locator.fill()` first confirms the target element is "visible," and Playwright's own
definition of visible is specific: a non-empty bounding box and no `visibility:
hidden` computed style. That check genuinely refuses to interact with a `display:
none` or zero-size element by default, which quietly defeats two of the five hiding
methods above without any extra code. It does **not** extend to `opacity: 0`,
which Playwright's own actionability documentation states explicitly is still
treated as visible, nor to an element that is technically laid out and displayed but
sitting off-screen or color-matched against its background. A scraper relying only
on Playwright's default actionability checks is protected against `display: none`
and zero-size traps, and exposed to opacity, off-screen and color-matching ones,
which is precisely why real honeypot writeups recommend those latter techniques over
plain `display: none`.

## The defensive habit, stated as a fact about behavior rather than a promise

The only reliable defense is code that reasons about what a human would actually
see before interacting with an element, not code that assumes a real browser engine
handles it automatically. Checking computed style directly, `display`, `visibility`,
`opacity`, actual bounding-box position relative to the viewport, and foreground
versus background color, before filling or following anything a page did not
obviously ask for, is a property of the automation script, not of how honest the
browser engine underneath it is. This is the same distinction [DataDome's own
behavioral-scoring layer draws](datadome-explained.md#behavior-does-not-stop-being-scored-once-you-are-past-the-door):
a click that lands somewhere no human would click is a signal about the driver, not
the engine, and no amount of engine-level realness changes what the code on top of
it decides to touch.

## Short answers to the questions that lead here

**What is a honeypot field?** A form input or link present in a page's markup,
hidden from a human visitor by CSS, that a script processing the raw HTML or DOM is
likely to fill in or follow, which the server reads as a sign the requester is not a
person.

**What CSS actually hides a honeypot?** `display: none`, `visibility: hidden`,
zero width/height, off-screen absolute positioning, and color-matching text against
its background are all documented in real use, individually and combined.

**Does using a real browser engine avoid honeypots automatically?** No. A honeypot
is a trap for what your automation code interacts with, not for what the browser
engine is. A genuinely real Firefox driven by code that clicks every link it finds
walks into a color-matched honeypot exactly like any other client would.

**Does Playwright's own click behavior protect against this?** Partially.
Playwright's default actionability checks refuse to click or fill an element with
`display: none` or a zero-size bounding box, but explicitly treat `opacity: 0` as
visible, and do not check whether an element is actually inside the viewport or
color-matched against its background.

**What is the actual defense?** Code that checks computed style and real position
before interacting with any element a script did not explicitly expect to need,
rather than processing every input or link a page happens to contain.

**Is this the same category of check as a fingerprinting detector?** No. Most of
this corpus is about the browser engine answering a JavaScript-visible question
honestly. A honeypot scores the automation code's own behavior, independent of how
real the engine underneath it is.

## Sources

- Scrapfly, ["What are honeypots and how to avoid them"](https://scrapfly.io/blog/posts/what-are-honeypots-and-how-to-avoid-them),
  retrieved 2026-08-30, for the CSS hiding techniques (`display: none`,
  color-matching) and the detection-script pattern of checking computed style.
- Stytch, ["How to block AI web crawlers without breaking your site"](https://stytch.com/blog/how-to-block-ai-web-crawlers/),
  retrieved 2026-08-30, for the hidden-form-field and tarpit description in the
  context of anti-crawler defenses generally.
- Cloudflare, [AI Labyrinth](https://developers.cloudflare.com/bots/additional-configurations/ai-labyrinth/),
  retrieved 2026-08-30, for a large-scale, `nofollow`-tagged production version of
  the hidden-link honeypot pattern, covered in full on [this project's separate page
  on AI-crawler blocking](beyond-robots-txt-anti-crawler-mechanisms.md).
- Playwright, [Auto-waiting / actionability documentation](https://playwright.dev/docs/actionability),
  retrieved 2026-08-30, for the exact definition of "visible" Playwright's own
  action methods check (`display: none` and zero bounding box excluded,
  `opacity: 0` explicitly included as visible), and which checks `force: true`
  disables.

**See also:** [Beyond robots.txt: how sites actually block AI crawlers now](beyond-robots-txt-anti-crawler-mechanisms.md)
for the wider stack this trap sits inside, [how DataDome's bot detection actually
works](datadome-explained.md) for the behavioral-scoring layer that scores automation
code the same way, and [what BrowserLeaks actually tests, surface by
surface](browserleaks-explained.md) for the engine-level checks this page
deliberately contrasts against.

---

*From the notes of [invisible_playwright](https://github.com/oss-invisible/playwright),
a Firefox patched at the C++ level driven by stock Playwright. A honeypot tests the
code driving the browser, not the browser's own realness, and no amount of engine
patching changes what a script decides to click.*
