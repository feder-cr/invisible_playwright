---
title: "hCaptcha, Explained"
description: "hCaptcha runs an invisible risk pass first and only falls back to a visual image-classification puzzle when that pass is unsure. It also doubles as a data-labeling business: puzzle answers train other companies' machine learning models. Read from hCaptcha's own docs."
parent: "Detectors, Explained"
grand_parent: "Guides"
nav_order: 42
---


# hCaptcha, Explained

hCaptcha is a real captcha, not a background score wearing a captcha's name. Most of the
time a visitor never sees it: an invisible pass runs first and only escalates to a
visible puzzle when that pass is unsure. When it escalates, the puzzle is an actual
image-classification task, select every image matching a label, the same family of
thing reCAPTCHA v2 and GeeTest ask for.

Be clear about what this page is before reading further, because that distinction
matters more here than on most pages in this corpus. This page explains the mechanism,
read from hCaptcha's own documentation and independent, checkable sources. It is not
instructions for solving the puzzle automatically, and `invisible_playwright` includes
no puzzle-solving or captcha-solving capability, for hCaptcha or any other vendor. That
boundary is stated once here, plainly, and repeated at the end of this page.

## What hCaptcha is

hCaptcha is a product of Intuition Machines, Inc., described in its own materials as
"the world's most widely used independent CAPTCHA service," positioned against Google's
reCAPTCHA on privacy and reach: "Unlike reCAPTCHA, we work in every country," with a
claimed "Zero-PII architecture" and compliance with GDPR, CCPA, LGPD and PIPL. Its
homepage describes current scale as "hundreds of millions of people around the world"
and "the largest independent service of its kind," without a specific volume figure.

An independent reference work places its introduction in 2018. The most concrete public
data point on adoption is independently reported rather than hCaptcha's own: Cloudflare's
April 2020 switch away from reCAPTCHA, citing Google's advertising business, reCAPTCHA
being blocked in China alongside every other Google service, and a newly announced
reCAPTCHA price that would have cost Cloudflare, in its own account, millions of dollars
a year to keep using for free.

## The two-stage model: an invisible pass, then maybe a puzzle

hCaptcha's own developer documentation describes the flow in two stages. First, "the
user starts an hCaptcha evaluation: they click the checkbox or submit button, or you
trigger it programmatically" through `hcaptcha.execute(widgetID)`. Second, once that
evaluation finishes, "the hCaptcha script inserted a unique token into your form data,"
which the integrating site's own backend then has to check.

The part that matters for automation is what happens between those two steps. A widget
configured with `data-size="invisible"` renders nothing, and per hCaptcha's own
documentation, "the widget will be rendered in the background and only presented when a
challenge is required." Whether a challenge is required is a per-session decision:
"the user will only be presented with a hCaptcha challenge if that user meets challenge
criteria." hCaptcha's FAQ names the inputs to that decision only at the category level:
"computed confidence in the visitor's humanity, the site difficulty setting, and other
security factors." Neither document lists the specific signals or weights behind that
confidence figure, and no page in this corpus claims to know them.

The clearest description of the invisible pass's intent comes from hCaptcha's own
Pro-tier documentation, describing its highest-friction-reduction mode: an evaluation
"derived from thousands of factors," built to "minimize challenges to real people to
less than 0.1% of users, while still increasing the costs of an attack in virtually all
cases." That shape, thousands of undisclosed factors feeding one pass/challenge
decision, matches the risk engines covered elsewhere in this corpus for
[Turnstile](cloudflare-turnstile-explained.md) and
[Arkose Labs](arkose-labs-funcaptcha-explained.md): broad category claims, no published
field list. One input is visible in the API itself: the server-side verification call
accepts an optional `remoteip` parameter, recommended "for accuracy," about as direct a
confirmation as a vendor gets that the requesting address feeds the decision too.

## The puzzle, when it appears

When the invisible pass escalates, a Basic (free, "Publisher") account has four fixed
difficulty modes to pick from: "Easy, Medium, Difficult, and Auto." hCaptcha's own FAQ
states a completed challenge takes "about the same as a traditional captcha: 3-10
seconds depending on the difficulty mode," the same rough time budget as a reCAPTCHA v2
checkbox-and-grid flow.

hCaptcha's own documentation does not spell out the puzzle's visual format in technical
detail. An independent, encyclopedic reference describes it as asking a visitor to
"select all images matching a generated query, called a label," which matches how
hCaptcha's own data-labeling materials describe the underlying task category: bounding
boxes, polygons, landmarks and attribute categorization on images and video. The puzzle
a visitor solves and the annotation task a machine-learning customer pays for are, per
that framing, the same category of work, which is the subject of the next section.

## The part that pays for the free tier: labeled data

