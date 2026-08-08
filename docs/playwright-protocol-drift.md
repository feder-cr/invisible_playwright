---
title: "Why a Playwright upgrade broke 97 of 133 tests overnight"
description: "A Playwright client upgrade added one undeclared field and broke 97 of 133 tests while the browser launched fine. What protocol drift is and how to catch it."
parent: "The Automation Layer"
grand_parent: "Guides"
nav_order: 6
---


# Why a Playwright upgrade broke 97 of 133 tests overnight

A Playwright upgrade breaks a custom Firefox driver when the newer client sends a
protocol field the browser's automation server never declared. The server validates
each command against a closed-world schema, so it rejects the whole call rather than
ignoring the unknown field, and the browser still launches, still loads pages, and one
specific API quietly stops working. That mismatch is protocol drift, and in one real
case it took down 97 of 133 end-to-end tests in a single upgrade.

Playwright does not talk to Firefox through the DevTools Protocol the way it does with
Chromium. It talks through Juggler, an internal automation protocol built for Firefox,
and the contract between the two sides is stricter than most people driving a browser
ever need to know about. This page is what that contract is, how a routine client
upgrade broke a large slice of a real test suite in one step, and the gate that now
catches this class of break automatically.

## The wire protocol is closed-world, not best-effort

Juggler's command schema is validated by a function that rejects, at the moment the
call is made, any payload field it was not told to expect. Not a warning, not an
ignored extra key: the specific command fails.

That single property is what makes this kind of break so easy to miss. The browser
still compiles. It still launches. It still loads pages and passes a basic smoke test,
because a smoke test rarely exercises every command in the protocol. Only the specific
API that carries the new, undeclared field breaks, and only when something actually
calls it.

## What one Playwright release actually changed

A client upgrade added new fields to two existing viewport-related commands. Nothing
about the command names changed, nothing in a changelog flagged it as a breaking
change for a third-party server, because from Playwright's own point of view it was
an addition, not a break. This driver deliberately runs
[stock, unmodified Playwright against a patched Firefox binary](stock-playwright-patched-binary.md),
which keeps upgrades cheap but also means an upstream field addition arrives
unannounced on the server that has to accept it.

The first of the two fields was found by hand, the ordinary way: something failed,
someone read the rejected payload, added the missing declaration, moved on. That felt
like the fix. It was not.

The second field took out a much larger share of a real end-to-end suite in one run,
all failing from the same single rejection, because every test in that suite builds a
browser context, and building a context is exactly the code path that sends the newly
added field. A fix that handles the field someone happened to notice, without checking
whether the client sends others, is a fix for the symptom that got reported, not the
category of problem.

## Why a basic launch test does not catch this

The field that caused the bulk of the failures is only sent when a context explicitly
sets a screen size. A bare "launch a browser, open a new context, confirm it works"
probe never sets one, so that exact check passes clean while every test in the suite
that does set a screen size fails. The gap between the two is the entire lesson: a
green result from a shallow probe and a green result from real usage are not the same
claim, and [assuming they are is how gates miss things they were built to catch](how-to-test-bot-detection.md).

## The decision that mattered more than the fix: declare, don't necessarily honour

Once a field is known, there are two ways to stop rejecting it: accept the value and
use it, or accept the value and ignore it. These are not interchangeable, and picking
the wrong one trades one bug for a worse one.

The two commands in question only ever read the original fields their handlers already
expected; the new ones were declared and explicitly discarded. That was deliberate,
not laziness. One of the new fields carried a screen dimension, and screen dimensions
in a fingerprinted browser are owned by the identity the session was built with, not by
whatever a client happens to request. Honouring a client-supplied screen size would let
a caller silently overwrite a value that has to
[stay internally consistent with everything else that value implies](pinning.md) - the
same principle that makes pinning one field without its correlated neighbours produce
an identity no real machine has. Accepting the field kept the client from erroring.
Ignoring its value kept the identity from contradicting itself.

## The gate: checking both directions, and proving it catches a real break

The fix for one release does not prevent the next one from doing the same thing again,
so the real fix is a gate that diffs the client's actual wire behaviour against the
server's declared schema before either side ships. That means recovering the exact set
of fields a specific client version sends and the exact set of events it subscribes to,
then checking both directions: commands the client can send that the server never
declared, and events the server might emit that the client never subscribed to. The
second direction fails silently in a different way - the client does not error, it
simply waits for an event that is never coming until it times out, which reads like a
hang rather than a protocol mismatch.

A gate like this is only worth anything once it has been shown to fail on purpose.
Remove a real declaration and it has to flag exactly that field; remove a subscribed
event and it has to flag that too. [A check that has only ever printed a pass is not a
gate](how-to-test-bot-detection.md), it is an assumption wearing a green light, and the
way to tell the difference is to break the thing on purpose once and watch the gate
notice.

## Conclusion

A closed-world protocol makes a browser driver's contract explicit, which is a genuine
strength: nothing gets silently misinterpreted. The cost is that the contract has to be
re-checked every time the other side changes, because "the browser still launches" and
"every command the client can send still works" are different claims, and only one of
them is what a real user of the browser actually needs. The fix that generalises is not
patching the one field someone noticed, it is a gate that recovers the client's actual
behaviour and diffs it against what the server declares, proven against a break planted
on purpose before it is trusted against a real one.

## Short answers to the questions that lead here

**Why did my custom Playwright server start failing after a client upgrade?** The
client likely added a field to a command your server's schema does not declare, and a
closed-world protocol rejects the whole call rather than ignoring the extra field.

**Why did a smoke test pass while real tests failed?** The new field is often only sent
under a specific condition, like a context that sets a non-default value. A shallow
probe that never triggers that condition never sends the field either.

**Should a custom server always honour every field a client sends?** No. If a field
would let the client override a value your server owns for a reason - like a fingerprint
identity's internal consistency - declaring it and ignoring it is often the correct fix,
not accepting whatever the client provides.

**How do I catch this before my users do?** Diff the client's actual wire traffic
against your server's declared schema, in both directions, and validate the diff tool
itself by removing a real declaration and confirming it gets flagged.

**See also:** [invisible_playwright vs Patchright](vs-patchright.md), for the other side
of the driver-versus-engine question this protocol sits underneath;
[how stock Playwright connects to a patched Firefox binary](stock-playwright-patched-binary.md),
for why the driver stays unmodified across upgrades;
[when Firefox launches but Playwright still cannot drive it](juggler-missing-packaged-build.md),
another green-launch failure that a smoke test misses; and
[how to test whether your setup is actually working](how-to-test-bot-detection.md), for
the same shallow-probe-versus-real-usage gap in a different context.

## Sources

- This project's own Juggler protocol contract documentation, including the two
  affected commands, the fields involved, the fraction of the test suite the second
  field took down, and the validated gate that checks both directions.

---

*From the notes of [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level and driven through a protocol that does not forgive
an undeclared field.*
