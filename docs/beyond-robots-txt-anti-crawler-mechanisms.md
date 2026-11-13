---
title: "Beyond robots.txt: How Sites Actually Block AI Crawlers Now"
description: "robots.txt is a request a crawler can ignore, not a lock. What sites actually run underneath it in 2026: rate limiting, behavioral bot scoring, IP and ASN blocking, and decoy mazes, read from Cloudflare's own docs and real practitioner writeups."
parent: "Detectors, Explained"
grand_parent: "Guides"
nav_order: 37
---


# Beyond robots.txt: How Sites Actually Block AI Crawlers Now

`robots.txt` is a text file a crawler can read and ignore. Nothing about HTTP forces
a client to fetch it, parse it, or honor a single line inside it. It has worked as
well as it has for thirty years because the crawlers that mattered, mostly search
engines with a reputation to protect, chose to honor it. A crawler with no brand to
protect and a mandate to collect training data has a different set of incentives, and
2026 is the year that gap became a line item.

This page is about what a site actually runs underneath robots.txt once it decides a
polite request is not enough. None of it is exotic. All of it is documented by the
vendors running it or reported by people who watched it happen to their own traffic.

## Why the file alone was never going to hold

Cloudflare's own numbers make the gap concrete. As of a mid-2026 measurement, only
2.98% of the top one million web properties actively blocked AI crawlers, even though
38.73% of them were seeing AI-bot traffic hit their servers. Separately, Cloudflare
found that only 37% of the top 10,000 domains have a robots.txt file at all, and most
of those never got around to adding an AI-specific disallow line. A file nobody wrote
blocks nothing, and a file that only asks nicely blocks only the crawlers that were
already going to comply.

Cloudflare says the quiet part directly in its own writing on the subject: "user
agents are trivial for bot operators to change." A `Disallow` line keyed to a
crawler's declared name works only until that crawler stops declaring its name, and
nothing about the standard requires it to keep declaring it.

## Layer 1: rate limiting

The simplest response treats crawler traffic as a load problem rather than an
identity problem: cap how many requests one IP or session can make in a window.
Scraping-infrastructure writeups describe the logic plainly, a real visitor rarely
loads dozens of pages a second, a scripted crawler often will, so a request-rate cap
catches the volume even when the requester's identity is unknown. It is cheap to run
and it is also the layer a distributed crawler defeats almost by construction: spread
the same total request volume across enough source addresses and every individual
address stays under the cap while the aggregate load on the origin does not move.
Practitioner accounts of the current AI-crawler wave describe exactly that shape,
several large model crawlers hitting a site concurrently producing a load pattern
that looks, from the server's side, functionally identical to an unintentional
denial-of-service.

## Layer 2: behavioral scoring, underneath the user agent

Cloudflare's blocking toggle is not, underneath, a user-agent filter. It runs a
machine-learned bot score against every request, built from what Cloudflare calls its
own global traffic signals, and the score is what a WAF rule actually keys on: block
or challenge everything scoring under a threshold, independent of what the request
claims to be. Cloudflare's own writeup on a specific evasive crawler makes the point
directly: the traffic it examined scored "firmly below 30" on its bot scale even after
the operator had already started rotating its declared identity, because the score is
not reading the declared identity in the first place. This is the same family of idea
behind [how a fingerprint separates a real engine from a claimed one](what-is-a-browser-fingerprint.md),
applied to a crawler instead of a browser tab.

## Layer 3: IP and ASN blocking, and why it is a losing race

A site can also block by network origin instead of by header: known crawler IP
ranges, or entire hosting-provider ASNs, dropped at the firewall. It works cleanly
against a crawler that publishes its ranges and only operates from them. It works
badly against everything else, because most AI crawlers do not publish complete
ranges, and a scraper that wants to keep collecting has residential proxy pools and
rotating cloud addresses available to it at commodity prices. [What ASN and IP
reputation actually score, and how fast an address's reputation decays once it
changes hands, is covered on its own page](asn-and-ip-reputation-in-bot-detection.md);
the short version is that blocking by address is a race the crawler operator can
choose to run, and choosing not to fight that race is why sites reach for the layers
below instead.

