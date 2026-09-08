---
title: "Anubis: The Proof-of-Work Firewall, Explained"
description: "Anubis makes a browser burn CPU on a SHA-256 puzzle before it can reach a git forge, and it is genuinely open source, unlike most of this corpus. Read from its own repository: the puzzle, the JWT cookie, and a real published critique of its limits."
parent: "Detectors, Explained"
grand_parent: "Guides"
nav_order: 34
---


# Anubis: The Proof-of-Work Firewall, Explained

Every other vendor in [the survey of how websites detect bots](how-do-websites-detect-bots.md) is a commercial product whose internals have to be pieced together from reverse engineering, because the company that built it has no reason to publish them. Anubis is different: it is MIT-licensed, its full source is on GitHub, and its author writes publicly about why it exists and what it does not solve. This page is built almost entirely from primary material, its own repository, its own documentation, and a named security researcher's own published analysis, not practitioner reconstruction. Say that plainly, because it is the exception in this corpus, not the rule.

Anubis was created by Xe Iaso, a pseudonymous developer, in January 2025, after Amazon's web crawler overloaded Iaso's own Git server while ignoring `robots.txt` entirely. It is now maintained by Techaro. The name is the joke and the mechanic at once: Anubis, the Egyptian god who weighs the hearts of the dead, and the software "weighs the soul" of an incoming HTTP request before deciding whether it deserves to reach the backend at all.

## What it actually does: a computational toll booth

Anubis sits in front of a web server as a reverse proxy. Before a request from anything that looks like a browser (identified, by default, by "Mozilla" in the User-Agent string) reaches the protected application, Anubis requires the client to solve a small cryptographic puzzle: find a nonce that, appended to a server-issued challenge string and run through SHA-256, produces a hash with a required number of leading zero hex digits.

The cost is tunable and grows fast. A default difficulty of five leading zeros takes on the order of a second on a real browser; six can take several minutes. Each added difficulty level multiplies the expected number of hash attempts by sixteen, since each hex digit is one of sixteen values, so the cost curve is deliberately steep rather than linear. A real browser solves this using Web Workers so the puzzle does not freeze the page, leaning on the native Web Crypto API where available and falling back to a pure-JavaScript SHA-256 implementation otherwise.

Once solved, Anubis issues an Ed25519-signed JWT, stored as a cookie, carrying the challenge string, the winning nonce, the passing hash, an issue time, an expiry, and a backdated "not before" claim for clock-skew tolerance. That token is valid for roughly a week, so a real visitor solves the puzzle about once per week of browsing rather than once per page load. For clients without JavaScript at all, a separate `metarefresh` challenge type, added around version 1.20, achieves a comparable delay using an HTTP meta-refresh redirect instead of a computed hash, specifically so that disabling JavaScript is not itself a way around the gate.

## The policy engine: not every request gets the same treatment

Later versions moved Anubis toward something closer to a lightweight, rule-driven firewall than a blanket "challenge everyone" gate. Its policy system evaluates each request against configurable rules, User-Agent regex, path matching, header inspection, CIDR ranges, GeoIP and ASN filtering, or custom CEL expressions for more complex logic, and can assign one of four outcomes: **ALLOW** it straight through, **DENY** it outright, **CHALLENGE** it with the proof-of-work puzzle, or **WEIGH** it, adding to a cumulative score that later rules and thresholds act on. The project's own configuration comments describe this in the same soul-weighing language the tool is named for: a client whose signals look clean has "a soul lighter than a feather" and passes without friction.

## Adoption: this is not a hypothetical

Anubis is deployed in front of real infrastructure at meaningful scale, not just a research demo. Documented adopters include GNOME's GitLab instance, the Linux kernel's mailing list and Git infrastructure, FFmpeg, Wine, UNESCO, FreeCAD, ScummVM, Duke University's digital archives, OpenWRT, and the Deutsche Nationalbibliothek. Its author's own stated tradeoff is explicit rather than hidden: deploying it "will result in your website being blocked from smaller scrapers and may inhibit 'good bots' like the Internet Archive," and the project's guidance is that most sites should try Cloudflare first and reach for Anubis only when that is not an option, which in practice means the gate a visitor meets is more often [Turnstile](cloudflare-turnstile-explained.md) than this one.

## A real, named critique worth reading in full

Security researcher Tavis Ormandy published an analysis arguing that Anubis's proof-of-work cost is not a meaningful deterrent against a well-resourced scraper. His own account: a native solver, a couple dozen lines of C rather than the JavaScript a browser is forced to run, cleared the challenge for roughly 11,000 separate Anubis-protected sites in about six minutes on a free-tier Google Cloud VM, and he estimated the aggregate compute cost of doing this continuously at well under a cent a month, trivial against the budget of any company running crawlers at scale.

That critique does not contradict what Anubis's own author and documentation say the tool is for. Read closely, the mechanism's actual value is not raising anyone's compute bill; it is that it "refuse[s] to render an expensive page for any client that will not run a modern browser's worth of JavaScript first," which is a real and different bar than "impose a compute tax a motivated attacker cannot pay." Anubis stops the high-volume, low-effort crawler that spoofs a browser User-Agent but runs no JavaScript at all, cheaply and by design. It does not stop a scraper that has already built a dedicated solver, and by August 2025 Codeberg, one of the forges running it, publicly acknowledged that AI scrapers had learned to solve Anubis's challenges. The tradeoff is asymmetric in a second, less flattering way too: the computational cost lands on every real visitor's device, including low-end and battery-constrained ones, while a determined attacker's marginal cost per site approaches zero once a solver exists.

