---
title: "Can a website detect typing by keystroke timing?"
description: "Keystroke detectors histogram per-key dwell times from page.type()'s real keydown/keyup pairs. Uniform gaps are the tell, not characters. Human cadence defeats it."
parent: "Detectors, Explained"
grand_parent: "Guides"
nav_order: 20
---


# Can a website detect typing by keystroke timing?

Yes, and it is worth being precise about what "detect" means here, because it is not
the thing most people worry about. A keystroke-timing detector does not read the
letters you send, and it does not care whether the events are trusted. It measures
*when* each key goes down and comes up, builds a small histogram out of those numbers,
and looks at the shape. Human typing has a messy, right-skewed shape. A script that
types at a fixed interval has a single spike. The spike is the signal.

This page is about the behavioural layer, not the fingerprint one. It answers the
question honestly: a real browser emitting real key events gets you past every check
that reads the *events*, and none of the checks that read the *rhythm*. The rhythm is
code you write, and this page is about writing it well.

## Yes, and it is a different question from your fingerprint

Keep two questions apart, because they have different answers and different fixes.

The first is "do these key events look genuine". That is about whether `keydown`,
`keypress` and `keyup` fire in the right order, carry the right `code` and `key`
values, arrive with `isTrusted` set to true, and reach the page through the same path
a physical keyboard would. This is a browser-level property. Automation that injects
events through a JavaScript shim answers this one badly, which is
[the same class of problem as a synthetic click that is not trusted](playwright-clicks-istrusted.md).

The second is "does the timing between these events look human". That is not a browser
property at all. It is a statistic over the gaps, and no engine can supply it for you,
because the engine does not decide when your loop calls the next keypress. You do.

invisible_playwright is built to answer the first question the way a real browser does.
It drives a genuine Firefox, patched at the C++ level, so the key events are real,
OS-level, trusted events rather than synthesized DOM dispatches. It does nothing about
the second question, and it cannot, which is the honest and slightly annoying core of
this whole topic.

## What a keystroke detector actually measures

Two numbers per key, both in milliseconds, and both easy to collect from ordinary DOM
events.

