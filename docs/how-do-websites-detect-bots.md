---
title: "How do websites detect bots?"
description: "The four bot detection layers - fingerprint, driver tells, network TLS/IP, behaviour - and which two a real-browser build actually neutralises."
parent: "Detectors, Explained"
grand_parent: "Guides"
nav_order: 9
---


# How do websites detect bots?

Not with one check. A modern detector reads four independent layers, scores each,
and blocks on the combination. That is the single most useful thing to understand
here, because it explains why a fix that clearly works still leaves you blocked: you
improved one layer and the verdict came from another.

This page names the four layers, says what each one measures, and is honest about
which two a real-browser build like this one actually neutralises and which two stay
your job no matter what engine you drive.

## The four layers a detector actually combines

Every signal a site collects falls into one of four buckets, and the buckets are
independent - a perfect score in one says nothing about the others.

1. **Fingerprint consistency.** Not "is this value rare", but "do these values agree
   with each other". Does `navigator.platform` match the WebGL renderer, the fonts,
   the timezone and the TLS stack.
2. **Automation-driver tells.** Direct evidence that a driver is attached:
   `navigator.webdriver`, remote-debugging-protocol artifacts, an untrusted click.
3. **Network signals.** The TLS handshake, HTTP/2 settings, and the reputation of the
   IP and its ASN. Decided before any JavaScript runs.
4. **Behaviour.** Pointer motion, typing rhythm, dwell time, request velocity, and
   per-account or per-IP rate.

A weak layer drags the whole score down. That is why a genuine-looking browser on a
flagged datacenter IP still loses, and why a pristine residential IP does not save a
browser whose fingerprint contradicts itself.

## Layer one: fingerprint consistency

This is the layer people mean when they say "fingerprinting", and it is the one most
misunderstood. Detectors rarely block a value for being unusual. They block a
*contradiction*: a set of values that no single real machine would report together.

A user agent that says Windows, a WebGL renderer that says a software rasterizer, a
font list from a Linux server, a timezone three continents from the exit IP. Each of
those is plausible alone. Together they describe a machine that does not exist.
[CreepJS](creepjs-explained.md) is built almost entirely around this idea: it takes a
clean copy of the built-ins, walks descriptors and prototypes, and records any place
where one answer disagrees with another - including a blocked probe, which it counts
as a lie rather than a blank.

A real-browser build attacks this layer at the root. Because the engine is an actual
patched Firefox, its JavaScript surface, its WebGL renderer string and its TLS stack
all come from the same real Windows browser and therefore already agree. There is no
seam to find, because there is no disguise layered over a different engine. This is
also why running a second spoofer on top tends to *create* the contradiction the first
one avoided - see [the checklist for a site that blocks you](playwright-detected-as-bot.md),
step two.

## Layer two: automation-driver tells

