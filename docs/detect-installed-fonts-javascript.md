---
title: "Detecting installed fonts in JavaScript by width"
description: "Detect installed fonts in JavaScript with no enumeration API: how the width-comparison probe tells present from absent, and why headless browsers fail it."
parent: "Canvas, WebGL, Fonts and Audio"
grand_parent: "Guides"
nav_order: 11
---


# Detecting installed fonts in JavaScript by width

There is a font-detection technique that needs no font-enumeration API, no
permission, and no privileged surface. It draws a short string in a candidate
font, draws the same string in a known fallback, and compares the two rendered
widths. If they differ, the candidate font is present. If they match to the
pixel, the browser fell back, so the font is absent.

That single comparison is old, cheap, and still in wide use, because it is hard
to disable without breaking real pages. This page is what the width-comparison
probe actually measures, why it is a different thing from hashing [`measureText`](https://developer.mozilla.org/en-US/docs/Web/API/CanvasRenderingContext2D/measureText)
output, what it returns on a bare headless browser, and how a browser that ships
its own font set answers it the same way on Windows, Linux and macOS.

## How the width-comparison probe works

The mechanism is a fallback trick. Set an element to a font stack whose only
entry is a generic family the platform is guaranteed to resolve, measure a test
string, and record that width as the baseline. Then prepend a candidate family
to the same stack and measure again. The browser uses the candidate if it can
resolve it, and falls back to the generic if it cannot. A different width means
the candidate rendered; an identical width means it did not exist.

```javascript
// The classic width-comparison presence probe, no enumeration API involved.
function fontIsPresent(family) {
  const probe = "mmmmmmmmmmlli wwww 0123456789";
  const canvas = document.createElement("canvas");
  const ctx = canvas.getContext("2d");

  ctx.font = "72px monospace";
  const baseline = ctx.measureText(probe).width;

  ctx.font = `72px "${family}", monospace`;
  const candidate = ctx.measureText(probe).width;

  // Present if the candidate changed the width away from the fallback.
  return candidate !== baseline;
}
```

The same idea works with a hidden DOM element and [`offsetWidth`](https://developer.mozilla.org/en-US/docs/Web/API/HTMLElement/offsetWidth) instead of a
canvas; the width number comes from the same text-shaping path either way. A
detector then runs this over a list of a few hundred font names and turns the
present/absent bits into a vector. Two facts about that vector are what make it
useful to a detector: it is stable for one machine, and it varies between
machines, because the installed-font set is one of the more distinctive things
about a device.

## Why this is different from hashing measureText

It is easy to lump this in with `measureText` fingerprinting, but the two probe
different things and a browser can answer one well and the other badly.

A `measureText` hash reads the exact sub-pixel width of one string in one font
and treats the fractional part as entropy. It does not care whether a font is
present; it cares that the same font renders to a slightly different width on
two machines because of the rasterizer behind it.

The width-comparison probe on this page throws that precision away. It only
asks a yes-or-no question: did the width move at all. A font that renders one
pixel wider than the fallback is just as "present" as one that renders forty
pixels wider.

That distinction matters for a disguise. Hiding from the hash means making the
fractional width deterministic and detached from the host rasterizer, which is
[what a bounded per-run offset on the text-shaping path does](measuretext-textmetrics-fingerprinting.md).
Hiding from the presence probe means controlling which families resolve at all,
which is a font-set problem, not a metrics problem.

Get the first right and the second wrong and you have a clean width hash sitting
on top of a font list that screams "Linux server". The two have to be solved
together, and this is one reason [headless browsers render a different set of fonts than a desktop](headless-fonts-differ.md).

## What the probe reads on a bare headless browser

Run the probe on stock headless Chrome or Firefox in a container and the result
is not "no fonts". It is a font set that belongs to the base image.

A default Linux container answers "present" for DejaVu, Liberation and a handful
of Noto families, and "absent" for every family a Windows desktop ships: no
Segoe UI, no Calibri, no Cambria, no Consolas. The probe does not need to know
those names to score you. It runs its whole list, gets a vector that matches no
consumer operating system, and that mismatch is the signal.

Claiming Windows in the user agent while the font vector is a Linux base image
is a one-line contradiction, and it is exactly the kind of internal disagreement
that [a browser that inspects consistency rather than values](how-to-test-bot-detection.md)
records as a lie.

The same probe list produces a different vector in each of these three cases:

| Environment | Fonts that resolve (present) | Windows desktop fonts (absent) |
|---|---|---|
| Default Linux container | DejaVu, Liberation, a few Noto families | Segoe UI, Calibri, Cambria, Consolas |
| Real Windows desktop | Segoe UI, Calibri, Cambria, Consolas and the rest of the Windows set | none, they are installed |
| Bundled font set (this project) | the same fixed Windows set on every host | none, shipped inside the binary |

The reverse failure is just as visible. A build that tries to suppress the probe
by forcing every candidate to fall back returns "absent" for everything,
including the generic families a real browser always has. An all-absent vector
is not a real machine either. A suppressed signal is itself a tell, so the goal
is not to answer "no" to everything, it is to answer the way a specific real
desktop would.

## Running the probe against a bundled font set

The approach this project takes is to stop borrowing the host's fonts at all.
The patched Firefox carries a fixed set of real Windows font files inside the
binary and builds the family list from a single manifest, so the same set is
present whether the process runs on a Windows laptop, a Linux server or a macOS
runner. The width-comparison probe sees that set and nothing from the machine
underneath.

Here is the probe run end to end against a reproducible identity. It measures a
list of families, splits them into present and absent, and prints the vector the
detector would score.

```python
from invisible_playwright import InvisiblePlaywright

CANDIDATES = [
    "Segoe UI", "Calibri", "Cambria", "Consolas", "Arial", "Tahoma",
    "Times New Roman", "Verdana", "Georgia", "Trebuchet MS",
    "DejaVu Sans", "Liberation Sans", "Ubuntu", "Noto Sans CJK SC",
]

PROBE_JS = """
(families) => {
  const probe = "mmmmmmmmmmlli wwww 0123456789";
  const ctx = document.createElement("canvas").getContext("2d");
  const widthIn = (stack) => { ctx.font = `72px ${stack}`; return ctx.measureText(probe).width; };
  const baseline = widthIn("monospace");
  const present = [], absent = [];
  for (const f of families) {
    (widthIn(`"${f}", monospace`) !== baseline ? present : absent).push(f);
  }
  return { present, absent };
}
"""

with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    page.goto("https://example.com")
    result = page.evaluate(PROBE_JS, CANDIDATES)
    print("present:", result["present"])
    print("absent :", result["absent"])
```

The Windows families resolve and the Linux-only families fall back, on every
host, because the resolution is decided by the bundled manifest and not by
whatever fonts the container happens to have. `browser` here is a real Playwright
`Browser`, so this is ordinary `page.evaluate` against a live page, nothing the
wrapper had to add.

## Why the answer is identical across operating systems

The reason this holds up is that both halves of the probe are host-independent by
construction.

The present/absent half is settled by the manifest. The family list is built
from the same checked-in data on all three platforms, so the set of names that
resolve is a property of the build, not of the OS font directory. There is no
path where the host's DejaVu or the host's system UI font leaks into the list
that content can enumerate.

The width half is where a naive bundle would still leak. If the widths came
straight from the platform rasterizer, the same bundled font would measure a
little differently under DirectWrite on Windows than under FreeType on Linux, and
the fractional widths would sort the machines back into OS buckets even with an
identical font set.

Instead the width for a run gets one bounded sub-pixel offset, applied once to
the last glyph of the run and derived from the session seed, rather than from
the host text engine. It is length-independent, so it does not grow with the
string, and it is deterministic, so the same seed gives the same width every
time. The result is that the width-comparison probe, and the finer width-hash
probe layered on it, both answer from the seed rather than from the operating
system.

You can watch that directly: run the block above with `seed=42` on two different
machines and the present/absent split and the measured widths match. Change the
seed and the widths shift together while the present/absent split stays fixed,
because the font set is the same identity and only the metrics jitter is
per-seed. That is the difference between a font disguise that is [consistent
across platforms](bundled-fonts-cross-platform.md) and one that merely looks
right on the machine you built it on.

## Conclusion

The width-comparison probe is durable precisely because it is simple: a fallback,
two measurements, a subtraction. You cannot answer it by patching a property,
because there is no property, only the rendered width of a string. The only way
to give it a coherent answer is to control the font set it resolves and the
widths it reads, together, and to make both come from the identity rather than
from the host.

That is the test to hold any stealth setup to. Enumerate the presence vector,
confirm it matches a real desktop instead of a base image, then read the widths
twice and confirm they are stable and detached from the OS underneath. A vector
that is all-present, all-absent, or quietly shaped like the server it runs on has
failed, whatever the headline verdict says.

## Short answers to the questions that lead here

**Can JavaScript detect installed fonts without an enumeration API?** Yes. It
draws a test string in a candidate font and in a generic fallback and compares
the two widths; a difference means the font is installed. No permission or
special API is required.

**How does the width comparison actually tell present from absent?** The browser
uses the candidate font if it can resolve it and the fallback if it cannot. The
same string in a resolved font measures a different width than in the fallback,
so a width that moves means present and a width that matches means absent.

**Is this the same as measureText fingerprinting?** No. `measureText`
fingerprinting reads the exact fractional width as entropy. The presence probe
only checks whether the width moved at all. A browser can hide from one and leak
the other, so both have to be handled.

**Why does a headless browser fail this probe?** Because its font set belongs to
the base image, not to the platform it claims. A Linux container answers
"absent" for every Windows desktop font, and that vector matches no real
consumer OS.

**Does hiding fonts by making them all absent work?** No. An all-absent vector is
not a real machine either, and a suppressed signal is its own tell. The aim is to
answer the way a specific real desktop would, not to answer no to everything.

**Why do the widths come out the same on Windows and Linux here?** Because the
font set is built from one manifest on every OS, and the width for a run carries a
single bounded, seed-derived offset instead of the host rasterizer's metrics, so
the numbers track the seed rather than the operating system.

## Sources

- This project's font architecture notes: a bundled Windows font set built from a
  single manifest, exposing the same families on all three operating systems, and
  a bounded per-run width offset on the text-shaping path that replaces the host
  rasterizer's metrics.
- The width-comparison presence technique as it appears in public detection
  suites, read as the fallback-and-subtract mechanism described above rather than
  from any one implementation.

**See also:** [why a bundled font set stays identical across platforms](bundled-fonts-cross-platform.md),
[how the measureText width hash is handled](measuretext-textmetrics-fingerprinting.md),
and [why headless browsers render a different font set](headless-fonts-differ.md).

---

*Written while maintaining [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level driven by stock Playwright. The font set is the
same on every host by construction, which is the only reason this probe answers
the same way whether the process runs on a laptop or a container.*
