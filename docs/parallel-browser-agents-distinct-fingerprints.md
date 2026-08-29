---
title: "Run parallel browser agents with distinct fingerprints"
description: "Run parallel agents with distinct, reproducible fingerprints. Honest limit: a shared exit IP links them as one network identity regardless of browser diversity."
parent: "AI Agents and Frameworks"
grand_parent: "Guides"
nav_order: 9
---


# Run parallel browser agents with distinct fingerprints

You want to run several browser agents at once, each looking like a different person
on a different machine. The direct answer: give every agent its own process and its
own seed, and each process derives a full, internally-consistent set of fingerprint
values that differs from every other process's, with almost no code beyond one launch
call per worker. That covers the browser layer. It is also only half of the problem,
and the half people skip is the half that gets them linked.

This page shows the working pattern, states exactly what it separates, and is equally
clear about what it does not: distinct fingerprints behind one exit address are still
one network identity, and no amount of per-agent variety changes that.

## One process per agent, one seed per process

The unit of a distinct identity here is a process with its own seed. Launch N agents
as N processes, give each a different seed, and you get N browsers that are each
internally consistent and mutually distinct: different canvas hash, different WebGL
renderer, different screen, different audio, roughly 400 correlated fields that agree
with each other inside one identity and differ across identities.

```python
from invisible_playwright import InvisiblePlaywright

def run_agent(seed):
    with InvisiblePlaywright(seed=seed) as browser:
        page = browser.new_page()
        page.goto("https://example.com")
        # ... the agent's work ...
        return page.title()
```

