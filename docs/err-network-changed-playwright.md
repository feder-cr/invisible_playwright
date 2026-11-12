---
title: "net::ERR_NETWORK_CHANGED in Playwright"
description: "net::ERR_NETWORK_CHANGED fires when the OS reports a network interface change mid-request, aborting the connection outright. Why this is common in CI and cloud environments, and why it is usually transient."
parent: "Testing and Troubleshooting"
grand_parent: "Guides"
nav_order: 16
---


# net::ERR_NETWORK_CHANGED in Playwright

`net::ERR_NETWORK_CHANGED` means the operating system reported a change to the network
while a connection was in progress, and the browser aborted that connection rather than
trying to continue it. Chromium's own network error list defines it in four words, code
-21, "The network changed." `page.goto()` throws it when this happens during navigation,
and the failure is almost never about the destination site at all: it is about the
network interface underneath the browser moving while a request was in flight.

## The mechanism behind it

Chromium watches for network changes through its own `NetworkChangeNotifier`, which
listens for OS-level signals distinct from any single event: an IP address changing, a
broader connectivity change, a general network change, and a DNS configuration change.
A public Chromium bug discussion of this exact error is direct about when it actually
bites: it is "flagged if the IP address changes during header reads or DNS lookups,"
meaning the abort is not limited to the moment a connection first opens; a change
arriving mid-response, while headers are still being read, kills an otherwise
successful request just as readily.

Developers investigating repeated instances of this error in Chromium's own tracker found
something specific and useful: the notifications can fire far more often than an actual
network change would justify. One developer's capture, described directly in that
discussion, showed `NETWORK_IP_ADDRESSES_CHANGED` firing "about once per second" on a
machine under heavy concurrent network activity, "killing a spdy session" each time. The
signal exists to protect a connection from surviving a genuine network change unnoticed;
under load it can fire far more often than that, and every firing aborts whatever
connection was mid-flight.

## Why this shows up so often in CI and cloud environments specifically

