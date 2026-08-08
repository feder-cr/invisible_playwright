---
title: "Configuration"
description: "Proxy schemes, DNS handling, and timezone auto-derived from egress IP. Every environment variable the wrapper reads, what each does, and when you need it."
parent: "Documentation"
nav_order: 3
---


# Configuration

Configuration covers three independent surfaces: proxy schemes and credentials, how
the browser's timezone is derived from the egress IP, and the environment variables
that change caching, binary selection and download behavior. None of it is required
for a first run - `InvisiblePlaywright()` works with defaults - but each setting below
exists because a specific situation needs it.

## Proxies

```python
proxy = {
    "server": "socks5://gate.example.com:1080",
    "username": "user",
    "password": "pass",
}
with InvisiblePlaywright(proxy=proxy) as browser:
    ...
```

Schemes supported: `socks5`, `socks4`, `http`, `https`. DNS is routed through the
proxy by default, no local leak - the SOCKS5-authentication and DNS-resolution
details, including the parts most guides get wrong, are in
[Playwright SOCKS5 proxy with authentication](playwright-socks5-proxy-authentication.md).
The endpoint needs an explicit port. `socks5://gate.example.com` with no `:1080` is
refused with a `ValueError` rather than launched unproxied, which is what it used to
do silently. If you are driving `firefox.launch()` yourself instead of using this
class, pass the same dict to `get_default_stealth_prefs(proxy=...)` so the SOCKS
preferences are written.

Around 90% of proxies are public, so their IPs are already known and blocked before
you ever use them. A perfect browser on a known IP still loses. For the clean 10%,
residential IPs that aren't already known, we recommend
[sx.org](https://sx.org/?c=invisible_playwright), who filter for and serve only IPs
that aren't already on those lists.

## Timezone

The browser timezone follows `timezone=`:

```python
# default: timezone is auto-derived from the egress IP (proxy egress if a
# proxy is set, otherwise the host's own public IP)
with InvisiblePlaywright(proxy=proxy) as browser:
    ...

# explicit IANA zone always wins, the only way to force a specific zone
with InvisiblePlaywright(proxy=proxy, timezone="America/New_York") as browser:
    ...
```

Timezone is not one value - it is several surfaces that a detector cross-checks
against each other and against your exit IP, which is why
[setting `timezone_id` and still getting flagged for a mismatch](timezone-proxy-mismatch.md)
is common enough to have its own page.

## Environment variables

The engine downloads itself on first use, verifies a sha256 shipped inside
`invisible-core`, and caches it - the size is on the [installation](installation.md)
page and it happens once per engine version. None of these are a required step; each
row is for a specific situation.

| Variable | When you need it |
|---|---|
| `INVISIBLE_PLAYWRIGHT_CACHE_DIR` | Put the cached engine somewhere else - another drive, a shared location, a path your CI already caches |
| `INVPW_BINARY_PATH` | Point at a binary you already have, and skip the download |
| `STEALTHFOX_GITHUB_TOKEN` | A rate-limited or corporate network that refuses anonymous GitHub downloads |
| `INVISIBLE_PLAYWRIGHT_SKEW=allow` | Run a Playwright version outside the tested range anyway |
| `INVPW_CURSOR_ENGINE` | `python` (default), `binary`, or `off` |
| `INVISIBLE_DOWNLOAD_DEADLINE` | Seconds allowed for one engine download, default `1800`. Raise it on a very slow link; `0` removes the bound |

```bash
export INVISIBLE_PLAYWRIGHT_CACHE_DIR=/mnt/big/engines
```

### Why the download has a deadline at all

`requests` timeouts are per socket operation, not per transfer. A connection
that delivers one byte every 59 seconds satisfies a 60-second timeout forever,
so before `invisible-core` 18.13.0 the engine download had no upper limit: it
could sit there as long as whatever was above it allowed. A CI job did exactly
that for 39 minutes and was killed by its own limit; on a laptop the same thing
is a launch that never returns and writes nothing to a log.

The bound is checked between chunks and the refusal names the deadline, how long
it had been running and how many bytes arrived, so a genuinely slow link is
distinguishable from a stalled one. The engine archives are 217 to 238 MB
depending on platform, so the default finishes anything above roughly 140 KB/s.
If your connection is honestly slower than that, raise the number rather than
removing the bound - `0` is there for the case where you are downloading over
something too unusual to put a number on, and it restores the old behaviour of
waiting indefinitely.

## Short answers to the questions that lead here

**What proxy schemes does invisible_playwright support?** `socks5`, `socks4`, `http`
and `https`. DNS resolves through the proxy by default, and an endpoint given
without an explicit port raises a `ValueError` instead of launching unproxied.

**How is the browser's timezone chosen?** By default it is derived from the egress
IP - the proxy's exit if one is set, otherwise the host's own public IP. Pass an
explicit IANA zone such as `timezone="America/New_York"` to override it; an explicit
zone always wins.

**Do I need to set any environment variables to use invisible_playwright?** No. Every
one in the table above addresses a specific situation - moving the cache directory,
pointing at a binary you already have, a network that blocks anonymous downloads -
and the defaults work without touching any of them.

**Why does the engine download have a deadline, and can I remove it?** Because a
per-socket timeout does not bound a slow transfer: a connection delivering one byte
every 59 seconds satisfies a 60-second timeout forever. The default of 1800 seconds
covers any link above roughly 140 KB/s; set `INVISIBLE_DOWNLOAD_DEADLINE=0` to remove
the bound entirely if your connection is genuinely slower than that.

**What happens if I pass a proxy server with no port?** The constructor raises a
`ValueError` rather than launching the browser unproxied. That is a deliberate change
from older behavior, which used to launch silently without the proxy.

**See also:** [Installation](installation.md) for the one-time engine download these
environment variables configure, [Pinning fingerprint fields](pinning.md) for forcing
specific values like GPU or screen size, and
[Playwright SOCKS5 proxy with authentication](playwright-socks5-proxy-authentication.md)
for the full detail behind the proxy schemes above.

## Next

[Pinning fingerprint fields](pinning.md) covers forcing specific values - a GPU
model, a screen size, a hardware concurrency count - while leaving everything else
seed-derived, and what breaks if you pin one field without its correlated neighbours.
