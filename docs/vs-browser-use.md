---
title: "invisible_playwright vs browser-use: different jobs"
description: "invisible_playwright vs browser-use compared: an LLM-driven agent framework against a Firefox engine you drive yourself with plain Playwright code."
parent: "Comparisons"
nav_order: 38
---


# invisible_playwright vs browser-use: different jobs

invisible_playwright and browser-use solve different problems, and this page is written by
people who maintain the former, so read the comparison with that in mind. browser-use puts
a language model in the loop, deciding each browser action from the page's current state.
invisible_playwright is a browser you drive yourself, with Playwright's own API and no
model involved at any step.

## What browser-use actually is

browser-use ([browser-use/browser-use on GitHub](https://github.com/browser-use/browser-use))
is an agent framework: you describe a task in plain language, and on each step it hands the
page state to a language model, which decides the next action, a click, a type, a scroll,
an extraction, and the framework carries it out through Playwright. What changes versus a
plain script is upstream of the click: nothing in your code decides the next step. The
model does, fed a fresh read of the page every time. The project states the shape plainly
in its own repository: it exists to "make websites accessible for AI agents", it is MIT
licensed, it wants Python 3.11 or newer, and it drives the browser through Playwright
while the model chooses the actions.

## What this project actually is

invisible_playwright is not an agent and decides nothing. It is a real Firefox, patched at
the engine level, driven with plain, synchronous Playwright code you write yourself:

```python
from invisible_playwright import InvisiblePlaywright

with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    page.goto("https://example.com/product/x")
    print(page.locator("#price").inner_text())
```

Given the same script and the same seed, it does the same thing every run, because "the
same thing" is exactly what you wrote. There is no model anywhere in that loop.

## Determinism versus adaptability

A fixed script is deterministic by construction, the same selectors and order every run,
until the page changes underneath it. Then it fails loudly, at the exact line that no
longer matches, with an error naming what was missing. An LLM-driven agent is the opposite
shape: fed a new layout or a moved field, it can often route around the change without a
code update, because it reasons about the page fresh each time. The same task run twice is
not guaranteed to take the same path, and a wrong guess can look like success: a crashed
script is easy to notice, a task that quietly completed the wrong step is not.

## Cost: a model call per step, or none at all

browser-use's loop calls a language model with the page state to decide each action, so a
task needing a dozen actions makes roughly that many calls, and the token cost scales with
how long and ambiguous the task is. The repository's own note about resources points the
same way: it warns that "Chrome can consume a lot of memory, and running many agents in
parallel can be tricky to manage", and steers heavy parallel use toward their hosted
service. invisible_playwright makes no model calls at all; the cost
of a run is your own compute and, through a proxy, the network.

## They compose, they do not compete

browser-use needs some browser under it, Chromium by default, driven through Playwright.
This wiki's own notes on its configuration surface found the launch levers
(`executable_path`, `user_data_dir`, `proxy`, `headless`, `args`) [all Chromium-family, with
MCP as the route that reaches a different engine at all](browser-use-detection.md).
invisible_playwright competes with none of that; the place the two could meet, an agent
deciding actions on a Firefox that reads as genuine, is MCP, not a shared config file.

## At a glance

| Question | browser-use | invisible_playwright |
|---|---|---|
| What decides the next action | A language model, called per step | Your own code |
| Default browser driven | Chromium, via Playwright | A patched Firefox |
| Needs an LLM API key | Yes | No |
| Same task, same result every run | Not guaranteed | Yes, given the same script and seed |
| Failure when the page changes | Can sometimes route around it | Raises, naming what was missing |

## Where browser-use is genuinely stronger

Being straight about this, because a comparison that only lists your own advantages is not
a comparison. browser-use is built for exactly the case where writing and maintaining
selectors is the bottleneck, and where reasoning about a page once is cheaper than
scripting it. Its community and ecosystem around agentic browsing are large and growing
fast, which means more examples and more people who already hit the problem you are about
to. It gets a first working result faster for someone who does not want to identify
selectors before writing a line. invisible_playwright does none of that; it expects you to
already know what you want to click.

## How to actually choose

- **A task that changes shape across sites, better described than coded?** browser-use
  fits, at the cost of a model call per step and a result not guaranteed to repeat.
- **A fixed task that must run the same way every time, at no per-step cost?** A plain
  Playwright script does that, and this project's job is making the Firefox underneath it
  read as genuine.
- **The target reads the browser itself closely** (fonts, GPU, canvas, TLS)**?** That
  question sits at the engine layer, covered in [vs Patchright](vs-patchright.md) and
  [vs Camoufox](vs-camoufox.md), regardless of which framework drives the browser.
- **Undecided?** Run the task both ways against your real target and compare what each gets
  flagged for.

## Short answers to the questions that lead here

**Is browser-use better than Playwright?** Not comparable. browser-use is an agent that
decides actions with a language model; Playwright is the automation API it runs on, and the
one invisible_playwright exposes directly with no model involved.

**Can browser-use use Firefox instead of Chromium?** Its documented setup is
Chromium-family, and the route that reaches a different engine is MCP rather than a
launch-option swap.

**Can I use browser-use and invisible_playwright together?** Not through browser-use's
launch configuration. An agent driving a Firefox engine like this one goes through MCP
instead, a different integration path than a shared config file.

**See also:** [invisible_playwright vs Patchright: driver vs engine](vs-patchright.md),
[invisible_playwright vs Camoufox: two patched Firefoxes](vs-camoufox.md), and [what you can
and cannot change about browser-use's own detection surface](browser-use-detection.md).

## Sources

- browser-use repository, https://github.com/browser-use/browser-use - the quoted
  one-line description, the MIT license, the Python 3.11 floor, Playwright underneath,
  the LLM-driven action loop, and the memory note about running many agents in parallel.
  Read 5 September 2026.
- This wiki's own [browser-use-detection.md](browser-use-detection.md), previously verified
  against browser-use's configuration surface, for the Chromium-family launch levers.

---

*From the notes of [invisible_playwright](https://github.com/feder-cr/invisible_playwright).
browser-use solves a real problem, deciding what to do on a page, that this project never
attempts.*