hCaptcha's business model is unusual among captcha vendors in that the company is
explicit, in its own public materials, about running a second product built on the same
challenges visitors solve. hCaptcha's own labeling page pitches a data-annotation service
directly at "companies working on artificial intelligence and machine learning
applications," describing the same task types, bounding boxes, polygons, landmarks,
categorization, that show up in the visual puzzle, and framing the pitch around solving
"the most labor intensive problem in machine learning: labeling massive amounts of data
in a timely, affordable, and reliable way."

hCaptcha's own FAQ is specific about what it does not do with the data it collects: "we
are not in the business of selling individually targeted ads," and it works "to protect
your personal data and limit collection rather than selling it to others." That is a
claim about advertising and personal data, narrower than a claim that labeled answers
are never monetized, and it should be read exactly that narrowly. An independent
reference states plainly that "hCaptcha makes revenue by selling its datasets to 3rd
party companies," and a blog post from HUMAN Protocol, the labor marketplace hCaptcha
is connected to, describes hCaptcha compensating the sites that embed it for "the work
their users do as they verify their humanity," framing that work as "annotation," a
"vital part of building and improving machine intelligence." The same post draws a
clean line on the other side of that arrangement: "Enterprise integrators... like
Cloudflare pay IM [Intuition Machines] for more features, rather than being
compensated." Free-tier integrations sit inside a labor-and-payment loop; Enterprise
integrations pay cash instead.

That split, an ad-and-labeling-funded free tier against a directly paid Enterprise tier,
is one axis of what actually changes between the two, and it is covered in full,
alongside the pricing figures and the feature differences that are more than framing,
on [hCaptcha Enterprise vs Standard](hcaptcha-enterprise-vs-standard.md).

## The server side: what actually proves the puzzle was solved

