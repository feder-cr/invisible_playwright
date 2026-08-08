---
title: "Playwright TargetClosedError: the causes and the fixes"
description: "Playwright TargetClosedError is usually not a timeout. Three specific Firefox and Juggler causes, their verbatim symptoms, and how to tell them apart."
parent: "Testing and Troubleshooting"
grand_parent: "Guides"
nav_order: 7
---


# Playwright TargetClosedError: the causes and the fixes

`TargetClosedError` means the thing Playwright was talking to went away. The client
had a live connection to a browser or a page, the other end vanished, and the next
call you made threw. That is all the error itself tells you, which is why the top
answer everywhere is "increase your timeout" and why that answer is almost always
wrong.

A timeout is a call that waited too long. `TargetClosedError` is a call that had
nothing left to wait for. Raising the timeout on a closed target just makes you wait
longer for the same exception. The useful question is not "how long should I wait" but
"why did the target close", and with Firefox under Playwright there are three specific
reasons we have actually hit, each with its own verbatim symptom. This page is how to
read the symptom instead of guessing.

## Why "increase the timeout" is the wrong fix

The three causes below produce the same final exception and completely different logs
on the way to it. If you only read the last line, they are indistinguishable and you
will treat all three as flakiness. If you read the lines above it, they separate
cleanly:

- One never lets the browser connect at all. The failure is at launch.
- One connects fine, drives fine, and dies the instant you send a particular command.
- One runs a whole session and dies mid-navigation, after a real page was open.

So the first move is not a config change. It is to turn on the transport log and read
what the browser said before it closed:

```bash
# Playwright prints every protocol message and the launch handshake to stderr.
DEBUG=pw:browser,pw:protocol python your_script.py 2> pw.log
```

Only one nearby problem is genuinely about time, and it is not this one: a launch that is
intermittently slow but eventually connects. Even there a raised per-request timeout is the
wrong lever, as [a launch that was slow one run in six](slow-browser-launch-timeout-budget.md)
shows. `TargetClosedError` is the opposite case: the target is already gone, so waiting
longer only postpones the same exception.

The three sections that follow tell you which of the three logs you are looking at.

## Cause 1: the automation layer is missing from the build

**Verbatim symptom:**

```
console.error: "unrecognized command line flag" "-juggler-pipe"
Error: Failed to load chrome://juggler/content/components/Juggler.js
playwright._impl._errors.TargetClosedError: Target page, context or browser has been closed
```

The browser process starts, prints those two lines, and exits before the client ever
completes the handshake. The tell is `chrome://juggler/...` failing to load and the
`-juggler-pipe` flag being called unrecognized: the binary launched, but the code that
speaks Playwright's Firefox protocol is not inside it, so the flag that turns that
protocol on is a flag the binary has never heard of.

We shipped a build in exactly this state. Firefox builds its automation layer as a set
of loose files under `chrome/juggler/`, gated on the standard WebDriver build flag,
which is on by default. A packaging step then assembles the distributable from a
manifest, and that manifest listed the automation layer's sibling component but not the
automation layer itself. The result: the browser ran standalone, `--screenshot` worked,
`--version` worked, every manual smoke test was green, and the one thing missing was the
one file Playwright needs. The development tree kept the loose files, so local runs drove
it perfectly; only the packaged release was empty. It is documented in full as
[the packaged build that shipped without its automation layer](juggler-missing-packaged-build.md),
and the lesson that outlived it is that a launch-and-screenshot check proves the browser
renders, not that it can be driven.

**How to confirm it:** launch the browser by hand with `-juggler-pipe` and watch for the
`chrome://juggler` load error, or list the automation files inside the package. If they
are absent, no client setting will help; the binary is the problem. Our fix was to add
the automation layer to the packaging manifest, and to add a release gate that actually
drives every built binary through Playwright before it can publish, because that is the
only check that exercises this path.

## Cause 2: the browser rejects a field it was sent

