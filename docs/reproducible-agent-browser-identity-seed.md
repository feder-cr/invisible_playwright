---
title: "Give each AI agent a reproducible browser identity"
description: "Give each AI agent a reproducible browser identity from one seed, so a repeated agent task keeps one stable device fingerprint instead of a fresh random one each launch."
parent: "AI Agents and Frameworks"
grand_parent: "Guides"
nav_order: 8
---


# Give each AI agent a reproducible browser identity

An agent that relaunches the browser between steps, or retries a task the next day,
usually gets a brand new device each time: a different GPU string, a different canvas
hash, a different screen, a different timezone. To anything watching, that is one
person who owns a new computer every few minutes. It also makes failures impossible to
debug, because you can never tell the site changing from the machine changing.

The fix is to pin the identity. This page shows how to give one agent one stable
browser identity that reproduces run after run, what that stability buys you, and the
one thing the seed does not cover so you do not mistake a browser fix for a whole-stack
fix.

## What a reproducible identity actually means

invisible_playwright derives the entire fingerprint from a single seed. The pipeline is
seed to fingerprint to browser preferences: the seed picks a coherent GPU, canvas hash,
audio profile, screen and timezone posture, and those get written into the engine as
preferences before the browser starts. Nothing is drawn at random at launch time.

The practical consequence is the whole point of this page: **the same seed produces the
same device every time.** Two runs a week apart, on two different machines, with seed
`42`, present the same GPU, the same canvas readback, the same screen metrics. A random
per-launch fingerprint becomes a fixed one you chose.

By default no two sessions share a fingerprint, which is the right behaviour when each
session is a separate identity. Passing a seed opts one agent into a single persistent
identity instead.

## Give one agent one seed (the two-line version)

