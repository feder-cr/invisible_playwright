---
title: "How to Read a Stealth-Browser Benchmark Without Being Misled"
description: "Almost every published comparison in this space ranks its own tool first, including implicitly this project's. What to check before trusting a benchmark: undisclosed targets, dates and IP types, non-comparable pass criteria, and shared implementations wearing different names."
parent: "Comparisons"
nav_order: 35
---


# How to Read a Stealth-Browser Benchmark Without Being Misled

Say the conflict of interest first, because every comparison in this space has one and
most don't say so: this project publishes comparison pages too, and they are written by
the people who maintain the tool being compared. That does not make them wrong, but it
does mean the same skepticism this page recommends for someone else's benchmark applies
to ours, including a hypothetical one that made this project look best. Nobody grading
their own homework gets a pass on that just because they said it out loud first.

This page is not about which tool wins. It's about what a comparison has to disclose
before "wins" means anything at all, and what tends to be missing from the ones that
circulate without it.

## Four things an undisclosed benchmark hides

**The test targets.** "We tested against major anti-bot vendors" names nothing
checkable. Which sites, how many, and were they picked because they're representative or
because they're the ones the tool already handles well? A benchmark that names its
targets can be reproduced by someone who disagrees with it. One that doesn't can only be
believed.

**The date.** Every detector this corpus documents changes its own scoring over time.
[Akamai told two versions of the same patched Firefox family apart](akamai-bot-manager-explained.md)
on one site while treating them identically on others, and the difference tracked the
build, not some property that held still. A pass rate from six months ago is a claim
about that detector's configuration six months ago, not about today, and a benchmark
that doesn't date itself is asking you to assume nothing changed.

**The IP type.** Datacenter versus residential versus mobile changes outcomes on its own,
independent of anything the browser does. [ASN and IP reputation are properties of the
network path, not the browser](asn-and-ip-reputation-in-bot-detection.md), and a
[datacenter address is detectable on its own terms](can-websites-detect-a-datacenter-proxy-ip.md),
so two runs on different IP classes are not testing the same thing even with identical
binaries. A benchmark that doesn't name the IP type used, or worse, runs different arms
through different IP types without saying so, has let the network do part of the
deciding and credited the browser for it.

**Whether "pass" means the same thing across tools.** This corpus's own [testing
methodology page](how-to-test-bot-detection.md) makes this point about testing your own
browser: sannysoft, CreepJS, BotD, FingerprintJS and BrowserLeaks each answer a different
question, and "we pass every suite" is five different claims wearing one sentence. A
benchmark comparing two automation tools has the identical problem one level up. A "pass"
against a sitekey test page, a "pass" meaning no block occurred on a live production
page, and a "pass" meaning a JavaScript test suite returned a clean verdict are three
different bars, and stacking them into one leaderboard column erases the difference that
matters.

## The tools might not even be the different things they're compared as