**Verbatim symptom:** launch succeeds, the first pages work, and then a specific call,
often the one that sets a viewport or creates a context with a screen size, closes the
target. The transport log shows the command going out and the connection dropping on the
response, rather than any launch error. Nothing is wrong with the flags or the binary.

Firefox's Playwright protocol is validated closed-world: every command payload is checked
against a declared schema, and a payload carrying any field the schema does not declare is
rejected outright, at runtime, with the browser otherwise perfectly healthy. When the
client and the browser drift apart on the exact fields a command carries, the client sends
a field the browser has never been told to expect, the browser rejects the whole command,
and the client sees the target go away.

This is not hypothetical and the blast radius is larger than it looks. A newer Playwright
release added extra fields to two viewport commands. One of them, an added `screenSize`
field, was undeclared on our side. It was sent only when a context set a screen size,
which is why a bare context probe passed and hid it, and it took out **97 of 133**
end-to-end tests in one stroke, every one of them from that single rejected command,
because every test builds a context. The build was green. The browser launched. The smoke
test passed. One undeclared field did the rest. The mechanism and the gate that now
catches it are written up under [protocol drift](playwright-protocol-drift.md).

**How to confirm it:** in the `pw:protocol` log, find the last command sent before the
close and look at its fields. If the drop happens on a specific command with a specific
payload and never on launch, this is protocol drift, and the fix is version alignment
between the client and the browser, not a timeout. Pin the Playwright version your browser
build actually supports; a client newer than the browser's declared schema is the usual
trigger.

## Cause 3: a content process crashes mid-session

**Verbatim symptom:** the session runs, a real page loads, and then, typically during a
navigation that moves to a new origin, this fires:

```python
page.on("crash", lambda page: print("content process gone:", page.url))
```

