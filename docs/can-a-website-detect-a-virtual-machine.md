---
title: "Can a website detect a virtual machine?"
description: "How a website infers a VM: software GPU renderers, odd core and memory counts, missing audio, generic metrics - what a browser fingerprint can and cannot hide."
parent: "Detectors, Explained"
grand_parent: "Guides"
nav_order: 15
---


# Can a website detect a virtual machine?

Short version: a website cannot see your hypervisor directly, but it can read a
handful of browser-visible values that a virtual machine or a cloud instance tends to
get wrong, and it can add them up into a VM-likelihood score. So the honest answer is
"partly". It can guess from what the browser reports, and it usually cannot prove
anything from inside the page. What it can prove lives outside the browser: the exit
IP and the network handshake.

This page is what a detector actually looks at, why a default VM answers those
questions in a way that stands out, how a real-hardware persona changes the answers,
and the parts that a browser fingerprint does not and cannot touch.

## What a detector can see, and what it cannot

A page runs JavaScript. JavaScript can read the values the browser chooses to expose,
and nothing below that line. It cannot read the CPUID leaf that says "hypervisor
present", it cannot query the BIOS vendor string, it cannot see the virtual disk
controller. Those are the classic VM tells, and they belong to native malware analysis,
not to a web page.

What a page can do is infer. Every value below is individually plausible and, taken
together, describes a machine. A physical desktop and a headless cloud instance answer
these questions differently, and a detector that has scored a few million real browsers
knows what the physical desktop looks like.

- The GPU vendor and renderer string.
- The number of logical CPU cores.
- The reported device memory.
- Whether an audio output device exists, and what it sounds like.
- The screen geometry and pixel ratio.

None of these is an automation flag. They are all "this is probably not someone's
desktop" flags, which is a different and often more damaging thing.

## The signals that feed a VM score

Here is what each one looks like when it comes from a virtualized or headless
environment, and why it stands out.

