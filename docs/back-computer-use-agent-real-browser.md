---
title: "Back a computer-use agent with a real browser engine"
description: "Run OpenAI's computer-use agent inside patched Firefox by implementing its Computer interface over an invisible_playwright page, and the honest limits."
parent: "AI Agents and Frameworks"
grand_parent: "Guides"
nav_order: 16
---


# Back a computer-use agent with a real browser engine

A computer-use agent is a loop: take a screenshot, send it to a model, get back a
coordinate to click or some text to type, apply it, screenshot again. The interesting
question for anyone running one against real sites is not the model. It is what browser
the screenshots come from and where the clicks land, because that is the part a site can
inspect.

The good news is that the popular reference implementation was built to let you swap that
part out. This page shows how, using the real invisible_playwright API, and it is honest
about what the swap fixes and what it leaves entirely up to you.

## The seam that makes this easy

OpenAI's open `cua-sample-app` does not hardcode a browser. The screenshot-and-click loop
drives a small, pluggable `Computer` interface, and the repository ships one
implementation of it called `LocalPlaywrightComputer`. Every action the model returns -
`screenshot`, `click`, `type`, `scroll`, `keypress`, `goto` - is a method the loop calls
on whatever `Computer` object you hand it. The loop does not know or care what is behind
those methods.

That seam is the whole opportunity. If you implement the same interface over a browser you
control, the same loop now runs inside your engine. Nothing about the agent's reasoning
changes. What changes is the thing on the other side of `screenshot()` and `click()`.

## What backing it with a real engine actually fixes

`LocalPlaywrightComputer` drives a stock automation build. invisible_playwright is a
Firefox patched at the C++ level and driven by stock Playwright, generating a full,
internally consistent identity per session - GPU, canvas, audio, fonts, screen, roughly
400 fields - from one seed. Point the `Computer` interface at one of its pages and two
concrete things move:

- **The engine the screenshots come from.** The pixels the model sees are rendered by a
  browser whose fingerprint, TLS handshake and driver layer read as a genuine Firefox
  rather than as an automation build announcing itself. This is why it clears most
  fingerprint, TLS and driver-layer checks: it is built to look like a real browser
  driven by a real person, not to suppress signals. A suppressed or blank surface is its
  own tell, so the goal is a present, plausible value, not a missing one.
- **The page the clicks land on.** Coordinate clicks arrive on a page inside that engine,
  and pointer motion between them follows a Bezier curve rather than teleporting from
  point to point.

What it does not touch is equally important, and the next section is about that.

## Two lines to launch, one class to adapt

The launch is the same two-line change as anywhere else in these docs. `InvisiblePlaywright`
returns a real Playwright `Browser`, so every method you already use is available:

```python
from invisible_playwright import InvisiblePlaywright

with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    page.goto("https://example.com")
    page.mouse.click(200, 140)   # the pointer arcs there on a Bezier curve
```

To feed that page to the agent, wrap it in a class that satisfies the `Computer`
interface. This is a representative subset of the methods the loop calls; each one is a
plain call on the real Playwright `page`:

```python
import base64
from invisible_playwright import InvisiblePlaywright


class InvisibleComputer:
    """Adapts an invisible_playwright page to cua-sample-app's Computer interface."""

    environment = "browser"
    dimensions = (1280, 800)

    def __init__(self, page):
        self._page = page

    def screenshot(self) -> str:
        png = self._page.screenshot()
        return base64.b64encode(png).decode()

    def click(self, x: int, y: int, button: str = "left") -> None:
        self._page.mouse.click(x, y, button=button)

    def type(self, text: str) -> None:
        self._page.keyboard.type(text)

    def keypress(self, keys: list[str]) -> None:
        for key in keys:
            self._page.keyboard.press(key)

    def scroll(self, x: int, y: int, scroll_x: int, scroll_y: int) -> None:
        self._page.mouse.move(x, y)
        self._page.mouse.wheel(scroll_x, scroll_y)

    def goto(self, url: str) -> None:
        self._page.goto(url)

    def get_current_url(self) -> str:
        return self._page.url


with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    computer = InvisibleComputer(page)
    # hand `computer` to the cua-sample-app Agent in place of LocalPlaywrightComputer;
    # the screenshot-and-click loop now runs inside the patched Firefox.
```

Everything here uses only documented Playwright methods on a real `Browser` and `Page`.
There is no wrapped subset to learn, which is the point of the two-line switch. Pass a
`seed` and the identity is reproducible, so a run that gets challenged can be replayed
exactly instead of guessed at.

## What it does not fix, and who supplies that

Swapping the engine fixes the engine and the page. It does not change two things that
decide most real outcomes, and pretending otherwise would be dishonest.

