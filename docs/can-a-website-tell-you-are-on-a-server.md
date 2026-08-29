---
title: "Can a website tell you are running on a server?"
description: "Websites spot a datacenter or headless host via software WebGL, missing audio, headless metrics, datacenter ASN - and what a real fingerprint can fix and cannot."
parent: "Browser Identity"
grand_parent: "Guides"
nav_order: 25
---


# Can a website tell you are running on a server?

Often, yes. Not because the code is automated, but because the host is a server:
no GPU, no sound card, no monitor, and an address that belongs to a datacenter. A
browser running there answers a handful of ordinary questions in a way that only a
server answers them, and a detector does not need anything clever to notice.

The honest split is worth stating up front. Most of these tells are JavaScript-visible
properties of the machine, and a browser that presents a real desktop persona answers
them the way a desktop does, whether the host underneath is a laptop, a Linux VM or a
Docker container. One of them is not a browser property at all: the exit IP is read at
the network layer, before any script runs, and no browser can disguise it. That one is
yours to solve with a clean proxy.

## What a server actually leaks

The failures that get a headless-host session blocked are almost never automation
flags like [`navigator.webdriver`](navigator-webdriver-explained.md). Those are solved
by every serious tool on day one. The ones that survive are hardware questions the
machine cannot answer honestly:

- **The GPU**, read through the [`WEBGL_debug_renderer_info`](https://developer.mozilla.org/en-US/docs/Web/API/WEBGL_debug_renderer_info)
  extension, which on a server names a software rasterizer instead of real hardware.
- **The audio device**, read through [`AudioContext`](https://developer.mozilla.org/en-US/docs/Web/API/AudioContext), which on a server does not exist.
- **The screen**, whose resolution, available height and device pixel ratio describe
  a display that no real desktop has.
- **The exit address**, whose ASN says datacenter.

None of these are in JavaScript's gift to change from a page-level script, which is
why a stealth plugin bolted onto a stock headless browser does not touch them. They
are decided by the box the browser runs on. The rest of this page walks the first three
(which a real-browser build answers on the browser side) and then the fourth (which it
cannot).

## The software renderer tell

Ask a browser on a normal desktop what draws its graphics and it names a real GPU
vendor and model. Ask a browser on a server and, if it renders WebGL at all, it names
a software rasterizer: strings like [`llvmpipe`](https://docs.mesa3d.org/drivers/llvmpipe.html)
or [`SwiftShader`](https://github.com/google/swiftshader), or a generic "software"
renderer. That string is a plain statement that there is no graphics hardware present,
and a detector reads it as exactly that.

There is a subtler version of the same tell, and it is the one that catches half-fixes:
the renderer string can be edited to say a real GPU while the pixels are still drawn by
software. A page that draws a scene and reads back the result gets output that does not
match the name, which is [a mismatch you cannot patch from a string override](renderer-string-vs-render.md).
The name and the pixels have to agree.

invisible_playwright presents a real GPU persona, and the readback matches it, because
the presented persona is what actually answers both the string query and the render.
The [WebGL renderer strings page](webgl-renderer-strings.md) covers what a consistent
vendor/renderer pair looks like and why the two halves have to line up.

## The missing audio device and the headless screen

Sound is the same story as graphics. An AudioContext reports a sample rate, an output
latency and a channel count, and on a real machine those come from a real sound card.
A container has none, so it answers with the defaults that a headless host produces,
and the defaults are a signature. An empty or default audio profile under a desktop user
agent is a server saying it is a desktop. The mechanism is in
[AudioContext fingerprinting](audiocontext-fingerprinting.md).

The screen is the third. Headless hosts report resolutions nobody runs, a device pixel
ratio no physical display has, and an available height equal to the full height, which
means there is no taskbar because there is no desktop. Each value on its own is weak;
together they describe a monitor that does not exist. The full set of these is in
[screen size and other headless tells](screen-size-headless-tells.md).

A real-browser build answers all three as a Windows desktop would: a real GPU persona,
a real AudioContext output, and Windows screen metrics with a plausible available height
and device pixel ratio. Those answers are the same whether the host is your laptop or a
Linux container, because they are presented by the browser rather than read from the box.

## What invisible_playwright presents instead

The launch is two lines, and the browser you get back is a real Playwright
[`Browser`](https://playwright.dev/python/docs/api/class-browser), so every method is the one you already know. Here is the launch plus reading back the
three signals this page is about:

```python
from invisible_playwright import InvisiblePlaywright

with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    page.goto("https://example.com")

    report = page.evaluate("""() => {
        const gl = document.createElement('canvas').getContext('webgl');
        const dbg = gl.getExtension('WEBGL_debug_renderer_info');
        const ac = new (window.AudioContext || window.webkitAudioContext)();
        return {
            renderer: gl.getParameter(dbg.UNMASKED_RENDERER_WEBGL),
            vendor:   gl.getParameter(dbg.UNMASKED_VENDOR_WEBGL),
            sampleRate: ac.sampleRate,
            screen: [screen.width, screen.height, screen.availHeight],
            dpr: window.devicePixelRatio,
            platform: navigator.platform,
        };
    }""")
    print(report)
```

Run that on a Linux server or inside a container and the renderer names a real GPU, the
vendor matches it, the AudioContext reports a desktop sample rate, the screen metrics
describe a Windows display with a taskbar, and `navigator.platform` reads `Win32`. The
same `seed=42` produces the same persona every run, so a value you saw once is a value
you can reproduce and diff, rather than a fresh random draw each launch.

Because the browser is a genuinely patched Firefox driven by stock Playwright, the layers
underneath the JavaScript match too: the TLS handshake and the driver surface read as a
real Firefox rather than as an automated one. That consistency is the reason it passes
most detection checks, and the way to confirm it is by comparison against a stock browser
on the same machine rather than by trusting a single verdict.

## The boundary: the datacenter IP is still visible

Here is the caveat, stated plainly because a page that only lists what it fixes is not
honest. Everything above happens inside the browser, at the JavaScript layer. The exit
IP does not. The server reads your address at the network layer before a single line of
script runs, and if that address sits in a datacenter ASN, a perfect browser fingerprint
does not hide it. The browser cannot disguise the IP, by design, because the IP is not
a browser property.

So a headless host with a flawless desktop persona and a datacenter exit has fixed the
JavaScript tells and left the network tell standing. The fix is a residential or mobile
proxy that presents a clean address, passed on launch:

```python
proxy = {
    "server": "socks5://gate.example.com:1080",
    "username": "user",
    "password": "pass",
}
with InvisiblePlaywright(seed=42, proxy=proxy) as browser:
    page = browser.new_page()
    page.goto("https://example.com")
```

And the address is only the first of the things the browser does not fix. IP reputation,
per-account quotas, rate limits, and behaviour and timing are all supplied by you: a
clean exit, human pacing, and sane request volume. The [configuration page](configuration.md)
covers proxy schemes and how the browser timezone is auto-derived from the exit so the
two do not tell different stories, and the [Docker and container detection walkthrough](playwright-docker-detection.md)
covers the host-side tells in the order they usually bite.

## Conclusion

Can a website tell you are on a server? From the browser alone, it usually can, through
software WebGL, a missing audio device and headless screen metrics. A real-browser build
answers those as a desktop, on the browser side, regardless of the Linux or container host
underneath, which is why the JavaScript-visible surface stops giving the server away. What
it does not and cannot fix is the datacenter IP, which is read at the network layer and
needs a clean proxy, along with the account limits and pacing that no fingerprint touches.
Fix the machine tells with the browser, fix the address with the proxy, and pace the
behaviour yourself.

## Short answers to the questions that lead here

**Can a website detect a headless or server browser?** Often, from the machine rather
than the automation: a software WebGL renderer, no audio device, headless screen metrics,
and a datacenter IP.

**Does invisible_playwright hide that I am on a server?** It presents a real GPU, real
audio and Windows screen metrics on the browser side, so the JavaScript-visible tells look
like a desktop. It does not hide the exit IP, which is a network-layer signal.

**Is the datacenter IP a browser problem?** No. The address is read before any script runs,
so no browser can change it. That one needs a residential or mobile proxy.

**Why does a software renderer give me away?** Because a real desktop names a real GPU, and
`llvmpipe` or `SwiftShader` is a plain statement that there is no graphics hardware present.

**Do I still need a proxy if my fingerprint is perfect?** Yes, if the host is in a
datacenter. A flawless browser on a datacenter ASN is still on a datacenter ASN.

**Does this work the same in Docker as on a real machine?** The browser-side answers are
the same because the persona is presented by the browser, not read from the host. The IP
and the pacing still depend on where and how you run it.

## Sources

- MDN Web Docs: [`WEBGL_debug_renderer_info`](https://developer.mozilla.org/en-US/docs/Web/API/WEBGL_debug_renderer_info),
  retrieved 2026-08-28, for the `UNMASKED_VENDOR_WEBGL` and `UNMASKED_RENDERER_WEBGL`
  tokens a software renderer answers with.
- MDN Web Docs: [`AudioContext`](https://developer.mozilla.org/en-US/docs/Web/API/AudioContext),
  retrieved 2026-08-28, for the sample rate, output latency and channel count a
  container reports with no sound card present.
- Mesa 3D Graphics Library documentation: [LLVMpipe](https://docs.mesa3d.org/drivers/llvmpipe.html),
  retrieved 2026-08-29, for the software rasterizer whose name is one of the two strings a
  GPU-less host answers `UNMASKED_RENDERER_WEBGL` with.
- Google SwiftShader, [project repository](https://github.com/google/swiftshader), retrieved
  2026-08-29, for the other software renderer a headless host commonly reports in place of
  a GPU.
- Playwright documentation: [`Browser`](https://playwright.dev/python/docs/api/class-browser),
  retrieved 2026-08-29, for the class the launch call in this article returns unchanged.
- This project's release gates, which read WebGL renderer, AudioContext output and screen
  metrics through the shipped binary on a Linux host and compare them against a stock
  desktop browser.
- The per-surface pages linked throughout, each read from its own detector's source rather
  than from a rendered verdict.

**See also:** [Docker and container detection](playwright-docker-detection.md),
[WebGL renderer strings](webgl-renderer-strings.md), and
[screen size and other headless tells](screen-size-headless-tells.md).

---

*Written while maintaining [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level driven by stock Playwright. It fixes the machine tells a
server leaks in JavaScript; the datacenter IP and the pacing are still yours to supply.*