If you already have Playwright code, switching is two lines, and adding a seed is one
keyword. The `browser` object is a real Playwright
[`Browser`](https://playwright.dev/python/docs/api/class-browser), so every method you
already use works unchanged.

```python
from invisible_playwright import InvisiblePlaywright

# Agent "alpha" is seed 42. It will look like this exact machine on every run.
with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    page.goto("https://example.com")
    page.click("#next")   # mouse arcs to the button on a Bezier curve
```

Map each agent to a seed and store it wherever you keep the agent's state. A dictionary
is enough to start:

```python
AGENT_SEEDS = {"alpha": 42, "bravo": 1337, "charlie": 2024}

def run_agent(name, url):
    with InvisiblePlaywright(seed=AGENT_SEEDS[name]) as browser:
        page = browser.new_page()
        page.goto(url)
        return page.title()
```

If you did not choose a seed up front, every session still has one. Read it back and
persist it so you can reattach the same identity later:

```python
sf = InvisiblePlaywright()
with sf as browser:
    print("seed =", sf.seed)   # write this down; it is the whole identity
```

## Prove it reproduces: read the same field twice

The claim is testable in a few lines. Launch the same seed twice and compare a
fingerprint surface that a random build would change every time. Here the WebGL
renderer string, read via the
[`WEBGL_debug_renderer_info`](https://developer.mozilla.org/en-US/docs/Web/API/WEBGL_debug_renderer_info)
extension, and a canvas readback via
[`toDataURL()`](https://developer.mozilla.org/en-US/docs/Web/API/HTMLCanvasElement/toDataURL),
which should be byte-identical across the two runs:

```python
from invisible_playwright import InvisiblePlaywright

PROBE = """() => {
  const gl = document.createElement('canvas').getContext('webgl');
  const ext = gl.getExtension('WEBGL_debug_renderer_info');
  const renderer = gl.getParameter(ext.UNMASKED_RENDERER_WEBGL);
  const c = document.createElement('canvas');
  const ctx = c.getContext('2d');
  ctx.fillText('reproducible', 4, 12);
  return renderer + '|' + c.toDataURL().length;
}"""

def sample(seed):
    with InvisiblePlaywright(seed=seed) as browser:
        page = browser.new_page()
        page.goto("https://example.com")
        return page.evaluate(PROBE)

a = sample(42)
b = sample(42)
assert a == b, (a, b)   # same seed, same GPU string, same canvas length
print("stable identity:", a)
```

Run it more than once, and against different seeds, to see the other half: seed `42`
and seed `1337` return different strings, because they are different machines. That
[the canvas would otherwise change every run](canvas-fingerprint-changes-every-run.md)
is exactly the noise the seed removes, and it is why
[two seeds cannot accidentally collide into the same device](can-two-devices-share-a-browser-fingerprint.md).

## Why an agent wants a stable device

A fixed identity is worth more to an agent than to a one-shot script, for two reasons.

The first is realness. A durable task that relaunches the browser between steps looks,
to a site, like one continuous visitor only if the device stays the same. Reproducibility
is what lets a multi-step or multi-day agent be one person instead of a new person per
step. This is one input into looking like a real browser driven by a real person, which
is the design goal that makes the fingerprint, TLS and driver layers read as a genuine
Firefox in the first place.

The second is debugging, and it is the one people underrate. When a run fails, a fixed
seed lets you replay the exact same machine and watch it fail again. With a random
fingerprint per launch you cannot tell whether the site changed its rules or your build
handed out a different device; a bisect stops being a bisect. Pin the seed and a failing
run stays failing until you fix it.

## What the seed does not pin

This is the honest half, and skipping it is how people ship a browser fix and call it a
stealth fix. **The seed controls the browser layer only.** It does not touch anything
outside the browser, and several of those things matter more than the fingerprint on any
given block.

- **It does not pin your IP.** The seed decides the device; the network exit is set by
  your proxy, separately. A perfectly reproducible browser on a fresh datacenter address
  each run is still a fresh datacenter address each run.
- **It does not fix IP reputation, per-account quotas, rate limits, or behaviour and
  timing.** Those you supply: a clean proxy, human pacing, sane request volume. A stable
  fingerprint does nothing about a hundred requests a minute from one exit.
- **Consistency still needs the exit to agree with the device.** The seed fixes the
  browser's timezone, but a CreepJS-style consistency check compares that timezone
  against your exit IP. If the proxy egresses on another continent, the two disagree and
  you are flagged for a mismatch, seed or no seed. Let the timezone follow the egress IP,
  or set it to match, as covered in [configuration](configuration.md) and in the
  dedicated note on [timezone and proxy mismatch](timezone-proxy-mismatch.md).

So the seed is one layer. It makes the device stable and real; it is silent about the
network and the behaviour, which the reader owns.

## Conclusion

Deriving the whole fingerprint from one seed turns a per-launch lottery into a device you
choose and can reproduce. Map each agent to a seed, persist it, and a repeated or
long-running task keeps one coherent identity instead of announcing a new computer every
few minutes; a failing run also becomes replayable, which is the difference between
debugging and guessing. Just remember the seed stops at the browser: pair it with a clean
exit whose timezone agrees with the profile, and with pacing that looks human, because
those are the layers the seed cannot reach.

## Short answers to the questions that lead here

**Does the same seed always give the same fingerprint?** Yes. The whole fingerprint is
derived from the seed, so GPU, canvas, audio, screen and browser timezone come back
identical run after run.

**Can I give each agent its own stable identity?** Yes. Assign each agent a seed and
store it. Same seed, same device, every launch; different seeds are different devices.

**Does the seed also fix my IP?** No. The seed is the browser layer only. The exit
address comes from your proxy and is set separately.

**If I pin the seed, am I safe from detection?** No. It makes the device stable and
realistic, but it does nothing about IP reputation, rate limits, per-account quotas, or
behaviour and timing. Those are yours to supply.

**Why does my pinned identity still get flagged for a mismatch?** Almost always the exit
disagrees with the device: the seed's timezone against a proxy on another continent. Let
the timezone follow the egress IP or set it to match.

**What should I actually store to reattach an identity later?** The seed. It is the
entire identity. Read `sf.seed` after launch if you did not choose one, and persist that
integer.

## Sources

- This project's quickstart and configuration pages, for the seed-to-fingerprint
  behaviour and the timezone-follows-egress default.
- This project's own release gates, which assert that one seed reproduces one identity
  (the same seed must yield the same values on relaunch) and that a suppressed or absent
  signal counts as a failure rather than a pass.
- Playwright's own [`Browser` API reference](https://playwright.dev/python/docs/api/class-browser)
  for what `new_page` returns: the object every example above drives is the standard
  Playwright API, not a fork of it.
- MDN's [`WEBGL_debug_renderer_info`](https://developer.mozilla.org/en-US/docs/Web/API/WEBGL_debug_renderer_info)
  and [`HTMLCanvasElement.toDataURL()`](https://developer.mozilla.org/en-US/docs/Web/API/HTMLCanvasElement/toDataURL)
  references for the two readback methods the reproduction proof above uses.

**See also:** [why a canvas hash changes every run without a fixed seed](canvas-fingerprint-changes-every-run.md),
[whether two devices can share a browser fingerprint](can-two-devices-share-a-browser-fingerprint.md),
and [what stealth does and does not fit for AI browser agents](ai-browser-agents-stealth.md).

---

*Written while maintaining [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level driven by stock Playwright. The seed is the browser;
the proxy and the pacing are still yours.*
