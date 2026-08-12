---
title: "Using the engine from Go, Java, C#, Ruby and Rust"
description: "The package is Python; the engine is not. Every official Playwright binding exposes the two launch options this engine needs, in Go, Java, C#, Ruby or Rust."
parent: "Integrations"
nav_order: 8
---

# Using the engine from Go, Java, C#, Ruby and Rust

The package is Python. The engine is not, and you can drive this patched Firefox from
Go, Java, C#, Ruby or Rust, because every
official Playwright binding exposes the two launch options it needs. Python is how
you obtain and configure the browser, not a requirement for driving it.

Every official Playwright binding exposes the same two launch options, because they
all mirror the same API: `executablePath` and `firefoxUserPrefs`. Those are exactly
the two halves this project needs, so the patched Firefox runs from any of them. The
Python package is how you obtain and configure it, not a requirement for driving it.

Verified 2026-07-27 by reading each binding's own launch options.

| Binding | Both options present |
|---|---|
| [playwright-go](https://github.com/mxschmitt/playwright-go) | `ExecutablePath`, `FirefoxUserPrefs` in `generated-structs.go` |
| [playwright-dotnet](https://github.com/microsoft/playwright-dotnet) | `ExecutablePath`, `FirefoxUserPrefs` in `BrowserTypeLaunchOptions.cs` |
| [playwright-java](https://github.com/microsoft/playwright-java) | `setExecutablePath`, `setFirefoxUserPrefs` on `LaunchOptions` |
| [playwright-ruby-client](https://github.com/YusukeIwaki/playwright-ruby-client) | `executablePath:`, `firefoxUserPrefs:` on `launch` |
| [playwright-rust](https://github.com/padamson/playwright-rust) | `executable_path()`, `firefox_user_prefs()` on the launch builder |

## Step one: get the two values

The binary has a CLI:

```bash
pip install invisible-playwright
invisible-playwright fetch     # downloads and caches it once, prints the absolute path
```

The preferences do not, and that is a real gap instead of an omission from this
page: there is no `prefs` subcommand today, so you generate them with a one-liner
and keep the JSON.

```bash
python -c "import json;from invisible_playwright import get_default_stealth_prefs;print(json.dumps(get_default_stealth_prefs(seed=1, humanize=True)))" > prefs.json
```

Regenerate it when you upgrade the package, since the pref set moves with the engine.
Treat `prefs.json` as belonging to a specific seed and a specific version, and keep
the seed written down next to it.

## Step two: launch

**Go**

```go
pw, _ := playwright.Run()
prefs := map[string]interface{}{}
raw, _ := os.ReadFile("prefs.json")
json.Unmarshal(raw, &prefs)

browser, _ := pw.Firefox.Launch(playwright.BrowserTypeLaunchOptions{
    ExecutablePath:   playwright.String("/path/from/invisible-playwright fetch"),
    FirefoxUserPrefs: prefs,
    Headless:         playwright.Bool(true),
})
```

**Java**

```java
Map<String, Object> prefs = new ObjectMapper()
        .readValue(new File("prefs.json"), new TypeReference<>() {});

Browser browser = playwright.firefox().launch(new BrowserType.LaunchOptions()
        .setExecutablePath(Paths.get("/path/from/invisible-playwright fetch"))
        .setFirefoxUserPrefs(prefs)
        .setHeadless(true));
```

**C#**

```csharp
var prefs = JsonSerializer.Deserialize<Dictionary<string, object>>(
        File.ReadAllText("prefs.json"));

var browser = await playwright.Firefox.LaunchAsync(new BrowserTypeLaunchOptions
{
    ExecutablePath   = "/path/from/invisible-playwright fetch",
    FirefoxUserPrefs = prefs,
    Headless         = true,
});
```

**Ruby**

```ruby
prefs = JSON.parse(File.read('prefs.json'))

playwright.firefox.launch(
  executablePath: '/path/from/invisible-playwright fetch',
  firefoxUserPrefs: prefs,
  headless: true,
)
```

## What you give up outside Python

Be clear-eyed about this. The Python wrapper does more than hand over two values, and
none of the extra is in the JSON:

- **Timezone and locale resolution.** `timezone="auto"` and `locale="auto"` look up
  the proxy's exit IP at launch and derive the zone and language list from it. Here
  you get whatever the prefs were generated with, so a session leaving from another
  country will disagree with itself unless you regenerate per proxy.
- **Humanised pointer motion.** The prefs turn the feature on, but the paths are
  drawn from the seed by the driver, so the pointer moves like a pointer only in
  Python.
- **The pin check.** The wrapper refuses to run a binary that does not match the
  version of the config that produced the prefs. Nothing enforces that pairing here.
  If you upgrade the binary and keep an old `prefs.json`, nothing will tell you.

So this route is right when the surrounding system is already in Go or Java and
rewriting it is not on the table. When the choice is open, Python gets you the parts
that are hard to reproduce by hand.

## Checking it worked

Navigate to any fingerprint page and read back the user agent and the WebGL renderer.
The user agent should say Firefox and the version should match
`invisible-playwright version`. If the renderer comes back as a software or basic
renderer, the prefs did not apply, or the machine has no GPU, and either way that is
worth knowing before you trust the session.

## Short answers to the questions that lead here

**Can I use this from Go, Java, C# or Ruby?** Yes. Every official Playwright binding
exposes `executablePath` and `firefoxUserPrefs`, because they all mirror the same API,
and those two options are the whole contract.

**Do I still need Python?** Only to obtain the binary and to generate the preferences
once. The automation itself does not.

**Is there a CLI for the preferences?** Not today. There is `fetch` for the binary,
which prints its path as its last line; the preferences come from a one-line Python
call whose JSON you keep.

**How often do I regenerate `prefs.json`?** Whenever you upgrade the package. The
preference set moves with the engine, and an old file paired with a new binary is a
mismatch that nothing warns you about.

**What do I lose outside Python?** Humanised pointer motion, timezone and locale
resolved from the proxy exit, and the pin check that refuses a binary which does not
match the config that produced the prefs.
