---
title: "Give an MCP browser server a stealth Firefox engine"
description: "Run Microsoft's Playwright MCP on patched Firefox via browserName and executablePath. Why executablePath alone gets the engine but not the profile."
parent: "AI Agents and Frameworks"
grand_parent: "Guides"
nav_order: 7
---


# Give an MCP browser server a stealth Firefox engine

An MCP browser server lets a model drive a real browser through a small set of
tools: navigate, click, type, snapshot. Microsoft's Playwright MCP is the common
one, and the question that leads here is whether you can make it drive something
that reads as a real Firefox instead of a stock automated one.

The short answer is that it exposes a concrete seam for exactly this, and that the
seam gets you the patched engine but not the whole disguise. This page shows the
config that works, measures what it actually buys you, and is honest about the two
things it does not touch.

## The seam Microsoft's Playwright MCP exposes

Playwright MCP launches a browser for you and speaks the Model Context Protocol to
the client on one side and Playwright to the browser on the other. Two of its
options are the whole story here:

- `--browser firefox` (or `browserName: "firefox"` in a config file) picks the
  Firefox channel instead of the default Chromium one.
- `--executable-path <path>` (also `launchOptions.executablePath` in a config file,
  or the `PLAYWRIGHT_MCP_EXECUTABLE_PATH` environment variable) tells Playwright to
  launch that exact binary instead of the one it downloaded for itself. This is the
  same [`executablePath` launch option](https://playwright.dev/docs/api/class-browsertype#browser-type-launch-option-executable-path)
  documented on `BrowserType.launch` for any Playwright script, not something
  specific to the MCP server.

Because the patched Firefox this project ships is a real Firefox binary that stock
Playwright launches unmodified, those two options are enough to point the MCP server
at it. Nothing custom is required on the MCP side. You are using a documented Firefox
launch option to hand it a different Firefox.

## Point the MCP server at the patched binary

First get the absolute path of the cached engine. The `fetch` command downloads it
if missing, verifies it, and prints the path as its last line:

```bash
FIREFOX="$(invisible-playwright fetch)"
echo "$FIREFOX"
```

Then hand that path to Playwright MCP. As command-line flags:

```bash
npx @playwright/mcp@latest --browser firefox --executable-path "$FIREFOX"
```

Or, if your MCP client is configured from a JSON file, the same thing declaratively:

```json
{
  "mcpServers": {
    "playwright": {
      "command": "npx",
      "args": [
        "@playwright/mcp@latest",
        "--browser", "firefox",
        "--executable-path", "/home/you/.cache/invisible-playwright/firefox-18/firefox"
      ]
    }
  }
}
```

The environment-variable form is handy when you cannot edit the args, for example
inside a container entrypoint:

```bash
export PLAYWRIGHT_MCP_EXECUTABLE_PATH="$(invisible-playwright fetch)"
npx @playwright/mcp@latest --browser firefox
```

Now every tool the model calls runs against the patched engine. The C++-level work
that is baked into the binary applies no matter who launches it: the TLS handshake
belongs to a genuine Firefox, the driver layer does not announce itself, and the
JavaScript surfaces the engine fixes at the source read like a real browser rather
than a headless one.

## What the executablePath gets you, and what it does not

This is the part worth being precise about, because it is easy to assume the
executablePath got you everything and then wonder why a fingerprint page still looks
half-real.

A stealth build has two layers. One is compiled into the binary: the handshake, the
driver layer, the engine-level API behaviour. That layer travels with the executable,
so a bare `executablePath` gets all of it.

The other layer is applied per profile at launch time. When a session starts through
this project's launcher, `invisible-core` derives a full identity from the seed and
writes it into the profile: the per-session screen metrics, the timezone and language
that have to agree with your exit, the WebRTC preferences, and the roughly 400
correlated fingerprint fields that make one machine look like one specific real
machine. A raw `executablePath` launch from a generic MCP server skips that step
entirely. Playwright starts the binary with a throwaway profile and none of the
seed-derived prefs are present.

So the honest split is:

- **executablePath alone**: the engine reads real. TLS, driver layer and the
  built-in API fixes are all there.
- **missing without a proper launch**: the per-profile, per-seed identity. Screen,
  timezone, language, WebRTC prefs and the correlated fingerprint fields are not
  applied, so those surfaces fall back to whatever the bare engine reports.

If you only need the engine-level realness, the MCP seam is enough. If you want the
full disguise, you launch through the wrapper.

## Apply the full profile: launch through invisible_playwright

The full profile is applied when the session is launched through
`invisible_playwright` rather than by a bare `executablePath`. The launch is the
same two lines as any other use of the wrapper, and what it returns is a real
Playwright `Browser`, so the navigate-and-click operations an MCP server performs are
just ordinary Playwright calls:

```python
from invisible_playwright import InvisiblePlaywright

with InvisiblePlaywright(seed=42, proxy={"server": "socks5://gate.example.com:1080",
                                         "username": "u", "password": "p"}) as browser:
    page = browser.new_page()
    page.goto("https://example.com")
    page.click("#next")          # mouse arcs to the button on a Bezier curve
    page.fill("#q", "hello")
```

Everything an MCP tool does maps onto methods on that `browser` and its pages. If your
agent framework wants a browser handle rather than a subprocess it launches itself,
the pattern is to launch here and hand the live object to the framework's browser
toolkit, which is exactly what the
[LangChain integration](langchain-agent-invisible-playwright-browser.md) does. The
`seed=42` above makes the whole identity reproducible, so a failing agent run can be
replayed against the same machine instead of a fresh random one, and you can
[pin individual fields](pinning.md) while leaving the rest seed-derived.

Whether the two layers or one is the right choice for your setup is the same decision
as [picking a stealth level](playwright-stealth-levels.md): do not stack a second
page-level spoofer on top of the engine, because two disguises answering the same
question produce a contradiction neither produces alone.

## What neither approach fixes

Both the MCP seam and a full wrapper launch make the browser look like a real browser.
Neither makes the session look like a real person on a trusted network, and it is
worth stating plainly so a clean fingerprint is not mistaken for a clean session.

- **IP reputation.** A perfect browser on a datacenter address is still on a
  datacenter address. The engine cannot change where the request exits. You supply a
  clean residential proxy; see [Configuration](configuration.md) for how the proxy is
  passed and how the browser timezone is derived from the exit.
- **Per-account quotas and rate limits.** These are counted server-side against the
  account and the address, not read off the browser. A convincing fingerprint does
  not raise a limit.
- **Behaviour and timing.** An agent that navigates, reads, and clicks at machine
  speed with no dwell between steps produces a rhythm no human produces. For an MCP
  agent this is the signal most likely to be left over, because the model's own
  latency shapes the pauses. The
  [AI browser agents guide](ai-browser-agents-stealth.md) covers what to do about the
  pause shaped like model latency.

This is why the product is designed to look like a real browser driven by a real
person, and why it passes most detection checks that read the fingerprint, the TLS
handshake and the driver layer, while still leaving the address and the pacing to you.

## Conclusion

Microsoft's Playwright MCP gives you a clean seam: `browserName: firefox` plus an
`executablePath` pointing at the patched binary, and every tool call runs against a
real, stealth Firefox engine. That gets you the compiled layer, which is most of what
reads as a bot at the engine level. It does not get you the per-seed profile that a
launch through `invisible_playwright` writes, and it does not touch your IP, your
quotas, or the rhythm of the agent's actions. Point the MCP server at the binary when
the engine is all you need; launch through the wrapper when you want the whole
identity; and supply a clean exit and human pacing either way.

## Short answers to the questions that lead here

**Can Playwright MCP use a custom Firefox binary?** Yes. Pass `--browser firefox`
with `--executable-path <path>`, or set `launchOptions.executablePath` in a config
file, or the `PLAYWRIGHT_MCP_EXECUTABLE_PATH` environment variable. Get the path from
`invisible-playwright fetch`.

**Does pointing executablePath at the patched binary make it fully stealth?** It gets
the engine layer, which is the TLS handshake, the driver layer and the built-in API
fixes. It does not get the per-seed profile, so the screen, timezone, language and
WebRTC prefs are not applied. A launch through `invisible_playwright` applies those.

**Which is better, the MCP executablePath or launching through the wrapper?** The MCP
seam if you only need engine-level realness; the wrapper launch if you want the full
seed-derived identity. Do not run both a patched engine and a page-level stealth
plugin at once.

**Will this get my agent past every check?** No, and be wary of anything that says it
will. It makes the browser read as real, which passes most fingerprint, TLS and
driver checks. It does not fix a datacenter IP, an account over its quota, or an
agent that clicks at machine speed.

**Do I still need a proxy?** For anything IP-sensitive, yes. The engine cannot change
where the request exits, and a clean fingerprint on a known-bad address still loses.

**How do I make agent runs reproducible?** Launch with a fixed `seed`. The same seed
gives the same machine every run, so a failed agent session can be replayed exactly
rather than hoping the next random draw reproduces it.

## Sources

- Microsoft's Playwright MCP launch options: `--browser`, `--executable-path` and the
  `PLAYWRIGHT_MCP_EXECUTABLE_PATH` environment variable, read from its own
  documentation rather than assumed.
- The underlying [`executablePath` launch option on `BrowserType.launch`](https://playwright.dev/docs/api/class-browsertype#browser-type-launch-option-executable-path),
  Playwright's own documentation for the mechanism `--executable-path` maps to.
- This project's own launch path, which applies the per-seed profile that a bare
  `executablePath` does not, and the `invisible-playwright fetch` command that prints
  the verified engine path.

**See also:** [AI browser agents and stealth](ai-browser-agents-stealth.md) for the
behaviour and timing an engine swap cannot fix, [the LangChain integration](langchain-agent-invisible-playwright-browser.md)
for handing a launched browser to an agent framework, and the
[CLI reference](cli-reference.md) for `fetch` and where the engine is cached.

---

*Written while maintaining [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level driven by stock Playwright. The executablePath seam
is real and useful; the honest half of the page is that the seam is the engine, not
the profile.*
