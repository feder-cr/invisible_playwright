---
title: "smolagents: hand the agent an invisible_playwright tool"
description: "smolagents is code-first; its bundled vision browser drives Chromium via helium, not Playwright, so you register a custom invisible_playwright tool instead."
parent: "AI Agents and Frameworks"
grand_parent: "Guides"
nav_order: 13
---


# smolagents: hand the agent an invisible_playwright tool

To hand a smolagents agent an invisible_playwright browser, skip the framework's
built-in vision-browser tool and register a custom tool function instead: launch
invisible_playwright directly, then pass that function to the agent so every browser
call drives a real stealth Firefox rather than the bundled Chromium-over-Selenium one.

smolagents is code-first. The agent does not pick from a menu of actions; it writes
Python and runs it, step after step, and it drives a browser through whatever tools you
have registered. That single design choice is why the integration here looks different
from a framework that owns its own browser object.

If you came looking for a config flag that swaps Firefox into the built-in browser, there
isn't one, and the reason is worth thirty seconds because it changes the whole approach.

## Why there is no Firefox switch in the built-in tool

The vision-browser example shipped with smolagents drives Chromium through helium, which
is a thin wrapper over Selenium. It is Selenium underneath, not Playwright, so there is
no Playwright launch call to redirect and no browser object to hand it a different engine.
You cannot configure invisible_playwright into that tool because that tool does not speak
Playwright at all.

So you do not patch the built-in browser. You register a **custom tool function** backed
by an invisible_playwright browser, and let the agent call that instead of the helium one.
The agent's reasoning loop, its step rhythm and your network exit are all unchanged by the
swap - the only thing that changes is which browser the tool drives, and therefore what
that browser's fingerprint, TLS handshake and driver layer look like from the page's side.

## The two-line launch the tool wraps

Every tool below wraps this two-line launch, which returns a real Playwright `Browser`,
so every method you already know keeps working unchanged:

```python
from invisible_playwright import InvisiblePlaywright

with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    page.goto("https://example.com")
    print(page.title())
```

`seed=42` fixes the identity: same GPU, same canvas hash, same fonts, same screen, run
after run. For an agent that is not cosmetic - when a step fails, a pinned seed lets you
replay the exact machine the failure happened on instead of guessing at the next random
draw. Drop the seed and every session gets a distinct fingerprint instead.

## Registering it as a tool the agent can call

smolagents tools are plain functions with a decorator and a typed signature. The pattern
that survives the agent's step loop is to launch the browser once, keep it open across
steps, and expose small verbs the agent can compose in the code it writes.

```python
from smolagents import tool
from invisible_playwright import InvisiblePlaywright

# launch once, keep it open for the whole agent run
_session = InvisiblePlaywright(
    seed=42,
    proxy={"server": "socks5://gate.example.com:1080", "username": "u", "password": "p"},
)
_browser = _session.__enter__()
_page = _browser.new_page()


@tool
def visit(url: str) -> str:
    """Navigate the browser to a URL and return the page title.

    Args:
        url: The absolute URL to open.
    """
    _page.goto(url)
    return _page.title()


@tool
def read_text(selector: str) -> str:
    """Return the visible text of the first element matching a CSS selector.

    Args:
        selector: A CSS selector, e.g. "h1" or ".price".
    """
    return _page.inner_text(selector)
```

Then pass those tools to the agent instead of the bundled browser tool:

```python
from smolagents import CodeAgent, InferenceClientModel

agent = CodeAgent(
    tools=[visit, read_text],
    model=InferenceClientModel(),
)
agent.run("Open https://example.com and tell me the main heading.")
```

The agent now writes code that calls `visit(...)` and `read_text(...)`, and every one of
those calls drives a patched Firefox. `_page` is an ordinary Playwright `Page`, so `click`,
`fill`, `screenshot`, `wait_for_selector` and the rest are all available if you want to
give the agent more verbs. Close the session with `_session.__exit__(None, None, None)`
when the run is done.

## What the swap fixes, and what it does not

Swapping the browser fixes the fingerprint, TLS handshake and driver layer - the tells
that give automation away - and nothing past that. Skipping this distinction is how
people end up surprised.

