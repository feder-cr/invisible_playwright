---
title: "net::ERR_CONNECTION_REFUSED in Playwright"
description: "net::ERR_CONNECTION_REFUSED means something actively rejected the connection attempt, not that nobody answered. The Docker and CI variant, where localhost inside a container is not the host machine, is the most common real cause."
parent: "Network, Proxy and WebRTC"
grand_parent: "Guides"
nav_order: 37
---


# net::ERR_CONNECTION_REFUSED in Playwright

`net::ERR_CONNECTION_REFUSED` means the machine at the other end of the address and port
actively refused the connection: it answered with a TCP reset instead of staying silent.
Chromium's own network error list defines it in five words, "A connection attempt was
refused," code -102. `page.goto()` throws it fast, usually in well under a second, because
a refusal is an immediate reply, not a wait for something that never comes.

That speed is itself informative. A connection that times out is one where nothing
answers at all, covered separately on [`ERR_CONNECTION_TIMED_OUT`](err-connection-timed-out-playwright.md).
A connection that is refused got an answer, and the answer was no. Something is running
at that address that can send a TCP RST, which usually means either nothing is listening
on that exact port, or something is actively and deliberately rejecting the attempt.

## The realistic causes

**Nothing is listening on that port.** The most literal reading: a dev server not yet
started, a service that crashed, or a port number that is simply wrong. Confirm the
process is actually running and bound to the port you are navigating to.

**A firewall or security group actively rejecting, not silently dropping.** Some
firewall configurations answer a blocked port with a reset (refused) and others simply
drop the packet (timeout). Which one you get depends on the device, not on Playwright.

