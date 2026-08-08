---
title: "Stagehand and stealth: why a Firefox engine won't drop in"
description: "Stagehand wraps Playwright with act, observe and extract, but it is TypeScript and Chromium-only, so a Python Firefox engine cannot drop in underneath it."
parent: "AI Agents and Frameworks"
grand_parent: "Guides"
nav_order: 14
---


# Stagehand and stealth: why a Firefox engine won't drop in

**Short answer: no.** A Python-launched Firefox cannot serve as the engine under
Stagehand's act/observe/extract loop, and the reason is not stealth quality - it is that
two seams do not line up: the language runtime and the browser engine. That is the honest
answer to a common question from people building agents: "I use Stagehand for the act /
observe / extract loop, and I want a stealthier engine under it. Can I swap in
invisible_playwright?"

Once those two seams are clear, the useful part is what does carry over: the things that
decide whether an agent gets blocked are mostly not the engine at all.

## What Stagehand actually is

Stagehand is a layer on top of Playwright. It keeps the raw Playwright page available and
adds three higher-level primitives driven by a language model: `act` (take an action
described in natural language), `observe` (list the actionable elements on the page), and
`extract` (pull structured data out with a schema). You write TypeScript, hand it an
instruction, and it turns that into Playwright calls.

Two facts about how it launches matter here:

- It is a **TypeScript / Node** library. The act/observe/extract methods are called from
  Node, and the browser it drives is created inside that Node process.
- It drives **Chromium**. The engine is a Chromium build or a Chromium channel such as
  Chrome. That is a hard assumption baked into how the session is created, not a
  configuration knob you point at an arbitrary binary.

Both are reasonable choices for the project. They are also exactly the two things that
make a Python-launched Firefox unable to sit underneath it.

## Why a Firefox engine cannot slot underneath it

invisible_playwright is a **Python** package that launches a **patched Firefox** and hands
you back a real Playwright `Browser`. That is the whole design, and it collides with
Stagehand on both seams at once.

The **language seam**: invisible_playwright launches the browser from a Python process.
Stagehand's act/observe/extract run in Node and expect to create and own the browser
themselves. There is no supported way for a Node library to adopt a `Browser` object that
a separate Python process launched and is holding open. The two runtimes do not share a
handle.

The **engine seam**: even setting the language aside, Stagehand expects Chromium. The
reason "just use a different engine" does not work is the same reason
[Chromium is not Chrome](chromium-is-not-chrome.md): the channel a framework assumes is a
structural assumption, not a string you can override to point at Firefox. A stealth
Firefox is the wrong shape for a slot cut for Chromium.

So the seam simply is not there. This is the same finding as the wider survey of
[which agent frameworks a stealth engine can actually help](ai-browser-agents-stealth.md):
the ones that let you inject a browser you launched yourself are the ones that fit, and a
CDP/Chromium-only, Node-only stack is not one of them. It is an honest limitation, not a
gap we are about to close by wishing.

## What invisible_playwright is instead

