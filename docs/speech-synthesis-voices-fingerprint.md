---
title: "speechSynthesis voices as a cross-platform fingerprint"
description: "speechSynthesis.getVoices() leaks the real OS; Windows user agent with Linux voice list contradicts itself. How a genuine platform returns expected voices."
parent: "Detectors, Explained"
grand_parent: "Guides"
nav_order: 19
---


# speechSynthesis voices as a cross-platform fingerprint

The Web Speech API has a small method that turns out to be a large tell.
[`speechSynthesis.getVoices()`](https://developer.mozilla.org/en-US/docs/Web/API/SpeechSynthesis/getVoices)
returns the list of text-to-speech voices the browser can use, and that list does not
come from the browser. It comes from the
operating system underneath it. Which means a single call reads back the real
platform, no matter what the user agent claims.

This page is what the voice list actually reports, why a headless or Linux server
pretending to be Windows fails the check, how a real browser on a real OS passes
it, and the honest limit of what passing it buys you.

## What getVoices() actually returns

Each entry the method returns is a
[`SpeechSynthesisVoice`](https://developer.mozilla.org/en-US/docs/Web/API/SpeechSynthesisVoice)
carrying a name, a `lang` tag such as `en-US`, a `default` flag, and a `localService`
boolean that says whether the voice is synthesized on the device or fetched from a
remote service. The engine does not invent any of this. It enumerates whatever speech backend the host OS exposes and
hands you the result.

That makes the shape of the list an operating-system signature:

- A Windows desktop returns the built-in Microsoft voices, with names and `lang`
  tags in a recognisable set, most of them `localService: true`.
- A macOS machine returns the Apple voice set, a different collection of names
  again.
- A bare Linux server, the kind that runs most automation, usually returns
  **nothing**. No speech backend is installed, so the array is empty. If one is
  installed, it returns the open-source Linux voices, which look nothing like the
  Windows set.

So the list is not just present-or-absent. Its members, their `lang` tags and
their `localService` flags are all cross-checkable against the platform the rest
of the browser is claiming.

## Why it contradicts a spoofed user agent

Most automation on a server runs headless on Linux and then sets a Windows user
agent string, either by hand or through a stealth layer. The user agent is a
string, so it changes for free. The voice list is not a string you can set from
JavaScript, so it does not change with it.

The result is a contradiction a detector reads in one line:

```javascript
// what a naive "Windows" server actually reports
navigator.userAgent      // "...Windows NT 10.0; ...Firefox/141.0"
speechSynthesis.getVoices().length   // 0   <-- no Windows desktop is empty
```

An empty voice array under a Windows user agent is a desktop with no speech
engine, which no real Windows desktop is. A voice list full of Linux names under
a Windows user agent is worse, because now the two fields name two different
operating systems out loud. Either way the check is cheap, deterministic and
needs no timing or interaction. It is the same family of "this is a datacenter"
signal as an [empty or default AudioContext](audiocontext-fingerprinting.md) or a
[font set that does not match the platform](playwright-docker-detection.md), and
it survives every property-patching stealth plugin for the same reason those do:
the value is not JavaScript's to rewrite.

There is a second-order trap here too. Patching `getVoices()` to return a
hand-built Windows list looks like the fix and is not. A tampering-focused suite
does not read the list, it reads whether the method was touched. A comparison
detector like [CreepJS](creepjs-explained.md) takes a clean copy of the built-ins
from a fresh iframe and records an overridden method by name, so a fabricated
voice list trades a platform mismatch for a lie about the API, which scores worse.

## Why a real browser on a real OS passes it

A real browser on a real OS passes because nothing about the voice list is
faked: invisible_playwright runs a genuine Firefox, patched at the C++ level, on
the real operating system of the machine you launch it from. It does not shim
`getVoices()` and it does not need to. When the browser asks the host for its speech voices, the host answers with
its actual voices, and if you are running on Windows those are the Windows voices,
consistent with the platform the rest of the fingerprint reports.

Nothing is being faked, so nothing can disagree with itself. The voice list, the
[emoji glyph shapes](emoji-fingerprinting-cross-platform.md), the font
enumeration and the audio stack all come from one real OS, which is why they
corroborate each other instead of contradicting each other. A spoof has to keep a
dozen surfaces in sync by hand; a real platform keeps them in sync by being one.

The practical consequence: if you want a session that reads as Windows, run it on
Windows. The voice list will then be a real Windows voice list, because it is one.

## Reading the voice list yourself

The launch is the two-line change from stock Playwright, and after that every
method is ordinary Playwright. Here is the whole check.

```python
from invisible_playwright import InvisiblePlaywright

with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    page.goto("https://example.com")

    # getVoices() can populate asynchronously; wait for it, then read it
    voices = page.evaluate("""() => new Promise(resolve => {
        const read = () => speechSynthesis.getVoices();
        let v = read();
        if (v.length) return resolve(v.map(x => ({name: x.name, lang: x.lang, local: x.localService})));
        speechSynthesis.onvoiceschanged = () => resolve(
            read().map(x => ({name: x.name, lang: x.lang, local: x.localService}))
        );
    })""")

    print("voice count:", len(voices))
    for v in voices[:5]:
        print(v["name"], v["lang"], v["local"])
```

One detail that catches people: on a fresh page `getVoices()` can return an empty
array on the very first synchronous call and then fill in a moment later, firing
`voiceschanged`. That is normal browser behaviour, not a bot signal, which is why
the snippet waits for the event before deciding the list is empty. A test that
reads the list once, synchronously, and concludes "empty" is measuring its own
race, not the browser.

Run the same snippet against a stock Firefox on the same machine and diff the two
lists field by field, the way
[the general testing method](how-to-test-bot-detection.md) recommends. On a real
desktop they match, because both are reading the same OS. On a Linux container
claiming Windows, the stock browser and the automated one agree with each other
and both disagree with the user agent, which tells you exactly where the problem
lives.

## What a matching voice list does and does not buy you

Here is the honest boundary, because this is where overclaiming starts.

A voice list that matches the claimed platform is **corroboration, not proof**. It
removes one contradiction. It tells a detector that this surface is consistent
with a real Windows desktop, which is a real and useful thing to remove, and it is
why invisible_playwright reads as a genuine Firefox across the fingerprint, TLS
and driver layers. It does not, and cannot, prove a human is driving.

None of the following are touched by having the right voices:

- **IP reputation.** A perfect voice list on a datacenter address is still on a
  datacenter address. You supply a clean exit; the browser does not.
- **Per-account quotas and rate limits.** These are counted server-side against
  your account and your address, and no browser property changes the count.
- **Behaviour and timing.** Pointer motion, typing rhythm and the pace of a
  session are watched independently of any fingerprint field.

The voice list is one field among roughly four hundred. Getting it right is
necessary and nowhere near sufficient. The product makes the browser look like a
real browser; looking like a real *person* is the pacing and the network path,
and those are yours to get right.

## Conclusion

`speechSynthesis.getVoices()` is a quiet reflection of the operating system, and a
disguise that changes the user agent but not the OS underneath will report a voice
list that names a different platform, or no list at all. The durable answer is not
to fabricate a list, which a tampering check catches, but to run a real browser on
the real OS you want to present, so the voices are simply the true voices. That
closes one honest contradiction. Pair it with a clean exit and human pacing,
because the voice list says nothing about either.

## Short answers to the questions that lead here

**Why does speechSynthesis.getVoices() return an empty array?** Because the OS
underneath has no speech backend installed, which is the normal state of a bare
Linux server. Under a Windows user agent that empty array is a contradiction.

**Can I just override getVoices() to return Windows voices?** You can, and a
tampering-focused suite will flag the override itself rather than read the list,
so you trade a platform mismatch for a detected lie about the API.

**Does the voice list prove a real user is present?** No. It corroborates the
claimed platform and removes one contradiction. It says nothing about IP, quotas,
rate limits or behaviour.

**Why does my first getVoices() call come back empty even in a real browser?** The
list can populate asynchronously and then fire `voiceschanged`. Wait for that
event before concluding the list is empty, or you are measuring a race.

**How do I make the voice list look like Windows?** Run on Windows. A genuine
Firefox on a real Windows host returns the real Windows voices with no shimming.

**Is the voice list enough on its own to avoid detection?** No. It is one field of
roughly four hundred, and a consistent browser on a bad IP with robotic timing
still loses.

## Sources

- The Web Speech API `SpeechSynthesis.getVoices()` and the `SpeechSynthesisVoice`
  interface (`name`, `lang`, `default`, `localService`), read from the public
  specification and Firefox's implementation behaviour.
- This project's platform-consistency gates, which compare enumerated OS surfaces
  (voices, fonts, audio) against the claimed platform rather than reading any one
  in isolation.

**See also:** [why getVoices() comes back empty on a server](speech-synthesis-voices.md),
[AudioContext fingerprinting](audiocontext-fingerprinting.md) for the same
OS-tell in the audio stack, and [how to test whether your browser is detected](how-to-test-bot-detection.md)
for the compare-against-stock method used above.

---

*Written while maintaining [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level driven by stock Playwright. The voice list is
one of the surfaces that is right for free when the OS is real, and impossible to
keep right by hand when it is not.*
