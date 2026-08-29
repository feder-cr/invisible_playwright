---
title: "Browser trust scores explained: what the number means"
description: "CreepJS trust, FingerprintJS confidence and reCAPTCHA v3 score measure different things: what each trust score means and why one green does not imply the rest."
parent: "Detectors, Explained"
grand_parent: "Guides"
nav_order: 8
---


# Browser trust scores explained: what the number means

CreepJS trust, FingerprintJS confidence and a reCAPTCHA v3 score all look like the
same kind of number, and they are not: one counts contradictions, one measures how
sure a match is, and one estimates behavioural risk. A high number from one says
almost nothing about what the next one will read, and treating them as interchangeable
is the single most common misreading behind "I pass one test and fail the next".

This page separates the three numbers people conflate, says what each one is actually
counting, and shows why passing one implies nothing about the others. Then it shows
how to read all three from one reproducible session so you can stop guessing which
number moved.

## Three numbers, three different questions

The three numbers answer three unrelated questions: CreepJS trust asks whether your
values contradict each other, a FingerprintJS visitor ID asks whether you are the same
visitor as last time, and a reCAPTCHA v3 score asks how risky your behaviour and history
look. Line them up and the confusion evaporates:

- **CreepJS trust** is a consistency and lie count. It is high when nothing you report
  contradicts anything else you report and nothing looks tampered with. It is not a
  rarity score, and being unusual does not lower it.
- **A FingerprintJS visitor ID** is a hash, and the thing next to it labelled
  **confidence** is a separate number that says how sure the library is that this ID
  belongs to the same visitor as before. It is about linkability, not about looking
  automated.
- **A reCAPTCHA v3 score** is a risk estimate between 0.0 and 1.0, built mostly from
  behaviour and reputation rather than from what the DOM reports at all.

| Number | The question it answers | What it reads | What moves it |
|---|---|---|---|
| CreepJS trust | Do my reported values contradict each other? | Descriptors, prototypes, stack traces, canvas and font surfaces | A patched or blocked probe, or two fields that disagree |
| FingerprintJS visitor ID (+ confidence) | Am I the same visitor as before? | A hash of the browser's components, in a fixed order | Any one component changing changes the whole ID |
| reCAPTCHA v3 score | How risky do my behaviour and history look? | Interaction, cookies, storage and exit reputation, not the DOM | A warmed-up profile and realistic behaviour, not a cleaner fingerprint |

Different inputs, different outputs, different failure modes. The rest of this page is
one section per number, then why they do not move together.

## CreepJS trust: a lie count, not a rarity score

[CreepJS records a blocked probe as a lie](creepjs-explained.md) and walks
descriptors, prototypes and stack traces looking for values that were patched after
the fact. Its trust figure goes up when the surfaces agree with each other and down
when one of them looks edited or refuses to answer.

The part people get wrong: rarity does not enter into it. An identity that no other
machine on earth shares can score perfectly, as long as every field is internally
coherent. Conversely, the most common machine in the world scores badly the moment two
of its fields disagree, because a lie is a contradiction and a contradiction is what
this tool counts.

So the way to raise this number is not to look average. It is to be consistent: one
platform reported everywhere, a canvas that hashes the same twice in a row, a font
list that belongs to the operating system you claim. That is a property of how the
identity is generated, not a value you can paste in.

## The FingerprintJS visitor ID and its separate confidence

[A FingerprintJS visitor ID is a hash of its components](fingerprintjs-visitor-id.md),
around forty-one of them, serialised in a fixed order. There is no partial match:
change one component and the whole ID changes as if everything had. That is why the
same identity gives you the same ID and a different identity gives you a completely
different one, with nothing in between.

The `confidence` sitting beside the ID is a different number entirely. It does not tell
you whether you look automated. It tells you how reliable the library thinks this
particular match is, based on how stable the components it collected tend to be. You
can have a low-confidence ID on a perfectly clean browser and a high-confidence ID on a
browser that is obviously a bot. The two numbers are answering "who is this" and "how
sure am I", and neither is answering "is this a robot".