A comparison implicitly promises that its rows are independent measurements. Sometimes
they aren't. Several entries in
[this corpus's own comparisons hub](comparisons.md) exist specifically to flag when two
differently-named tools share more plumbing than their names suggest: multiple
Chrome-only, driverless-CDP projects sit in close lineage to each other, and a stealth
plugin that patches the page in JavaScript can be functionally the same handful of
overrides as another plugin under a different package name, because both are shimming
the same well-known tells.

That matters for reading a benchmark because a table that lists five tools as five
independent data points, when three of them share a code ancestor or the same underlying
CDP quirks, is reporting less independent evidence than it looks like it's reporting.
Three rows agreeing is weaker evidence than it appears if the three rows are close
cousins, not three separate engineering efforts arriving at the same answer.

## What a trustworthy report actually looks like, with a real example

A GitHub issue is not usually where you'd point someone for methodology, but
[`daijro/camoufox#555`](https://github.com/daijro/camoufox/issues/555), already covered
in more depth in [how Akamai Bot Manager actually works](akamai-bot-manager-explained.md),
is a genuinely good example of what disclosure looks like in practice. The reporter named
exact software versions (Camoufox v135.0.1-beta.24, Playwright 1.58.0), the platform
(Linux), confirmed both arms shared the same IP, named the specific fingerprint fields
compared (JA3, JA4, Akamai's own HTTP/2 signature, all matched), and reported one
concrete, checkable inconsistency (a viewport reported as 1280x720 to the automation
layer against 1920 read back from `window.innerWidth` inside the page) rather than a bare
"it got detected." It also reported the negative result honestly: several other
Akamai-protected sites treated both browsers identically, which the reporter included
even though it undercut the more dramatic claim.

Compare that to the shape most circulating benchmarks actually take: a screenshot of a
green checkmark on a fingerprinting test page, no build number, no date, no IP type
named, and a target described only as "top e-commerce sites." One of these can be
independently checked by someone with no stake in the outcome. The other can only be
taken on faith, and faith is exactly what a benchmark exists to replace.

## What would make a benchmark trustworthy

None of this is unique to browser stealth. Software and academic benchmarking both ran
into the same problem earlier and arrived at similar answers. The ACM's own artifact
review framework, built for exactly the "can I trust this published result" question,
distinguishes **reproducibility** ("an independent group can obtain the same result using
the author's own artifacts") from **replicability** ("an independent group can obtain the
same result using artifacts which they develop completely independently"), and awards
separate badges for whether artifacts are merely available versus whether results were
actually reproduced by someone other than the author. A benchmark in this space doesn't
need a formal badge, but the checklist behind it transfers directly:

- **Disclosed software versions**, both the tool under test and the framework driving it,
  named precisely enough that someone else could install the same ones.
- **A dated result**, because every detector on the other side of the comparison keeps
  changing.
- **A named IP type**, at minimum residential versus datacenter versus mobile, since
  that alone can flip an outcome independent of the browser.
- **A comparable pass criterion across every arm**, the same test suite, the same site,
  the same definition of success, not a different bar for each tool being compared.
- **A reproducible setup**, ideally a script or a repository someone else can run,
  which is the practical form the ACM's "artifacts available" badge takes outside
  academia.
- **The negative results included**, not just the wins. The Camoufox issue above is more
  credible, not less, for reporting that most tested sites showed no difference at all.

## Reading this corpus's own comparisons with the same checklist

[This project's comparison pages](comparisons.md) try to apply some of this: [the
Camoufox comparison](vs-camoufox.md) has a section titled "Where I could not verify a
difference" that states plainly which claims about the other project's internals could
not be checked against their own source, rather than filling the gap with an assumption
in this project's favor. That is a real attempt at the discipline above, and it is still
written by the maintainer of one of the two tools being compared. Read it, and every other
comparison here, the way this whole page argues you should read anyone else's: check
whether the targets, dates, IP types and pass criteria are actually disclosed, and treat
the absence of a disclosed negative result as a gap, not as evidence there wasn't one.
That standard doesn't relax for a benchmark that happens to favor this project. It's the
same standard, applied without an exception clause for the author.

## Conclusion

A benchmark is a claim, not evidence, until it discloses what it tested, when, over what
kind of IP, and what counting as a "pass" actually meant, in terms specific enough that
someone with no stake in the outcome could rerun it and get a different answer if the
original claim was wrong. Most comparisons in this space, including some published under
this project's own name, fall short of that on at least one axis. The fix isn't to
distrust every number; it's to ask the four questions above before treating any of them
as settled, this page's own examples included.

## Short answers to the questions that lead here

**Why do all the stealth-tool comparisons disagree with each other?** Usually because
they tested different sites, on different dates, through different IP types, using
different definitions of "pass." Those four variables alone can flip a leaderboard
without either browser changing at all.

**Is a benchmark useless if it doesn't disclose everything?** Not useless, but it should
be read as a claim rather than a settled result. Weight it in proportion to what it did
disclose, and be specifically wary of a comparison that reports only wins.

**How do I know if two "different" tools are actually different?** Check whether they
share an underlying implementation, a common CDP quirk, a common upstream library, or the
same well-known JavaScript overrides under a different package name. Agreement between
close cousins is weaker evidence than agreement between independent efforts.

**Should I trust this project's own comparison pages?** Apply the same checklist to them
that this page recommends for anyone else's: are versions, dates, IP types and pass
criteria disclosed, and are negative results included alongside the positive ones. Being
the tool's own maintainer is a real conflict of interest regardless of how the page is
written.

**What's the single best sign a benchmark is trustworthy?** A reproducible setup:
something a stranger with no stake in the outcome could actually run themselves and get a
checkable answer from, rather than a screenshot or a summary paragraph.

**Does a bigger sample of sites make a benchmark more trustworthy?** Only if the sites
are named and the sample wasn't selected to favor one side. An undisclosed large sample
is not more trustworthy than a disclosed small one; it's just a bigger unverifiable claim.

**See also:** [How to test bot detection without a false pass](how-to-test-bot-detection.md),
for the companion question of testing your own browser rather than reading someone else's
published comparison; [the comparisons hub](comparisons.md), for this project's own
attempts, imperfect as any author's, to apply this standard to itself; and
[invisible_playwright vs Camoufox: two patched Firefoxes](vs-camoufox.md), for the
specific comparison that names what it could and could not verify.

## Sources

- ACM, [Artifact Review and Badging](https://www.acm.org/publications/policies/artifact-review-and-badging-current),
  retrieved 2026-08-30, for the reproducibility/replicability distinction and the
  disclosed-artifact badging framework the checklist above is adapted from.
- GitHub, [`daijro/camoufox` issue #555](https://github.com/daijro/camoufox/issues/555),
  retrieved 2026-08-30, for the disclosed-methodology example: named versions, matched
  IP and network fingerprints, a specific reported inconsistency, and included negative
  results across other tested sites.
- This project's own [how to test bot detection without a false pass](how-to-test-bot-detection.md)
  and [invisible_playwright vs Camoufox](vs-camoufox.md) pages, for the internal
  precedent this page holds itself to, including where each already discloses a limit
  on what it could verify.

---

*From the notes of [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level. This page names its own conflict of interest in the
first paragraph because that's the discipline it's asking of everyone else's benchmark,
including any that would have made this project look good.*
