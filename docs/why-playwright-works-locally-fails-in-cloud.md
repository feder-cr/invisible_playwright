---
title: "Why Playwright Works Locally but Fails in the Cloud"
description: "Identical Playwright script passes on your laptop but gets blocked on CI or cloud runner because the exit IP moved from residential to datacenter, not the code."
parent: "Testing and Troubleshooting"
grand_parent: "Guides"
nav_order: 12
---


# Why Playwright Works Locally but Fails in the Cloud

You wrote the script, it worked on your laptop, you moved it to a server or a CI
runner, and now the same script gets a challenge page, a short body, or an outright
block. Nothing in the code changed. What changed is the exit IP: a home connection is
residential, a cloud or CI runner is datacenter, and that difference gets scored
before your page ever loads. This is one of the most common and most misdiagnosed
failures in browser automation, and the answer is usually not in your code at all.

This page explains what actually changed between the two environments, how to prove
it in a few minutes, and the one variable you have to supply yourself because no
browser layer can invent it.

## The one thing that changed: the exit IP

Your home connection hands you a residential IP. Residential ranges carry clean
reputation by default, because that is where ordinary people browse from. A cloud
instance or a CI runner hands you a datacenter IP, and datacenter ranges are
pre-scored as high-risk before a single line of JavaScript runs. The scoring happens
at the network layer, on the connection itself, ahead of any page logic.

That timing is the whole point. It means the decision can be made before your
fingerprint, your user agent, or your behaviour is ever inspected. A browser that
would sail through every in-page check still arrives on a connection the receiving
side has already flagged. The identical script and the identical fingerprint
therefore pass at home and fail in the cloud, and the difference is not something you
can patch in the browser because it was decided before the browser spoke.

This is also why "buy a better proxy" is both the right instinct and the wrong first
move. It is the right layer, but you should confirm the layer before you spend on it.

## Isolate the variable before you spend money on it

The trap here is changing two things at once. You moved from laptop to server, and
you moved from residential to datacenter, in the same step. If the run fails you do
not yet know which move caused it, and most people guess wrong and start rewriting
browser code.

The clean way to isolate it is to hold the browser identity constant across both
environments and let only the network differ. `invisible_playwright` makes that
straightforward, because a fixed seed produces the same browser everywhere:

```python
from invisible_playwright import InvisiblePlaywright

# Run this line for line on the laptop AND on the cloud runner.
with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    page.goto("https://example.com")
    print(page.title())
```

