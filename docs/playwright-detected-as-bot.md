---
title: "Playwright detected as a bot on one site: a checklist"
description: "A troubleshooting checklist for when Playwright gets detected as a bot on one site: check the free fixes first, before buying a better proxy on day one."
parent: "Testing and Troubleshooting"
grand_parent: "Guides"
nav_order: 2
---

# Playwright detected as a bot on one site: a checklist

A troubleshooting order for when automation gets a different page than a human does.
It is written for Playwright but almost none of it is Playwright-specific.

Most sites that show automation a different page are not reacting to "a bot" in the
abstract. They are reacting to one specific, findable mismatch: a value you set by
hand that disagrees with another value, a machine that reveals it has no display or
audio hardware, a leftover automation artifact, a behaviour no human produces, or a
network exit that tells a different story than the browser does. This checklist works
through those causes in the order they are actually the culprit - cheapest and most
likely first, expensive and least likely last.

The order matters. Most people start at the bottom of this list, buy a better proxy,
and find out three days later that the problem was a user agent they set themselves.

## 0. Prove it is actually detection

Before anything else, open the same URL by hand from the same machine and the same
network. Not from your laptop, from the machine that runs the automation.

If the manual visit also gets the wrong page, this is not a browser fingerprint
problem: it is the IP, the ASN, or the country. Nothing in the rest of this list will
help, and the fix is a different exit.

If the manual visit works and the automated one does not, the difference is the
browser or the behaviour, and the list below is in the order that difference usually
lives.

## 1. Check what you overrode yourself

This is the most common cause and the most embarrassing one.

Every value you set by hand is a value that now has to agree with every value you did
not. [A user agent claiming one platform](playwright-user-agent.md), on a browser
reporting another. A timezone you pinned, against an IP on a different continent. A
language list from your own machine, on an exit somewhere else.

Grep your own code for `user_agent`, `locale`, `timezone_id`, `viewport`,
`extra_http_headers` and any stealth plugin you installed. Then remove all of them
and try again. If it starts working, add them back one at a time and stop at the one
that breaks it.

Detectors rarely check whether a value is unusual. They check whether two values that
should agree, do.

## 2. Check whether you are running two spoofers at once

If you use a patched browser or a stealth build **and** a JavaScript stealth plugin,
they are both answering the same questions and they will not give the same answer.
Two disguises produce a contradiction that neither produces alone.

Pick [one layer](playwright-stealth-levels.md). If the engine handles the
fingerprint, turn off the page-level patching and any header generator the framework
offers.

## 3. Check the machine, not the browser

Open a fingerprinting page and read the report instead of the verdict. The things
that most often give away a server:

- **A software WebGL renderer.** If the GPU string mentions a basic, software or
  llvmpipe renderer, the browser has announced it is running somewhere without
  graphics hardware. No amount of property patching hides that. Worse, and less
  obvious: the string can say NVIDIA while the pixels are still drawn by a software
  rasterizer, which is [a mismatch you cannot patch](renderer-string-vs-render.md).
- **[A screen that does not exist](screen-size-headless-tells.md).** Odd
  resolutions, a device pixel ratio that no real display has, an available height
  equal to the full height, meaning no taskbar.
- **[Font lists that do not match the claimed platform](bundled-fonts-cross-platform.md).**
  Claiming Windows with a Linux font set is a one-line check.
- **[Hardware concurrency and device memory](hardware-concurrency-device-memory.md)**
  that pair oddly with the claimed machine.
- **No audio device.** Sample rate, output latency and channel count come from real
  hardware, and a container that has none answers with defaults that say so. See
  [AudioContext fingerprinting](audiocontext-fingerprinting.md).
- **An empty speech voice list.** `speechSynthesis.getVoices()` returns what the
  operating system has installed, so an empty array under a Windows user agent is a
  server saying it is a desktop. See
  [why getVoices() comes back empty](speech-synthesis-voices.md).

None of these are automation flags. They are all "this is a datacenter" flags, and
they survive every stealth plugin ever written because they are not in JavaScript's
gift to change.

## 4. Only now, check the automation tells