If you are in Python, you do not need a wrapper to get act/observe/extract behaviour. You
launch the browser and drive it with whatever model loop you like, because the object you
get back is a plain Playwright [`Browser`](https://playwright.dev/python/docs/api/class-browser)
with every standard method:

```python
from invisible_playwright import InvisiblePlaywright

with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    page.goto("https://example.com")

    # "observe": hand the page to your model as text or a DOM summary
    visible_text = page.inner_text("body")
    links = [a.get_attribute("href") for a in page.query_selector_all("a")]

    # "act": your model returns a selector, you drive it with stock Playwright
    page.click("#submit")          # mouse arcs to the target on a Bezier curve

    # "extract": read structured fields with the same standard API
    price = page.inner_text("#price")
```

The two-line launch is the entire adoption cost, exactly as in the
[Quickstart](quickstart.md): `from invisible_playwright import InvisiblePlaywright`, then
`with InvisiblePlaywright(seed=42) as browser:`. Everything after that is ordinary
Playwright, so any observe/extract loop you would have written for Stagehand ports to
Python methods directly - `page.inner_text`, `query_selector_all`, `get_attribute`,
`page.click`. Pass a `seed` and the fingerprint is reproducible run after run, which is
what turns a flaky agent failure into one you can replay.

The pattern of handing an already-launched browser to an agent framework is covered in
full for one Python framework in
[giving a LangChain agent an invisible_playwright browser](langchain-agent-invisible-playwright-browser.md);
the shape is the same for any Python agent loop.

## Why the engine passes most checks, and what it does not touch

invisible_playwright is built to look like a real Firefox driven by a real person, and
that is genuinely most of the fingerprint problem - but it is easy to over-read what
swapping the engine buys you, so the framing has to stay honest.

The GPU, canvas, audio, fonts and screen are seed-derived and internally consistent; the
TLS handshake is a real Firefox handshake because it is a real Firefox; and there is no
driver flag announcing automation. Those are the layers a public suite like
[CreepJS, sannysoft or BotD](how-to-test-bot-detection.md) reads, and they are the layers
where a genuine engine wins by being genuine rather than by patching over a Chromium one.

What the engine does **not** touch, and no engine choice does:

- **IP reputation.** A perfect browser on a datacenter address, or on a proxy IP that a
  thousand other people are using this minute, is still on that address. The engine cannot
  change where the packets come from. You supply a clean exit.
- **Per-account quotas and rate limits.** These are counted server-side against your
  account and your address, not read out of your browser. A better fingerprint does not
  raise a limit.
- **Action rhythm.** An agent that clicks the instant the DOM is ready, types at a uniform
  interval, and pauses for exactly as long as a model takes to respond, has a timing
  signature no fingerprint hides. This one is engine-independent by definition: it is the
  same whether the model drives Chromium or Firefox, TypeScript or Python. It is described
  at length in [AI browser agents and stealth](ai-browser-agents-stealth.md) under the
  pause shaped like model latency.

So the accurate sentence is: the engine makes the browser read as a real one, which
handles the fingerprint, TLS and driver layers. It does nothing about the address, the
account limits, or the rhythm, and those are supplied by you - a clean proxy and human
pacing - regardless of which framework you started from.

## If you want the act/observe/extract ergonomics in Python

You have two honest options, and neither is "make Stagehand use Firefox".

Keep Stagehand and accept its engine. If the act/observe/extract authoring experience is
what you value most and Chromium is acceptable for your target, use Stagehand as designed.
You do not get a real-Firefox fingerprint that way, and the caveats above about IP and
rhythm still apply.

Or stay in Python and build the loop on the real Playwright API, as in the example above.
You give up Stagehand's prompt-to-action convenience and you write the model glue
yourself, but you get the genuine-Firefox engine and a reproducible seed. For a running
agent that hits detection anyway, work the
[one-site detection checklist](playwright-detected-as-bot.md) in order: it is almost never
the engine by that point.

## Conclusion

Stagehand and invisible_playwright do not compose, and the reason is structural, not a
quality gap. Stagehand is TypeScript driving Chromium; invisible_playwright is Python
launching Firefox. There is no seam where a browser launched by a Python process can be
adopted by a Node library that assumes it owns a Chromium session. In Python you do not
need the wrapper anyway: the object you get is a stock Playwright `Browser`, so any
observe/extract loop ports to standard methods. And whichever route you take, remember what
the engine is for. It makes the browser real, which passes the fingerprint, TLS and driver
checks. It does not fix your IP, your quotas, or your timing, and pretending otherwise is
how an agent that scores clean still gets blocked.

## Short answers to the questions that lead here

**Can I use invisible_playwright as the engine under Stagehand?** No. Stagehand is
TypeScript driving Chromium; invisible_playwright is Python launching Firefox. Neither the
language boundary nor the Chromium assumption has a supported override.

**Does Stagehand support Firefox at all?** It is built around Chromium and a Chromium
channel such as Chrome. The engine assumption is structural, not a binary path you point
elsewhere.

**How do I get act / observe / extract behaviour in Python then?** Drive the real
Playwright `Browser` you get back with your own model loop: `inner_text` and
`query_selector_all` for observe/extract, `click` and `type` for act. It is standard
Playwright, so there is nothing new to learn.

**Will switching engines get my agent past detection?** It handles the fingerprint, TLS
and driver layers, which is most checks. It does nothing for IP reputation, account
quotas, or your action rhythm, which you still supply.

**Is the timing signal really engine-independent?** Yes. A uniform click-and-type cadence
and a pause the length of a model response look the same on Chromium or Firefox. No browser
choice changes it.

**Is this a temporary limitation you will fix?** No. It is a consequence of two runtimes
and two engine families, not a missing feature. The Python route above is the supported
answer.

## Sources

- Stagehand's own documentation and configuration for how it launches a browser: a
  TypeScript library that creates and owns a Chromium (or Chromium-channel) session.
- [Playwright's `Browser` API reference](https://playwright.dev/python/docs/api/class-browser)
  for what a launched browser exposes to any driver code, standard-method by standard-method.
- This project's [Quickstart](quickstart.md) and [Configuration](configuration.md) for the
  real launch API and proxy handling.
- This set's survey of
  [which agent frameworks accept an injected browser](ai-browser-agents-stealth.md), read
  from each project's source rather than assumed.

**See also:** [AI browser agents and stealth](ai-browser-agents-stealth.md) for the
framework-by-framework fit, [why Chromium is not Chrome](chromium-is-not-chrome.md) for why
an engine assumption is structural, and
[the one-site detection checklist](playwright-detected-as-bot.md) for when an agent gets
blocked despite a clean fingerprint.

---

*Written while maintaining [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level driven by stock Playwright. The seam this page says is
absent is absent because two runtimes do not share a browser handle, not because we have
not gotten to it yet.*
