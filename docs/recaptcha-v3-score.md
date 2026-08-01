---
title: "reCAPTCHA v3 score: why a fresh browser scores badly"
description: "reCAPTCHA v3 returns a number instead of a puzzle, and a clean fresh browser tends to score low anyway. The reason is not the fingerprint, it is that the score is mostly about history, and a new profile has none."
parent: "Detectors, Explained"
grand_parent: "Guides"
nav_order: 5
---


# reCAPTCHA v3 score: why a fresh browser scores badly

reCAPTCHA v3 does not give you a puzzle. It gives the site a number between 0.0 and
1.0, and a clean automated browser tends to land at the bottom of that range even when
every fingerprint check it faces comes back clean.

The reason is not the fingerprint. It is that the score is largely about **history**,
and a fresh profile has none.

## What a brand new profile is saying

Launch a browser with an empty profile and it presents itself as something that has
never been anywhere. No cookies from Google, no analytics cookies from any site, no
consent decisions recorded, no storage.

There is a kind of person who browses like that: someone using a private window for the
first action of their day. There are far more automated sessions that look that way,
because a fresh container profile is the default state of automation, and the score
reflects that base rate rather than anything specific you did wrong.

So the honest framing is not "how do I trick the score". It is that a session with no
past is genuinely unusual, and the score is measuring exactly that.

## What a browser that has been used actually carries

Three groups, and they are not interchangeable:

**Google's own cookies.** A browser that has loaded anything Google, which in practice
is every browser, carries a set of them on `.google.com`: a visitor identifier, a
consent record, a security-settings record, and one specific to reCAPTCHA itself.

**Analytics and advertising cookies on the sites visited.** Analytics identifiers, a
pixel cookie, a session cookie. These are set by third parties and they accumulate as a
side effect of ordinary browsing.

**Consent-manager cookies.** In Europe especially, a browser that has visited a handful
of sites has a stored consent decision on several of them, because a banner was
answered.

The shape matters more than the count. A profile with one enormous cookie on one domain
is not the same as a profile with a few cookies across a dozen domains, and only the
second looks like a week of normal use.

## The trap: an anachronism is worse than an absence

This is the part that is genuinely easy to get wrong, and we did.

Google **deprecated one of its long-standing cookies in 2022**. If you seed a profile
from an article written before that, or from a capture taken years ago, your browser
carries a cookie that current browsers no longer receive.

A missing cookie says "this browser has not been here". A cookie that stopped existing
three years ago says something far more specific: this profile was assembled from an
old template. It is the same failure as claiming a GPU that no longer ships, or a user
agent for a browser version that was never released.

The general rule, and it applies well beyond cookies:

> Fabricated history has to be plausible **for today**. A stale artefact is a stronger
> signal than a missing one, because absence has innocent explanations and an
> anachronism does not.

Which means seeded history needs maintaining. It is not a file you write once.

## The consistency problem, again

The cookies have to agree with everything else about the session.

Google's consent cookie carries a **region and language token**. If it says one country
while your timezone, your locale and your exit address say another, you have created
exactly the kind of contradiction that
[the timezone page](timezone-proxy-mismatch.md) is about, and you have created it in a
place most people never think to check.

Same for the sites the cookies come from. A browsing history full of one country's
sites, on an address in another, is a story that does not hold together.

And the same rule as everywhere else: **derive it from the identity, not from a random
number generator**. A profile whose cookies change on every launch is a browser that
forgets everything nightly and yet still has a history, which is not a thing.

## How this project does it

The seeded persona includes a sampled browsing history, and the cookies are built from
it: a few realistic cookies per visited site, chosen by what kind of site it is, plus
the Google set that any real browser carries. Every value is derived from the persona
seed, so the same identity presents the same cookies in every session, and two
identities do not share them.

The consent cookie's language token is derived from the session timezone, so a European
persona does not present an American consent record.

And the deprecated cookie is deliberately excluded, which is in the code with a comment
saying why, because that is the sort of thing that gets helpfully added back by someone
reading an old blog post.

Honest limits, since this page is about a score and scores invite overclaiming:

- **Cookies are one input.** Behaviour on the page and the reputation of your address
  both matter, and neither is fixed by a cookie jar.
- **A seeded history is not a real history.** It is a plausible starting point, not a
  substitute for a profile that has genuinely been used.
- **Test on real pages.** A score measured against a demo key set up for testing tells
  you very little, because the risk model there is not the one running in production.

## Checking your own

```js
document.cookie;                       // first-party only
// and in devtools: Application, Cookies, look across domains
```

What to look at:

- Cookies exist on more than one domain, and on Google's.
- The consent record's region agrees with your timezone and locale.
- Nothing is present that current browsers no longer receive.
- Relaunch the same identity and confirm the set is the same.
- Launch a different identity and confirm the set is different.

## Short answers to the questions that lead here

**Why is my reCAPTCHA v3 score low even though I pass every bot test?** Because the
score is weighted toward history and reputation, and a fresh profile has neither. Bot
tests measure the browser, the score measures the session.

**Does a good fingerprint raise the score?** It stops the fingerprint from lowering it.
That is not the same thing.

**Should I keep a persistent profile?** For this, yes. A profile that accumulates real
usage beats any seeded approximation, and it is the one approach that improves on its
own over time.

**Do cookies alone fix it?** No. They remove one reason to distrust the session.

**Is it the proxy?** It contributes, and it is checked alongside everything else. If
your address is shared by many sessions at once, that will show.

**Can I test my score safely?** Test against real production pages rather than demo
keys. A demo key is configured for demonstrations and its risk model is not the one you
care about.

**See also:** [the checklist for being detected on one site](playwright-detected-as-bot.md),
where behaviour is step five and the address is step seven, and
[when the timezone does not match the proxy](timezone-proxy-mismatch.md), which is the
consistency argument in its clearest form.

---

*From the notes of [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level. The excluded deprecated cookie is a real line in our
source, with a comment explaining why, because we did include it once.*
