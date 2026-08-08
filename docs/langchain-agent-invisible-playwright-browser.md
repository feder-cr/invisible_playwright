---
title: "Give a LangChain agent an invisible_playwright browser"
description: "Give LangChain's PlayWrightBrowserToolkit an invisible_playwright browser so its tools inherit a real-browser fingerprint. The one signal the swap does not cover."
parent: "AI Agents and Frameworks"
grand_parent: "Guides"
nav_order: 4
---


# Give a LangChain agent an invisible_playwright browser

LangChain's browser toolkit builds its tools on top of a Playwright browser you
hand it. That one design choice is the whole opportunity here: if you launch the
browser yourself and pass it in, every tool the agent gets - navigate, click,
extract text - runs inside whatever browser you launched. Give it a stock
Playwright Firefox and the agent's page loads look automated. Give it an
invisible_playwright browser and the same loads carry a real-browser fingerprint
instead.

This page is the exact handoff, a runnable example, and the honest part: the swap
fixes what the page looks like, not how the agent behaves.

## The integration point: from_browser takes a browser you already launched

`PlayWrightBrowserToolkit.from_browser()` accepts an already-constructed async
Playwright `Browser`. It does not launch one for you and it does not care where it
came from, only that it is a real async `Browser` object with the standard methods.
That is the seam.