**A software or generic GPU renderer.** A machine with no graphics hardware falls back
to a software rasterizer, and the [WebGL renderer
string](https://developer.mozilla.org/en-US/docs/Web/API/WEBGL_debug_renderer_info)
says so in plain text: a basic or generic renderer name, or the well-known software
rasterizer strings. A real desktop reports a specific GPU model from a specific vendor.
This is the single loudest VM tell
in the browser, because it is a string you can read directly rather than a statistic
you have to accumulate. Worse, the string and the pixels can disagree: a renderer that
names real hardware while a software path actually draws the frame is
[a mismatch you cannot paper over with a property override](renderer-string-vs-render.md).
The full surface is in [WebGL renderer strings](webgl-renderer-strings.md).

**Odd core and memory counts.**
[`navigator.hardwareConcurrency`](https://developer.mozilla.org/en-US/docs/Web/API/Navigator/hardwareConcurrency)
and
[`navigator.deviceMemory`](https://developer.mozilla.org/en-US/docs/Web/API/Navigator/deviceMemory)
are coarse, but their distribution across real browsers is not uniform. A tiny core
count paired with a claim of a modern desktop, or a memory
value that never appears on consumer hardware, moves the score. These two also have to
agree with each other and with the rest of the persona, which is
[why pinning one without its neighbours backfires](hardware-concurrency-device-memory.md).

**Missing or default audio.** An
[`AudioContext`](https://developer.mozilla.org/en-US/docs/Web/API/Web_Audio_API)
exposes a sample rate, an output latency and a channel count that come from a real
audio device. A container with no sound hardware answers with defaults that say
exactly that, and the rendered audio
fingerprint carries the same information. See
[AudioContext fingerprinting](audiocontext-fingerprinting.md) for what is actually
being measured.

**Generic display metrics.** A resolution nobody ships, a device pixel ratio no real
panel has, an available screen height equal to the full height (meaning no taskbar and
no window chrome). Each of these is a small nudge; together they describe a virtual
framebuffer rather than a monitor.

A detector does not need any single one of these to be damning. It needs several of
them to point the same way at once, which is exactly what an unconfigured VM delivers.

## What invisible_playwright does about the browser-visible layer

invisible_playwright is a Firefox patched at the C++ level and driven by stock
Playwright. Its whole design is to look like a real browser run by a real person, and
that is why it passes most fingerprint, TLS and driver-layer checks: those layers read
as a genuine Firefox because the browser genuinely is one, not a JavaScript costume
over a headless engine.

For the VM signals specifically, the build derives one coherent real-hardware persona
from a seed and overrides the browser-visible values to match it: the WebGL vendor and
renderer name a real GPU (and the pixels are drawn to agree with the string, not just
the string), `hardwareConcurrency` and device memory take values a real desktop
reports, the `AudioContext` presents a real device's output characteristics, and the
screen metrics describe a physical display. Every field is drawn from the same seed, so
they agree with one another rather than being individually plausible and jointly
contradictory, which is the failure mode a VM score is built to catch.

Switching from plain Playwright is two lines, and the operation this page is about -
loading a page that scores your environment - is ordinary Playwright after that:

```python
from invisible_playwright import InvisiblePlaywright

# same seed -> same GPU, cores, memory, audio and screen every run
with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    page.goto("https://example.com")
    # read the values a VM detector would read
    report = page.evaluate("""() => {
        const gl = document.createElement('canvas').getContext('webgl');
        const dbg = gl && gl.getExtension('WEBGL_debug_renderer_info');
        return {
            renderer: dbg ? gl.getParameter(dbg.UNMASKED_RENDERER_WEBGL) : null,
            cores: navigator.hardwareConcurrency,
            memory: navigator.deviceMemory,
            sampleRate: new (window.AudioContext || window.webkitAudioContext)().sampleRate,
            screen: [screen.width, screen.height, window.devicePixelRatio],
        };
    }""")
    print(report)
```

Run that inside the build and the renderer names a real GPU, the core and memory
counts sit in the range real desktops report, the sample rate is a real device's, and
the screen looks like a monitor. Run it in a stock headless browser on the same cloud
instance and you will typically see a software renderer, a low core count and screen
metrics that describe a framebuffer. That difference is the VM score, and it is the
part a browser can control.

Because the persona is seed-derived, `seed=42` gives the same machine on every run,
which is what makes a suspicious result reproducible instead of a one-off you cannot
chase.

## The parts a browser fingerprint cannot fix

This is the honest caveat, and skipping it is how people get surprised.

Matching the browser-visible layer to a real desktop covers the browser-visible layer.
It does nothing about:

- **The exit IP.** A datacenter address is a datacenter address, and a large share of
  VM detection in practice is just the IP's reputation and ASN. A perfect persona on a
  known cloud range still reads as a cloud instance. You supply the exit; the build
  only sets the browser to match it. See
  [why a container gets caught on the network and machine layers](playwright-docker-detection.md).
- **Hypervisor timing side-channels.** Some detectors time operations whose cost
  differs under virtualization. That is a property of the CPU you are actually running
  on, not of any value the browser reports, and no fingerprint override changes it.
- **Behaviour, rate and quota.** Human pacing, per-account limits and request velocity
  are yours to supply. A consistent browser hammering one endpoint from one address
  creates the exact signal it was trying to avoid.

So invisible_playwright helps with the browser-visible half of VM detection and does
not, on its own, fix the IP, the timing floor or your behaviour. It is designed to be
paired with a clean residential exit and human pacing, not to replace them. There is no
setting that makes a browser undetectable, and any tool that claims one is selling the
claim rather than the result.

## Conclusion

Can a website detect a virtual machine? It can make a strong guess from the browser
side - a software GPU renderer, odd core and memory counts, missing audio, generic
display metrics - and it can prove very little from inside the page while proving quite
a lot from your IP and handshake. invisible_playwright addresses the guessable,
browser-visible half by presenting one coherent real-hardware persona instead of the
default VM answers, and it is honest about the half it does not touch. Match the
browser to a real desktop, put it behind a clean exit, pace it like a person, and the
VM score stops being the thing that gives you away. Get any one of those three wrong and
the other two will not save you.

## Short answers to the questions that lead here

**Can JavaScript detect that I am in a VM?** Not directly. It cannot read CPUID, BIOS
strings or the hypervisor. It infers from browser-visible values like the GPU renderer,
core count, audio device and screen, and scores how VM-like they look together.

**What is the biggest browser-side VM tell?** A software or generic WebGL renderer
string. It is a value you can read in plain text rather than a statistic you have to
accumulate, so it stands out immediately.

**Does invisible_playwright hide that I am on a cloud server?** It fixes the
browser-visible signals - GPU, cores, memory, audio, screen - to one coherent real
desktop persona. It does not change your IP, which is often the actual reason a cloud
session gets flagged.

**Can a website detect a VM through timing?** Some detectors time operations that cost
more under virtualization. That is a property of the CPU you run on, not a browser
value, so no fingerprint override affects it.

**Will a real-hardware persona alone get me through?** No. Pair it with a clean
residential exit and human pacing. The persona covers the machine the browser reports,
not the address it connects from or how fast you click.

**Is there a setting that makes the browser undetectable?** No, and be wary of anything
that says otherwise. This lowers the browser-visible VM score to a real desktop's; the
IP, timing floor and behaviour are still yours to get right.

## Sources

- The public fingerprint suites this project tests against, read for which environment
  values they collect and how they weight them - GPU renderer, hardwareConcurrency,
  deviceMemory, AudioContext output and screen metrics.
- This project's release gates, which compare each of those fields against a stock
  Firefox on the same machine rather than reading a verdict.

**See also:** [WebGL renderer strings](webgl-renderer-strings.md) and
[hardwareConcurrency and deviceMemory](hardware-concurrency-device-memory.md) for two
of the signals above, and [how to test whether your browser is detected](how-to-test-bot-detection.md)
for the compare-against-stock method that catches a VM tell a verdict hides.

---

*Written while maintaining [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level driven by stock Playwright. It presents one coherent
real-hardware persona to the browser-visible layer; the IP, the timing floor and the
pacing are still yours to supply.*