If your goal is to be recognised again, you want this ID stable. If your goal is to not
be recognised, you want it to change. Either way, it is orthogonal to the trust number
above.

## The reCAPTCHA v3 score: behaviour and history, not the DOM

reCAPTCHA v3 hands the site a number between 0.0 and 1.0, and
[a fresh browser tends to score low on reCAPTCHA v3](recaptcha-v3-score.md) even when
every fingerprint check it faces comes back clean. The reason is that this score is
largely about history and behaviour, and a brand new profile has neither.

An empty profile is a browser that has never been anywhere: no cookies, no consent
decisions, no storage, no prior interaction. There are far more automated sessions that
look like that than human ones, and the score reflects that base rate rather than any
DOM property you did or did not spoof. Perfecting your canvas hash does not move a
number that was never reading your canvas.

This is the honest caveat the other two numbers do not carry: a stable, consistent,
non-lying fingerprint does nothing for this one. It does not manufacture the past you
do not have. That part is warmed-up profiles, realistic behaviour and a reputable exit,
none of which a fingerprint layer can supply.

## Why passing one says nothing about the others

The three numbers are independent, so a high score on one predicts nothing about the
other two. Put them side by side and the independence is obvious:

- You can score high on CreepJS trust and low on reCAPTCHA v3, because a perfectly
  consistent browser with no history is exactly what a consistent bot also looks like.
- You can have a stable, high-confidence FingerprintJS ID and still fail CreepJS,
  because a stable ID only means your components did not move, not that they are
  telling the truth.
- You can pass reCAPTCHA v3 on a warmed profile and fail CreepJS, because behavioural
  reputation says nothing about whether a descriptor was patched.

They cross-cut. Each of the [five detector deep-dives in this set](how-to-test-bot-detection.md)
measures a different axis, and a green on one axis is not a green on the others. The
practical rule is to name which number you are looking at before you react to it,
because the fix for a low CreepJS trust (consistency) is not the fix for a low
reCAPTCHA v3 score (history), and applying one to the other wastes days.

## A blocked probe lowers the score, it does not hide from it

One instinct makes two of the three numbers worse at once: suppressing a signal.
Blocking canvas, returning an empty font list, refusing WebRTC, randomising a value per
call so nothing can pin it. reCAPTCHA v3 does not read any of those surfaces, so there
is nothing here for it to punish.

Every one of those is counted against you, not for you:

- CreepJS records a blocked probe as a lie by name, so blocking lowers trust directly.
- A value that changes on every read is a tampering tell, and a per-call-changing
  canvas or audio hash reads as randomisation rather than a real device.
- A missing signal is still a signal. An empty result where a real browser has data is
  itself the anomaly.

The consistency that raises a trust number is the opposite of hiding. This is why a
seed-derived identity, where every surface is drawn from one seed and stays put, scores
better than any suppression strategy: it answers every probe with a coherent value
instead of a refusal.

## Measure two of the three from one seeded session