invisible_playwright is built to **look like a real browser driven by a real person**.
That is why it passes most detection checks: the fingerprint surface (GPU, audio, fonts,
canvas, screen - roughly 400 fields), the TLS handshake and the driver layer all read as
a genuine Firefox rather than an automated one. `navigator.webdriver` is absent, not set
to `false`. Against the public suites - [CreepJS](creepjs-explained.md), BotD,
FingerprintJS, sannysoft - that is what moves a score.

What the swap does **not** touch:

- **IP reputation.** A perfect browser on a known datacenter address still loses. You
  supply a clean exit; the browser cannot invent one.
- **Per-account quotas and rate limits.** These are counted server-side, per identity, and
  no fingerprint changes the count.
- **Behaviour and timing.** An agent's pause between steps is shaped like model latency,
  and a form filled in eighty milliseconds is not human pacing. The tool moves the mouse
  on a Bezier curve, but the agent's overall rhythm is yours to shape. See
  [what fits and what does not for AI browser agents](ai-browser-agents-stealth.md).

Nothing here is undetectable, and no page can promise that. The claim is narrower and
true: the fingerprint, TLS and driver layers stop being the thing that gives you away, so
the problems that remain are the ones you can actually reason about - your address and your
pacing.

## Conclusion

Because smolagents is code-first and its bundled vision browser runs on helium over
Selenium, the integration is not a config flag but a custom tool: a function the agent
calls, backed by an invisible_playwright browser launched once and kept open across steps.
The wrapper hands you a real Playwright `Browser`, so the tool body is ordinary Playwright.
That closes the fingerprint, TLS and driver gaps by demonstration. It does not close IP
reputation, quotas or timing, and the reader supplies a clean proxy and human pacing for
those.

## Short answers to the questions that lead here

**Can I set Firefox in the smolagents built-in browser tool?** No. The bundled vision
browser drives Chromium through helium, which is Selenium underneath, so there is no
Playwright launch to redirect. Register a custom tool backed by invisible_playwright
instead.

**Does swapping the tool change how the agent thinks?** No. The reasoning loop and step
rhythm are unchanged. Only the browser the tool drives changes, and with it the
fingerprint, TLS and driver layers.

**Will this make my agent undetectable?** No, and be wary of anything that says otherwise.
It makes the browser read as a real Firefox, which fixes the fingerprint and driver tells.
Your IP and your pacing are still yours to get right.

**Do I keep one browser open or launch per step?** Keep one open for the whole run. Launch
it once outside the tool functions and share the `Page`; relaunching per step throws away
the identity and is slower.

**How do I make a failing agent step reproducible?** Pass a fixed `seed`. The same seed
produces the same machine every run, so you can replay the exact session a step failed on.
See [pinning fingerprint fields](pinning.md) for forcing individual values.

**Does the proxy go in the tool or the agent?** In the browser launch, as the `proxy=`
argument. The agent and its model never see it; the timezone is auto-derived from the exit
by default.

## Sources

- [smolagents](https://github.com/huggingface/smolagents) and its bundled
  [vision-browser example](https://github.com/huggingface/smolagents/blob/main/src/smolagents/vision_web_browser.py),
  retrieved 2026-08-28: the example calls `helium.start_chrome()` and imports
  `selenium.webdriver` directly, with no Playwright import anywhere in the file.
- [helium](https://github.com/mherrmann/helium), retrieved 2026-08-28: described in its
  own README as a wrapper that forwards each call to Selenium underneath.
- This project's [quickstart](quickstart.md) and [configuration](configuration.md) pages
  for the real launch, proxy and seed API used above.

**See also:** [AI browser agents and stealth](ai-browser-agents-stealth.md) for the
timing signal a fingerprint cannot fix, [giving a LangChain agent an invisible_playwright
browser](langchain-agent-invisible-playwright-browser.md) for the same swap in a
framework that does speak Playwright, and [invisible_playwright versus the Selenium
driverless builds](vs-selenium-driverless.md) for why the engine, not a page patch, is
what carries the disguise.

---

*Written while maintaining [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level driven by stock Playwright. The tool wrapper here is the
same two-line launch every other page uses; only the caller changed.*
