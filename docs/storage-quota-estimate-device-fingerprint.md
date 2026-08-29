---
title: "Does storage quota estimate reveal disk size?"
description: "storage.estimate() returns a quota derived from total disk size, so it buckets as a device fingerprint. Keep it plausible, agreeing with deviceMemory and hardwareConcurrency."
parent: "Browser Identity"
grand_parent: "Guides"
nav_order: 34
---


# Does storage quota estimate reveal disk size?

Partly, and it is one of the surfaces almost no stealth tool audits. When a page calls
[`navigator.storage.estimate()`](https://developer.mozilla.org/en-US/docs/Web/API/StorageManager/estimate),
the browser answers with two numbers: how much origin-scoped storage is already used,
and a quota it is allowed to grow into. That quota is not a constant. It is computed
from the total size of the disk the profile lives on, not the free space on it, which
still varies by device and can be bucketed as a fingerprint, per the
[Storage Standard](https://storage.spec.whatwg.org/) that defines both values and
[Firefox's own quota rules](https://developer.mozilla.org/en-US/docs/Web/API/Storage_API/Storage_quotas_and_eviction_criteria),
which size it from total disk space specifically so free space cannot be read this way.

It does not reveal your exact disk size, and it says nothing about your IP reputation
or how fast you are sending requests. What it does do is add one more hardware-shaped
value that has to agree with every other hardware-shaped value you report. This page
is what the number means, why it is a fingerprint at all, how to read it, and the one
consistency rule that matters.

## What estimate() actually returns

`navigator.storage.estimate()` returns two numbers in one call: `usage`, the bytes this
origin already occupies, and `quota`, the ceiling it is allowed to grow into. Calling it
is one line:

```python
from invisible_playwright import InvisiblePlaywright

with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    page.goto("https://example.com")
    est = page.evaluate("() => navigator.storage.estimate()")
    print(est)   # {'quota': ..., 'usage': ..., 'usageDetails': {...}}
```

`usage` is how many bytes this origin currently occupies across IndexedDB, Cache
Storage, service worker registrations and the rest. `quota` is the ceiling the origin
may grow into before the browser starts evicting. On a real Firefox that ceiling is
10% of the total disk size the profile lives on, or 10 GiB, whichever is smaller. A
256 GB laptop and a 2 TB workstation both land on that same 10 GiB ceiling, but a
machine with a 32 GB disk lands under it and stands out. That low-end split is the
whole point for a fingerprinter: the quota does not identify you on its own, but it
drops you into a bucket, and a bucket combines with everything else.

## Why a storage quota is a fingerprint at all

Individually the number is coarse. It sits on a handful of common disk-size steps, and
most machines above roughly 100 GB of total disk converge on the same capped quota, so
it is a weak signal in isolation. Fingerprinting does not use it in isolation. It uses
it the way it uses every coarse value: as one more bit that has to be consistent with
its neighbours.

The neighbours here are the other hardware surfaces. `navigator.deviceMemory` reports a
bucketed RAM figure. `navigator.hardwareConcurrency` reports a logical-core count. A
storage quota implying a large fast disk on a machine that also claims 512 MB of RAM
and two cores is not one plausible value plus another plausible value. It is a
combination no ordinary device produces, and combinations are exactly what the better
detectors score. The same logic runs through
[hardware concurrency and device memory](hardware-concurrency-device-memory.md): each
field is defensible alone and only the cross-check catches the contradiction.

There is a second consistency trap on the same surface. `usage` reflects what the
origin has stored, and a session that has visited nothing yet but reports megabytes of
prior Cache Storage is describing a profile with a history it did not build. Storage
lives per origin and is affected by how the browser partitions it, which is its own
topic covered in [service worker storage partitioning](service-workers-storage-partitioning.md).

## Where the value comes from in invisible_playwright

The quota you read is produced by the patched Firefox build's own storage layer, the
same code path a stock Firefox uses. It is not a hardcoded constant swapped in over the
top of the real API, and that distinction is what makes it survive a cross-check. A
number injected by a page-level script has to be kept in agreement with `deviceMemory`,
`hardwareConcurrency`, the platform string and the disk the profile genuinely sits on,
by hand, forever. A number that comes out of the real storage layer is already
consistent with the real environment the build is running in, because it is measuring
that environment rather than asserting a value about it.

Every session gets a hardware persona from its seed, and the storage surface is part of
that persona rather than a bolted-on afterthought. Pass a seed and the whole persona,
storage quota included, comes back identical run after run:

```python
from invisible_playwright import InvisiblePlaywright

def read_hardware(seed):
    with InvisiblePlaywright(seed=seed) as browser:
        page = browser.new_page()
        page.goto("https://example.com")
        return page.evaluate("""async () => {
            const est = await navigator.storage.estimate();
            return {
                quota: est.quota,
                deviceMemory: navigator.deviceMemory,
                cores: navigator.hardwareConcurrency,
            };
        }""")

# same seed -> same persona -> same numbers every run
print(read_hardware(42))
print(read_hardware(42))
```

Because it is reproducible, a quota that ever looks wrong is debuggable: the same seed
gives the same machine, so you can replay it instead of guessing. Reproducibility is
the theme of the whole [quickstart](quickstart.md), and it is what turns an intermittent
detection into a fixed one.

## How to read and sanity-check it yourself

Read the three hardware values together, in the same session, and ask whether a real
device would report that combination. The check is a few lines:

```python
from invisible_playwright import InvisiblePlaywright

with InvisiblePlaywright(seed=7) as browser:
    page = browser.new_page()
    page.goto("https://example.com")
    report = page.evaluate("""async () => {
        const e = await navigator.storage.estimate();
        return {
            quotaGB: Math.round(e.quota / (1024 ** 3)),
            usageMB: Math.round(e.usage / (1024 ** 2)),
            deviceMemory: navigator.deviceMemory,
            cores: navigator.hardwareConcurrency,
            platform: navigator.platform,
        };
    }""")
    print(report)
```

What you are checking for:

- The quota sits in a bucket a normal disk would produce, not a suspiciously round or
  suspiciously tiny number.
- It does not contradict `deviceMemory` and `hardwareConcurrency`. A huge quota beside
  a minimal RAM and core count is the tell, not the quota by itself.
- `usage` matches what this origin has actually done. A fresh origin reporting a large
  prior usage describes a history the session did not build.
- The reading is stable for a fixed seed and moves as a set, not one field at a time,
  when the seed changes.

If you pin one hardware field by hand, pin its neighbours too, or the very consistency
this surface is checked for is what you break. [Pinning fingerprint fields](pinning.md)
covers forcing a specific value while keeping the correlated ones in step.

## What it does not tell anyone, and what you still have to supply

A plausible storage quota makes the browser read as a real machine on this one surface.
It changes nothing about the parts of a session that live outside the browser. Being
honest about that boundary is the difference between a tool that helps and a claim that
gets you blocked anyway.

`navigator.storage.estimate()` reveals nothing about:

- **Your IP reputation.** A perfect hardware persona on a datacenter address that a
  detector already knows loses on the address. That is a proxy problem, not a
  fingerprint one, and you supply the clean exit.
- **Per-account quotas and limits.** Storage quota is about disk space, not about how
  many actions an account is allowed. A site enforcing its own limits does not care
  what your disk reports.
- **Request rate.** Hammering one endpoint is a velocity signal that no browser property
  hides. Human pacing is yours to add.
- **Behaviour and timing.** The pointer path, the typing rhythm, the pause before a
  click. invisible_playwright arcs the mouse on a Bezier curve, but the overall shape of
  a session is still yours to make human.

This is the honest frame for the whole product. invisible_playwright is built to look
like a real Firefox driven by a real person, and that is why it passes most in-browser
detection: the fingerprint, the TLS handshake and the driver layer read as a genuine
Firefox rather than an automated one. On its own it does not fix the IP, the account
limits, the rate or the behaviour. Pair it with a clean proxy and human pacing and the
surfaces line up; skip those and a spotless storage quota will not save the session. If
you want to see this separation in a live report, work through
[how to test bot detection without a false pass](how-to-test-bot-detection.md).

## Conclusion

`navigator.storage.estimate()` does leak a coarse, bucketed view of total disk size, and
it is a fingerprint for the same reason every hardware surface is: not because the number
is unique, but because it has to agree with `deviceMemory`, `hardwareConcurrency` and the
platform you claim. invisible_playwright answers it from the real build's storage layer as
part of one seed-derived hardware persona, so the quota is consistent with its neighbours
by construction and reproducible for debugging. It is a real but narrow win. It says
nothing about your IP, your account limits, your request rate or your behaviour, and those
are the parts you still have to get right.

## Short answers to the questions that lead here

**Does navigator.storage.estimate() reveal my exact disk size?** No. It returns a quota
derived from total disk size, capped well below the real number on most modern drives, so
it names a bucket instead of an exact size.

**Can a storage quota be used as a fingerprint?** Yes, as a coarse bucket that combines
with other signals. On its own it is weak; cross-checked against RAM and core count it
becomes a consistency test.

**What should the quota agree with?** `navigator.deviceMemory` and
`navigator.hardwareConcurrency`, plus the platform string. A large quota beside minimal
RAM and cores is the contradiction detectors look for.

**Is the number faked in invisible_playwright?** It comes from the patched Firefox build's
own storage layer rather than a hardcoded constant, which is why it stays consistent with
the rest of the hardware persona instead of having to be kept in agreement by hand.

**Will a good storage quota stop me being blocked?** No. It helps with the in-browser
fingerprint and nothing else. IP reputation, per-account quotas, rate limits and behaviour
are separate and are yours to supply.

**Does the quota reveal my request rate or IP?** No. It is a disk-space signal only. Rate
and address are measured elsewhere and no browser property hides them.

## Sources

- [Storage Standard](https://storage.spec.whatwg.org/) and MDN's
  [StorageManager: estimate() method](https://developer.mozilla.org/en-US/docs/Web/API/StorageManager/estimate),
  which define `usage` and `quota`, retrieved 2026-08-28.
- MDN, [Storage quotas and eviction criteria](https://developer.mozilla.org/en-US/docs/Web/API/Storage_API/Storage_quotas_and_eviction_criteria),
  which documents that Firefox sizes the quota from total disk size rather than free
  space, retrieved 2026-08-28.
- This project's per-seed hardware persona and the cross-field consistency gates that
  check the storage quota against `deviceMemory` and `hardwareConcurrency`.

**See also:** [hardware concurrency and device memory](hardware-concurrency-device-memory.md)
for the neighbouring surfaces this quota must agree with,
[service worker storage partitioning](service-workers-storage-partitioning.md) for how
storage is scoped and why usage can surprise you, and
[configuration](configuration.md) for the proxy and timezone settings that cover the parts
a storage quota does not.

---

*Written while maintaining [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level driven by stock Playwright. The storage quota is a
surface almost nobody audits, which is exactly why it is worth keeping in a plausible
bucket.*
