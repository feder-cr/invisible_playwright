"""The Playwright side of the engine: a server that speaks Playwright's protocol and drives Juggler.

| module | what it does |
|---|---|
| `server.py` | the Playwright channel: browsers, contexts, pages, frames, routes; every call ends in the Juggler client |
| `dispatcher.py` | the object tree Playwright's client talks to, and the guids it names them by |
| `_marshal.py` | values across the channel, both ways |
| `transport.py` | the in-process transport between the client and this server |
| `perimeter.py` | what the server refuses on purpose, said as errors rather than pretended |

⛔ THE JUGGLER CLIENT ITSELF IS NOT HERE. Since 0.30.0 the pipe, the protocol
mirror, the frame lifecycle, the injected script, the input actions, the
keyboard and the human rhythm (`connection`, `protocol`, `lifecycle`,
`injected`, `actions`, `keyboard`, `keylayout`, `_profile`, `_behaviour`,
`_motion`, `_pacing`) live once in `invisible_core.juggler`, shared with
invisible-selenium and invisible-puppeteer (decision D85). A copy of any of them
in this folder is the defect that move removed, and
`tests/test_juggler_lives_in_the_core.py` refuses it.

⛔ What lives here REACHES the user, because it sits under
`src/invisible_playwright/`. A tool that only serves us goes in `scripts/`,
which the wheel does not include.
"""
