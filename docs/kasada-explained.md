---
title: "How Kasada's bot detection actually works"
description: "Kasada does not publish its mechanism, but reverse-engineering writeups do: an obfuscated bytecode VM, a proof-of-work token that keeps renewing through the session, and integrity checks aimed at the JS engine itself."
parent: "Detectors, Explained"
grand_parent: "Guides"
nav_order: 25
---


# How Kasada's bot detection actually works

Kasada is a commercial bot-detection vendor used across retail, travel and ticketing
sites, and unlike BotD or CreepJS its client-side script is not open source. There is no
source tree to point at. What follows is pieced together from public
reverse-engineering writeups, one archived proof-of-concept solver, and Kasada's own
description of its product, which is deliberately thin on mechanism. Where the sourcing
is solid, that is said plainly. Where it is not, that is said too.

## The script itself: a bytecode VM, not ordinary obfuscated JavaScript

Kasada ships a JavaScript file to the browser (reverse-engineers have referred to it as
`p.js` or `ips.js`, and the exact filename varies by integration) built around a custom
bytecode virtual machine, not plain obfuscated JS. A 2026 reverse-engineering
writeup from [kernel.sh](https://www.kernel.sh/blog/detection) describes decoding a file
of roughly 449KB, most of it a 437KB encoded string table, wrapped in time-based seeds,
integrity checksums, custom alphabet encoding and per-site variants. The actual challenge
logic runs as bytecode inside that VM, not as readable JS the surrounding file merely
obfuscates, so decoding the JS wrapper does not by itself tell you what the challenge
checks. It tells you how to reach the interpreter that runs the real logic.

That VM design is also why third-party reverse-engineering of Kasada has a short shelf
life in practice. Kasada rotates the bytecode and regenerates the surrounding bundle on
its own release cycle, so a solver built against one snapshot of the VM tends to stop
working once the vendor ships the next one. That is a fact about the ecosystem of
unofficial solvers and their maintenance burden, not a property of any single piece of
analysis.

## What the VM actually checks

The strings kernel.sh recovered show it probing a wide mix of surfaces: ordinary
properties like `navigator.webdriver` and `screen.width`, deeper browser APIs like
`RTCPeerConnection` and `getBattery`, and canvas fingerprinting primitives. The decoded
output also contains internal rule names (the writeup gives examples such as `bot606`
and `bot1375_mfsk`), which is evidence of a large, named catalog of detection rules,
not a handful of ad hoc checks. Worth being precise about what that evidence
actually shows: the rule names surfaced in decoding, the logic behind each named rule did
not, so this page can say the ruleset is broad and cannot say what any one named rule
specifically tests.

It also runs integrity checks that read as familiar to anyone who has looked at how
CreepJS works: cross-frame consistency between the top document and other frames,
whether the `navigator.webdriver` getter is the engine's real native getter or something
that has been moved onto the instance, and general window-property leak detection for
globals automation frameworks tend to leave behind ([the same lie-detection shape CreepJS
uses](creepjs-explained.md), independently arrived at). One technique kernel.sh describes
that is less commonly written up elsewhere: "crash-and-capture" probes that deliberately
trigger a `TypeError` and then inspect the resulting `Error` object, its message text and
its stack-trace shape, against what the specific engine and version it claims to be would
actually produce. It is the same underlying idea as inspecting `Function.prototype.toString`
output, aimed at an exception instead of a function's own source text.

## The proof-of-work challenge, and why it is not really the defense

A May 2026 [crawlex.net analysis](https://blog.crawlex.net/blog/proof-of-work-anti-bot/)
of proof-of-work anti-bot systems is worth reading in full for anyone assuming Kasada's
puzzle works the way Anubis's roughly one-second delay does, or the way mCaptcha's
escalating puzzles do. By that account Kasada's own puzzle is calibrated to be cheap for
a real browser, on the order of a couple of milliseconds to solve and verifiable
server-side in microseconds, which rules out "make scraping computationally expensive" as
the actual goal.

The framing the article lands on: the puzzle is forced attestation, not a compute tax.
Solving the underlying hash on its own, without also running the VM's anti-instrumentation
checks that produced it, gets an attacker nothing, because the token binds cryptographically
to the fingerprint and integrity signals collected during that same VM execution. You
cannot compute the proof-of-work output and skip the checks around it; by this account
they are the same computation.

## The token does not expire quietly, it has to keep renewing

Two token types recur across the sourcing this page checked, though the exact header
names are not fully consistent between writeups from different points in time. The
clearest account, from crawlex.net, describes an "expensive" session token alongside a
"cheap", single-use per-request token that gets reissued through the life of the session,
plus a version marker pinning which challenge revision issued them. A 2023 [ZenRows
writeup](https://www.zenrows.com/blog/kasada-bypass) names an overlapping but not
identical header set. That disagreement is itself a small piece of evidence that Kasada's
exact header names have shifted across integrations and time, which fits a vendor that
rotates its client script on its own schedule instead of freezing an external contract.

The point that survives the naming disagreement is the one worth taking away: this is not
a check that runs once at page load and then stops watching. The computational challenge
gets reissued through the session, so whatever is driving the browser has to keep
answering it, not just answer it once on arrival. Continuous, not one-time.

## What sits outside the JavaScript challenge entirely

The VM, however thorough, only ever sees what the browser it runs inside can see. The
ZenRows account of Kasada also lists layers that never touch JavaScript at all: IP
reputation, flagging datacenter ranges the way [ASN and IP reputation checks generally
do](asn-and-ip-reputation-in-bot-detection.md); TLS handshake fingerprinting, comparing
cipher suites, TLS version and extension order against what the claimed browser should
present; and ordinary HTTP header analysis, order and version included. Kasada's own site
adds a server-side layer on top of that: its [Bot Defense
page](https://www.kasada.io/bot-defense) states it ingests "more than a trillion data
points every week" and identifies anomalous session behavior in under two milliseconds
using models trained across that traffic. None of that runs in a browser, and none of it
is inspectable from a page.

## What a genuinely real Firefox answers, and what it does not

One line from Kasada's own bot-defense page is worth taking at face value rather than
reading as marketing filler: it describes the obfuscated VM as designed to "force
attackers to run their code in real browsers." Read literally, that names exactly the
class of check an engine-level build answers by construction rather than by patching.
`navigator.webdriver`'s getter is the engine's own native getter, because nothing
overrode it, which is the same underlying argument as [the toString native-code
check](tostring-native-code-detection.md). A cross-frame comparison finds the same
built-ins in every frame, because nothing patched only the top document. A deliberately
triggered `TypeError` produces the exact message and stack shape the real engine
produces, because it is the real engine throwing it, not a shim standing in for one. None
of that is anything specific to Kasada; it is what actually being the browser you claim
to be gets you against a checker built around the question kernel.sh's writeup keeps
returning to: does the JS engine, the DOM and the protocol layer all tell the same story.

What that does not touch is everything in the section above it. A real Firefox on a
flagged datacenter address still arrives with a bad ASN attached to the first packet. A
real Firefox's TLS handshake still has to actually be Firefox's handshake end to end, not
a user agent claiming Firefox over some other stack's fingerprint. Kasada's server-side
scoring, the trillion-points-a-week model, has no interface a browser build can speak to
at all; it scores the account and the session pattern, not the engine. And the
proof-of-work token itself still has to be produced by whatever page-load actually
happens, which is a routine consequence of a real browser running the real page's own
script rather than a claim about any tool solving Kasada's challenge on someone's behalf.

This project does not claim invisible_playwright passes Kasada, and could not know that
in general even if it wanted to. Whether a given session gets through depends on the exit
IP's reputation, account-level history and behavioral pacing at least as much as on the
browser answering honestly, and a browser build controls none of those three.

## Short answers to the questions that lead here

**Is Kasada's detection script open source?** No. Everything in this article is
reconstructed from public reverse-engineering writeups and Kasada's own limited
marketing language, not from a source tree anyone can read.

**Does Kasada only check once, when the page loads?** No. The token structure above, a
longer-lived session token alongside a short-lived per-request one, gets reissued through
the session, so the challenge keeps renewing instead of running once and stopping.

**Is the proof-of-work puzzle itself what stops bots?** Not on its own. It is calibrated
to be cheap for a real browser to solve; by the sourcing above its role is binding the
token to the integrity checks that ran alongside it, not taxing compute.

**What does invisible_playwright do about Kasada specifically?** Nothing Kasada-specific.
It ships a real Firefox engine that is not patched at the JavaScript layer, which answers
the class of check that asks whether the engine is telling the truth about what it is. It
does not touch IP reputation, account history or Kasada's server-side scoring.

**Does a clean browser fingerprint mean a Kasada-protected session will pass?** No
guarantee. The network and behavioral layers described above run independently of what
the browser reports about itself.

**Why do third-party Kasada solvers keep breaking?** Because the VM bytecode and the
surrounding JS bundle both rotate on Kasada's own release schedule. An external
reimplementation is built against one snapshot and has to be redone against the next.

**Where can I read the actual reverse-engineering instead of a summary?** The kernel.sh
writeup linked in Sources below is the most detailed public account this page found, and
it is worth reading directly rather than through a paraphrase.

**See also:** [How CreepJS decides you are lying](creepjs-explained.md), for the same
integrity-checking approach worked out against a tool you can actually read the source
of; [Function.prototype.toString and the native code check](tostring-native-code-detection.md),
for the specific native-getter argument Kasada's VM also relies on; and [what ASN and IP
reputation are in bot detection](asn-and-ip-reputation-in-bot-detection.md), for the
network layer that sits entirely outside Kasada's client-side script.

## Sources

- kernel.sh, ["Bot detection is dead, long live bot detection: reverse-engineering Kasada
  in an afternoon"](https://www.kernel.sh/blog/detection), retrieved 2026-08-30, for the
  VM/bytecode structure, the decoded fingerprint and rule names, and the crash-and-capture
  probe technique.
- crawlex.net, ["The proof-of-work renaissance: how Kasada, hCaptcha, and Anubis use
  compute as a tax"](https://blog.crawlex.net/blog/proof-of-work-anti-bot/), dated
  2026-05-03, retrieved 2026-08-30, for the proof-of-work calibration, the session and
  per-request token split, and the forced-attestation framing.
- Kasada's own site, ["Bot Defense"](https://www.kasada.io/bot-defense), retrieved
  2026-08-30, for the vendor's own description of its mechanism, including the "force
  attackers to run their code in real browsers" language quoted above.
- ZenRows, ["How to Bypass Kasada in 2026"](https://www.zenrows.com/blog/kasada-bypass),
  retrieved 2026-08-30, for the network-layer signals, IP reputation, TLS fingerprinting
  and HTTP header analysis, that sit outside the JavaScript challenge.
- GitHub, [`0x6a69616e/kpsdk-solver`](https://github.com/0x6a69616e/kpsdk-solver)
  (archived), retrieved 2026-08-30, for the `x-kpsdk-*` header family and its own note
  that Firefox performed more reliably against this defense than Chromium in the author's
  testing.

---

*From the notes of [invisible_playwright](https://github.com/feder-cr/invisible_playwright).
Kasada does not publish a source tree the way BotD or CreepJS do, so this page leans on
public reverse-engineering rather than our own release gates. Where the other detector
pages here say "read from source," this one says "read from someone else's afternoon of
decoding it," and names them.*