## Layer 4: honeypots, tarpits, and the AI-specific version of both

A honeypot is a link or field a real visitor never sees and a script that blindly
processes every element in the DOM will trigger. [How that trick actually works, the
CSS that hides it and the reason `opacity: 0` is a different case from
`display: none`, has its own page](honeypot-fields-explained.md). A tarpit is the
same idea aimed at whole pages: serve a crawler an endless sequence of generated
content instead of a block page, so the crawler keeps consuming CPU and bandwidth
instead of your actual pages.

Cloudflare ships a purpose-built version of this aimed specifically at AI training
crawlers, called AI Labyrinth. Per Cloudflare's own documentation, the mechanism adds
invisible links carrying a `nofollow` directive throughout a protected site. A crawler
that honors `nofollow`, which is most of the ones with a reputation to protect, simply
never follows them and never notices the trap exists. A crawler that ignores it walks
into "a maze of never-ending links" generated to look like real content. Cloudflare's
own description of what happens next: "their details are recorded and used by all
Cloudflare customers who choose to block AI bots," turning one site's trap into a
shared blocklist entry rather than a one-off inconvenience for a single crawler
operator.

## Layer 5: the policy layer catching up to the traffic

The newest development is not a new detection mechanism at all, it is Cloudflare
restructuring what "block AI bots" even means. Starting September 15, 2026, per
Cloudflare's own announcement, the single AI-bot toggle splits into three named
categories: **Search** ("any behavior that collects or indexes your content, so it
can answer questions about it later"), **Agent** ("automated behavior that is acting,
usually in real time, on a person's behalf, to get something done right now"), and
**Training** ("a crawler taking your content to train or fine-tune a model"). New
domains onboarding after that date get Training and Agent blocked by default on
pages that carry ads, while Search stays allowed by default. Cloudflare's own
documentation is direct about the collateral effect: a crawler that serves more than
one purpose, and Cloudflare names Googlebot, Applebot and Bingbot specifically, gets
treated by whichever rule is most restrictive, so a site that blocks Training blocks
those crawlers too unless it opts back in. Alongside the free managed-robots.txt
feature Cloudflare also ships, which prepends its own `Disallow` directives for
crawlers like `Google-Extended` and `Applebot-Extended` ahead of whatever a site
already had, the policy layer is now doing work the plain-text file never could on
its own: it is enforced at the edge, not merely requested in a file a crawler is free
to skip.

## Where a real browser engine fits, and where it does not

This corpus exists because a page that runs JavaScript can tell a genuine browser
engine from a scripted stand-in, and layer 2 above, the behavioral bot score sitting
under every request, is exactly that kind of check. A crawler that is a real browser
engine rather than a bare HTTP client answers a JavaScript-backed consistency check
the way any other real instance of that engine does, for the same reason [a real
Firefox passes most of BotD's detector list for free](botd-explained.md): there is
nothing spoofed to catch.

That is the entire boundary, and it is worth being precise about it. Layers 1, 3 and
5 above have nothing to do with what a browser engine is. A rate limit counts
requests regardless of what sent them. An IP block reads the network address a
connection arrives on, which [no fingerprint changes](can-websites-detect-a-datacenter-proxy-ip.md).
A site's choice to block the Training category under the new policy layer is a
classification decision made before your request is even inspected. None of that is
a promise this project or any browser-identity tool makes, and a claim that engine
realness clears a rate limit or an IP block would be false on its face.

## Short answers to the questions that lead here

**Does robots.txt actually stop AI crawlers from scraping a site?** No, by design.
It is an advisory convention every well-known standard describes as voluntary. A
crawler that chooses not to read it, or reads it and ignores it, faces no protocol-level
consequence for doing so.

**What actually stops a non-compliant crawler, if not robots.txt?** A layered stack:
rate limiting by request volume, a machine-learned bot score independent of declared
identity, IP/ASN blocking, honeypots and tarpits that catch a scraper's own
crawling logic, and, as of late 2026, a policy layer that treats Search, Agent and
Training traffic as three separately blockable categories.

**Can IP blocking alone keep an AI crawler out?** Only against one that only operates
from published, stable ranges. Most large-scale scraping today runs on rotating
residential or cloud addresses precisely because a fixed-range block is easy to set
up and easy to route around.

**What is Cloudflare's AI Labyrinth?** A honeypot built for crawlers specifically:
invisible, `nofollow`-tagged links that a compliant crawler skips and a non-compliant
one falls into, generating an endless sequence of decoy pages while logging the
crawler for Cloudflare's shared blocklist.

**Will blocking the "Training" category also block Google Search?** It can. Cloudflare's
own documentation states that a multi-purpose crawler like Googlebot gets treated by
whichever category rule is strictest, so blocking Training blocks Googlebot's crawl
on the pages that rule covers unless the site opts back in.

**Does invisible_playwright help get past any of this?** Only the part that is
actually a browser-identity check. A real, engine-level Firefox answers a
JavaScript-backed consistency probe honestly, which is what behavioral bot scoring
partly measures. It changes nothing about a rate limit, an IP or ASN block, a
honeypot your own crawling logic walks into, or a site's policy decision to block a
traffic category outright.

## Sources

- Cloudflare, ["Declare your AIndependence: block AI bots, scrapers and crawlers with
  a single click"](https://blog.cloudflare.com/declaring-your-aindependence-block-ai-bots-scrapers-and-crawlers-with-a-single-click/),
  retrieved 2026-08-30, for the 2.98%/38.73% blocking-versus-traffic figures, the
  machine-learned bot-score mechanism, and the "user agents are trivial... to change"
  quote.
- Cloudflare, ["Control content use for AI training with Cloudflare's managed
  robots.txt and blocking for monetized content"](https://blog.cloudflare.com/control-content-use-for-ai-training/),
  retrieved 2026-08-30, for the 37%-of-top-10,000-domains robots.txt figure, the
  managed robots.txt mechanism, and the ad-page-only blocking feature.
- Cloudflare, ["Your site, your rules: new AI traffic options for all customers"](https://blog.cloudflare.com/content-independence-day-ai-options/),
  retrieved 2026-08-30, for the Search/Agent/Training three-tier system, its
  September 15, 2026 effective date, the default-blocking rules, and the
  multi-purpose-crawler quote naming Googlebot, Applebot and Bingbot.
- Cloudflare, [AI Labyrinth](https://developers.cloudflare.com/bots/additional-configurations/ai-labyrinth/),
  retrieved 2026-08-30, for the invisible `nofollow`-link mechanism and the shared
  detection quote.
- Stytch, ["How to block AI web crawlers without breaking your site"](https://stytch.com/blog/how-to-block-ai-web-crawlers/),
  retrieved 2026-08-30, for the named-crawler landscape, the rate-limiting and IP/UA
  blocking mechanics and their stated limitations, and the honeypot/tarpit
  description.

**See also:** [Honeypot fields and hidden links: how sites trap scrapers](honeypot-fields-explained.md),
[what ASN and IP reputation score in bot detection](asn-and-ip-reputation-in-bot-detection.md),
[can websites detect a datacenter or proxy IP?](can-websites-detect-a-datacenter-proxy-ip.md),
and [how Cloudflare Turnstile actually works](cloudflare-turnstile-explained.md) for the
challenge layer that sits beside, not instead of, everything on this page.

---

*From the notes of [invisible_playwright](https://github.com/oss-invisible/playwright),
a Firefox patched at the C++ level driven by stock Playwright. It answers the
browser-identity layer honestly; it does not touch a rate limit, an IP block, a
honeypot your own code walks into, or a site's decision to block a traffic category
outright.*
