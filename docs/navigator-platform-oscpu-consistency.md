---
title: "navigator.platform and oscpu on a spoofed OS"
description: "navigator.platform, oscpu and appVersion come from the OS Firefox runs on, so a Linux build under a spoofed Windows user agent leaks Linux on all three."
parent: "Browser Identity"
grand_parent: "Guides"
nav_order: 19
---


# navigator.platform and oscpu on a spoofed OS

The user agent gets all the attention, but it is not the only string that names your
operating system. `navigator.platform`, `navigator.oscpu` and `navigator.appVersion`
each carry an OS identity too, and in Firefox they come from a different place than the
user agent does. If you change the user agent and leave these alone, you have three
properties still telling the truth about a machine you were trying to disguise.

This page is what those three properties are, why they leak the real host under a
spoofed user agent, why `navigator.oscpu` is a Firefox-only signal that a detector can
read as an engine tell, how to measure all of it, and how this project keeps the four
strings agreeing.

## The three properties that describe the operating system

Four properties, read from JavaScript, all claim to describe where the browser runs.
On a real Windows Firefox they look like this:

| Property | What it is | Example on a real Windows Firefox |
|---|---|---|
| `navigator.userAgent` | the familiar user agent string | `Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:151.0) ...` |
| `navigator.platform` | a short OS token (`Win32`, `Linux x86_64`, `MacIntel`) | `Win32` |
| `navigator.oscpu` | a longer OS string, and a Firefox-only property | `Windows NT 10.0; Win64; x64` |
| `navigator.appVersion` | a legacy field whose tail repeats the platform | `5.0 (Windows)` |

A real Windows Firefox returns Windows on every one of them, because they are all
generated from the same underlying fact: the operating system the browser is running
on. They agree because they have no way not to.

That is exactly why disagreement is a signal. A detector does not need to know which of
your values is "correct". It only needs two of them to describe different operating
systems, and the session is flagged as inconsistent. Detectors rarely ask whether a
value is unusual; they ask whether two values that should agree, do. The user agent and
`navigator.platform` are one of the cheapest such pairs to check.

## Why a spoofed user agent is not enough

Here is the part that surprises people. In Firefox, the user agent and these three OS
properties are **not** all read from the same source.

`navigator.userAgent` is easy to override: Firefox exposes a preference for it, and any
tool that sets the user agent is setting that string. But `navigator.platform`,
`navigator.oscpu` and `navigator.appVersion` are, by default, derived from the operating
system the browser was **compiled and run on**. Change only the user agent and Firefox
keeps deriving the other three from the real host.

The consequence is precise and it bites automation running in Linux containers. Take a
Linux Firefox, set the user agent to a Windows string, and you get:

```
navigator.userAgent   -> Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:151.0) ...
navigator.platform    -> Linux x86_64      <- the host, not the user agent
navigator.oscpu       -> Linux x86_64      <- the host, not the user agent
navigator.appVersion  -> 5.0 (X11)         <- the host, not the user agent
```

One string says Windows and three say Linux. This is the same class of failure as
[claiming Windows with a Linux font set](playwright-detected-as-bot.md): a value you set
by hand now contradicts values you did not think to set, and the contradiction is what a
detector reads. It is also why
[rotating the user agent alone does not rotate the browser](playwright-user-agent.md) -
the string you changed keeps disagreeing with everything you left honest.

The fix is not another JavaScript override injected into the page, which is itself
detectable when a probe compares a property's descriptor against a clean copy. The fix
is to make Firefox itself report the right OS, before any page script runs.

## navigator.oscpu is a Firefox-only property, and that matters

`navigator.oscpu` deserves its own note, because it is not a property every browser has.

Chromium-family browsers do not expose `navigator.oscpu` at all. Reading it there
returns `undefined`. Firefox is one of the few engines that still exposes it, so its mere
presence tells a script which engine it is talking to before any value is even compared.
This is one more case of [Chromium not being Chrome](chromium-is-not-chrome.md) in
reverse: a property that exists in one engine and is absent in another is an engine
fingerprint on its own.

Two things follow. First, if you are presenting as Firefox, `navigator.oscpu` must be
present and it must name the same OS as everything else, because a real Firefox has it
and a real Firefox agrees with itself. Second, `navigator.oscpu` is one of the strings a
suite like [CreepJS](how-to-test-bot-detection.md) or a public detector reads directly,
so it is not an obscure corner - it is on the list of things that get compared.

An empty or missing `navigator.oscpu` on a browser claiming to be Firefox is the worst of
both worlds: absent where Firefox is present, and therefore a tell that the identity is
constructed rather than real. Presence with the right value is the only pass.

## Measuring it: read all four and check they agree

The test is short. Launch, read the four properties, and confirm they tell one story.
`InvisiblePlaywright` returns a real Playwright `Browser`, so this is ordinary Playwright
code:

```python
from invisible_playwright import InvisiblePlaywright

with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    page.goto("https://example.com")

    ids = page.evaluate("""() => ({
        userAgent:  navigator.userAgent,
        platform:   navigator.platform,
        oscpu:      navigator.oscpu,
        appVersion: navigator.appVersion,
    })""")

    for k, v in ids.items():
        print(f"{k:11} {v}")
```

Against this project's build the four lines describe one operating system:

```
userAgent   Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:151.0) Gecko/20100101 Firefox/151.0
platform    Win32
oscpu       Windows NT 10.0; Win64; x64
appVersion  5.0 (Windows)
```

`navigator.oscpu` is present (so the engine reads as Firefox, not Chromium) and every
line names Windows, on a build that may well be running on Linux in a container.

Turn that into an assertion so a regression cannot pass quietly. Note that this asserts
the **presence** of the right OS, not merely the absence of a Linux string - an empty
value is a failure, not a pass:

```python
from invisible_playwright import InvisiblePlaywright

def check_os_identity(browser):
    page = browser.new_page()
    page.goto("https://example.com")
    ids = page.evaluate("""() => ({
        userAgent:  navigator.userAgent,
        platform:   navigator.platform,
        oscpu:      navigator.oscpu,
        appVersion: navigator.appVersion,
    })""")

    assert ids["platform"] == "Win32", ids["platform"]
    assert "Windows" in ids["oscpu"], ids["oscpu"]
    assert "Windows" in ids["appVersion"], ids["appVersion"]
    assert "Windows" in ids["userAgent"], ids["userAgent"]
    # Firefox-only property must be present, not empty:
    assert ids["oscpu"], "oscpu is empty: reads as non-Firefox or constructed"
    return ids

with InvisiblePlaywright(seed=42) as browser:
    print(check_os_identity(browser))
```

Because the identity is derived from the seed, the same `seed=42` gives the same strings
on every run, so a failure is reproducible rather than a coin toss you have to catch. If
you want to see the leak this prevents, run the same four-line read on a stock Linux
Firefox with only the user agent overridden and diff the two outputs field by field -
`platform`, `oscpu` and `appVersion` will move, and that diff is the whole point.

## How this project pins them, with no C++ patch

The interesting engineering detail is that this one does not need a source patch.

Firefox already ships built-in preferences that override each of these three properties.
The project sets them directly, so the values are decided inside the engine before any
page script runs:

- `general.platform.override` set to `Win32`
- `general.oscpu.override` set to `Windows NT 10.0; Win64; x64`
- `general.appversion.override` set to `5.0 (Windows)`

The user agent itself is derived from the upstream Firefox version, so
`general.useragent.override` stays in step with the real Firefox release the build is
based on rather than being hand-typed - a user agent pinned to some frozen version is a
tell of its own.

Because these are ordinary Firefox `about:config` preferences and not a page-level
monkeypatch, the property values look native to a script inspecting them: there is no
extra getter to notice, no descriptor that differs from a clean copy. The engine reports
Windows because it was told to report Windows, at the layer where a real Windows Firefox
reports it. That is the difference between an OS identity that survives inspection and one
that is painted on top of the page.

You do not set any of this yourself; it is part of what the default identity carries. If
you are curious about which fields are seed-derived and which are fixed, or about
[forcing specific values while leaving the rest seed-derived](pinning.md), that is a
separate topic - here the point is only that the four OS strings are made to agree at the
engine, and that they stay agreed across runs of the same seed.

## Conclusion

The user agent is one of four strings that name your operating system, and in Firefox it
is the only one of the four that is trivial to override. `navigator.platform`,
`navigator.oscpu` and `navigator.appVersion` are derived from the host the browser runs
on, so spoofing the user agent alone leaves three honest properties contradicting the one
you changed. `navigator.oscpu` adds a second edge: it is a Firefox-only property, so its
absence or emptiness is itself an engine tell.

Make all four describe one operating system, at the engine rather than in the page, and
read them back to confirm it. A browser that agrees with itself about what it is running
on has closed a check that costs a detector nothing to run.

## Short answers to the questions that lead here

**Why does navigator.platform say Linux when my user agent says Windows?** Because
Firefox derives `navigator.platform` from the operating system it is running on, not from
the user agent string. Overriding the user agent does not touch it. You have to override
the platform property too.

**Does Chrome have navigator.oscpu?** No. Chromium-family browsers do not expose
`navigator.oscpu` at all; reading it returns `undefined`. It is a Firefox property, so its
presence alone tells a script which engine it is.

**Is setting navigator.platform in JavaScript enough?** It is detectable. A page-level
override changes the value but can leave a descriptor that differs from a clean copy of
the property. The durable fix is to have the engine report the value, before page scripts
run.

**Which properties leak the real OS under a spoofed user agent?**
`navigator.platform`, `navigator.oscpu` and `navigator.appVersion`. All three are
derived from the build host by default, so all three have to be pinned to match the user
agent.

**What should navigator.oscpu return for a Windows Firefox?** `Windows NT 10.0; Win64;
x64`. It must be present (Firefox has it) and it must name the same OS as the user agent,
platform and appVersion.

**Do I need to configure any of this in invisible_playwright?** No. The four OS strings
are pinned to one operating system as part of the default identity, and the same seed
reproduces them on every run.

## Sources

- Firefox exposes built-in preference overrides for the user agent, platform, oscpu and
  appVersion (`general.useragent.override`, `general.platform.override`,
  `general.oscpu.override`, `general.appversion.override`), documented in
  `about:config` and readable back from `navigator`.
- This project's own pref reference, which records that these three OS properties are
  derived from the build-time OS by default and are pinned to Windows values through the
  built-in overrides, with no source patch required.
- Direct measurement with the code on this page, reading the four properties from a real
  Playwright `Browser` and asserting they name one operating system.
- MDN Web Docs,
  [`Navigator.oscpu`](https://developer.mozilla.org/en-US/docs/Web/API/Navigator/oscpu)
  (documented as a non-standard, Firefox-specific property) and
  [`Navigator.platform`](https://developer.mozilla.org/en-US/docs/Web/API/Navigator/platform)
  (documented as deprecated but still widely read for OS detection).

**See also:** [why you should not set the user agent yourself](playwright-user-agent.md),
[Client Hints and the headers that must agree with it](client-hints-sec-fetch.md), and
[the checklist for when one site detects you](playwright-detected-as-bot.md).

---

*Written while maintaining [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level driven by stock Playwright. The user agent is the
string everyone changes; navigator.platform and navigator.oscpu are the two nobody
remembers, and they are the ones that give the container away.*
