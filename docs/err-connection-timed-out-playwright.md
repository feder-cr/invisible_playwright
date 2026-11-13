---
title: "net::ERR_CONNECTION_TIMED_OUT in Playwright"
description: "net::ERR_CONNECTION_TIMED_OUT is the browser's own network stack giving up on a connection attempt, a separate mechanism from Playwright's configurable TimeoutError and not fixed by raising it."
parent: "Testing and Troubleshooting"
grand_parent: "Guides"
nav_order: 15
---


# net::ERR_CONNECTION_TIMED_OUT in Playwright

`net::ERR_CONNECTION_TIMED_OUT` means the browser's own network stack attempted a
connection and gave up waiting for anything to answer. Chromium's own network error
list defines it in five words, code -118, "A connection attempt timed out."
`page.goto()` throws it when this happens on navigation, and the detail worth fixing in
your head before anything else is that this timeout belongs to the browser's network
code, not to Playwright.

## Not the same thing as Playwright's own TimeoutError

This is the confusion that sends people to the wrong fix. Playwright's `TimeoutError` is
a driver-level concept: the client library made a call, `page.goto()`,
`locator.click()`, whatever it is, and gave up waiting for that call to finish within
the configured `timeout`, whether that is the default 30 seconds or a value you set
yourself. It fires from Playwright's own side, counting from when the call was made,
regardless of what the browser's network stack is still doing underneath it. [The page
dedicated to that error](playwright-timeout-30000ms-exceeded.md) covers it in full.

