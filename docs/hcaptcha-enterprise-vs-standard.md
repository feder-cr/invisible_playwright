---
title: "hCaptcha Enterprise vs Standard"
description: "hCaptcha Enterprise is not a harder version of the free widget, it is the same invisible-pass-plus-puzzle mechanism with custom difficulty tuning, dedicated risk models, an SLA, and a cash relationship instead of the free tier's labeled-data economy. What changes, from hCaptcha's own docs."
parent: "Detectors, Explained"
grand_parent: "Guides"
nav_order: 43
---


# hCaptcha Enterprise vs Standard

This page assumes you already know how hCaptcha's two-stage mechanism works, the
invisible risk pass and the visual puzzle it escalates to when that pass is unsure.
[That is covered in full on its own page](hcaptcha-explained.md), and this one does not
repeat it. What follows is specifically what changes between the free Basic tier, the
mid-tier Pro plan, and Enterprise: what each adds, what it costs, and which parts stay,
underneath, the same mechanism.

The short version: Enterprise is not a stricter widget. It is the same
checkbox-or-invisible, escalate-to-a-puzzle model, given a visible risk score, custom
challenge content, difficulty controls a free integration never sees, and a support
contract, sold as a metered or negotiated product, not embedded for free in
exchange for the puzzle answers themselves.

## Three tiers, not two

hCaptcha's own pricing and plans pages describe three tiers, not a simple free/paid
split. **Basic** is free, with an allowance hCaptcha's own plans page states as "10,000
requests/month," and hCaptcha's own FAQ describes Basic ("Publisher") accounts as
choosing among four fixed difficulty modes: "Easy, Medium, Difficult, and Auto." **Pro**
is a paid, self-serve tier priced, per hCaptcha's own pricing page, at "$99/month"
billed annually or "$139/month" billed monthly, including 100,000 evaluations a month
with overage at "$0.99/1K" beyond that, with a two-week free trial. **Enterprise** is
custom-priced and sold through a sales contact, not a checkout page.

## What Pro adds over Basic

Pro's own documentation describes its centerpiece as "99.9% Passive" mode, the default
for new Pro sitekeys, an evaluation "derived from thousands of factors" aimed at showing
a visible challenge to "less than 0.1% of users" while still, per hCaptcha's own
framing, "increasing the costs of an attack in virtually all cases." hCaptcha's own
plans page lists Pro as also adding bot risk scores and threat signatures, custom
widget themes, more detailed analytics than Basic, and room for a small team, up to five
members, to share sitekeys and dashboards.

None of this exists on Basic. A Basic integration is limited to the four fixed
difficulty levels named above and does not get the passive-mode risk evaluation, the
score, or the analytics.

## What Enterprise adds on top of Pro

This is the part that is a genuine capability gap instead of a bigger number on the
same feature, and hCaptcha's own plans page names it directly: **private learning**,
described as training detection models on an Enterprise customer's own traffic patterns
in place of a shared, general model, plus **custom challenges** and **challenge content
control**, meaning an Enterprise account can bring its own images or questions instead
of using hCaptcha's default challenge pool. hCaptcha's plans page also lists **human
threat detection**, **difficulty tuning**, called out specifically as including the
ability to "raise and lower challenge difficulty based on time of day," and, on the
program-management side, multi-user dashboards with SAML SSO, advanced analytics and
reporting APIs, and Enterprise-level SLAs.

hCaptcha's own FAQ separately describes Enterprise as unlocking two difficulty modes
Pro and Basic do not have at all, "Passive" alongside "99.9% Passive," plus
"sophisticated custom threat models" and "detailed bot scores" beyond what Pro's flat
risk score exposes.

Put together, the shape of the gap is: Pro turns on a shared risk model and a passive
mode; Enterprise turns on a model trained specifically on that customer's own traffic,
gives that customer direct control over what the puzzle even looks like, and adds the
operational scaffolding (SLA, SSO, reporting) an organization actually deploying this at
scale asks for. None of it is a stricter version of the puzzle itself, it is control
over when and how the puzzle appears, and how well the risk model fits one customer's
own traffic instead of the general population hCaptcha sees.