**Container and pod networking churn.** A real report shows `net::ERR_NETWORK_CHANGED`
firing intermittently, roughly half the time, [from inside a Kubernetes cluster on
AWS](https://github.com/microsoft/playwright/issues/13062), while the identical test
suite ran cleanly on the reporter's own local machine. Container network namespaces are
attached, reconfigured, and torn down far more actively than a developer's own laptop's
network interface, and each of those events is exactly the class of signal
`NetworkChangeNotifier` reacts to.

**VPN or proxy connections toggling mid-run.** Connecting or disconnecting a VPN while a
page is loading changes the effective network path and the IP address the OS reports,
precisely the shape this error is designed to catch, whether the toggle was deliberate or
an artifact of a flaky VPN client reconnecting on its own.

**A Docker network being recreated or a container restarting its own networking.** The
same namespace mechanics [covered in detail on the ERR_CONNECTION_REFUSED
page](err-connection-refused-playwright.md#the-docker-and-ci-networking-variant-in-detail)
mean a container's network can be torn down and rebuilt independently of what the browser
process itself is doing, mid-request, with no warning visible to Playwright.

**Genuine interface switching**, a machine moving from Wi-Fi to Ethernet, from one Wi-Fi
network to another, or waking from sleep with its network stack re-initializing. Less
common on a CI runner than the container cases above, but the same underlying signal.

**Heavy concurrent I/O on the same host.** The Chromium discussion above found spurious,
repeated `NETWORK_IP_ADDRESSES_CHANGED` notifications firing under load with no actual
network event behind them, meaning a loaded CI runner, several jobs sharing one machine's
network stack, can trigger this error with nothing genuinely changed.

## Diagnostic checklist

1. **Check whether the failure is one-off or systematic.** A single intermittent
   occurrence is consistent with the mechanism above; a failure on every run points at
   something actively toggling the network on that machine or pipeline stage.
2. **Correlate the failure's timing against any network-affecting process on the same
   host or pod.** A VPN client, a container orchestrator reattaching a pod's network, or
   a deployment step restarting networking are the concrete triggers worth checking for.
3. **In Kubernetes or another orchestrated environment, check for pod network events
   around the failure time.** The real report cited above traced this shape directly to
   a cluster; the orchestrator's own event log is where to look for a reattachment
   coinciding with the failure.
4. **If the environment is otherwise stable, test whether concurrent load correlates
   with the failures.** The spurious-notification behavior documented in Chromium's own
   bug tracker means a busy machine can produce this error with no real network change
   behind it at all.
5. **Treat a genuinely one-off occurrence as retryable**, since the failure describes an
   interruption, not a structural block.

## What Firefox reports, and where the mapping stops being clean

`invisible_playwright` drives a patched Firefox, and here the honest answer is that
Firefox has no single, dedicated error constant playing the same role as Chromium's
`ERR_NETWORK_CHANGED`. Its own low-level watcher for this class of event is
`nsINetworkLinkService`, which tracks whether the machine's network link is up or down;
[a real Mozilla bug](https://bugzilla.mozilla.org/show_bug.cgi?id=1593693) against that
exact service shows it capable of reporting link status incorrectly, confirming the
watcher exists and has its own edge cases, but Firefox does not fold a mid-request
interface change into one dedicated, generically-named result code the way Chromium's
error list does. In practice, a network change interrupting a Firefox connection
mid-flight tends to surface through the general-purpose `NS_ERROR_NET_RESET`, if the
interruption reads as the connection dying, or `NS_ERROR_NET_TIMEOUT`, if it reads as
the replacement path never responding. Do not search for a literal Firefox equivalent
of the Chromium string; look instead at whether one of those two codes appeared at the
same moment as a real network event on the host.

## The honest boundary

A network interface changing while a connection is open is an operating-system-level
event, reported to the browser's network stack below any layer a browser's identity
touches. `invisible_playwright` passes the connection straight to the patched engine and
does not suppress, filter, or retry around a network-change notification the OS
delivers. A stock Playwright browser, a stock Firefox, and this project's build all
abort an in-flight connection identically when the machine underneath them reports a
network change, because nothing about a cleaner fingerprint changes what the operating
system tells the browser about its own network interfaces.

## Short answers to the questions that lead here

**What does net::ERR_NETWORK_CHANGED mean?** The operating system reported a change to
the network, an IP address change, a connectivity change, or a DNS configuration change,
while a connection was in progress, and the browser aborted it rather than continuing.

**Why does this happen so often in CI or cloud environments specifically?** Container
and pod networking is reattached and reconfigured far more actively than a developer's
own machine's network interface, and each of those events is exactly what this
mechanism watches for. A real report shows this failing roughly half the time inside a
Kubernetes cluster while never reproducing locally.

**Is this always a real network change?** Not necessarily. Chromium's own bug tracker
documents the underlying notification firing spuriously under heavy load, once per
second in one captured case, with no genuine network event behind it.

**Should I retry after seeing this error?** Usually, yes, if the occurrence looks
one-off. The error describes an interruption of an otherwise viable connection, not a
structural failure, and a retry after a transient interface event commonly succeeds.

**Does Playwright or invisible_playwright cause spurious network-change reports?**
No. The notification comes from the operating system's own network-change signals,
below any layer either Playwright or this project's stealth patching touches.

**What does Firefox call this instead of ERR_NETWORK_CHANGED?** There is no single
direct equivalent. Firefox's own network-link watcher is `nsINetworkLinkService`, but a
mid-request interruption from a real network change typically surfaces through the more
general `NS_ERROR_NET_RESET` or `NS_ERROR_NET_TIMEOUT` rather than one dedicated code.

## Sources

- Chromium's [`net/base/net_error_list.h`](https://chromium.googlesource.com/chromium/src/+/main/net/base/net_error_list.h),
  for `ERR_NETWORK_CHANGED` (-21), retrieved 2026-08-30.
- The Chromium bug discussion ["Frequently net::ERR_NETWORK_CHANGED error
  pages"](https://groups.google.com/a/chromium.org/g/chromium-bugs/c/79IS7AQCGcA), for
  the developer-confirmed mechanism (aborting on `NETWORK_IP_ADDRESSES_CHANGED` during
  header reads or DNS lookups) and the documented case of spurious, repeated
  notifications firing under heavy load, retrieved 2026-08-30.
- [microsoft/playwright#13062](https://github.com/microsoft/playwright/issues/13062), a
  real report of `net::ERR_NETWORK_CHANGED` firing intermittently inside a Kubernetes
  cluster on AWS, not reproducing on the reporter's local machine.
- Mozilla Bugzilla [1593693](https://bugzilla.mozilla.org/show_bug.cgi?id=1593693), for
  `nsINetworkLinkService`, Firefox's own network-link status watcher, and a documented
  case of it reporting link status incorrectly.

**See also:** [ERR_CONNECTION_TIMED_OUT in Playwright](err-connection-timed-out-playwright.md)
for a connection that never got a response at all rather than one interrupted mid-flight,
[ERR_CONNECTION_REFUSED in Playwright](err-connection-refused-playwright.md) for the
Docker and container networking detail this page's CI causes build on, and [ERR_CONNECTION_RESET
in Playwright](err-connection-reset-playwright.md) for the closely related shape of a
connection dying mid-request for a different reason.

---

*From the notes of [invisible_playwright](https://github.com/oss-invisible/playwright),
a Firefox patched at the C++ level. A network-change abort comes from the operating
system's own signals to the network stack; no fingerprint work sits anywhere near that
layer.*