The way to stop guessing which number moved is to make the identity reproducible, so a
change in a score is a change you made rather than a change in the draw. The wrapper is
stock Playwright with a seeded fingerprint, so the `browser` you get back is a real
Playwright [`Browser`](https://playwright.dev/python/docs/api/class-browser) and every method is the one you already know:

```python
from invisible_playwright import InvisiblePlaywright

def read_canvas(page):
    return page.evaluate("""() => {
        const c = document.createElement('canvas');
        const ctx = c.getContext('2d');
        ctx.textBaseline = 'top';
        ctx.font = '14px Arial';
        ctx.fillText('trust-score-probe', 2, 2);
        return c.toDataURL();
    }""")

sf = InvisiblePlaywright(seed=42)
with sf as browser:
    page = browser.new_page()
    page.goto("https://example.com")
    first = read_canvas(page)
    second = read_canvas(page)
    assert first == second   # stable within a session, so not a per-call tell
```

Reading the same value twice in one session is the cheapest consistency check there
is: if `first` and `second` disagree, something is randomising per call, which is the
exact tampering pattern that costs you CreepJS trust. Here they match, which is the
behaviour you want.

Now confirm it is stable across sessions too, which is what keeps a FingerprintJS ID
where you put it, and what a consistency check rewards:

```python
def canvas_for_seed(seed):
    with InvisiblePlaywright(seed=seed) as browser:
        page = browser.new_page()
        page.goto("https://example.com")
        return read_canvas(page)

a = canvas_for_seed(42)
b = canvas_for_seed(42)   # same seed, relaunched
c = canvas_for_seed(7)    # a different identity

assert a == b   # same seed reproduces the same machine
assert a != c   # a different seed is a different, still-consistent machine
```

Measured this way, the same seed reproduces byte-identical canvas output run after run,
and a different seed produces a different value that is itself internally consistent.
That reproducibility is what lets you attribute a score change to the one thing you
changed. If you have not passed a seed, log `sf.seed` from the instance first so a run
that scored interestingly can be replayed exactly.

The honest boundary, again: this makes the CreepJS-style consistency axis and the
FingerprintJS linkability axis controllable and repeatable. It does not warm up a
profile, and the reCAPTCHA v3 number will still start low on a session with no past no
matter how clean the fingerprint is.

## Conclusion

Three numbers, three axes. CreepJS trust counts contradictions and lies, a
FingerprintJS ID and its confidence measure whether you are the same visitor as before,
and a reCAPTCHA v3 score estimates risk from behaviour and history. Passing one is not
passing another, suppressing a signal lowers the two that read the DOM rather than
hiding from either, and the only way to tell which number your last change actually
moved is to hold the identity still with a seed and compare. Name the axis before you
react to the number, and the contradictory results stop being contradictory.

## Short answers to the questions that lead here

**Is a CreepJS trust score the same as a reCAPTCHA score?** No. CreepJS trust counts
internal contradictions and tampering; a reCAPTCHA v3 score is a 0.0 to 1.0 risk
estimate built mostly from behaviour and history. A high one does not predict the
other.

**What does the FingerprintJS confidence number mean?** How sure the library is that
this visitor ID matches the same visitor as before. It is about the reliability of the
match, not about whether the browser looks automated.

**I pass CreepJS but still get a low reCAPTCHA score, why?** Because reCAPTCHA v3 is
mostly reading history and behaviour, and a fresh profile has neither. A clean
fingerprint does not supply a past, and past is what this score is missing.

**Does a rare fingerprint lower my trust score?** No. CreepJS trust is about
consistency, not rarity. An unusual but coherent identity can score perfectly; a common
but contradictory one scores badly.

**Does blocking canvas or fonts raise my score?** It lowers it. A blocked probe is
recorded as a lie, and a value that changes per read is a tampering tell. Answering
every probe with a coherent value scores better than refusing.

**Which score should I optimise for?** The one your target actually uses. Optimise
consistency for a CreepJS-style check, a warmed profile and realistic behaviour for a
reCAPTCHA-style score, and decide whether you want the FingerprintJS ID to stay put or
change.

## Sources

- The three detectors named above, each with its own page in this set, read from their
  own source rather than from a rendered verdict: [CreepJS](https://github.com/abrahamjuliot/creepjs)
  on tampering, [FingerprintJS](https://github.com/fingerprintjs/fingerprintjs) on
  the hashed visitor ID and its confidence, and [reCAPTCHA v3](https://developers.google.com/recaptcha/docs/v3)
  on the behavioural score, retrieved 2026-08-28.
- This project's own reproducibility measurements: identical seed reproduces
  byte-identical canvas output within and across sessions, which is the property a
  consistency check rewards.

**See also:** [how CreepJS decides you are lying](creepjs-explained.md) for the
consistency axis, [why a fresh browser scores low on reCAPTCHA v3](recaptcha-v3-score.md)
for the history axis, and [how to test whether your browser is detected](how-to-test-bot-detection.md)
for the compare-against-a-stock-browser method that keeps these numbers honest.

---

*Written while maintaining [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level driven by stock Playwright. The three numbers on this
page get conflated because they all look like a percentage; none of them are the same
percentage.*