| | Basic (free) | Pro | Enterprise |
|---|---|---|---|
| Price | $0 | $99-139/mo, 100K evals incl. | Custom |
| Difficulty control | 4 fixed modes (Easy/Medium/Difficult/Auto) | Adds 99.9% Passive | Adds Passive, plus time-of-day tuning |
| Risk score visible to you | No | Yes (bot risk scores, threat signatures) | Yes, plus custom threat models |
| Model trained on | Shared, general | Shared, general | Your own traffic ("private learning") |
| Challenge content | hCaptcha's default pool | hCaptcha's default pool | Customer-supplied images/questions |
| Team/SSO/SLA | No | Up to 5 users | Multi-user dashboards, SAML SSO, SLA |

## The relationship changes too, not just the feature list

This is the differentiator that is easy to miss if you only read the feature tables,
and it is stated plainly in a blog post from HUMAN Protocol, the labor marketplace
hCaptcha's compensation model runs through: "hCaptcha also compensates integrators for
the work their users do as they verify their humanity," framing that work as
"annotation" feeding machine-learning training. The same post draws the Enterprise line
explicitly: "Enterprise integrators of the hCaptcha security service like Cloudflare pay
IM [Intuition Machines] for more features, rather than being compensated."

Read plainly, a free-tier integration sits inside a labeled-data economy: visitors solve
puzzles, the site owner is compensated for that work, and hCaptcha's own labeling
product resells the resulting annotations to machine-learning customers, described on
[the hCaptcha explainer page](hcaptcha-explained.md#the-part-that-pays-for-the-free-tier-labeled-data)
in full. An Enterprise integration is a direct commercial customer instead, paying
Intuition Machines in money for the extra tier of product, rather than the arrangement
running the other direction. That is a genuinely different relationship, not a
messaging choice, and it is the closest hCaptcha equivalent to
[the free-vs-metered split covered on the reCAPTCHA Enterprise page](recaptcha-enterprise-vs-v3.md#pricing-the-free-tier-is-smaller-than-v3s-used-to-feel),
even though the mechanisms behind the two vendors' free tiers are not the same thing.

## What this means for anything actually being scored

Nothing about Enterprise's extra machinery changes what a browser engine can honestly
answer versus what it cannot, because every tier sits on the same underlying widget and
`siteverify` mechanism described on [the hCaptcha explainer page](hcaptcha-explained.md):
an invisible pass first, a visual puzzle only if that pass is unsure, and a server-side
token check either way. [The reasoning that applies to a Playwright session against the
free tier](does-playwright-trigger-hcaptcha.md) applies without modification against
Enterprise's version of the same pass: a real, engine-level browser answers the
JavaScript-visible fingerprint surface honestly, which narrows one input among several
undisclosed ones, and does nothing for the address you connect from or the history a
session does or does not carry. Private learning specifically cannot be addressed by
engine realness at all, because a model trained on one Enterprise customer's own traffic
patterns is a property of that customer's deployment and its accumulated data, not of
any single browser session run against it.

`invisible_playwright` does not include a private-learning workaround, a custom-challenge
bypass, or any feature that changes a hCaptcha risk score or a puzzle escalation
decision, on Basic, Pro, or Enterprise. What honest engine behavior affects here is
exactly what it affects on the free tier and no more: it keeps the fingerprint-and-engine
portion of the assessment from contradicting itself.

## Short answers to the questions that lead here

**Is hCaptcha Enterprise just a harder version of the free widget?** No. All three
tiers run the same invisible-pass-then-puzzle mechanism. Enterprise adds a risk model
trained on the customer's own traffic, control over the puzzle's content, time-of-day
difficulty tuning, and operational features (SSO, SLA, reporting) the free and Pro tiers
do not have.

**What is the actual difference between Pro and Enterprise?** Pro turns on a shared,
general risk model and a passive (low-friction) mode. Enterprise adds a model trained
specifically on that customer's traffic ("private learning"), custom challenge content,
an extra "Passive" difficulty tier, and enterprise support and reporting.

**Does Enterprise cost money the way Pro does?** Yes, but it is negotiated rather than a
published metered rate; Pro is a published $99-139/month tier with per-evaluation
overage, while Enterprise pricing is not published and requires contacting hCaptcha
directly.

**Is the free tier really "paid for" by the puzzle answers?** According to hCaptcha's
own labeling product and a HUMAN Protocol blog post describing the compensation model,
yes in substance: free-tier site owners are compensated for the annotation work their
visitors do, and hCaptcha's own labeling business resells that annotation category to
machine-learning customers. hCaptcha's own privacy FAQ denies selling individually
targeted ads or personal data specifically, which is a narrower claim than a denial that
labeled answers are monetized.

**Do Enterprise customers participate in that same labeling economy?** Per the same
HUMAN Protocol post, no: "Enterprise integrators of the hCaptcha security service like
Cloudflare pay IM for more features, rather than being compensated," describing a direct
cash relationship instead.

**Does a real browser engine answer Enterprise's assessment any differently than
Basic's?** No. All three tiers share the same underlying mechanism, and the reasoning
that already applies to a Playwright session against Basic or Pro applies to
Enterprise's version of the same pass unchanged. Private learning, being trained on a
specific customer's own traffic history, is outside what any single browser session can
affect at all.

**See also:** [hCaptcha, Explained](hcaptcha-explained.md) for the underlying mechanism
this page builds on, and [Does Playwright Trigger hCaptcha More Often?](does-playwright-trigger-hcaptcha.md)
for what a Playwright session specifically does to that mechanism regardless of tier.

## Sources

- hCaptcha, [Pricing](https://www.hcaptcha.com/pricing), retrieved 2026-08-30, for the
  Basic/Pro/Enterprise price points and the Pro evaluation allowance and overage rate.
- hCaptcha, [Plans](https://www.hcaptcha.com/plans), retrieved 2026-08-30, for the
  feature-by-tier breakdown (free-tier request allowance, Pro's risk scores and threat
  signatures, Enterprise's private learning, custom challenges, challenge content
  control, time-of-day difficulty tuning, multi-user dashboards, SAML SSO, and SLAs).
- hCaptcha, [Pro Features](https://docs.hcaptcha.com/pro), retrieved 2026-08-30, for the
  "99.9% Passive" mode description, its "thousands of factors" and "less than 0.1%"
  framing, and the Pro pricing and trial terms.
- hCaptcha, [Frequently Asked Questions](https://docs.hcaptcha.com/faq), retrieved
  2026-08-30, for the four Basic-tier difficulty modes and the Enterprise-only "Passive"
  mode plus "sophisticated custom threat models" and "detailed bot scores" framing.
- HUMAN Protocol, ["How does hCaptcha fit into HUMAN Protocol?"](https://humanprotocol.org/blog/how-does-hcaptcha-fit-into-human-protocol),
  retrieved 2026-08-30, for the integrator-compensation model on the free tier and the
  explicit statement that Enterprise customers like Cloudflare pay Intuition Machines
  directly rather than being compensated.
- hCaptcha, [Data Labeling](https://www.hcaptcha.com/labeling), retrieved 2026-08-30, for
  the annotation-service pitch that the free-tier economy above feeds into.

---

*From the notes of [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level driven by stock Playwright. Enterprise's extra
machinery is risk-model customization, challenge content control, and a paid support
contract on top of a mechanism this project already writes about on the main hCaptcha
page; nothing here should be read as a claim that engine realness moves a private
learning model trained on someone else's traffic, which this project has no access to.*
