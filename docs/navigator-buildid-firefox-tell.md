---
title: "navigator.buildID and the stale build date tell"
description: "navigator.buildID is a Firefox-only build-date property. Freezing it to a 2018 constant is a worse tell than the real value, with the score change we measured."
parent: "Browser Identity"
grand_parent: "Guides"
nav_order: 17
---


# navigator.buildID and the stale build date tell

Do not spoof `navigator.buildID`. It is a Firefox-only property that reports
when the browser binary was compiled, and pinning it to a fixed constant on a
current build creates a build date that disagrees with your version and engine.
That contradiction is a stronger tell than the real value, which a genuine
binary already reports correctly with nothing to set.

Everyone spoofs the user agent. Almost nobody thinks about `navigator.buildID`.
It is easy to miss because Chromium does not have it, so a habit built
on Chromium never learns to check it. And the first instinct when you do notice
it, which is to pin it to a nice round constant, is the one that turns a quiet
property into a loud one.

This page is what the property is, why a frozen value is a worse tell than the
real one, the reCAPTCHA score change we measured when we stopped freezing it,
and how to read your own.

## What navigator.buildID is, and why it is Firefox-only

[`navigator.buildID`](https://developer.mozilla.org/en-US/docs/Web/API/Navigator/buildID) is a string of the form `YYYYMMDDHHMMSS` that encodes the
timestamp of the build the browser was compiled from. A binary compiled on the
morning of April 26th 2026 reports something like `20260426192818`. It is a
Gecko property: Chrome and Chromium have no equivalent, so a detector that reads
it already knows it is talking to something claiming to be Firefox, and can hold
the value up against everything else the page claims to be.

Two things make it interesting to a fingerprinter. First, on a real Firefox
install it is not a free variable: it is a fact about the binary, and it moves
in lockstep with the version, the release channel and the update history.
Second, [Firefox's resistFingerprinting mode](resist-fingerprinting.md) has a
well-known behaviour here. With `privacy.resistFingerprinting` enabled, the
browser deliberately reports a fixed value, `20181001000000`, so that every RFP
user looks identical on this one field. That single date, October 1st 2018, is therefore a value a detector
recognises on sight: it does not mean "old browser", it means "this user has
resistFingerprinting on", which is itself a small, distinctive population.

## The tell: a frozen build date on a fresh binary

The tell is a hand-pinned build date sitting on a fresh binary: a value frozen
to the past while the version, the TLS handshake and the JavaScript surface all
report something current. A consistency check does not need the "right" build
date to flag it, only two values that cannot both be true at once.

Here is the trap. If you build or patch a Firefox in 2026 and then hardcode
`general.buildID.override` to `20181001000000`, you have not hidden in the crowd.
You have created an internal contradiction: a user agent that says Firefox 151,
a TLS handshake and a JavaScript surface that match a current build, and a build
date from 2018. That is a gap of more than seven years between two values that,
on a real machine, cannot disagree.

A consistency-based detector does not need to know the "right" buildID. It only
needs to notice that this one is impossible given the rest of the identity. The
same logic that [CreepJS applies to every surface it can reach](creepjs-explained.md)
applies here: it is not asking whether a value is unusual, it is asking whether
two values that should agree, do. A 2018 build date next to a 2026 everything-else
does not agree with anything, and a browser that reports it has told the page it
is spoofed without the page having to fingerprint a single pixel.

This is the general shape of the most expensive stealth mistakes. A blank value,
a suppressed signal, a frozen constant: each one feels safe because it is not
"leaking" anything, and each one is a distinctive value in its own right. It is
the same failure the [detected-on-one-site checklist](playwright-detected-as-bot.md)
keeps circling back to. The value you set by hand now has to agree with every
value you did not, and a build date is one of the few you cannot casually change
in one place without it disagreeing everywhere else.

## What we measured when we removed the override

We shipped the frozen date once. An earlier version of this project set
`general.buildID.override` to `20181001000000` on a binary compiled in 2026,
whose real compiled build id was `20260426192818`. That is the seven-and-a-half
year discrepancy described above, sitting live on every session.

On 2026-04-28 we removed the override entirely and let Firefox emit its own
compiled buildID, the one that tracks the binary. We ran it as an A/B knockout
against reCAPTCHA v3, which returns a continuous risk score rather than a
verdict, so a small change is measurable if you run enough of it. Removing the
frozen date moved the [reCAPTCHA v3 score](recaptcha-v3-score.md) in the good
direction:

- `+0.083` over a 30-run A/B, with the frozen date as the only variable.
- `+0.021` on a 100-run confirmation, same setup, larger sample.
- `+0.155` on an isolated overnight single-variant run.

The absolute numbers are small and they vary, which is exactly why we insist on
many runs rather than one: this domain is non-deterministic and a single pass
proves nothing. But the sign was consistent across every framing. Freezing the
build date cost score. Letting it move with the binary recovered it. The
"safe-looking" constant was the more detectable choice, measurably, and the fix
was to stop setting the value at all rather than to set it to a cleverer one.

## What the shipped build does instead

The current build does not touch `navigator.buildID`. There is no override in
the profile, so the property returns whatever the binary was actually compiled
from, and that value tracks the version and the release. A session on this
build reports a build date that is consistent with its user agent, its engine
and its TLS handshake, because all of them come from the same real binary rather
than from four different spoofing decisions that have to be kept in sync by hand.

The broader principle sits behind the whole design. The build spoofs the machine
surfaces that genuinely differ between hosts, the GPU, the audio device, the
fonts, the screen, all derived from one seed so they agree with each other. But
the browser's own identity, the parts that describe the engine rather than the
machine, are left to be what the engine actually is. A patched build gets this
for free where a JavaScript shim has to fabricate it, and fabricating a build
date is precisely where the shim invents a contradiction. This is the same
reason the [user agent is derived from the real upstream version](playwright-user-agent.md)
rather than pinned to a hand-typed string.

## How to check your own buildID

Read it the same way a detector would, from the page, and compare it against
what a stock Firefox on the same machine reports. The `browser` returned below
is a real Playwright `Browser`, so every method is the upstream one:

```python
from invisible_playwright import InvisiblePlaywright

with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    page.goto("https://example.com")

    build_id = page.evaluate("() => navigator.buildID")
    ua = page.evaluate("() => navigator.userAgent")

    print("navigator.buildID :", build_id)
    print("navigator.userAgent:", ua)

    # The tell is a disagreement between these two. A build date years
    # older than the version in the user agent is the contradiction a
    # consistency check is looking for. The frozen RFP value is its own
    # signal.
    year = int(build_id[:4]) if build_id and len(build_id) >= 4 else None
    print("build year        :", year)
    if build_id == "20181001000000":
        print("WARNING: this is the resistFingerprinting sentinel value")
```

Two things to look for. A build date whose year is far from the version's era is
the internal contradiction. The exact string `20181001000000` is the
resistFingerprinting sentinel, which is not neutral: it marks the session as
part of the RFP population. A healthy result is a build date that a real,
current Firefox of the same version would also report.

Run it against a stock Firefox next, and diff the two. This is the
[compare-do-not-read-verdicts method](how-to-test-bot-detection.md) applied to
one field: the reference is the real browser, and anything that differs between
the two, other than values that legitimately depend on the machine, is a
candidate tell. On this field the two should match, because there is nothing
machine-specific about a build date.

## Conclusion

`navigator.buildID` is a small property that punishes the obvious fix. It is
Firefox-only, so tooling built on Chromium habits never learns to watch it, and
the instinct to pin it to a tidy constant is the instinct that manufactures a
seven-year contradiction with the rest of the identity. The value that looks
safest, a frozen date, is the one a consistency check flags, and we have the
score delta to show it cost us. The property that needs no spoofing at all is
the one a real binary already reports correctly. Leave it alone, and check that
whatever you run actually does.

## Short answers to the questions that lead here

**What is navigator.buildID?** A Firefox-only string of the form
`YYYYMMDDHHMMSS` that encodes when the browser binary was compiled. Chromium has
no equivalent, so reading it already tells a detector you claim to be Firefox.

**Should I spoof navigator.buildID?** No. Freezing it to a constant creates a
build date that disagrees with your version and engine, which is a stronger tell
than the real value. A real binary reports a consistent one on its own.

**What is 20181001000000?** The fixed value Firefox reports when
`privacy.resistFingerprinting` is on. It is recognisable on sight and marks the
session as part of the RFP population rather than hiding it.

**Does it really affect detection scores?** In our A/B against reCAPTCHA v3,
removing a frozen 2018 build date from a 2026 binary moved the score in the good
direction by `+0.083`, `+0.021` and `+0.155` across three runs of different
sizes. Small, but consistent in sign.

**Why does Chromium not have this problem?** Because it does not expose a
buildID property, so there is nothing to freeze and nothing to contradict. The
tell is specific to spoofing a Gecko identity.

**How do I read my own?** `page.evaluate("() => navigator.buildID")`, then
compare it against a stock Firefox on the same machine. They should match, and
the year should be consistent with your version.

## Sources

- This project's release notes and A/B measurements for the 2026-04-28 change
  that removed the build-date override, including the three reCAPTCHA v3 score
  deltas quoted above.
- Firefox's documented resistFingerprinting behaviour, which reports the fixed
  `20181001000000` value for `navigator.buildID`.
- The public detection suites named across this set, each read from its own
  source rather than from its rendered verdict.

**See also:** [what the reCAPTCHA v3 score actually measures](recaptcha-v3-score.md),
[how the user agent is derived rather than pinned](playwright-user-agent.md), and
[why CreepJS treats a contradiction as a lie](creepjs-explained.md).

---

*Written while maintaining [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level driven by stock Playwright. The frozen build
date was our own mistake, shipped and then measured out.*