The puzzle, or the invisible pass, produces a token, and hCaptcha's own documentation is
direct that the token proves nothing until a server checks it. The check is a
server-to-server call to `https://api.hcaptcha.com/siteverify`, POSTing the site's secret
key and the token (an optional `remoteip`, plus a `sitekey` to stop a token being
replayed under a different site's key, are also accepted). The response is JSON: a
`success` boolean, a `challenge_ts` timestamp, the `hostname`, `error-codes` on failure,
and, Enterprise-only, a `score`/`score_reason` pair carrying the risk assessment itself.
A token "can only be used once and must be verified within a short period of time," a
default of 120 seconds, the same single-use, time-boxed shape as
[the Turnstile token](cloudflare-turnstile-explained.md#the-token-and-why-passing-the-widget-is-not-the-finish-line).

## What invisible_playwright does and does not do here

Read this section carefully, because it is the point of this page. `invisible_playwright`
is a patched Firefox that answers the JavaScript-visible parts of a browser fingerprint
honestly, canvas and WebGL output, font enumeration, timing consistency, whether the
engine claiming to be a real browser actually behaves like one. Whatever hCaptcha's
undisclosed "thousands of factors" include on the fingerprint side, a genuinely real
engine answers that category the way any other real instance of that engine does, for
the same reason argued on [the Turnstile page](cloudflare-turnstile-explained.md#where-an-engine-level-browser-actually-helps-and-where-it-does-not)
and [the GeeTest page](geetest-v4-explained.md#what-invisible_playwright-does-and-does-not-do-here).

That is where the honest boundary sits, and it sits well short of the puzzle. Selecting
the images that match a label is a human-interaction and image-recognition problem, not
a fingerprint problem, and no amount of engine-level realness answers it for you.
`invisible_playwright` does not solve, automate past, or sell a solution to hCaptcha's
visual challenge, and it never will as a feature of this project. A tool claiming to
"bypass hCaptcha" is describing a different, separate product category, commercial
captcha-solving services exist for exactly this reason, not something this project
does, sells, or intends to build.

## Short answers to the questions that lead here

**Is hCaptcha a real captcha, like GeeTest, or a silent score, like Turnstile?** Both, in
sequence. An invisible risk pass runs first and handles most sessions with no visible
step at all; only the sessions it leaves unsure escalate to an actual visual puzzle.

**What does the hCaptcha puzzle actually ask you to do?** Per independent description of
the format, select every image matching a given label, in the same family as a
reCAPTCHA v2 image grid, taking roughly 3 to 10 seconds by hCaptcha's own account of
typical completion time.

**Does hCaptcha publish what its invisible pass checks?** No. hCaptcha's own materials
describe the decision only at category level, "computed confidence," "thousands of
factors," an accepted `remoteip` parameter, without publishing the specific signals or
their weights.

**Is it true that hCaptcha sells the data from the puzzles people solve?** Its own
labeling product pitches image and video annotation directly to AI and ML companies
using the same task categories as the visitor-facing puzzle, and an independent
reference states it sells datasets to third parties; hCaptcha's own privacy FAQ denies
selling individually targeted ads or personal data specifically, a narrower claim than a
denial that labeled answers are monetized.

**Does invisible_playwright solve hCaptcha's puzzle for me?** No. That is a
human-interaction, image-recognition challenge, not a browser-fingerprint check, and this
project does not include a puzzle-solving or captcha-solving capability of any kind, for
hCaptcha or any other vendor.

**Does a real, engine-level browser help against hCaptcha at all?** Only against
whatever portion of the invisible pass reads genuine browser-engine behavior, canvas,
WebGL, font and timing consistency, the same category argued throughout this corpus. It
has no effect on the puzzle interaction itself.

**Is hCaptcha the same thing whether I'm on the free tier or Enterprise?** The underlying
widget and verification mechanism described on this page is shared. What changes across
tiers, difficulty controls, risk-score visibility, custom challenges, and the
labeling-versus-cash relationship, is covered on
[hCaptcha Enterprise vs Standard](hcaptcha-enterprise-vs-standard.md).

## Sources

- hCaptcha, [Developer Guide](https://docs.hcaptcha.com/), retrieved 2026-08-30, for the
  two-stage widget flow, the `hcaptcha.execute()` trigger, and the `siteverify` endpoint,
  its parameters, response fields and the single-use/120-second token behavior.
- hCaptcha, [Invisible Captcha](https://docs.hcaptcha.com/invisible), retrieved
  2026-08-30, for the `data-size="invisible"` behavior and the challenge-criteria framing.
- hCaptcha, [Configuration](https://docs.hcaptcha.com/configuration), retrieved
  2026-08-30, for the documented widget parameters (`data-size`, `data-theme`, the
  callback hooks) and confirmation that difficulty selection is not a client-side option.
- hCaptcha, [Pro Features](https://docs.hcaptcha.com/pro), retrieved 2026-08-30, for the
  "99.9% Passive" mode description and its "thousands of factors" / "less than 0.1%"
  framing.
- hCaptcha, [Frequently Asked Questions](https://docs.hcaptcha.com/faq), retrieved
  2026-08-30, for the challenge-decision factors ("computed confidence... site
  difficulty... other security factors"), the four Basic-tier difficulty modes, the
  3-10 second completion-time figure, and the privacy/no-targeted-ads statement.
- hCaptcha, [Pricing](https://www.hcaptcha.com/pricing) and
  [Plans](https://www.hcaptcha.com/plans), both retrieved 2026-08-30, for tier
  positioning referenced briefly here (full comparison on the Enterprise-vs-Standard
  page).
- hCaptcha, [homepage](https://www.hcaptcha.com/) and [About](https://www.hcaptcha.com/about),
  both retrieved 2026-08-30, for the "hundreds of millions of people," "largest
  independent service," "every country," "Zero-PII architecture" claims, and the
  company's relationship to Intuition Machines.
- hCaptcha, [Data Labeling](https://www.hcaptcha.com/labeling), retrieved 2026-08-30, for
  the annotation-service pitch to AI/ML companies and its overlap with the puzzle.
- HUMAN Protocol, ["How does hCaptcha fit into HUMAN Protocol?"](https://humanprotocol.org/blog/how-does-hcaptcha-fit-into-human-protocol),
  retrieved 2026-08-30, for the integrator-compensation model and the explicit statement
  that Enterprise customers like Cloudflare pay Intuition Machines rather than being paid.
- HandWiki, [HCaptcha](https://handwiki.org/wiki/HCaptcha), retrieved 2026-08-30, an
  independent encyclopedic mirror, for the 2018 introduction date, the "select all images
  matching a generated query, called a label" challenge description, the claim that
  hCaptcha "makes revenue by selling its datasets to 3rd party companies," and the
  ~10-million-users-per-month 2019 adoption figure.
- BleepingComputer, ["Cloudflare drops Google's reCAPTCHA due to privacy concerns"](https://www.bleepingcomputer.com/news/technology/cloudflare-drops-googles-recaptcha-due-to-privacy-concerns/),
  published 2020-04-13, retrieved 2026-08-30, independent corroboration of Cloudflare's
  April 2020 move to hCaptcha and its stated reasons (privacy, China accessibility, cost).

**See also:** [Does Playwright Trigger hCaptcha More Often?](does-playwright-trigger-hcaptcha.md)
for what a Playwright session specifically does to the invisible pass described above;
[hCaptcha Enterprise vs Standard](hcaptcha-enterprise-vs-standard.md) for the tier
comparison this page defers to; [GeeTest v4 (Slide/Click Captcha), Explained](geetest-v4-explained.md)
and [Arkose Labs (FunCaptcha), Explained](arkose-labs-funcaptcha-explained.md) for two
other vendors whose visible puzzle sits behind a similar undisclosed risk pass; and
[How Cloudflare Turnstile actually works](cloudflare-turnstile-explained.md) for the
non-interactive counterpart of the same fingerprint-versus-human-gate split.

---

*From the notes of [invisible_playwright](https://github.com/oss-invisible/playwright),
a Firefox patched at the C++ level and driven by stock Playwright. This page explains a
mechanism and a business model, not a workaround: the product does not solve captchas,
and a claim that any tool defeats hCaptcha's puzzle step is a different promise than
anything documented here.*
