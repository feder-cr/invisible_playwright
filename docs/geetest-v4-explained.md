---
title: "GeeTest v4 (Slide/Click Captcha), Explained"
description: "GeeTest v4 is a puzzle captcha: drag a piece into place, tap icons in order, or clear a match-three board, encrypted client-side and verified against GeeTest's own server. Read from GeeTest's own docs and independent reverse engineering."
parent: "Detectors, Explained"
grand_parent: "Guides"
nav_order: 39
---


# GeeTest v4 (Slide/Click Captcha), Explained

GeeTest is a commercial captcha vendor, widely deployed on sites outside the United
States, especially across e-commerce, gaming and finance in Asia. Version 4 of its
Behavior Verification product is the one most people mean when they say "GeeTest":
a slide puzzle, an icon-selection challenge, a match-three board, or a one-tap pass,
picked per session by GeeTest's own risk engine rather than fixed per site.

Be clear about what this page is before reading further. GeeTest v4 is, functionally,
a captcha: a visible, interactive puzzle a visitor is asked to solve. This page
explains the mechanism, read from GeeTest's own documentation and from independent,
public reverse engineering. It is not instructions for solving the puzzle
automatically, and `invisible_playwright` does not include a puzzle-solving or
captcha-solving capability of any kind. That distinction matters more here than on
most pages in this corpus, so it is stated once, plainly, before anything else.

## The challenge types, and how one gets picked

Per GeeTest's own overview documentation, v4's "Intelligent Verification Mode"
selects a challenge type automatically per session, from a set that includes Slide
Puzzle, Icon Selection, Match-Three (a Gobang-style board), and OneTap Pass, plus a
voice-based path for accessibility. GeeTest's own docs describe the selection logic
in outline rather than in full: "the system automatically selects the most suitable
challenge based on risk signals," so a session GeeTest's engine scores as higher-risk
is more likely to land a harder challenge type, and a lower-risk session may get
OneTap Pass, which asks for nothing more than a single tap. GeeTest does not publish
the scoring model behind that choice, and no page in this corpus claims to know it.

## What the client side actually does

For the Slide Puzzle path specifically, independent, open-source reverse engineering
of the widget (a public GitHub project documenting the slide challenge) describes the
mechanism concretely: the server returns a background image and a separate puzzle
piece, along with the vertical (`y`) position where the piece belongs, and the
visitor's job is to drag the piece left or right until it seats into the matching
gap in the background. The same project's notes on the challenge's data collection
are worth quoting directly because they cut against an assumption a lot of writing
about captchas makes: GeeTest, per this independent analysis, "collects near to no
browser information. Just a few IDs and the solution to the image challenge." The
heavy client-side work in this project's own documentation is not a sprawling
fingerprint payload, it is AES and RSA encryption wrapped around the request
specifically, in the reverse-engineer's own words, "to make reverse engineering
harder," a cost imposed on automated analysis rather than a fingerprint collected
from a real device.

GeeTest's own docs separately describe an "Environment Detection" layer running
alongside the puzzle itself, stated to identify "automated tools, malicious plugins,
and incognito mode usage," and an image pool GeeTest refreshes, per its own
documentation, on an hourly basis specifically to keep a cached or memorized set of
puzzle images from staying valid for long.

## The server side: what actually proves the puzzle was solved

