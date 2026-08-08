---
title: "Playwright new_page vs new_context: the viewport tell"
description: "Playwright new_page can ship the stock 1280x720 viewport and skip your per-context fingerprint defaults. new_context does not. The measured tell and the fix."
parent: "The Automation Layer"
grand_parent: "Guides"
nav_order: 13
---


# Playwright new_page vs new_context: the viewport tell

**The difference between `new_page` and `new_context` is who owns the context
settings.** `new_context` hands you a fresh, isolated browser profile and lets you
set its viewport, device pixel ratio and colour scheme yourself. `new_page`
creates that context implicitly and takes whatever defaults apply, which for the
viewport means Playwright's stock 1280x720. On a plain browser that costs nothing.
On a browser carrying a seed-derived fingerprint, a viewport nobody chose can
contradict everything around it.

Every Playwright tutorial reaches for `browser.new_page()` first, because it is
one line and it gives you a page. What none of them mention is that the same
convenience decides your viewport for you, and the value it picks is the stock
default: 1280 by 720.

This page is about a specific gap between `new_page` and `new_context`, why it is
easy to miss, what it measured on our own product before we closed it, and how to
check your own setup for the same shape of bug.

## The convenience method that ships a default

`new_page` and `new_context` are not two ways to do the same thing. A context is a
browser profile: its own cookies, its own storage, and its own per-context
settings including `viewport`, `device_scale_factor` and `color_scheme`. A page
lives inside a context. When you call `browser.new_page()`, Playwright creates a
context for you implicitly and opens one page in it.

The catch is which settings that implicit context is born with. If you never pass
a `viewport`, the context takes Playwright's default of 1280x720, and so does the
page. `new_context` gives you the seam to set those values yourself:

| | `browser.new_page()` | `browser.new_context()` |
|---|---|---|
| What it is | Convenience: creates an implicit context and opens one page in it | Explicit: hands you the context, you open pages in it |
| Context settings (`viewport`, `device_scale_factor`, `color_scheme`) | Whatever defaults apply to the implicit context | You set them yourself |
| Isolation (cookies, storage) | Lives in the one implicit context | A fresh isolated profile per call |
| Best for | Quick one-page scripts | Cookies and storage scoped per task |


```python
from invisible_playwright import InvisiblePlaywright

with InvisiblePlaywright(seed=42) as browser:
    # convenience: the implicit context uses whatever defaults apply
    page = browser.new_page()

    # explicit: you own the context and everything on it
    context = browser.new_context()
    page2 = context.new_page()
```

Both return a working page and both load a URL. Nothing errors, nothing warns.
That is exactly why the difference goes unnoticed until a detector notices it for
you.

## The tell: same seed, two different windows

`InvisiblePlaywright` derives a full machine from a seed and installs the parts
that belong to a context, including the viewport, the device pixel ratio and the
preferred colour scheme, as the defaults for every context you open. That is what
makes the fingerprint coherent: a session that claims a certain screen also
reports a window size and a colour scheme that fit it.

We found, and then measured, that `new_page` could slip past those defaults. Same
seed, same session, the two calls side by side:

```
new_page:    innerWidth 1280   screenWidth 1920   dark False
new_context: innerWidth 1906   screenWidth 1920   dark True
```

Read that carefully. The screen width agrees at 1920, because the screen is
delivered a different way and both calls got it. What diverged is precisely the
two values a context owns: the window width and the colour scheme. 1280 is not a
size the seed chose. It is Playwright's stock viewport, arriving because the
implicit context was created without the profile's viewport applied to it.

A 1280-wide window inside a 1920 screen is plausible on its own. What is not
plausible is the *same seed* reporting two different windows depending on which
method the author happened to type. And 1280x720 is a well-known automation
default, so a scoring page that has seen it a million times treats it as a weak
prior toward "this is a driver". You did not choose 1280, and you did not choose
to advertise it.

## Why new_page could slip past the defaults

Playwright's public `Browser.new_page` does not create the page itself. It forwards
to an internal implementation object, and that implementation's own `new_page`
calls *its own* `new_context` to build the implicit context. So if a wrapper
installs its defaults by intercepting `new_context` on the public browser object,
the interception is simply on the wrong object: the implicit context is built one
layer down, by a `new_context` the wrapper never wrapped. `browser.new_context()`
called directly hits the wrapped method and gets the defaults; `browser.new_page()`
routes around it and gets Playwright's.

This is worth understanding as a mechanism, not a one-off bug, because the same
trap exists in any wrapper over Playwright, not just this one. Any layer that
customises contexts has to cover *both* entry points, because `new_page` is not
sugar over the public `new_context` you can see. It has its own path to a context,
and a wrapper that only knows about one of the two paths will be right half the
time and quietly wrong the other half, depending on which call the caller reached
for first.

## The fix, and what it changes for your code

Both entry points now apply the same per-session defaults, in both the sync and
async APIs, from one shared source so they cannot drift apart. `new_page` and
`new_context` give the same seed the same window and the same colour scheme.

```python
from invisible_playwright import InvisiblePlaywright

with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    same_width = page.evaluate("() => window.innerWidth")

    ctx = browser.new_context()
    other = ctx.new_page()
    other_width = other.evaluate("() => window.innerWidth")

    assert same_width == other_width   # both carry the seed's viewport now
```

