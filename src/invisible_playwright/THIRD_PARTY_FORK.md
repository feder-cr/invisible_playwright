# Playwright fork bundled in this package

This package includes and redistributes a MODIFIED copy of
[Playwright](https://github.com/microsoft/playwright), by Microsoft Corporation,
under the **Apache-2.0** license. The `invisible_playwright` code that is not in
the folder below stays **MIT**; `pyproject.toml` declares it as
`license = "MIT AND Apache-2.0"`, because saying MIT alone would misstate what
the user receives.

## What is vendored

| folder | what it is | license |
|---|---|---|
| `_pw/` | Playwright's Python client, version **1.61.1** | Apache-2.0 (`_pw/LICENSE`) |

The client's namespace is `invisible_playwright._pw` instead of `playwright`, so
it does not collide with a stock `playwright` that might be installed alongside
it.

## ⛔ The Node driver was removed on 2026-08-28

`_driver/` held Playwright's Node driver - 6 MB of JavaScript in git - and
`_node.py` downloaded a 92 MB `node.exe` on first use because the runtime is too
large to version. Both are gone, together with `_pw/_impl/_driver.py` and
`PipeTransport`. **A first install now downloads nothing but the browser.**

What answers the client instead is the in-process Python server in `_juggler/`,
which speaks the same protocol. It became the default on the same day, and it
did so on evidence rather than on the code looking finished:

- `run_e2e.py`: **188 passed on BOTH transports**, zero failures;
- the transport judge: 50 green on both, **0 red only on the Python arm**;
- the protocol diff: **parity** on methods, parameter names, object types,
  initializer field names, events and object parentage;
- the realness gates, run on that path for the first time: FpJS Pro all critical
  flags clean (18/18), fingerprint consistency 43/43, and no observable
  crossings into the page's realm.

### What went with it

- `invisible-playwright show-trace`. The trace viewer is a Node application.
  Smaller than it looks: tracing is outside this package's perimeter and its
  dispatchers refuse it by name, so that command could only ever open a trace
  some other tool had produced.
- The redistribution notices that belonged to the driver bundle - `LICENSE`,
  `NOTICE`, `ThirdPartyNotices.txt` - which covered code that is no longer
  shipped. ⛔ The Apache-2.0 declaration itself did NOT go with them: `_pw/` is
  still here and still redistributed, and `tests/test_fork.py` asserts that
  `_pw/LICENSE` ships.
- The ability to RE-DERIVE three things that were extracted from the bundle.
  All three artefacts are committed and still valid; what is gone is
  regenerating them. `injected.js` (the selector engines, actionability and
  `expect` that run in the page), `keylayout.py` (both in `invisible_core.juggler`
  since 0.30.0, with their extractors in the core's `scripts/`), and the four rules
  `prefs_byte_parity.py` compares our `user.js` writer against - now frozen in
  `tests/gates/driver_prefs_rules.json` with the sha of the bundle and of the
  exact window they came from.
- ⛔ And the second arm. `judge_both_transports.py` and `diff_protocol.py` exist
  only while there are two implementations to compare. To get one back:

      git worktree add /tmp/judge <the last commit that carried _driver/>
      INVPW_DRIVER_TREE=/tmp/judge python scripts/judge_both_transports.py <bin>

  A worktree rather than an installed release, because a published wrapper pins
  a published core and a sealed engine and therefore cannot drive a locally
  built binary - which is the only kind worth judging.

### The changes that used to live in the driver bundle

They are history now, kept because they say what the fork was for:

- **[B177]** fixed `set_content`, which in the stock driver did not wait for
  loading the way a page driven indistinguishably needs. ⛔ Not lost: the Python
  server implements `set_content` itself, through `document.open/write/close` in
  the MAIN world, and its docstring names the same defect - the utility world
  has an extended principal and Gecko refuses `document.open()` from there.
- **Removed ~643 KB** of subsystems this package does not use: android and
  electron support, the bidi protocol, the recorder, and the chromium and webkit
  engines.
- **Neutralized `_exposeConsoleApi`** and **removed `console.debug`** from the
  injected code, both because a page could observe them. ⛔ Not lost: those
  changes are in `injected.js`, which is extracted, committed, and still what
  the server injects (from `invisible_core.juggler` since 0.30.0). The core's
  `scripts/gen_injected_source.py --check` refuses any bundle that
  does not carry the `MODIFIED by invisible_playwright` markers, so an upstream
  bundle can never be mistaken for ours.

## Why `_pw/` is in git

Without it the package **installs but does not import**: `launcher.py` imports
from `invisible_playwright._pw`, and a wheel built from a checkout that does not
contain it raises `ImportError` on every `import invisible_playwright`. Leaving
it out of git was a broken package that only the user would see. The full story
of the decision is in the workbench, in
`docs/firefox-stealth-architecture/72-next-steps.md`, entry 23.
