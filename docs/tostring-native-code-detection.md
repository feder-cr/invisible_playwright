---
title: "Function.prototype.toString and the [native code] check"
description: "Every JavaScript override is a function, and every function carries its own source. That single fact is the ceiling on page-level stealth, and it explains a whole category of detection rather than one check."
parent: "The Automation Layer"
grand_parent: "Guides"
nav_order: 1
---


# Function.prototype.toString and the [native code] check

Every override you write in JavaScript is a function, and every function carries its
source. That single fact is why page-level stealth has a ceiling, and it is worth
understanding in detail because it explains a whole category of detection rather than
one check.

## The check

```js
Object.getOwnPropertyDescriptor(Navigator.prototype, 'webdriver').get.toString();
// real:  "function get webdriver() { [native code] }"
// yours: "() => false"
```

A native function has no JavaScript source, so the engine prints a placeholder. Replace
it with your own and `toString` prints what you typed, because that is what the function
is.

This is one line, it needs no library, and it works on any property you have redefined.

## Then you patch toString, and that is the trap

The obvious response is to make `toString` lie:

```js
const orig = Function.prototype.toString;
Function.prototype.toString = function () {
  if (this === myFakeGetter) return 'function get webdriver() { [native code] }';
  return orig.call(this, ...arguments);
};
```

Now `Function.prototype.toString` is itself a JavaScript function, and calling
`toString` on *it* prints your source. So you special-case that too, and the next
question is what happens when someone gets a clean copy of `toString` from a fresh
iframe and calls it on your getter with the original implementation.

That is the shape of the whole game: each layer of the disguise is a new object with the
same problem, and the detector only has to find one.

## The four ways this is actually checked

Reading a real lie-detection implementation is more instructive than any list, and the
techniques cluster into four:

**A pristine reference.** Create an iframe, reach into its `contentWindow`, and take a
clean copy of the built-ins from a context your patch never ran in. Then compare. About
fifteen lines, and it defeats page-level patching generally rather than any specific
patch.

**Stack-trace inspection.** Throw inside a call and look at the frames. A native call
and a call that passes through your replacement leave different stacks, and a check can
match on the presence of `at Function.toString` or `at Object.toString` where it does
not belong. The disguise is found by how the disguise behaves, not by what it says.

**Descriptor and prototype walking.** For each API, take
`Object.getOwnPropertyDescriptor(proto, name)`, check whether the value is unexpectedly
`undefined`, pull out the getter, and compare `Object.getPrototypeOf(fn)` against the
native prototype. An override moved from the prototype to the instance shows up here
without any cleverness at all.

**Recording the blocking.** If your environment prevents the reference iframe from being
created, that prevention is written down as a finding. Suppressing the probe is not
neutral; it is a distinct, nameable event.

[The page on CreepJS](creepjs-explained.md) goes through these against a real
implementation.

## Why this argues for the engine, and what that does not fix

If the value is decided below the JavaScript layer, there is no override. The getter is
the engine's own native getter, so `toString` prints `[native code]` because it is
native code. The prototype chain is untouched. A pristine copy from a fresh iframe
returns the same answer, because both contexts ask the same compiled code. The stack has
no extra frame because there is no extra call.

The lie detector finds nothing, for the straightforward reason that nothing lied.

The cost is real and worth stating: you are maintaining a browser build against
upstream, the binary is large, and you are tied to the engine you patched. Anyone
presenting that as free has not done it.

And it fixes exactly one category. The plausibility of your GPU, whether your font set
matches your claimed platform, whether your timezone agrees with your address: all
unchanged, all still as hard as they were.
[The three levels page](playwright-stealth-levels.md) is the longer version of that
argument, and [this is what it looks like against a specific page-level tool](vs-playwright-stealth.md)
rather than in the abstract.

## Checking your own

```js
const props = ['webdriver', 'plugins', 'languages', 'hardwareConcurrency', 'userAgent'];
for (const p of props) {
  const d = Object.getOwnPropertyDescriptor(Navigator.prototype, p);
  console.log(p,
    'own:', Object.getOwnPropertyNames(navigator).includes(p),
    'native:', d && d.get ? /\[native code\]/.test(d.get.toString()) : 'n/a');
}
```

What you want:

- `own` is `false` for all of them. These live on the prototype, and finding one on the
  instance means something redefined it there.
- `native` is `true` for every getter that exists.
- The same script run inside a fresh cross-origin iframe gives the same answers.

If the third one disagrees with the first two, you have found the gap the pristine-copy
technique exists to find.

## Short answers to the questions that lead here

**How do sites detect `Function.prototype.toString` patching?** By taking a clean copy
of `toString` from a context your patch did not run in, and calling it on your function.

**Does `toString` spoofing work?** Against a check that calls your `toString`, yes.
Against one that brings its own, no.

**Why does `[native code]` matter?** It is the difference between a value the browser
computes and a value something replaced. It is one string comparison.

**Is `navigator.webdriver` the only property this applies to?** No, it applies to
anything you redefine. It is the famous example because it is the one people patch
first.

**Can I avoid it by patching earlier?** Timing helps against a page reading the value
before your script runs, and it does nothing here. The inspection happens whenever the
detector likes, and your override is still an override.

**Does an engine-level build make me undetectable?** No. It removes this category. The
machine you are running on still answers for itself.

**See also:** [navigator.webdriver is not the tell you think it is](navigator-webdriver-explained.md),
which is the same argument from the most famous property, and
[the ChromeDriver cdc_ variable](cdc-variable-explained.md), which is the same argument
about artefacts you did not choose to add.

---

*From the notes of [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level. Everything above is the reason for that choice, and
the paragraph about what it does not fix is the reason it is not a complete answer.*
