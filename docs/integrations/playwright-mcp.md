---
title: "Using invisible_playwright with Microsoft's Playwright MCP"
description: "Microsoft's own playwright-mcp takes a browser and an executable path as arguments, so pointing it at this engine is configuration, not a separate server."
parent: "Integrations"
nav_order: 7
---

<script type="application/ld+json">
{
  "@context": "https://schema.org",
  "@type": "BreadcrumbList",
  "itemListElement": [
    {
      "@type": "ListItem",
      "position": 1,
      "name": "Home",
      "item": "https://feder-cr.github.io/invisible_playwright/"
    },
    {
      "@type": "ListItem",
      "position": 2,
      "name": "Integrations",
      "item": "https://feder-cr.github.io/invisible_playwright/integrations/README.html"
    },
    {
      "@type": "ListItem",
      "position": 3,
      "name": "Using invisible_playwright with Microsoft's Playwright MCP"
    }
  ]
}
</script>

# Using invisible_playwright with Microsoft's Playwright MCP

You do not need a separate MCP server. Microsoft's own `playwright-mcp` takes a
browser and an executable path as arguments, so pointing it at this engine is
configuration, not code.

Written against `microsoft/playwright-mcp` as of 2026-07-27.

## The short version

```bash
pip install invisible-playwright
python -c "from invisible_playwright import ensure_binary; print(ensure_binary())"
```

The second command downloads the patched Firefox on first run, caches it, and prints
the path. Give that path to the MCP server:

```jsonc
{
  "mcpServers": {
    "playwright": {
      "command": "npx",
      "args": [
        "@playwright/mcp@latest",
        "--browser", "firefox",
        "--executable-path", "/absolute/path/printed/above"
      ]
    }
  }
}
```

The same two settings exist as environment variables, `PLAYWRIGHT_MCP_BROWSER` and
`PLAYWRIGHT_MCP_EXECUTABLE_PATH`, if your client is easier to configure that way.

## What this gets you, and what it does not

It gets you the engine: a Firefox whose fingerprint surfaces are patched in C++
rather than shimmed from JavaScript, driven by the standard MCP tool set.

**It does not get you the seeded profile.** The identity this package generates lives
in `firefox_user_prefs`, and `--executable-path` has no way to carry them. So the
browser reports the build's own defaults, the same on every launch and on every
machine that runs the same build, with no per-session identity and nothing to
reproduce a session from.

That distinction matters more than it sounds. Sharing one identity across every user
of a build is its own signal. If you need a per-session machine, the MCP route is not
the one; drive the browser from Python with `InvisiblePlaywright(seed=...)` instead,
where the prefs are generated and applied for you.

## Making the prefs work anyway

If your client can launch a wrapper script instead of the binary directly, you can
have both. Write the prefs into a profile once, then point the MCP server at a
Firefox that uses it:

```python
from pathlib import Path
from invisible_playwright import ensure_binary, get_default_stealth_prefs

profile = Path.home() / '.invisible-mcp-profile'
profile.mkdir(exist_ok=True)
prefs = get_default_stealth_prefs(seed=1, humanize=True)
(profile / 'user.js').write_text(
    '\n'.join(f'user_pref({k!r}, {str(v).lower() if isinstance(v, bool) else repr(v)});'
              for k, v in prefs.items()),
    encoding='utf-8',
)
print('binary :', ensure_binary())
print('profile:', profile)
```

Then add `--user-data-dir /path/to/.invisible-mcp-profile` to the MCP server's
arguments alongside `--executable-path`. Firefox reads `user.js` on startup and
applies it over the profile's own prefs.

This is a workaround, not the supported path. It pins one identity for that profile
rather than one per session, and the profile keeps cookies and storage between runs,
which is either what you wanted or a correlation problem depending on the job.

## Checking it worked

Ask the agent to navigate to a fingerprint page and read back the user agent and the
WebGL renderer. If the user agent says Firefox and the renderer is a consumer GPU
rather than a software rasterizer, the engine is live. If the renderer comes back as
a basic or software renderer, you are on a machine with no GPU and that is worth
knowing before you trust the session.

## Short answers to the questions that lead here

**Is there an MCP server for this project?** No, and there does not need to be.
Microsoft's own `playwright-mcp` accepts `--browser firefox` together with
`--executable-path`, which is both halves of what this engine needs.

**Can an MCP client drive a stealth browser at all?** Yes, as long as the client lets
you set the launch arguments. If it does, you can point it at any binary.

**How do I get the preferences in?** Through a wrapper script that the client launches
instead of the binary directly, so the profile is applied before the browser starts.

**Does this give me humanised pointer motion?** No. That is drawn by the Python driver,
so an MCP session moves the pointer the way any automation does.

**Is this useful for AI agents?** It is the practical route when an agent framework
already speaks MCP and rewriting it around a Python wrapper is not on the table.