invisible_playwright returns exactly that. `InvisiblePlaywright(...)` used as an
async context manager yields a real `playwright.async_api.Browser` - every
[Browser method works as documented upstream](https://playwright.dev/python/docs/api/class-browser),
there is no wrapped subset. So you launch the invisible_playwright browser, pass it
straight to `from_browser`, and the toolkit builds its tools on top of it without
knowing anything changed.

The consequence is concrete. The toolkit's `navigate_browser`, `click_element`,
`extract_text` and the rest all drive a browser whose GPU, audio, fonts, screen and
roughly 400 fingerprint fields read as a genuine Firefox, and whose TLS handshake
and driver layer read as one too. That is why the agent's individual page loads pass
most of the checks a plain automated browser fails - the same checks covered in
[how to test whether your browser is detected](how-to-test-bot-detection.md).

## A runnable example

Two moving parts. Launch the invisible_playwright browser, then feed it to the
toolkit. Because `from_browser` wants an async browser, use the async entry point.

```python
import asyncio
from invisible_playwright.async_api import InvisiblePlaywright
from langchain_community.agent_toolkits import PlayWrightBrowserToolkit


async def main():
    # seed=42 makes the fingerprint reproducible: same GPU, same canvas hash,
    # same audio context, every run. Drop the seed for a fresh identity per run.
    async with InvisiblePlaywright(seed=42) as browser:
        toolkit = PlayWrightBrowserToolkit.from_browser(async_browser=browser)
        tools = toolkit.get_tools()

        # `tools` are now standard LangChain tools bound to the stealth browser.
        # Wire them into whatever agent you already use. Everything the agent
        # navigates to loads through the real-browser fingerprint above.
        for tool in tools:
            print(tool.name)


asyncio.run(main())
```

The `browser` you pass in is a real async Playwright `Browser`, so nothing about the
toolkit call is special-cased. If you already had LangChain browser code, this is a
two-line change to what launches the browser and nothing else.

Add a proxy the same way you would anywhere else in invisible_playwright, since the
exit address is not something the fingerprint can supply for you:

```python
proxy = {"server": "socks5://gate.example.com:1080", "username": "u", "password": "p"}
async with InvisiblePlaywright(seed=42, proxy=proxy) as browser:
    toolkit = PlayWrightBrowserToolkit.from_browser(async_browser=browser)
    ...
```

Proxy schemes, DNS routing and how the timezone auto-derives from the exit IP are in
[configuration](configuration.md).

## Why the page loads pass but the session can still fail

The browser swap changes what each page load looks like; it does not change how the
agent acts once the page is open, and that gap - specific to agents, not a general
disclaimer - is where a session can still fail after individual loads pass.

An LLM agent reads the page, calls a model, waits for the response, then acts. That
produces a rhythm with a shape no person makes: long pauses clustered around model
latency rather than reading speed, no
pointer drift during those pauses, actions landing dead centre on their targets
because the coordinates come from an accessibility tree rather than a hand, and no
wasted hovers or changes of mind.

The fingerprint the toolkit now carries is invisible to that signal, and the signal
is invisible to the fingerprint. They are measured separately, so they have to be
fixed separately. This is exactly the split laid out in
[AI browser agents and stealth](ai-browser-agents-stealth.md): a block at the first
request points at the machine or the address, and a block that arrives after a few
actions points at behaviour. A stealth browser addresses the first class and leaves
the second where it was.

## What the browser swap does and does not supply

Stated plainly so a clean fingerprint is not mistaken for a clean session.

What the swap gives the agent's page loads:

- A per-session fingerprint (GPU, audio, fonts, screen, roughly 400 fields) that
  reads as a real Firefox, reproducible from a seed.
- A TLS handshake and driver layer that match that story, which no in-page property
  patch can reach.

What you still have to supply yourself:

- **A clean exit.** A perfect browser on a known datacenter or already-blocked IP
  still loses. Pass a proxy you trust.
- **Human pacing.** The agent's action timing is yours to shape; the browser does
  not slow the agent down or add deliberation.
- **Account and rate discipline.** Per-account quotas, request velocity and
  reputation are session-level concerns the fingerprint has no view into.

The rule from the rest of these notes holds here: look real, do not merely avoid
leaking. A suppressed or empty signal is itself a tell. The full method, including
comparing against a stock browser field by field, is in
[the troubleshooting checklist](playwright-detected-as-bot.md).

## Keeping runs reproducible while you debug the agent

Agent loops are hard to debug because two things vary at once: the site and the
identity. Pin the identity and only the site is left to explain a failing run.

```python
sf = InvisiblePlaywright(seed=42)
async with sf as browser:
    print("seed =", sf.seed)   # log it to replay this exact identity later
    toolkit = PlayWrightBrowserToolkit.from_browser(async_browser=browser)
    ...
```

Same seed, same browser, every field it implies identical run after run. When an
agent run gets challenged, you can replay the same identity and know the fingerprint
was constant, which isolates the variable to the site or to the agent's behaviour -
and behaviour is the half the browser cannot fix for you.

## Conclusion

The handoff is small on purpose. `from_browser(async_browser=...)` takes a browser
you launched, invisible_playwright hands back a real async Playwright `Browser`, and
the toolkit builds its tools on top without special-casing anything. The agent's
navigate, click and extract tools then inherit a real-browser fingerprint, which is
why the individual page loads pass most checks a plain automated browser fails.

The honest boundary is that this fixes what the page looks like, not how the agent
behaves. Action rhythm, exit reputation and rate discipline are separate problems
with separate fixes, and the browser swap is silent about all three. Do the swap,
then pace the agent and give it a clean exit, and you have addressed both halves
instead of one.

## Short answers to the questions that lead here

**How do I use a stealth browser with LangChain's PlayWrightBrowserToolkit?** Launch
an invisible_playwright browser as an async context manager and pass it to
`PlayWrightBrowserToolkit.from_browser(async_browser=browser)`. The returned object
is a real async Playwright `Browser`, so the toolkit accepts it unchanged.

**Do the agent's tools inherit the fingerprint?** Yes. `navigate_browser`,
`click_element` and `extract_text` all drive the browser you passed in, so every
page they load carries its fingerprint.

**Does this make my agent undetectable?** No, and no tool should claim that. It makes
the page loads look like a real Firefox, which passes most fingerprint, TLS and
driver checks. It does nothing about the agent's action timing, your exit IP, or
rate limits.

**Why does my agent still get blocked after a few clicks?** That pattern points at
behaviour, not fingerprint. An agent pauses for model latency, does not drift the
pointer, and lands dead centre on targets. See
[AI browser agents and stealth](ai-browser-agents-stealth.md).

**Sync or async?** Async. `from_browser` expects an async browser, so use
`from invisible_playwright.async_api import InvisiblePlaywright`.

**Do I still need a proxy?** Yes if the exit matters. The fingerprint cannot supply a
clean IP; pass a `proxy` dict as shown in [configuration](configuration.md).

## Sources

- LangChain's `PlayWrightBrowserToolkit.from_browser(async_browser=...)` signature,
  which accepts an already-launched async Playwright `Browser`.
- Playwright's own [Browser class API](https://playwright.dev/python/docs/api/class-browser),
  which is what "no wrapped subset" is checked against.
- This project's own API: `InvisiblePlaywright` returns a real
  `playwright.async_api.Browser`, documented in
  [the quickstart](quickstart.md).
- The agent-timing signal is measured separately from the fingerprint, covered in
  [AI browser agents and stealth](ai-browser-agents-stealth.md).

**See also:** [AI browser agents and stealth](ai-browser-agents-stealth.md) for the
CDP-versus-Playwright split behind why this works, [the AI agents guide](guides-ai-agents.md)
for the rest of this section, and [the detection checklist](playwright-detected-as-bot.md)
for working a block in order.

---

*Written while maintaining [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level driven by stock Playwright. The browser swap was
the easy half; pacing the agent was the half that actually mattered.*