## What this means for a real, engine-level browser

This is the one detector in this whole corpus where the honest answer is: engine realness is close to irrelevant to whether the gate opens. Anubis is not testing whether a browser is lying about its fingerprint, checking canvas output, or looking for automation-framework tells. It is testing one thing: can this client execute a modern JavaScript engine fast enough to solve a SHA-256 search in a reasonable time. Any real browser, patched or unpatched, stealth or not, answers that the same way, because the puzzle does not touch fingerprint surfaces at all. A plain HTTP client with no JavaScript engine fails it regardless of how convincingly its headers are spoofed, which is the entire point: it is a liveness test for "do you run JavaScript like a browser," not a fidelity test for "are you the specific browser you claim to be."

`invisible_playwright` does not do anything specific to Anubis, and there is nothing Anubis-specific to do: a genuinely real Firefox, C++-patched or stock, solves its puzzle the same way any other real Firefox does, because the puzzle is engine-agnostic by construction. What this page is really useful for is the opposite lesson: not every gate in this space is a fingerprint problem, and treating Anubis as one would be a category error. The gates that do read [what a browser fingerprint is made of](what-is-a-browser-fingerprint.md) are a separate family, and knowing which of the two you are behind decides whether any of that work applies at all.

## Short answers to the questions that lead here

**Is Anubis actually open source?** Yes, genuinely, MIT-licensed, full source on GitHub, unlike almost everything else covered in this corpus. That is why this page can cite its own repository and documentation as a primary source rather than reconstructing it from the outside.

**What does the proof-of-work puzzle actually require?** Finding a nonce such that SHA-256 of the challenge string plus that nonce produces a hash with a configured number of leading zero hex digits. Default difficulty (five zeros) takes about a second on a real browser; each added digit multiplies the expected work by sixteen.

**How long does the cookie last once I solve it?** About a week, via an Ed25519-signed JWT, so a real visitor is not re-solving the puzzle on every page load.

**Does Anubis check my browser fingerprint?** No, not in the sense this corpus otherwise means. It checks whether you can execute JavaScript at browser speed, not whether your canvas hash, WebGL renderer, or navigator properties look genuine.

**Is proof-of-work actually a strong defense?** Security researcher Tavis Ormandy's published analysis argues no, against a motivated attacker: he solved challenges for roughly 11,000 Anubis-protected sites in about six minutes using a native solver on a free cloud VM. It is effective against the high-volume, no-JavaScript crawler class it was built for, and by the Anubis maintainers' own framing that JavaScript-execution requirement, not the compute cost itself, is the actual mechanism.

**Does deploying Anubis affect legitimate crawlers?** Yes, by its own maintainer's admission. It can block smaller scrapers and "good bots" like the Internet Archive, which is a stated, acknowledged tradeoff, not a hidden side effect.

**Does a real, engine-level Firefox pass Anubis differently than a stock one?** No, and that is the point of this page. Anubis is not testing fingerprint fidelity at all, so there is nothing for engine-level realness to improve here. Any real JavaScript engine running fast enough passes the same way.

**See also:** [How Kasada's bot detection actually works](kasada-explained.md) for a commercial vendor using proof-of-work as part of a much larger, closed-source stack; [How Cloudflare's AI Labyrinth works](cloudflare-ai-labyrinth-explained.md) for a completely different anti-crawler philosophy, wasting a bot's time with content instead of computation; and [how do websites detect bots?](how-do-websites-detect-bots.md) for where a liveness test like this sits among fingerprint-based layers.

## Sources

- [`TecharoHQ/anubis`](https://github.com/TecharoHQ/anubis) on GitHub, retrieved 2026-08-30, the project's own
  source and README, for the MIT license, the challenge-type directory structure, and the stated tradeoff
  about blocking smaller scrapers.
- Anubis documentation, [Introduction](https://techarohq-anubis.mintlify.app/introduction), retrieved 2026-08-30,
  for the policy-engine actions (ALLOW/CHALLENGE/DENY/WEIGH), the challenge types, and the metarefresh
  no-JavaScript fallback.
- crawlex.net, ["Browser-based proof-of-work: how Anubis gates crawlers with hash puzzles"](https://blog.crawlex.net/blog/anubis-proof-of-work-crawler-gating/),
  retrieved 2026-08-30, for the exact difficulty/cost-curve figures, the JWT cookie contents and one-week
  validity, and the "refuse to render an expensive page" framing of the mechanism's real function.
- Wikipedia, [Anubis (software)](https://en.wikipedia.org/wiki/Anubis_(software)), retrieved 2026-08-30, for
  the creation history (Xe Iaso, January 2025, the Amazon-crawler origin story), the adopter list, and the
  Tavis Ormandy critique and Codeberg's August 2025 acknowledgment, cross-checked against the crawlex.net
  account of the same critique.

---

*From the notes of [invisible_playwright](https://github.com/feder-cr/invisible_playwright), a Firefox
patched at the C++ level. Anubis is the one detector here this project can actually read the source of, and
the honest conclusion is that engine-level realness has almost nothing to do with passing it: the puzzle
tests JavaScript execution speed, not fingerprint fidelity, and any real browser clears it the same way.*