The two-line launch is the whole integration. The `browser` returned is a real
Playwright [`Browser`](https://playwright.dev/python/docs/api/class-browser), so
every method your agent already calls works unchanged.

To actually run these side by side, put each one in its own process. Separate
processes keep the identities from sharing any in-memory state, and they let the work
run on multiple cores:

```python
from concurrent.futures import ProcessPoolExecutor

SEEDS = [11, 22, 33, 44]

if __name__ == "__main__":
    with ProcessPoolExecutor(max_workers=len(SEEDS)) as pool:
        results = list(pool.map(run_agent, SEEDS))
    print(results)
```

Four workers, four seeds, four distinct browser identities running at the same time.
If your agents are I/O bound rather than CPU bound, the
[asyncio concurrency pattern](run-invisible-playwright-concurrently-asyncio.md) gives
each worker its own seed the same way, with a Semaphore bounding how many run at once.

## Why each identity holds together

Distinct is easy. Distinct *and* consistent is the part that matters, because a
detector does not score a single value, it cross-checks values against each other. A
browser that reports one platform in its user agent, a GPU string from a different
platform, and a font list from a third is not a new identity, it is a broken one, and
[CreepJS records that kind of contradiction by name](creepjs-explained.md).

Deriving every field from one seed is what keeps a given agent coherent. The canvas
hash, the WebGL vendor and renderer, the screen geometry and the audio context are
drawn together from the seed, so they belong to the same imaginary machine rather than
being rolled independently. That is also why two different seeds produce two genuinely
different machines instead of two random piles of values, which is the same reason
[two real devices rarely share a fingerprint](can-two-devices-share-a-browser-fingerprint.md).

## Each identity is reproducible, which is what makes it debuggable

A seed is not just a source of variety, it is a handle. Because the identity is a pure
function of the seed, an agent that failed on seed `33` can be relaunched on seed `33`
and you get the exact same browser: same canvas hash, same renderer, same screen, run
after run.

```python
# reproduce one agent's exact identity to debug it in isolation
with InvisiblePlaywright(seed=33) as browser:
    page = browser.new_page()
    page.goto("https://example.com")
    # same machine every time, so a failing run is a bisect, not a guess
```

If you let each session draw a random identity instead, a failing worker tells you
nothing, because you cannot separate the site changing from the machine changing. Pin
the identity per worker and a failure stays put while you work on it. You can log the
drawn seed with `sf.seed` when you did not pass one, and pin specific fields while
leaving the rest seed-derived through [field pinning](pinning.md).

## The honest limit: one exit IP is one network identity

Here is the part that the fingerprint layer cannot fix for you.

Distinct browser fingerprints separate the agents at the browser layer. They do not
separate them at the network layer. If all N processes leave through the same exit
address, a receiving site sees N different-looking browsers arriving from one IP at
the same time, and correlating them is trivial: the address is the join key.
Fingerprints varying while the source address does not is itself a pattern, and a
suspicious one. A convincing browser on a
[datacenter or shared exit IP](can-websites-detect-a-datacenter-proxy-ip.md) is still
on that IP.

So the browser layer is necessary and not sufficient. To make N agents read as N
independent visitors you also have to vary what the fingerprint cannot touch:

- **The exit address.** Give each identity its own clean egress rather than fanning
  them all out through one. A per-worker proxy is a dict passed at launch:

  ```python
  proxy = {"server": "socks5://gate.example.com:1080",
           "username": "u", "password": "p"}
  with InvisiblePlaywright(seed=seed, proxy=proxy) as browser:
      ...
  ```

  With `timezone` left at its default the browser zone is derived from that proxy's
  egress IP, so the exit and the browser tell the same story instead of
  [contradicting each other](timezone-proxy-mismatch.md).
- **The pace.** Per-account quotas and rate limits do not care how varied your
  fingerprints are. N agents hammering one endpoint in lockstep produce a velocity
  signal that a single agent never would, so [throttle and jitter each
  worker](how-to-rate-limit-your-scraper-playwright.md).
- **The behaviour.** Timing and interaction still have to look human per agent, which
  is [its own topic for AI-driven sessions](ai-browser-agents-stealth.md).

The product supplies the first column: a real Firefox, driven by stock Playwright,
whose fingerprint, TLS and driver layer read as a genuine browser, which is why it
clears most fingerprint and automation checks on its own. You supply the clean exit
and the human pacing. Neither half substitutes for the other.

## Putting the two halves together

A worker that varies both layers at once is the shape you actually want to deploy: a
distinct seed for the identity, a distinct proxy for the network, and pacing that is
not shared across the pool.

```python
from concurrent.futures import ProcessPoolExecutor
from invisible_playwright import InvisiblePlaywright

# each tuple is one independent visitor: its own identity AND its own exit
AGENTS = [
    (11, {"server": "socks5://a.example.com:1080", "username": "u", "password": "p"}),
    (22, {"server": "socks5://b.example.com:1080", "username": "u", "password": "p"}),
]

def run_agent(job):
    seed, proxy = job
    with InvisiblePlaywright(seed=seed, proxy=proxy) as browser:
        page = browser.new_page()
        page.goto("https://example.com")
        return page.title()

if __name__ == "__main__":
    with ProcessPoolExecutor(max_workers=len(AGENTS)) as pool:
        print(list(pool.map(run_agent, AGENTS)))
```

Two seeds, two exits, two visitors that share no join key. Add more by adding rows,
not by adding code.

## Conclusion

N processes with N seeds give you N internally-consistent, mutually-distinct and
individually-reproducible browser identities, and that part is a two-line change per
worker. The trap is treating distinct fingerprints as distinct visitors. Behind one
shared exit address they remain a single network identity, and per-account quotas and
rate limits never saw the fingerprints in the first place. Vary the seed for the
browser, vary the proxy for the network, vary the pace for the behaviour, and only
then are your N agents actually N.

## Short answers to the questions that lead here

**Do parallel agents get different fingerprints automatically?** Different seeds give
different fingerprints. Pass a distinct seed per process, or let each draw its own and
log `sf.seed` so you can reproduce it.

**Are the fingerprints internally consistent, not just random?** Yes. Every field is
derived from the one seed, so the values agree with each other inside an identity
rather than being rolled independently.

**Can I reproduce one agent's identity later?** Yes. The identity is a pure function
of the seed, so relaunching with the same seed gives the same canvas, renderer and
screen every run.

**Does this make N agents look like N different people?** At the browser layer, yes.
At the network layer, only if each has its own exit. N distinct browsers behind one IP
are still one network identity.

**Will distinct fingerprints get me around per-account rate limits?** No. Quotas and
rate limits are counted per account and per address, not per fingerprint. Throttle and
pace each worker separately.

**One process or one context per agent?** One process per agent is the clean boundary:
separate seeds, no shared in-memory state, and it uses multiple cores.

## Sources

- This project's [quickstart](quickstart.md) and [configuration](configuration.md)
  pages for the launch API, the seed behaviour and the proxy dict shape used in every
  example above.
- This project's [fingerprint generation](reproducible-agent-browser-identity-seed.md),
  which derives roughly 400 correlated fields from a single seed, so identities are
  distinct across seeds and consistent within one.
- The release gates for the network-layer caveat: a self-inflicted velocity flag that
  turned out to be one address making too many requests, not the fingerprints.
- Playwright's own [`Browser` API reference](https://playwright.dev/python/docs/api/class-browser),
  for `new_page` and confirmation that the object each worker launches above is the
  standard Playwright API, not a modified one.

**See also:** [running invisible_playwright concurrently with asyncio](run-invisible-playwright-concurrently-asyncio.md) for the I/O-bound version of this pattern, [whether two devices can share a fingerprint](can-two-devices-share-a-browser-fingerprint.md) for why distinct seeds read as distinct machines, [whether a datacenter exit IP is detectable](can-websites-detect-a-datacenter-proxy-ip.md) for the network half you still have to solve, and [giving one agent a reproducible identity](reproducible-agent-browser-identity-seed.md) for the seed-to-fingerprint pipeline behind the consistency claim above.

---

*Written while maintaining [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level driven by stock Playwright. The fingerprints being
distinct was never the hard part; remembering that one exit IP undoes all of it was.*