- **The click rhythm.** The loop still clicks a coordinate, waits for the model, and
  clicks the next coordinate. That cadence has a shape: long pauses clustered around model
  latency rather than around reading speed, and clicks that land dead-centre on targets.
  The Bezier motion between points helps, but the higher-level timing is the agent's, and
  a site that watches behaviour rather than fingerprints is watching that. Human pacing is
  something you add on top; see [the pause shaped like model latency](ai-browser-agents-stealth.md)
  and [what human pointer motion actually looks like](human-mouse-movement.md).
- **The network path.** The screenshots come from a real-looking Firefox, but the traffic
  still exits wherever your host or proxy sends it. A genuine browser on a datacenter
  address, or on an IP a thousand other people are using this minute, still loses. IP
  reputation, per-account quotas and rate limits are not browser properties, and no engine
  swap touches them. You supply the clean exit.

Set a proxy the same way as any other session, and let the browser timezone follow the
exit rather than pinning it by hand:

```python
proxy = {"server": "socks5://gate.example.com:1080", "username": "u", "password": "p"}
with InvisiblePlaywright(seed=42, proxy=proxy) as browser:
    page = browser.new_page()
    computer = InvisibleComputer(page)
    ...
```

The honest summary: this raises the browser and the page from "obvious automation" to
"indistinguishable from a real Firefox", which clears the fingerprint, TLS and driver
layer. It leaves behaviour timing and network reputation exactly where they were, on your
side of the line. See [why a clean fingerprint can still be blocked](why-blocked-with-a-clean-fingerprint.md)
for what remains after the engine stops being the problem.

## Prove the swap did what you think

Do not trust the diff, measure it. Run the agent's own `screenshot()` against a public
fingerprinting page, once with `LocalPlaywrightComputer` and once with the adapter above,
and compare the reports field by field rather than reading the verdicts. The fields that
move - GPU renderer, canvas hash, the driver flag, the platform string - are what the
swap bought you. The fields that do not move, and the timing and address it cannot touch,
are what you still owe. The method is the same one in
[how to test bot detection without a false pass](how-to-test-bot-detection.md): assert the
right signal is present, not that a wrong one is absent, and run it more than once.

## Conclusion

The pluggable `Computer` interface in OpenAI's `cua-sample-app` is the seam that lets you
run the exact same screenshot-and-click loop inside a different browser. Implementing it
over an invisible_playwright page fixes the two things a site inspects first - the engine
the screenshots come from and the page the clicks land on - and that is genuinely most of
the fingerprint battle. It is not the whole session. The coordinate-click rhythm and the
network path are still yours, and an agent that looks like a real Firefox on a bad IP with
robotic pacing is still an agent that gets caught. Swap the engine, then supply the pacing
and the exit.

## Short answers to the questions that lead here

**Can I use invisible_playwright with OpenAI's computer-use agent?** Yes. The
`cua-sample-app` drives a pluggable `Computer` interface; implement that interface over an
invisible_playwright page and the loop runs inside the patched Firefox.

**Does this make my agent undetectable?** No, and no tool honestly can. It makes the
browser engine, TLS and driver layer read as a real Firefox, which clears most
fingerprint checks. It does not fix your IP, your rate limits, or the timing of the clicks.

**What exactly does the engine swap change?** The screenshots are rendered by a real-looking
Firefox instead of an automation build, and the clicks land through Bezier pointer motion.
The higher-level click cadence and the network path are unchanged.

**Do I have to rewrite the agent's loop?** No. You implement one small adapter class with
methods like `screenshot`, `click`, `type` and `scroll`, each a plain call on the real
Playwright page. The loop stays as it is.

**Why does behaviour timing still matter?** Because a site can watch when you act, not just
what your browser reports. Pauses shaped like model latency and clicks dead-centre on
targets are a behavioural signal the engine does not touch.

**What proxy should I use?** A clean one. [Around 90% of proxies are already known and
blocked](configuration.md) before you ever use them, and a perfect browser on a known IP
still loses. Set it via the `proxy` argument and let the timezone auto-derive from the exit.

**See also:** [AI browser agents and stealth](ai-browser-agents-stealth.md) for what
applies across every agent framework, [how browser-use gets detected](browser-use-detection.md)
for the CDP-driven case, and [the checklist for being detected on one site](playwright-detected-as-bot.md)
for the order to debug in once something flags you.

## Sources

- OpenAI's open `cua-sample-app` and its `Computer` interface, including the shipped
  `LocalPlaywrightComputer` implementation, read from the repository rather than assumed.
- This project's own API: `InvisiblePlaywright` returns a real Playwright `Browser`, so
  the adapter above uses only documented Playwright methods - [`Page`](https://playwright.dev/python/docs/api/class-page),
  [`Mouse`](https://playwright.dev/python/docs/api/class-mouse) and
  [`Keyboard`](https://playwright.dev/python/docs/api/class-keyboard) - unchanged.
- This project's release gates and the field-by-field comparison method documented in the
  testing pages linked above.

---

*Written while maintaining [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level driven by stock Playwright. The engine swap is the easy
half; the pacing and the exit are the half that decides the session.*
