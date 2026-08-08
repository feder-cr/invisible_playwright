---
title: "invisible_playwright vs fingerprint-suite: injection vs engine"
description: "fingerprint-suite injects a Bayesian-generated fingerprint into a Playwright or Puppeteer page. invisible_playwright sets it in the Firefox engine instead."
parent: "Comparisons"
nav_order: 12
---


# invisible_playwright vs fingerprint-suite: injection vs engine

**invisible_playwright and fingerprint-suite generate a browser fingerprint the
same way - as one coherent statistical identity rather than a grab-bag of
independently randomized fields - but apply it at different layers.**
fingerprint-suite injects the generated fingerprint into a running Playwright or
Puppeteer page; invisible_playwright makes the patched Firefox engine report those
values in its own code, so there is no property-overwrite for a detector to find.
The generation approach converges; the layer it lands on is where the two part.

Most of the tools on this site's comparison pages fix a short list of specific
properties. `fingerprint-suite`, maintained by Apify as part of the Crawlee
ecosystem, does something closer to what this project does: it generates a whole
coherent fingerprint using a statistical model, not a handful of hand-picked
overrides. Where it puts that fingerprint is the part worth comparing carefully.

## What fingerprint-suite actually does

fingerprint-suite is Apify's open-source toolkit that generates a coherent browser
fingerprint with a Bayesian network and injects it into a Playwright or Puppeteer
page. The toolkit is modular: a header generator, a fingerprint generator, an injector,
and underneath them a generative Bayesian network the project built specifically to
produce realistic, internally consistent fingerprints rather than independently
randomized field values. Point it at a Playwright or Puppeteer browser instance and
it wraps context creation, generating a fingerprint constrained by whatever you ask
for - device type, operating system - and injecting the result into the page before
your own code runs.

That statistical-generation piece is a real, substantive design choice, and it's
the same broad idea this project uses to turn one seed into one coherent identity
rather than a set of independently randomized fields that could describe a machine
that doesn't exist. Two different projects, in two different codebases, arriving at
"generate the fingerprint as a coherent statistical unit" as the right approach to
generation is worth taking as a signal about the problem, not a coincidence.

## Where the two part ways: injection versus the engine

The generation strategy converges. What happens with the result does not.

| Dimension | fingerprint-suite | invisible_playwright |
|---|---|---|
| Fingerprint generation | Coherent statistical model (a Bayesian network) | Coherent statistical identity derived from one seed |
| Where it is applied | Injected into the page via Playwright or Puppeteer | Reported by the patched Firefox engine in its own code |
| Engine coverage | Chromium and Firefox | Firefox (source-patched) |
| Property-overwrite detection surface | Present - values are written over the real ones | Absent - nothing is overwritten |

`fingerprint-injector` does exactly what its name says: it injects the generated
fingerprint into a running Chromium or Firefox instance via Playwright or
Puppeteer's own page APIs, the same mechanism [every init-script stealth tool on
this site uses](vs-playwright-stealth.md). The browser's real properties exist
first; the injector's script overwrites them before the page's own code runs.

That overwrite is detectable independent of how realistic the generated values are.
[Overwriting a native property leaves a trace in the property itself](tostring-native-code-detection.md) -
`Function.prototype.toString` on a patched getter, or a descriptor shape a native
property wouldn't have - regardless of whether the value it now returns is drawn
from a sophisticated Bayesian model or hand-picked. A more realistic fingerprint
value doesn't change whether the mechanism that installed it left a trace.

This project's approach starts from the same statistical premise and applies it
one layer down: the seed drives what the Firefox build itself reports, in its own
C++, before any page exists to inject anything into. There is no overwrite to find,
because nothing was overwritten - the properties were never anything else.

## What this means practically

If the goal is a coherent, internally-consistent fingerprint rather than
independently randomized fields, `fingerprint-suite`'s generation model is a
legitimate answer to that specific problem, and shares real conceptual ground with
how this project approaches it. It does not, by the nature of injection, close the
detection surface that checks for the injection mechanism itself rather than the
values it produces - and neither does any other page-level tool covered on this
site, regardless of how the values inside it were generated.

It's also worth being precise about scope: `fingerprint-injector` targets both
Chromium and Firefox through Playwright and Puppeteer, so it isn't tied to one
engine the way CDP-specific driver patches are. That breadth is real. It doesn't
change which layer the injection happens at.

## How to actually choose

- **Want a statistically coherent fingerprint, applied to a browser you're already
  driving with Playwright or Puppeteer, and the overwrite-detection surface isn't
  your concern?** `fingerprint-suite` does exactly that, on either Chromium or
  Firefox.
- **Need the fingerprint to be true at the engine level, with nothing for a
  property-overwrite check to find?** That requires the values to originate in the
  browser's own code rather than be injected afterward, which is what this project
  and other engine-level, source-patched approaches provide.
- **Undecided, or not sure which layer your actual blocker is at?** [Test which
  layer is actually failing](how-to-test-bot-detection.md) rather than assuming -
  a property-overwrite check and a values-look-wrong check produce different
  symptoms, and only one of them cares how the values were generated.

## Conclusion

`fingerprint-suite` and this project agree on the harder half of the problem -
that a fingerprint has to be generated as one coherent statistical identity, not a
grab-bag of independently randomized fields - and disagree on where that identity
gets installed. Injection after the fact is real, useful engineering, and it
inherits the one limitation every injection-based approach shares regardless of how
good the values underneath it are.

## Short answers to the questions that lead here

**Is fingerprint-suite the same kind of tool as playwright-stealth?** Mechanically,
yes - both inject values into the page via Playwright or Puppeteer. fingerprint-suite's
generation model is considerably more sophisticated (a Bayesian network producing a
coherent fingerprint rather than a short list of hand-patched properties), but the
injection layer is the same.

**Does fingerprint-suite work with Firefox?** Yes, `fingerprint-injector` supports
both Chromium and Firefox through Playwright, unlike CDP-specific tools that only
reach Chromium.

**Can an injected fingerprint be detected even if the values are realistic?** Yes.
The overwrite mechanism itself - a patched property's descriptor shape, or what
`Function.prototype.toString` returns for it - is a separate signal from whether
the value it reports looks plausible.

**Should I use fingerprint-suite and an engine-level tool together?** Not
meaningfully on the same session - if the engine already reports the target
fingerprint honestly, injecting a second one on top reintroduces the exact overwrite
signal an engine-level approach exists to avoid.

**See also:** [invisible_playwright vs playwright-stealth: page vs engine](vs-playwright-stealth.md),
the same page-versus-engine question with a simpler generation model on the other
side, [why every override carries its source](tostring-native-code-detection.md),
for the general mechanism behind the limitation described above, and
[invisible_playwright vs playwright-with-fingerprints](vs-playwright-with-fingerprints.md),
the same injection layer sourcing its values from a remote service instead.

## Sources

- `fingerprint-suite`'s own repository and README, read directly, for its module
  breakdown, its Bayesian-network generation approach, and its Playwright/Puppeteer
  injection API.

---

*From the notes of [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level, on a project that got the statistics right and
put them one layer higher than where this one does.*