The important practical consequence: you do not have to abandon `new_page`. The
convenience method is the one in the quickstart and the class docstring, and it
stays the convenience method. It just no longer ships a viewport nobody chose. If
you were already reaching for `new_context` because you wanted per-context
isolation, cookies scoped to a task, storage that does not bleed between jobs,
keep doing that for those reasons. Choose between them on isolation, not on
whether the fingerprint comes out right, because now it comes out right either
way.

## How to check your own setup for the same shape of bug

Whatever tool you use, the check is the same three lines and it takes a session,
not a test suite. Open a page each way, read the value each way, compare:

```python
from invisible_playwright import InvisiblePlaywright

with InvisiblePlaywright(seed=7) as browser:
    a = browser.new_page()
    b = browser.new_context().new_page()

    for label, page in (("new_page", a), ("new_context", b)):
        w = page.evaluate("() => window.innerWidth")
        dark = page.evaluate(
            "() => matchMedia('(prefers-color-scheme: dark)').matches"
        )
        print(f"{label:12} innerWidth {w}  dark {dark}")
```

Two rules make this a real check rather than a green light:

- **Pin the seed.** With a random identity every run, a run that agrees tells you
  nothing, because the two calls might have landed on the same value by luck. A
  fixed seed makes the comparison reproducible. This is the same reason a fixed
  identity is [the debugging habit that saves the most time](playwright-detected-as-bot.md):
  a difference you can reproduce is a difference you can fix.

- **Do not test against a value that is true by accident.** If your host is a
  1280-wide light-themed machine, both calls will report 1280 and light, and a
  broken wrapper passes. Pick a seed whose profile is dark, or run the comparison
  where the host default cannot happen to match, so a real divergence has to show.
  A signal that is present only because the environment agreed with the bug is
  [the false pass that testing by verdict produces](how-to-test-bot-detection.md).

The general principle is the one that runs through all of these notes: assert that
the right value is present, do not assert that a wrong value is absent, and make
the comparison against something that cannot be true for the wrong reason. The
viewport is one field; [the screen dimensions a headless browser reports](screen-size-headless-tells.md)
are another surface where a stock default reads as a datacenter, and the same
compare-do-not-assume method finds both.

## Conclusion

`new_page` is a convenience, and conveniences make choices for you. The choice
this one makes is a viewport, and the default it reaches for is a value automation
tools are known by. On a plain browser that costs nothing. On a browser carrying a
seed-derived fingerprint it is an internal contradiction: the same identity
reporting two different windows depending on a method name.

The gap is closed here, both entry points apply the same per-session defaults, and
you can keep writing the one-line `new_page` from the quickstart. But the shape of
the bug outlives the fix. Any layer over Playwright that customises contexts has
two doors to cover, not one, and the only way to know it covered both is to open a
page each way, with a pinned seed, and read the values back.

## Short answers to the questions that lead here

**What is the difference between new_page and new_context in Playwright?** A
context is an isolated browser profile with its own cookies, storage and
per-context settings like viewport and colour scheme; a page lives inside one.
`new_page` creates an implicit context for you and opens a page in it, so the
context settings are whatever defaults apply. `new_context` hands you the context
so you set those yourself.

**Why does new_page use a 1280x720 viewport?** Because 1280x720 is Playwright's
built-in default, and unless something sets a viewport on the implicit context
that context is born with the default. It is not a value your identity chose.

**Does new_page bypass my viewport settings?** It can, in any wrapper that only
customises the public `new_context`, because `new_page` builds its context through
an internal path. In this package both paths apply the same defaults now, so it
does not.

**Should I always use new_context instead of new_page?** No. Choose between them
on isolation: use `new_context` when you want cookies and storage scoped per task.
For the fingerprint they now behave the same, so `new_page` stays the convenient
default.

**How do I test whether my automation ships a default viewport?** Open a page with
each method under a pinned seed, read `window.innerWidth` back from both, and
compare. Pick a seed whose profile differs from your host default so a match
cannot happen by accident.

**Is a 1280 viewport enough to get flagged on its own?** Rarely on its own, but it
is a well-worn automation default and it feeds a scoring model as a weak signal.
The real problem is inconsistency: the same seed reporting two windows is a
contradiction a single value never is.

## Sources

- Playwright's own [`Browser` API reference](https://playwright.dev/python/docs/api/class-browser)
  for what a context owns and how an implicit context is created: it states plainly
  that `new_page` "creates a new page in a new browser context", read from the
  upstream reference rather than inferred.
- This project's release notes for the entry that measured the 1280-against-1906
  divergence, and the fix that wraps both entry points from one shared set of
  defaults.

**See also:** [the checklist for when automation is detected on one site](playwright-detected-as-bot.md),
[the screen and viewport tells a headless browser leaks](screen-size-headless-tells.md),
and [pinning specific fingerprint fields](pinning.md) when you want to fix a value
rather than let the seed choose it.

---

*Written while maintaining [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level driven by stock Playwright. This one was a
mistake in our own wrapper, found by reading Playwright's source and then measured
before it was believed.*
