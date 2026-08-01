---
title: "speechSynthesis.getVoices() returns an empty array"
description: "An empty speechSynthesis.getVoices() is two unrelated problems wearing the same symptom: an async timing gotcha that hits every browser, and a voice list that describes the wrong operating system."
parent: "Browser Identity"
grand_parent: "Guides"
nav_order: 11
---


# speechSynthesis.getVoices() returns an empty array

There are two separate problems hiding behind the same symptom, and the fixes have
nothing to do with each other.

1. **It is empty on the first call and fills in a moment later.** A timing problem,
   in every browser, on every machine. Fixed with an event listener.
2. **It is empty and stays empty**, or it comes back full of names that do not belong
   to the operating system you claim to be running. A different problem, and the one
   this page is really about.

## Problem one: the async gotcha

```js
const voices = speechSynthesis.getVoices();
console.log(voices.length);   // 0
```

The voice list is populated asynchronously. Call `getVoices()` during page load and
you can easily get there first. The fix is the `voiceschanged` event:

```js
function whenVoicesReady() {
  return new Promise(resolve => {
    const voices = speechSynthesis.getVoices();
    if (voices.length) return resolve(voices);
    speechSynthesis.addEventListener(
      'voiceschanged',
      () => resolve(speechSynthesis.getVoices()),
      { once: true }
    );
  });
}
```

Note the early return. On a warm page the list is already there and `voiceschanged`
will never fire again, so a listener-only version hangs forever. This is the single
most common way the workaround is written wrong.

This is the answer for the ordinary version of the question, and it is the same in
Chrome, Firefox and Safari. If that fixed it, you are done and the rest of this page
does not apply to you.

If you are hitting it inside Electron, a CEF-embedded control, a Docker container or
a CI runner, the listener will not save you, because there is nothing to wait for. No
speech engine is installed, so the list is empty and stays empty. That is the second
problem.

## Problem two: the list is a statement about your machine

`getVoices()` does not return a list defined by the browser. It returns the speech
voices installed in the **operating system**, which means the array is a description
of the host.

A normal Windows machine returns something like:

```
Microsoft David - English (United States)
Microsoft Zira - English (United States)
Microsoft Mark - English (United States)
```

A Linux server returns something quite different, if it returns anything:

```
native
espeak-ng mb-en1
```

A minimal container usually returns nothing, because no speech engine is installed.

So this one API answers three questions at once: which operating system, roughly
which install, and whether it is a desktop or a stripped-down server. Nobody thinks of
the speech synthesis API as a fingerprinting surface, which is exactly what makes it
useful as one.

Three tells, in order of how obvious they are:

- **Empty list plus a Windows user agent.** Most real Windows installs have several
  Microsoft voices. Zero is a server or a browser with speech disabled.
- **`espeak` names plus a Windows user agent.** The browser has just contradicted
  itself, and there is no ambiguity about which half is lying.
- **A Windows voice list on a machine that is wrong about it in some other way.**
  Which brings us to the interesting failure.

## Why filtering the list does not work

The obvious implementation is to take whatever the OS registry hands back and filter
it down to a plausible Windows set. We looked at that approach, and it has a problem
that only shows up when you deploy.

On Windows it works, because the Microsoft voices are genuinely there and filtering
keeps them.

On Linux there is nothing to filter. The registry returns `espeak-ng` entries and
nothing else, so a filter looking for Microsoft names removes everything and returns
an empty array. The code that made the fingerprint correct on a developer laptop
produces the strongest possible tell on the server it was written for.

This is a shape worth recognising, because it is not specific to voices: **an
implementation that filters a real list is only as good as the list underneath it**.
On the host where the underlying data is missing, filtering does not degrade
gracefully, it degrades to empty.

We went the other way. The voice list is fabricated from a preference and the OS
registry is never consulted, so the same code returns the same list on any host. Each
voice carries a synthetic URI encoding its name, language, default flag and local
flag, and the accessors read those fields back out of it. There is no path where
the answer depends on what happens to be installed on the machine.

## What to check

```js
const voices = await whenVoicesReady();
console.table(voices.map(v => ({
  name: v.name, lang: v.lang, local: v.localService, def: v.default
})));
```

Compare against what a real machine of the type you claim to be would return:

- The **names** belong to the claimed platform. Microsoft voices for Windows, Apple
  voices for macOS.
- The **count** is plausible. A handful, not one, and not thirty.
- `localService` is `true` for the built-in voices. A remote voice implies a cloud
  service is configured.
- Exactly one voice has `default` set.
- The **languages** are consistent with `navigator.languages` and with the timezone
  you are claiming. A machine with only English voices, an Italian locale and a
  Rome timezone is three answers that do not describe one person.

That last one is the general principle again. Every one of these surfaces is cheap to
get individually right and only interesting when compared with the others.

## Also worth knowing

Disabling the API entirely, by turning `webspeech.synth.enabled` off, removes the
list. It also removes `speechSynthesis` behaviour that a real browser has, which puts
you back in the same position as an empty array: a browser missing a capability every
normal browser has, and [suppression is not neutral](creepjs-explained.md).

## Short answers to the questions that lead here

**Why is `getVoices()` empty on first call?** The list loads asynchronously. Listen for
`voiceschanged`, and check the list once before you attach the listener.

**Why does `voiceschanged` never fire?** Because the list was already populated when
you attached the listener. It fires on change, not on ready.

**Why is it empty in headless Chrome or headless Firefox?** Usually not headless
itself. The machine has no speech voices installed, which is normal for a container
or a CI image.

**Why is it empty in Docker?** Nothing in a standard base image installs a speech
engine. Installing `espeak-ng` gives you voices, and gives you Linux voice names.

**Can I install voices to fix it?** On Linux, yes, and the result is a list that says
Linux. If your user agent says Windows, you have replaced one problem with a clearer
one.

**Is `getVoices()` used for fingerprinting?** Yes, and it is a strong signal precisely
because it is an OS-level list that most people never think to align with the rest of
their fingerprint.

**See also:** [why headless browsers render different fonts](headless-fonts-differ.md),
the same "the OS answers, not the browser" problem on a much more commonly checked
surface, and [WebGL renderer strings](webgl-renderer-strings.md).

---

*From the notes of [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level. The filtering approach described above is one we
tried and abandoned for the reason given.*