This layer is direct evidence that a program is driving the browser. The classic tell
is [`navigator.webdriver`](https://developer.mozilla.org/en-US/docs/Web/API/Navigator/webdriver),
which reads `true` on a stock automated browser and
`undefined` on a real one - and setting it to `false` is its own signature, because a
clean browser reports `undefined`, not `false`. [The webdriver flag has more history
and nuance than it looks](navigator-webdriver-explained.md).

Beyond that flag: artifacts left by the remote-debugging protocol the driver speaks,
injected globals, and events that carry [`isTrusted: false`](https://developer.mozilla.org/en-US/docs/Web/API/Event/isTrusted)
because they were synthesised by a script rather than produced by real input. [BotD](botd-explained.md)
leans on a family of these, testing behaviours that differ between a driven and a
hand-used browser.

invisible_playwright is driven by *stock* Playwright with no page-level patching bolted
on, and the engine exposes no driver tell of this kind: `navigator.webdriver` reads
`undefined`, there is no leftover automation global, and the input it generates is
trusted input. Combined with layer one, this is the pair a real-browser build genuinely
neutralises - to a detector, the first two layers read as a genuine person's browser.

## Layer three: the network, which no in-page test can see

Before a single byte of your page runs, the site has already seen your TLS handshake
and the IP it came from. Neither is a browser property JavaScript can reach, so no
fingerprinting page can show them to you and no page-level stealth trick can change
them.

Two sub-signals live here:

- **The handshake itself.** The ordering and contents of a TLS
  [ClientHello](https://datatracker.ietf.org/doc/html/rfc8446#section-4.1.2), summarised
  as a JA3 or JA4 hash, plus HTTP/2 settings and header order. A request that announces
  "I am Firefox" in the user agent while presenting a handshake that is not Firefox's is
  a decisive mismatch. Because this engine *is* Firefox, its handshake matches its user
  agent for free - [why the TLS-fingerprint-versus-user-agent mismatch is the tell most
  scraper stacks trip on](tls-fingerprint-user-agent-mismatch.md).
- **IP and ASN reputation.** A datacenter range, an ASN with a bad history, or an exit a
  thousand other automated sessions are using this minute. This is *not* something the
  browser can fix. A real-browser build does nothing for it.

So the handshake half of this layer comes out right because the engine is real; the
reputation half does not, and that is the honest boundary. You supply the reputation
half with a clean proxy.

## Layer four: behaviour, rate, and the account

The fourth layer does not fingerprint at all - it watches. A pointer that teleports
from coordinate to coordinate. Keystrokes at a metronome-perfect interval. A form
filled in eighty milliseconds. Requests at a cadence no hand produces, or a per-account
quota blown through in a minute.

This engine ships Bezier-curve mouse motion by default, which addresses the crudest
pointer tells, but the shape of your session - how fast you page, how long you dwell,
how many requests per minute from one exit, how many actions per account - is authored
by *your* code, not the engine. A block that arrives minutes into a session rather than
at the first request almost always lives here. For automated agents there is an extra
wrinkle: the pause between steps can be shaped like model latency, which is a
behavioural signal in its own right.

Rate limits, per-account quotas and pacing are supplied by you. No engine substitutes
for human pacing.

## Which two layers a build like this actually neutralises

Put concretely, and this is the whole honest claim:

- **Layers one and two** (fingerprint consistency and driver tells) are what a real,
  patched-Firefox engine driven by stock Playwright neutralises, because there is no
  disguise to catch out and no driver to detect.
- **Layers three and four** (IP reputation and behaviour/rate) are yours to supply: a
  clean proxy for the reputation half of the network layer, and human pacing for the
  behavioural one.

Switching from plain Playwright is a two-line change, and the launched object is a real
Playwright [`Browser`](https://playwright.dev/python/docs/api/class-browser) with every method intact:

```python
from invisible_playwright import InvisiblePlaywright

# seed=42 pins the whole identity so a blocked run is reproducible;
# the proxy is the half of layer three the engine cannot supply for you.
proxy = {"server": "socks5://gate.example.com:1080", "username": "u", "password": "p"}

with InvisiblePlaywright(seed=42, proxy=proxy) as browser:
    page = browser.new_page()
    page.goto("https://example.com")
    page.click("#submit")   # a trusted click; the pointer arcs on a Bezier curve
```

That call handles layers one and two and the handshake half of three. The proxy dict
you passed handles the reputation half. The pacing between your own actions - and
whatever per-account budget you spend - is the part no library can author for you.

If you want to measure the split yourself, open a fingerprinting suite through this
engine and through a stock browser on the same machine and diff the fields;
[the method for doing that without fooling yourself](how-to-test-bot-detection.md)
is a page of its own, and it is the fastest way to see which of the four layers a given
block actually came from.

## Conclusion

"How do websites detect bots" has a four-part answer, and the parts are independent.
Fingerprint consistency and driver tells are the two a real-browser build reads as a
genuine person's browser. The network's reputation half and the behavioural layer are
not the engine's to fix and never claim to be - you bring a clean exit and human
pacing. Anyone selling you a single box that closes all four is selling you a slogan;
the useful mental model is the taxonomy, because it tells you which layer to look at
when a block arrives.

## Short answers to the questions that lead here

**How do websites detect bots?** By scoring four independent layers - fingerprint
consistency, automation-driver tells, network TLS and IP reputation, and behaviour -
and blocking on the combination, not on any one signal.

**Does a stealth browser make me undetectable?** No, and be wary of anything that says
so. A real-browser build neutralises the fingerprint and driver layers; it does nothing
for a bad IP or robotic behaviour, which are yours to fix.

**Which layer is invisible_playwright actually solving?** The first two - fingerprint
consistency and automation-driver tells - plus the handshake half of the network layer,
because the engine is a real patched Firefox. IP reputation and pacing are not the
engine's job.

**Why do I still get blocked with a perfect fingerprint?** Almost always layer three or
four: a datacenter or reused IP, or a session whose speed and rhythm no human produces.

**Can a website see my TLS handshake?** Yes, before any JavaScript runs, and no in-page
test can show it to you. A user agent that disagrees with the handshake is a decisive
tell.

**Is randomising my user agent a good idea?** No. It has to agree with the engine, the
platform, the fonts and the handshake. Changing it alone manufactures a contradiction
in layer one rather than hiding anything.

## Sources

- This project's own detection gates and field-by-field comparisons against a stock
  Firefox on the same machine, which is how the four-layer split was measured rather
  than assumed.
- The public detection suites named in these notes (CreepJS, BotD, sannysoft,
  FingerprintJS, BrowserLeaks), each read from its own source, and the per-layer pages
  linked throughout this set.

**See also:** [the checklist for a site that blocks you](playwright-detected-as-bot.md)
for working the layers in order, [what the webdriver flag really proves](navigator-webdriver-explained.md)
for layer two, and [the TLS-versus-user-agent mismatch](tls-fingerprint-user-agent-mismatch.md)
for the handshake half of layer three.

---

*Written while maintaining [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level driven by stock Playwright. It closes the fingerprint
and driver layers so you can spend your attention on the two it honestly cannot: the IP
and the pacing.*