**A local dev server that had not finished starting yet.** [A real report](https://github.com/microsoft/playwright/issues/35879)
shows this exact error firing in headless mode specifically, immediately, against a
server still starting up: headful mode's slightly longer startup window happened to give
it enough time, headless did not.

**A CI runner whose network path or firewall differs from a developer's own machine.**
[A GitHub Actions report](https://github.com/microsoft/playwright/issues/21414) and
[a CircleCI report](https://github.com/microsoft/playwright/issues/20343) both show
`ERR_CONNECTION_REFUSED` against `localhost` reproducing reliably in CI and not locally,
which is the signature of an environment-specific networking difference instead of a
code regression.

## The Docker and CI networking variant, in detail

This is the shape worth understanding on purpose, because "it works on my machine and
fails in the container" is the single most common real cause behind this error, and the
reason is not intuitive if you have not hit it before.

Docker containers run in their own network namespace, a Linux kernel feature that gives a
process its own IP addresses, its own loopback interface, and its own view of "localhost"
entirely separate from the host machine's. As it is put directly: "when a server listens
on 127.0.0.1 inside the container network namespace" and something outside that
namespace, including the host, tries to reach `127.0.0.1`, "these are different
interfaces, so no connection is made." `localhost` inside a container refers to that
container, never to the host, and never to a sibling container, no matter how natural it
feels to type it out of habit.

This produces `ERR_CONNECTION_REFUSED` in a specific, recognizable shape: Playwright runs
inside a container and navigates to `http://localhost:3000`, expecting to reach a server
on the host machine or in a different container. It reaches its own container's loopback
instead, where nothing is listening, and gets refused. The same code works perfectly on a
developer's own machine, because there `localhost` genuinely is the machine running both
the browser and the server, no namespace boundary in between, which is exactly why the
bug reads as "nothing changed" when the entire network topology did. Port publishing
(`-p 3000:3000`) does not fix this either if the server itself is bound to `127.0.0.1`,
not `0.0.0.0`: "the server is listening on 127.0.0.1 inside the container network
namespace" while the forwarded traffic arrives on the external interface, an address the
server was never told to listen on. [A real report](https://github.com/microsoft/playwright/issues/24582)
shows exactly this shape: the port listed as exposed in a Compose file, and the connection
still refused.

The fixes follow directly from the cause: bind the target service to `0.0.0.0` so it
accepts connections on every interface, use Docker's own DNS name `host.docker.internal`
to reach the host from inside a container, and, for container-to-container traffic under
Compose, address the other service by its **service name** on the shared network rather
than by `localhost`. On Linux hosts, `host.docker.internal` is not wired up by default the
way it is on Docker Desktop, and needs `--add-host=host.docker.internal:host-gateway` or
the equivalent Compose `extra_hosts` entry.

## Diagnostic checklist

1. **Confirm the target process is running and bound to that port**, from inside the
   same environment that will run the browser, not from your own machine. `curl
   http://target:port` inside the container or runner is the fastest check.
2. **If this is Docker, ask what `localhost` resolves to from where you are asking.**
   Playwright's process, the target service, and your own terminal can each be in a
   different network namespace, each with its own idea of `localhost`.
3. **Check what interface the target service binds to**, and address other Compose
   services by their service name, not `localhost`, which does not cross
   container boundaries at all.
4. **In CI specifically, test from inside the runner**, since a firewall or security
   group there can behave differently from your own machine even when the code and the
   port are both correct.
5. **If a local dev server is involved, rule out a startup race** before assuming
   anything about networking: a navigation that fires before the server has finished
   binding produces this exact error in headless mode.

## What Firefox calls the same failure

`invisible_playwright` drives a patched Firefox rather than Chromium, and Firefox's own
networking layer does not use `net::ERR_*` naming. The identical event, a connection
actively refused, is `NS_ERROR_CONNECTION_REFUSED` in Firefox's own error list, defined
as: "The connection attempt failed, for example, because no server was listening at
specified host:port." The causes above apply unchanged; only the string in the log
differs.

## The honest boundary

A refused connection is answered below any layer a browser's identity touches: the
operating system's TCP stack sent the reset, whether because nothing is listening or
because a firewall chose to reject rather than drop. `invisible_playwright` passes the
navigation straight to the patched engine and does not retry a refused port or paper over
a network namespace it did not create. A stock Playwright Chromium, a stock Firefox, and
this project's build all see the identical refusal from the identical port, because the
fingerprint layer this project touches sits far above where a TCP RST gets generated.

## Short answers to the questions that lead here

**What does net::ERR_CONNECTION_REFUSED mean?** Something at that address and port
actively rejected the connection attempt with a TCP reset, rather than staying silent.
Usually nothing is listening on that exact port, or a firewall chose to reject rather than
drop the attempt.

**Why does this happen constantly in Docker but never on my own machine?** Docker
containers each have their own network namespace, so `localhost` inside a container never
refers to the host machine or to a sibling container. This is the single most common real
cause of this error under automation.

**What is the fix for the Docker case?** Bind the target service to `0.0.0.0` instead of
`127.0.0.1`, use `host.docker.internal` to reach the host from inside a container, and
address other Compose services by their service name rather than `localhost`.

**Is this the same as ERR_CONNECTION_TIMED_OUT?** No. A refusal is an immediate answer
that says no. A timeout is silence, with nothing answering at all within the wait period.

**Can invisible_playwright's stealth patching cause or fix this?** No. The refusal is
generated at the TCP layer by whatever is, or is not, listening on the target port,
entirely below any fingerprint or identity surface this project touches.

## Sources

- Chromium's [`net/base/net_error_list.h`](https://chromium.googlesource.com/chromium/src/+/main/net/base/net_error_list.h),
  for `ERR_CONNECTION_REFUSED` (-102), retrieved 2026-08-30.
- Mozilla's [`xpcom/base/ErrorList.py`](https://searchfox.org/mozilla-central/source/xpcom/base/ErrorList.py)
  (viewed via the Fossies mirror), for `NS_ERROR_CONNECTION_REFUSED` (13) and its exact
  definition, retrieved 2026-08-30.
- [microsoft/playwright#35879](https://github.com/microsoft/playwright/issues/35879),
  `ERR_CONNECTION_REFUSED` in headless mode specifically against a dev server that had
  not finished starting.
- [microsoft/playwright#21414](https://github.com/microsoft/playwright/issues/21414) and
  [microsoft/playwright#20343](https://github.com/microsoft/playwright/issues/20343),
  reports of this error reproducing reliably in GitHub Actions and CircleCI against
  `localhost` and not locally.
- [microsoft/playwright#24582](https://github.com/microsoft/playwright/issues/24582), a
  report of the error persisting against a Docker Compose service despite the port being
  listed as exposed.
- pythonspeed.com, ["Connection refused? Docker networking and how it impacts your
  image"](https://pythonspeed.com/articles/docker-connection-refused/), for the network
  namespace explanation of why `localhost` inside a container is not the host, retrieved
  2026-08-30.
- Docker's own documentation, [Networking on Docker Desktop](https://docs.docker.com/desktop/features/networking/),
  for `host.docker.internal` and its platform-specific availability, retrieved 2026-08-30.

**See also:** [ERR_PROXY_CONNECTION_FAILED in Playwright](err-proxy-connection-failed-playwright.md)
for the equivalent failure when it is a configured proxy, not the destination, that is
unreachable, [ERR_CONNECTION_TIMED_OUT in Playwright](err-connection-timed-out-playwright.md)
for the silent variant with no reset at all, and [Playwright in Docker: what actually
changes detection-wise](playwright-docker-detection.md) for the rest of what differs
about running this project inside a container.

---

*From the notes of [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level. A refused connection is the operating system's TCP
stack answering; the browser's identity layer never gets a say in it.*