GeeTest's own server-integration documentation lays out the verification exchange
plainly, and it is worth walking through because it is the part that actually decides
pass or fail, not the puzzle interaction itself. After a visitor completes a
challenge, the client returns four values to the site's own backend: `lot_number`,
a serial number for that specific verification attempt; `captcha_output`, an
opaque, encrypted blob representing the outcome; `pass_token`, a token standing in
for the completed session; and `gen_time`, a timestamp. The site's backend then
computes an HMAC-SHA256 signature over the `lot_number` using its own private
`captcha_key`, and POSTs all four values plus that signature to GeeTest's own
validation endpoint, `gcaptcha4.geetest.com/validate`. GeeTest's response is JSON
carrying a `status` field (whether the validation request itself succeeded) and a
`result` field (`"success"` or `"fail"` for the visitor's actual attempt), with a
`reason` field on failure describing things like an expired token.

The puzzle a person sees is only the front half of this. The actual security
boundary is server-to-server, exactly the same shape as [the token exchange
Cloudflare Turnstile runs through its Siteverify endpoint](cloudflare-turnstile-explained.md#the-token-and-why-passing-the-widget-is-not-the-finish-line):
a client-side interaction produces a token, and the token is only worth anything
once a server the visitor cannot reach has checked it against the issuer directly.

## What invisible_playwright does and does not do here

This is the section to read most carefully on this page. `invisible_playwright` is
a patched Firefox that answers the JavaScript-visible parts of a browser fingerprint
honestly, the same category of thing covered throughout this corpus: canvas and
WebGL output, font enumeration, timing consistency, whether the engine claiming to
be a real browser actually behaves like one. GeeTest's own "Environment Detection"
layer sits in that category, and a genuinely real engine answers it the way any other
real instance of that engine does, for the same reason argued
[on the Turnstile page](cloudflare-turnstile-explained.md#where-an-engine-level-browser-actually-helps-and-where-it-does-not)
and [the DataDome page](datadome-explained.md#what-an-engine-answers-honestly-and-what-sits-outside-its-reach).

That is where the honest boundary sits, and it sits well short of the puzzle itself.
Dragging a slider into the correct gap, tapping icons in the order a prompt asks for,
or clearing a match-three board is a human-interaction problem, not a fingerprint
problem, and no amount of engine-level realness answers it for you. `invisible_playwright`
does not solve, automate, or sell a solution to GeeTest's puzzle step, and any tool
that claims to "bypass GeeTest" as a captcha-solving promise is describing a
different, separate product category, commercial puzzle-solving services exist for
exactly this reason, not something this project does or intends to do.

## Short answers to the questions that lead here

**What is GeeTest v4?** A commercial captcha product offering several interactive
challenge types, slide puzzle, icon selection, match-three and a one-tap pass,
selected per session by GeeTest's own risk engine.

**Is GeeTest the same kind of thing as Cloudflare Turnstile?** No. Turnstile mostly
runs invisible, non-interactive checks and shows a checkbox only when its own signals
are uncertain. GeeTest's most common deployment mode, the slide puzzle, is visible
and interactive by design in the common case, not a fallback for an uncertain
signal.

**What does GeeTest actually collect from the browser?** According to independent
reverse engineering of the slide-puzzle client, very little beyond a few session
identifiers and the puzzle solution itself, with the request wrapped in AES/RSA
encryption to raise the cost of automated analysis rather than to hide a large
fingerprint payload.

**How does GeeTest verify a completed challenge?** Server-to-server. The client
returns a `lot_number`, `captcha_output`, `pass_token` and `gen_time`; the site's own
backend signs the `lot_number` with its private key and posts everything to GeeTest's
`validate` endpoint, which returns success or failure.

**Does invisible_playwright solve GeeTest's puzzle for me?** No. That is a
human-interaction challenge, not a browser-fingerprint check, and this project does
not include a puzzle-solving or captcha-solving capability of any kind.

**Does a real, engine-level browser help against GeeTest at all?** Only against the
environment-detection layer running alongside the puzzle, the part checking whether
the browser claiming to be real actually behaves like one. It has no effect on the
puzzle interaction itself.

## Sources

- GeeTest, [Behavior Verification overview](https://docs.geetest.com/BehaviorVerification/overview/overview/),
  retrieved 2026-08-30, for the challenge-type list, the Intelligent Verification
  Mode description, the Environment Detection claim, and the hourly image-refresh
  claim.
- GeeTest, [server-side deployment and validation documentation](https://docs.geetest.com/BehaviorVerification/deploy/server),
  retrieved 2026-08-30, for the `lot_number`/`captcha_output`/`pass_token`/`gen_time`
  parameters, the HMAC-SHA256 signing step, the `validate` endpoint, and the
  success/failure response shape.
- [`gravilk/geetest-v4-slide-documented`](https://github.com/gravilk/geetest-v4-slide-documented)
  on GitHub, retrieved 2026-08-30, independent, public reverse engineering of the
  slide-puzzle client, for the `y`-position mechanism, the AES/RSA encryption
  purpose, and the "collects near to no browser information" finding.

**See also:** [How Cloudflare Turnstile actually works](cloudflare-turnstile-explained.md)
for the non-interactive counterpart to a visible puzzle, [how DataDome's bot
detection actually works](datadome-explained.md) for another vendor's split between
an engine-honest layer and a human-interaction layer, and [browser trust scores
explained](browser-trust-score-explained.md) for what a risk-based challenge
selection like GeeTest's is actually scoring.

---

*From the notes of [invisible_playwright](https://github.com/oss-invisible/playwright),
a Firefox patched at the C++ level driven by stock Playwright. This page explains a
puzzle captcha's mechanism, not a way around it: the product does not solve captchas,
and a claim that any tool defeats GeeTest's puzzle step is a different promise than
anything documented here.*