`net::ERR_CONNECTION_TIMED_OUT` is a different mechanism entirely: the browser's own
network code concluding that a specific connection attempt is never going to succeed,
with its own internal logic, independent of anything Playwright's `timeout` option is set
to. A real, concrete report makes this impossible to miss: [a user configured Playwright's
navigation timeout to 180 seconds](https://github.com/microsoft/playwright/issues/5062),
across every place that setting can be applied, and still received `page.goto:
net::ERR_TIMED_OUT` after roughly 30 seconds. The issue was filed as a request to make
Chromium's socket connect timeout configurable at all, because it was not: Playwright's
`timeout` governs how long Playwright waits for the call, with no lever over how long the
browser's own network stack waits for a TCP connection attempt before reporting failure.

Two different errors can appear for what looks like the same symptom, "the page never
loaded," and they mean opposite things about where to look. `TimeoutError` means
Playwright stopped waiting; the browser might still be trying. `net::ERR_CONNECTION_TIMED_OUT`
means the browser itself concluded the attempt failed; Playwright is just reporting what
it was told.

## The realistic causes

**Nothing answers the connection attempt at all.** Unlike [`ERR_CONNECTION_REFUSED`](err-connection-refused-playwright.md),
where something actively rejects the attempt with a TCP reset, a timeout means silence:
no reset, no acceptance, nothing. A firewall configured to drop packets rather than
reject them produces exactly this, and so does a genuinely unreachable address with no
route to it at all.

**The destination is too far away or too slow on a path with no active rejection.**
Real user reports show this exact error appearing intermittently against sites that are
in fact reachable moments later, from the same network. [One report](https://github.com/microsoft/playwright/issues/10851)
and [another](https://github.com/microsoft/playwright/issues/30406) both describe this
shape: not every attempt times out, the site loads fine in a normal browser session
seconds apart, and the failure reads as a transient network-path issue rather than
anything structurally broken.

**A proxy silently swallowing the connection instead of answering.** A misconfigured or
overloaded proxy can accept the client's connection and then never actually establish
one to the destination, leaving the browser waiting on a socket that will never resolve
either way until the browser's own internal ceiling is reached.

**A VPN, corporate network, or restrictive egress rule that drops rather than rejects.**
The same distinction as the firewall case above, seen specifically in environments where
outbound traffic is filtered by policy rather than by a route simply not existing.

## Diagnostic checklist

1. **Reproduce with `curl -v --connect-timeout 30` against the same target.** `curl`
   reports connection timeouts explicitly and lets you set the ceiling yourself, isolating
   whether the delay is on the network path or specific to the browser.
2. **Check whether the failure is consistent or intermittent.** Consistent points at a
   structural block, a dead route, or a firewall dropping packets; intermittent, on a
   target reachable moments later, points more at a transient path issue or load.
3. **Test with any proxy removed.** Clears without it, the proxy is implicated,
   specifically as something accepting the connection and never completing its own leg
   to the destination.
4. **Do not raise Playwright's timeout as the first move.** The real report cited above
   shows this directly: raising `timeout` to 180 seconds changed nothing about when
   `net::ERR_CONNECTION_TIMED_OUT` fired, because that ceiling belongs to the browser's
   own network stack, not to Playwright's driver-level wait.

## What Firefox calls the same failure

`invisible_playwright` drives a patched Firefox, and Firefox's networking layer does not
use `net::ERR_*` naming. The identical event, a connection attempt abandoned after
waiting with no response, is `NS_ERROR_NET_TIMEOUT` in Firefox's own error list, defined
as: "The connection was lost due to a timeout error." [A real Playwright report against
Firefox specifically](https://github.com/microsoft/playwright/issues/13027) shows this
exact string firing intermittently in a CI environment, on a test suite that passed
reliably on a developer's own machine, the same environment-dependent shape the
Chromium-side reports above show. The causes are identical; only the name in the log
differs.

It is worth naming the ceiling this timeout races against, because it explains why the
browser-level error is rarer to see in practice than Playwright's own `TimeoutError`. The
operating system's own TCP connection-attempt ceiling is typically far longer than either
browser's internal one: on Linux, the kernel's default SYN retry behavior takes roughly
127 seconds before giving up entirely, [documented in the kernel's own `tcp(7)` manual
page](https://man7.org/linux/man-pages/man7/tcp.7.html). Since Playwright's default
`timeout` is 30 seconds, far shorter than that ceiling, most navigations that would
eventually produce this error are cut off by Playwright's own `TimeoutError` first,
unless that default has been raised, or the browser's own internal connect timeout, not
configurable through Playwright, fires first.

## The honest boundary

A connection attempt that never receives a response is a network-path failure, resolved
entirely below any browser-identity layer this project touches. `invisible_playwright`
passes the navigation straight to the patched engine's own network stack and does not
retry a stalled connection, shorten or extend the browser's internal connect timeout, or
make an unreachable path reachable. A stock Playwright browser, a stock Firefox, and
this project's build all wait out an identical silent connection attempt identically,
because nothing about a cleaner fingerprint changes how long a TCP handshake is willing
to wait for a SYN-ACK that never arrives.

## Short answers to the questions that lead here

**What does net::ERR_CONNECTION_TIMED_OUT mean?** The browser's own network stack
attempted a connection and gave up after receiving no response at all, neither an
acceptance nor a rejection.

**Is this the same as Playwright's TimeoutError?** No. Playwright's `TimeoutError` is
the driver giving up on waiting for a call to return, governed by the `timeout` option
you configure. `net::ERR_CONNECTION_TIMED_OUT` is the browser's own network code
concluding a connection attempt failed, on its own internal ceiling, independent of
Playwright's setting.

**Will raising Playwright's timeout fix it?** No, and a real report confirms this
directly: raising the navigation timeout to 180 seconds did not change when
`net::ERR_TIMED_OUT` fired, because that ceiling is not the one Playwright's `timeout`
option controls.

**How is this different from ERR_CONNECTION_REFUSED?** A refusal is an active answer:
something rejected the attempt with a TCP reset, fast. A timeout is silence: nothing
answered at all within the browser's own waiting period.

**Why does it happen intermittently against a site that is otherwise reachable?** Real
reports show exactly this shape, a target that loads fine seconds later from the same
network. Treat it as a transient network-path or load issue and check consistency before
assuming a structural block.

**What does Firefox call this instead of ERR_CONNECTION_TIMED_OUT?** `NS_ERROR_NET_TIMEOUT`,
defined as a connection lost to a timeout error, with the identical set of causes behind
it.

## Sources

- Chromium's [`net/base/net_error_list.h`](https://chromium.googlesource.com/chromium/src/+/main/net/base/net_error_list.h),
  for `ERR_CONNECTION_TIMED_OUT` (-118), retrieved 2026-08-30.
- Mozilla's [`xpcom/base/ErrorList.py`](https://searchfox.org/mozilla-central/source/xpcom/base/ErrorList.py)
  (viewed via the Fossies mirror), for `NS_ERROR_NET_TIMEOUT` (14) and its exact
  definition, retrieved 2026-08-30.
- [microsoft/playwright#5062](https://github.com/microsoft/playwright/issues/5062), a
  real report of `net::ERR_TIMED_OUT` firing after roughly 30 seconds despite
  Playwright's own navigation timeout being set to 180 seconds, filed as a request to
  make the browser's own socket connect timeout configurable.
- [microsoft/playwright#10851](https://github.com/microsoft/playwright/issues/10851) and
  [microsoft/playwright#30406](https://github.com/microsoft/playwright/issues/30406),
  intermittent `net::ERR_CONNECTION_TIMED_OUT` reports against targets confirmed
  reachable moments later.
- [microsoft/playwright#13027](https://github.com/microsoft/playwright/issues/13027),
  `NS_ERROR_NET_TIMEOUT` reported against Firefox specifically, intermittent in CI and
  not reproducing locally.
- The Linux manual page for [`tcp(7)`](https://man7.org/linux/man-pages/man7/tcp.7.html),
  for the kernel's own default TCP SYN retry ceiling, retrieved 2026-08-30.

**See also:** [TimeoutError: Timeout 30000ms Exceeded](playwright-timeout-30000ms-exceeded.md)
for the driver-level timeout this page distinguishes itself from, [ERR_CONNECTION_REFUSED
in Playwright](err-connection-refused-playwright.md) for the active-rejection counterpart
to this silent one, and [ERR_NETWORK_CHANGED in Playwright](err-network-changed-playwright.md)
for a connection that was interrupted mid-flight rather than never answered at all.

---

*From the notes of [invisible_playwright](https://github.com/oss-invisible/playwright),
a Firefox patched at the C++ level. The browser's own connect timeout is not a Playwright
setting and this project does not extend or shorten it; a silent connection attempt is
the network answering nothing, which no fingerprint work reaches.*
