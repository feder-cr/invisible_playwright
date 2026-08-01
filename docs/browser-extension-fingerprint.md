---
title: "Browser extensions are a fingerprint surface"
description: "An installed browser extension is itself a fingerprint surface, checkable three separate ways, and the most common reason to add one is to hide a fingerprint, which is where it turns circular."
parent: "Browser Identity"
grand_parent: "Guides"
nav_order: 12
---


# Browser extensions are a fingerprint surface

Adding an extension to an automated browser feels like adding a feature. It is also
adding something a page can look for, in three different ways, and the most common
reason people add one is to hide a fingerprint, which is where this gets circular.

## The three ways a page finds an extension

**Web-accessible resources.** An extension that exposes files to pages does so at a URL
containing its own identifier. Fetch that URL and see whether it loads.

```js
fetch('moz-extension://<id>/<file>')       // resolves only if that extension is here
```

The identifier differs per browser and, in Firefox, per profile, which limits the
scan. It does not eliminate it, because extensions that need page access often need
predictable resources.

**What it does to the page.** An ad blocker removes elements, so a page can insert a
bait element with a name matching a common filter list and check whether it survived.
A style-injecting extension changes computed values. Anything that modifies the DOM
leaves the modification behind, and the modification is the detection.

**The behaviour it adds.** An extension that patches `navigator` or the canvas has to
run before the page, and it leaves the same traces as any other page-level override:
own properties where prototypes are expected, getters that are not native,
[a `toString` that prints your source](tostring-native-code-detection.md).

## Why a stealth extension is usually the wrong shape

This is worth stating plainly, because it is the most common reason someone asks how to
load one.

A stealth extension operates at the page layer. It runs inside the document, it is
made of JavaScript, and everything on
[the three levels page](playwright-stealth-levels.md) about that layer applies to it. It
also has to run before the page's own scripts, which is a race it does not always win.

And if the browser already sets those values in its engine, the extension is now a
second opinion arguing with the first. Two components deciding what `navigator` says do
not average out; they disagree, and the disagreement is easier to spot than either
answer alone. That is the same argument as
[turning off a crawler's own fingerprint generator](playwright-detected-as-bot.md).

So: extensions for functionality, yes. Extensions for stealth on top of a stealth
engine, no.

## Extensions that are fine, and how to think about the rest

Ask what the extension does to the page:

- **Nothing visible to the page.** A tab manager, something that only touches its own
  UI. Effectively invisible.
- **Changes what the page sees.** Blockers, style injectors, script managers. Findable,
  and worth knowing that ad blockers are extremely common on real browsers, so their
  presence is not by itself unusual. Their *absence* is more unusual than people
  assume.
- **Rewrites browser APIs.** The fingerprinting-protection kind. These are the ones
  that create the arguing-with-the-engine problem.

The second point deserves emphasis, because it cuts against the instinct. A very large
share of real browsers run an ad blocker. A profile that carries none is not
suspicious on its own, but it is one more small way in which a fresh automated profile
differs from a used personal one, alongside
[having no cookies](recaptcha-v3-score.md).

## How to load one, if you decide to

There is no extension argument in this wrapper. What there is is a persistent profile,
and an extension installed into that profile stays there:

```python
with InvisiblePlaywright(seed=42, profile_dir="/path/to/profile") as browser:
    ...
```

Launch once with that path, install the `.xpi` the normal way through the browser's own
add-ons page, then reuse the directory. Without `profile_dir`, every session builds a
fresh profile from the seed, which is exactly why an extension installed in one run is
gone in the next.

Pair a stable profile with a stable seed. A profile that accumulates cookies and
storage while the hardware it claims changes every launch is a session whose history
and machine disagree about how long it has existed.

## Checking what yours is exposing

```js
// does a known resource resolve?
fetch('moz-extension://<id>/manifest.json').then(r => console.log('present', r.status))
                                           .catch(() => console.log('not found'));

// did something remove my bait element?
const bait = document.createElement('div');
bait.className = 'ad-banner ads adsbox';
bait.style.height = '10px';
document.body.appendChild(bait);
setTimeout(() => console.log('blocked:', bait.offsetHeight === 0), 100);
```

Then run the same checks with no extensions loaded and compare. Anything that differs
is what the extension is telling the page about you.

## Short answers to the questions that lead here

**Can websites detect browser extensions?** Yes, through web-accessible resources,
through what the extension does to the page, and through the traces any API override
leaves.

**Does using an ad blocker make me more detectable?** It makes you findable, not
unusual. Ad blockers are very common on real browsers.

**Should I use a stealth extension with a stealth browser?** No. Two layers deciding
the same values contradict each other, and the contradiction is a stronger signal than
either.

**How do I add an extension in Playwright with Firefox?** Through a persistent profile:
install it once into that profile, then reuse the directory.

**Why does my extension disappear between runs?** Because a fresh profile is generated
each launch unless you pass a persistent one.

**Are extension IDs the same everywhere?** In Firefox they are typically per-profile,
which limits blind scanning. Do not rely on that as protection; extensions that need
page access tend to expose predictable resources anyway.

**See also:** [Function.prototype.toString and the native code check](tostring-native-code-detection.md),
which is what a page-level extension runs into, and
[three ways to make Playwright undetected](playwright-stealth-levels.md), for why the
layer an extension lives at has a ceiling.

---

*From the notes of [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level. This page exists because someone asked in our
discussions and got an answer about a different kind of extension entirely.*