[`navigator.webdriver`](navigator-webdriver-explained.md), leftover automation
globals, a headless user agent, a missing or malformed `chrome` object on a Chromium
build. These are worth checking and they
are almost never still the problem in 2026, because every tool fixes them first.

If you find one, note that setting it to `false` is not the fix: a clean browser
reports `undefined`, and `false` is a value with its own signature.

Two more automation-layer tells worth ruling out here rather than later:
[`pageshow.persisted` being permanently false](bfcache-pageshow-persisted.md), because
most drivers disable the back/forward cache outright, and
["Execution context was destroyed" errors](execution-context-destroyed.md) that turn
out to be a site reacting mid-visit rather than the ordinary navigation race.

## 5. Check the behaviour

Some sites do not fingerprint at all, they watch.

A pointer that jumps from coordinate to coordinate without passing through the space
between. Keystrokes at a perfectly uniform interval. A form filled in eighty
milliseconds. A page that is never scrolled. A session with no mouse movement at all
before a click.

If the block appears only after an interaction, and only on some pages, this is the
one. It also explains blocks that arrive minutes into a session rather than at the
first request.

## 6. Check the network layer

TLS fingerprints, HTTP/2 settings frames, header order. A real browser's handshake is
distinctive, and [a User-Agent that claims one browser while the handshake belongs to
another](tls-fingerprint-user-agent-mismatch.md) is decisive.

You cannot fix this from JavaScript. Either the browser is real, or the request is
made by something that impersonates the handshake too.

While you are here, check what the browser says about the network on its own account.
[WebRTC through a proxy](webrtc-leak-proxy.md) is the common one, and it fails in both
directions: it can report your real address next to the proxy's, or report nothing at
all, which is its own signal.

## 7. Now consider the exit

If everything above is clean and it still fails, the IP is a reasonable next
suspicion. Datacenter ranges, an ASN with a bad reputation, an exit that a thousand
other people are using this minute, a country that does not match the rest of the
session.

That last one is worth separating out, because it is not really about the IP being
bad. It is about the browser and the exit telling different stories, and it is cheap
to fix: see [when the timezone does not match the proxy](timezone-proxy-mismatch.md)
for everything that has to agree.

It is seventh on this list, not first, because it is the expensive fix and the least
likely single cause.

## The debugging habit that saves the most time

Fix the identity so a failure is reproducible.

If every run gets a different fingerprint, a failing run tells you nothing: you
cannot tell the site changing from the machine changing. Whatever tool you use, find
its way to pin the identity across runs, and change it deliberately rather than
accidentally.

It is the single biggest difference between debugging this and guessing at it, and
it is why this project derives every surface from one seed: the same seed gives the
same machine every time, so a bisect is a bisect.

## Short answers to the questions that lead here

**Why does Playwright work on every site except one?** Because that site checks
something the others do not, and it is usually about the machine rather than the
automation. Work the list above in order.

**Is it the proxy?** It is seventh on the list for a reason. It is the expensive fix
and the least likely single cause, and people buy a better proxy before checking the
free things.

**How do I tell detection from a normal failure?** Compare the response against what a
human browser gets on the same URL. A different page, a challenge, or a body that is
suspiciously short are detection. A timeout is often just a timeout.

**Does headless mode itself get detected?** Less than people think. What gets detected
is what tends to come with it: no GPU, no fonts, no audio device, a screen size nobody
has.

**Should I randomise the user agent?** No. It has to agree with the engine, the
platform, the fonts and everything else. Randomising it alone creates contradictions
rather than hiding anything.

**See also:** [WebGL renderer strings](webgl-renderer-strings.md) and [why headless renders different fonts](headless-fonts-differ.md) for step three, [what sannysoft actually checks](sannysoft-explained.md) before you trust a green table, and, if every step above comes back clean and the block persists anyway, [why you might still be blocked with a clean fingerprint](why-blocked-with-a-clean-fingerprint.md).

---

*Written while maintaining [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level driven by stock Playwright. Steps 1, 2, 3 and 5
above are all mistakes I have made.*