`seed=42` fixes the whole identity: the same GPU string, the same canvas and audio
hashes, the same fonts and screen, run after run, on any machine. The `browser`
object is a real Playwright [`Browser`](https://playwright.dev/python/docs/api/class-browser),
so every method you already use works
unchanged.

Now the logic is simple. Run that exact script on the laptop and on the cloud runner.
If the browser is byte-for-byte the same identity in both places and only one of them
gets blocked, the browser is not the variable. The network is. You have isolated it
without touching a single fingerprint field, and you know where to spend.

One honest exception to fold in: a cloud runner also lacks a GPU, real fonts, and an
audio device, which are machine tells rather than automation tells. Read
[the container detection checklist](playwright-docker-detection.md) to rule those out
first, because a headless container can differ from your laptop in the machine layer
as well as the network layer.

## Route the cloud run through a residential exit

Once the network is confirmed as the variable, the fix is to give the cloud run an
exit that looks like an ordinary person's connection. That is a proxy configured with
a residential endpoint, passed to the same class:

```python
from invisible_playwright import InvisiblePlaywright

proxy = {
    "server": "socks5://gate.example.com:1080",
    "username": "user",
    "password": "pass",
}

with InvisiblePlaywright(seed=42, proxy=proxy) as browser:
    page = browser.new_page()
    page.goto("https://example.com")
    print(page.title())
```

The browser layer is now identical to your laptop run, and the connection is now a
residential one rather than a datacenter one. DNS is routed through the proxy by
default so it does not leak around the tunnel, and by default the browser timezone is
derived from the proxy's egress IP so the two do not tell different stories. The
[Configuration](configuration.md) page covers the proxy schemes, and
[rotating proxies across a run](how-to-rotate-proxies-playwright.md) covers spreading
requests over several exits instead of hammering one.

Confirm the exit from inside the browser rather than assuming it. Open a page that
reports your address and read it on the page, because a proxy that silently failed to
attach looks exactly like a proxy that attached and got blocked.

## The honest caveat: plumbing is not reputation

This is where overclaiming would be easy and wrong. `invisible_playwright` supplies
the proxy plumbing. It does not, and cannot, make a datacenter IP look residential on
its own. If you point the `proxy` field at another datacenter range, you have moved
the block one hop, not removed it. The tool keeps the browser honest and consistent;
the exit's reputation is a property of the exit, and you have to source a clean one.

The same boundary applies to everything that is not a browser property. The tool is
designed to look like a real Firefox driven by a real person, which is why it passes
most in-page checks: the fingerprint, the TLS handshake, and the driver layer read as
a genuine browser. It does not fix, and you still own:

- **IP reputation**, the subject of this page, which is a network property.
- **Per-account quotas and rate limits**, which are counted server-side against your
  identity regardless of how the browser looks.
- **Behaviour and timing**, the pointer motion and pacing that a watching site scores.
  The [checklist for a single-site block](playwright-detected-as-bot.md) works through
  these in the order they usually matter.

A consistent browser on a clean residential exit is the browser-and-network layers
handled. It is not a guarantee, because the layers above it are yours to run well.

## Conclusion

When a Playwright script passes locally and fails in the cloud, start from the
assumption that your code is fine and the exit IP moved from residential to
datacenter. Prove it by pinning the browser identity with a seed and running the
identical script in both places: if only the cloud run fails, the network is the
variable. Then route the cloud run through a residential exit and confirm the address
from inside the page. The browser layer `invisible_playwright` gives you is the same
everywhere by design; the clean exit, the quotas, and the pacing are the parts you
supply, and naming them honestly is the difference between a run that works and a
claim that does not.

## Short answers to the questions that lead here

**Why does my Playwright script work locally but fail in CI or the cloud?** Almost
always the exit IP. Home is a residential IP with clean reputation; a cloud or CI
runner is a datacenter range that is pre-scored as high-risk before any JavaScript
runs. Same code, different network.

**Is it my code or my browser?** Neither, usually. Pin the identity with a seed, run
the same script on both machines, and if only the cloud run is blocked the browser is
not the variable.

**Does a residential proxy fix it?** It fixes the layer this page is about. Route the
cloud run through a clean residential exit and the network stops looking like a
datacenter. It does not fix quotas, rate limits, or behaviour.

**Can invisible_playwright make a datacenter IP look residential?** No. It supplies
the proxy plumbing and keeps the browser fingerprint consistent, but the exit's
reputation is a property of the exit. Point it at another datacenter range and the
block moves one hop.

**Why does the exact same fingerprint pass and fail?** Because the fingerprint was
never the deciding factor in the cloud case. The connection was scored at the network
layer before the page loaded, so an identical browser lands on a flagged IP.

**Should I run headless in the container?** Headless is rarely the tell. The tells are
what comes with a container: no GPU, no fonts, no audio device, and a datacenter IP.
Handle those and headless is fine.

## Sources

- This project's own measurements, where the same seeded identity is run on a
  residential connection and a datacenter runner and only the network differs, which
  is what isolates IP reputation as the variable.
- [Playwright's `Browser` class reference](https://playwright.dev/python/docs/api/class-browser),
  for what the launched `browser` object exposes once the wrapper hands it back.
- Playwright's proxy option at browser and context level, documented at
  [Playwright network documentation](https://playwright.dev/python/docs/network).
- The proxy, timezone, and DNS behaviour documented on the
  [Configuration](configuration.md) page and exercised by the release gates.

**See also:** [why a good browser on a bad IP still gets blocked](web-scraping-getting-blocked-proxies.md),
[running Playwright undetected in Docker](how-to-run-playwright-docker-undetected.md),
and [rotating proxies across a run](how-to-rotate-proxies-playwright.md).

---

*Written while maintaining [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level driven by stock Playwright. The seed that pins the
browser is what let me prove, more than once, that a failing cloud run was the exit
and not the code.*