The [`crash` event](https://playwright.dev/python/docs/api/class-page#page-event-crash) is
the honest signal here: Playwright's own docs define it as what fires when a page's process
dies, for instance from over-allocating memory. `TargetClosedError` is only what you get on
the *next* call you make to that dead page; the crash came first and the transport log
shows the content process actor vanishing, not a protocol rejection and not a launch
error. If you were not listening for `crash`, all you see is the downstream
`TargetClosedError` and it looks like a random close.

We hit this on Windows in headless mode, on sites that do heavy cross-process navigation.
The interaction was between the way the browser was hidden and the way the OS sandbox
isolates content processes: a new content process spawned during a cross-origin navigation
could not reparent itself, exited cleanly, and Playwright reported the page as crashed. It
is a real Firefox-under-automation failure mode rather than a stealth choice, and the point
worth keeping is diagnostic: `page.on('crash')` firing mid-session is a different animal
from a navigation that merely races, which surfaces as
["Execution context was destroyed"](execution-context-destroyed.md) and is usually benign.
One is a dead process; the other is a live page that moved. Our builds handle the crash
case, and the general defensive habit for any Playwright-driven Firefox is to attach a
`crash` listener so a real crash never masquerades as a mystery close.

## How to tell the three apart

Read the log from the top, not the bottom. The distinguishing line is always above the
`TargetClosedError`, never in it.

| Signal in the log | Cause | Where it lives | Fix direction |
|---|---|---|---|
| `Failed to load chrome://juggler/...`, `-juggler-pipe` unrecognized, dies at launch | Automation layer absent from the build | The binary | Use a build whose package includes the automation layer |
| Launch and first pages fine, drop on one specific command's response | A protocol field the browser does not recognize | Client/browser version drift | Align the Playwright version with the browser |
| `page.on('crash')` fired first, mid-session, around a navigation | A content process died | The browser process at runtime | Listen for `crash`; use a build that handles it |

If the log is silent above the exception, you are missing it: add `DEBUG=pw:browser,pw:protocol`
and a `crash` listener, reproduce once, and one of these three rows will match. A raised
timeout matches none of them.

## What a reproducible identity buys you here

The reason these three were separable at all is that we could replay the exact run that
failed. That is the practical argument for a seed. `invisible_playwright` is
[stock Playwright with a patched Firefox underneath](stock-playwright-patched-binary.md),
so the `browser` you get back is a real
Playwright `Browser` and every method is the documented one; the only thing added is that
the whole identity derives from one seed, and pinning it makes a failing run reproducible
instead of a one-off you can never get back.

```python
from invisible_playwright import InvisiblePlaywright

# Attach a crash listener so cause 3 never hides behind a bare TargetClosedError,
# and pin the seed so a failing run replays byte for byte.
with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    page.on("crash", lambda p: print("content process crashed at:", p.url))
    page.on("close", lambda p: print("page closed"))

    try:
        page.goto("https://example.com")
        page.click("#submit")
    except Exception as exc:
        # The exception type alone will not tell you which of the three it was.
        # The crash listener above and the pw:protocol log will.
        print("failed:", type(exc).__name__, exc)
        raise
```

Run the same script twice with the same seed and you get the same browser, so a bisect is
a bisect rather than a guess. That is the same discipline the
[quickstart](quickstart.md) recommends for any hard-to-catch failure, and it is what let
us prove the protocol-drift count was one field and not flakiness.

If your crash is Windows-and-headless specific and involves the browser process being torn
down, the related note on
[orphaned browser processes on Windows](orphaned-browser-process-windows.md) covers the
teardown side of the same territory.

## Conclusion

`TargetClosedError` is not one bug and it is not a timeout. It is the generic report that
the other end closed, and the useful information is always in the lines before it: a
`chrome://juggler` load failure means the automation layer never shipped in that build; a
drop on one specific command means the client sent a field the browser does not declare;
a `crash` event means a content process died. Turn on the transport log, attach a crash
listener, pin your identity so the run replays, and the three separate on sight. Then you
fix the actual cause instead of waiting longer for the same exception.

## Short answers to the questions that lead here

**What causes TargetClosedError in Playwright?** The target the client was connected to
closed. With Firefox we have hit three distinct causes: the automation layer missing from
the packaged build, a protocol field the browser rejects, and a content process crash.
Each shows a different line above the exception.

**Will increasing the timeout fix it?** No. A timeout is a call that waited too long;
`TargetClosedError` is a call with nothing left to wait for. A longer timeout only delays
the same error.

**Why does the browser launch fine but Playwright cannot drive it?** The binary can render
pages without the code that speaks Playwright's protocol. If you see `Failed to load
chrome://juggler/...` and `-juggler-pipe` called unrecognized, the automation layer is not
in that build, and a `--screenshot` smoke test will still pass.

**Why did it break right after I upgraded Playwright?** Likely protocol drift: the newer
client sends a command field the browser's schema does not declare, so the browser rejects
that one command at runtime while everything else works. Align the client version with the
browser build.

**How do I know a crash from a normal navigation error?** Listen for `page.on('crash')`.
If it fires, a content process died and the later `TargetClosedError` is just the fallout.
If instead you see "Execution context was destroyed" with no crash, a live page navigated
and that is usually harmless.

**How do I make a rare TargetClosedError reproducible?** Pin the identity. With a fixed
seed the same run replays exactly, so you can bisect it; without one, an intermittent close
is gone as soon as it happens.

## Sources

- This project's own release archive: the packaged build that shipped without its
  automation layer, and the protocol-drift measurement in which one undeclared viewport
  field took out 97 of 133 end-to-end tests with the build green.
- The Playwright transport log (`DEBUG=pw:browser,pw:protocol`) and the
  [`page.on('crash')` event](https://playwright.dev/python/docs/api/class-page#page-event-crash),
  read from a real failing run rather than from the final exception type.

**See also:** [the checklist for a browser detected as a bot](playwright-detected-as-bot.md)
once the browser is actually drivable, and [protocol drift](playwright-protocol-drift.md)
for the version-alignment gate behind cause 2.

---

*Written while maintaining [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level driven by stock Playwright. All three causes above are
failures this project shipped, diagnosed, and gated against, not hypotheticals.*