**Dwell time** is how long a single key is held: the gap between its
[`keydown`](https://developer.mozilla.org/en-US/docs/Web/API/Element/keydown_event) and its
own [`keyup`](https://developer.mozilla.org/en-US/docs/Web/API/Element/keyup_event). On a
human hand this varies with the finger, the key, and whether you were mid-word or reaching
for a symbol. It is rarely constant.

**Flight time** is the gap between releasing one key and pressing the next: the `keyup`
of one character to the `keydown` of the following one. This is where human typing is
most irregular. Common digraphs you type every day are fast. An unusual letter pair, a
reach for the number row, or the instant right after a typo are slow. The distribution
is wide and right-skewed, with a long tail of pauses.

A detector wires up three listeners and records timestamps:

```javascript
// what the page runs, not what you run
const events = [];
input.addEventListener('keydown', e => events.push(['down', e.code, e.timeStamp]));
input.addEventListener('keyup',   e => events.push(['up',   e.code, e.timeStamp]));
// later: reconstruct dwell and flight per key, histogram them, look at the variance
```

The classifier is usually nothing clever. Near-zero variance in flight time is the
strongest single feature, because no hand produces it. A standard deviation of zero
milliseconds across forty keystrokes is not a fast typist, it is a `for` loop. This is
the same family of tell as
[a pointer that teleports instead of moving](human-mouse-movement.md): the giveaway is
the absence of natural variance, not any single suspicious value.

## Why page.type() with a fixed delay is the giveaway

Here is the trap, and it is a comfortable one to fall into because it looks like the
responsible thing to do.

Playwright's `page.type()` sends a real `keydown`/`keyup` pair per character, and it
accepts a
[`delay` argument](https://playwright.dev/python/docs/api/class-keyboard#keyboard-type)
meant to slow typing down. So people reach for it:

```python
from invisible_playwright import InvisiblePlaywright

with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    page.goto("https://example.com/form")
    # every event is real and trusted... and every gap is exactly 100 ms
    page.type("#comment", "the quick brown fox", delay=100)
```

Every character in that call is separated by the same 100 milliseconds. The events are
impeccable: real, trusted, correct `code` values, perfect ordering. And the flight-time
histogram is a single bar. You have produced the cleanest possible bot signature while
believing you added human behaviour. A fixed `delay` does not simulate a human, it
simulates a metronome, and the metronome is exactly what the detector is tuned to find.

Randomising the delay a little is better and still not right, because uniform jitter
(say, 80 to 120 ms picked flat) has the wrong *shape*: it is symmetric and bounded,
where human flight times are skewed with a long slow tail. A detector that looks at the
distribution and not just the variance can tell a uniform draw from a human one.

## Giving type() a human cadence

The fix is to control the gaps yourself, per character, with a distribution that has
the right shape: mostly quick, occasionally slow, never identical. You keep the real,
trusted key events that the engine already gives you, and you supply the rhythm.

```python
import random
import time
from invisible_playwright import InvisiblePlaywright


def human_type(page, selector, text):
    page.click(selector)
    for ch in text:
        page.keyboard.press(_to_key(ch))
        # right-skewed flight time: usually fast, sometimes a real pause
        base = random.gauss(0.11, 0.03)          # ~110 ms centre
        pause = max(0.03, base)
        if random.random() < 0.08:               # occasional longer stall
            pause += random.uniform(0.15, 0.5)
        time.sleep(pause)


def _to_key(ch):
    # letters and digits map to themselves; space and common punctuation
    # have Playwright key names. Extend as your inputs require.
    return {" ": "Space", "\n": "Enter"}.get(ch, ch)


with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    page.goto("https://example.com/form")
    human_type(page, "#comment", "the quick brown fox jumps over")
```

Two things make this pass where the fixed delay fails. The gaps come from a skewed
distribution rather than a constant, so the flight-time histogram has variance and a
tail instead of a spike. And the occasional longer stall reproduces the way real typing
stops to think, which a flat jitter never does. If you want dwell variance too, hold
some keys fractionally longer by pressing `down` and `up` separately with a short sleep
between them, rather than the atomic `press`.

The seed still does its job here. `seed=42` fixes the fingerprint so the *machine* is
reproducible; the typing rhythm is deliberately not seeded, because the point of a
rhythm is that it differs every time. If you need a failing run to be reproducible while
you debug, seed the `random` module too, and unseed it in production.

## What invisible_playwright fixes here, and what it does not

The honest split, stated plainly, because overclaiming this would be both wrong and a
liability.

What it fixes: the events are real. Because you are driving an actual Firefox rather
than a JavaScript event shim, the `keydown`/`keyup` pairs are trusted OS-level events
with correct `code`, `key` and modifier state, indistinguishable at the event layer
from a physical keyboard. The fingerprint, the TLS handshake and the driver layer all
read as a genuine Firefox, which is *why* the browser passes most detection: it is not
pretending to be real, it is real. That is the whole design.

What it does not fix, and cannot: the cadence. The browser cannot make your loop pause
like a person, because your loop is upstream of the browser. If you script a uniform
`delay`, you ship a uniform histogram, and a genuine browser emitting it does not make
it look human. The same honesty applies to everything outside the browser process. A
real browser does not clean up a datacenter IP, reset a per-account quota, respect a
rate limit, or slow down a session that clicks faster than a person reads. Those are
yours to supply: a clean exit, human pacing, and sane per-account volume. A perfect
browser on a hostile network still loses, and
[a clean fingerprint is not the whole session](why-blocked-with-a-clean-fingerprint.md).

If you take one rule from this page: the engine gives you a real keyboard, and you are
still the typist.

## Conclusion

A website can absolutely detect typing by keystroke timing, and it does it with two
numbers per key and a histogram, not with anything exotic. `page.type()` gives you real,
trusted key events, which defeats every check that reads the events. A fixed `delay`
then hands the timing checks a perfect metronome signature, which is the opposite of
what you intended. Drive the cadence yourself from a skewed distribution with an
occasional pause, keep the real events the engine already provides, and pair the browser
with a clean exit and human pacing, because the browser fixes the hardest half of this
and none of the half that is your behaviour.

## Short answers to the questions that lead here

**Can a website see how fast I type?** Yes. It reads the timestamps of `keydown` and
`keyup`, computes the gaps, and looks at their distribution. It does not need anything
you cannot already see in the DOM.

**Does page.type() with a delay make it look human?** No. A fixed `delay` produces
identical gaps, which is the single strongest bot signal there is. Real events, robotic
rhythm.

**Is the problem that the events are fake?** Not with invisible_playwright. The events
are real, trusted, OS-level key events. The problem is purely the timing, which you
control.

**What distribution should I use for the gaps?** A right-skewed one, centred around
100 to 130 ms with a long tail, plus an occasional longer stall. Not a flat uniform
range, which has the wrong shape even though it has variance.

**Should I randomise dwell time too?** It helps against detectors that look at both.
Press `down` and `up` separately with a small sleep between them to vary how long each
key is held, instead of the atomic `press`.

**If my events are real, why am I still blocked?** Probably not the keyboard. Check the
exit IP, the request rate, and the account quota, none of which the browser touches.
Timing is one behavioural surface among several.

## Sources

- Standard Playwright keyboard API:
  [`keyboard.press()`](https://playwright.dev/python/docs/api/class-keyboard#keyboard-press)
  and [`keyboard.type()`](https://playwright.dev/python/docs/api/class-keyboard#keyboard-type),
  which documents the `delay` argument and the per-character event dispatch, read from the
  upstream documentation rather than a summary of it. See also MDN's
  [`keydown`](https://developer.mozilla.org/en-US/docs/Web/API/Element/keydown_event) and
  [`keyup`](https://developer.mozilla.org/en-US/docs/Web/API/Element/keyup_event) event
  references for the two events a detector listens to.
- This project's release gates and behavioural notes, including the measurement that a
  constant inter-key interval collapses the flight-time histogram to a single bar while
  the events themselves remain fully trusted.

**See also:** [Bezier-curve mouse motion](human-mouse-movement.md) for the pointer
equivalent of this problem, [why a scripted click can still be untrusted](playwright-clicks-istrusted.md)
for the event-integrity layer, and [the behavioural pacing an AI agent has to add on top](ai-browser-agents-stealth.md).

---

*Written while maintaining [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level driven by stock Playwright. The engine gives you a
real keyboard; the rhythm is still yours to type.*
