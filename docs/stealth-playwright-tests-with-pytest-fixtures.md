---
title: "Run stealth Playwright tests with pytest fixtures"
description: "Wire pytest-asyncio fixtures around invisible_playwright: a session-scoped stealth browser, a per-test seeded context, and fingerprint realness assertions."
parent: "Scraping with Playwright"
grand_parent: "Guides"
nav_order: 81
---


# Run stealth Playwright tests with pytest fixtures

If you drive a stealth browser from a Python test suite, you want two things that pull
in opposite directions: launch the browser as few times as possible, because launching
is the slow part, and give every test a clean, isolated identity, because shared state
between tests is how a green suite hides a real bug.

pytest's fixture scopes are built for exactly this tension. A session-scoped fixture runs
once for the whole suite; a function-scoped fixture runs fresh for every test. This page
wires that pair around invisible_playwright, shows how to assert fingerprint realness
inside a test against an allowed open-source detector, and is honest about the one thing
a passing fixture cannot prove.

Because invisible_playwright returns a real Playwright
[`Browser`](https://playwright.dev/python/docs/api/class-browser), none of this needs a
special test harness. It is the ordinary pytest-asyncio setup you already know, pointed
at a browser that reads as a genuine Firefox instead of an automated one.

## The fixture pair, and why Python changes the shape

The integration point here is different from a JavaScript test-runner plugin. There, the
runner owns the browser and a plugin injects behaviour into it. In Python you are driving
the wrapper directly: your fixtures decide when the engine launches and what identity each
test gets, with nothing in between.

That directness is the whole reason the fixture pair works cleanly:

- A **session-scoped** fixture launches invisible_playwright once. The engine resolves and
  starts a single time for the entire run, and every test shares that one process.
- A **function-scoped** fixture hands each test its own
  [browsing context](https://playwright.dev/python/docs/browser-contexts), torn down when
  the test ends, so cookies and storage never leak from one test into the next.

Install what you need first:

```bash
pip install invisible-playwright pytest pytest-asyncio
```

And turn on pytest-asyncio's auto mode so you do not decorate every test by hand. In
`pytest.ini` (or the `[tool.pytest.ini_options]` table of `pyproject.toml`):

```ini
[pytest]
asyncio_mode = auto
```

## A session browser and a per-test context

The launch itself is the same two lines as the [Quickstart](quickstart.md); the fixture
just wraps them so the suite shares one browser. Note the `loop_scope="session"` on the
session fixture: a session-scoped async fixture needs a session-scoped event loop, and
leaving it off is the most common reason this setup fails on the first async test.

```python
# conftest.py
import pytest_asyncio
from invisible_playwright.async_api import InvisiblePlaywright


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def browser():
    # launched once for the whole suite; seed fixed so the run is reproducible
    sf = InvisiblePlaywright(seed=42)
    async with sf as b:
        yield b


@pytest_asyncio.fixture
async def context(browser):
    # fresh per test: its own cookies, storage and pages, closed afterwards
    ctx = await browser.new_context()
    yield ctx
    await ctx.close()
```

A test then asks only for `context` and gets an isolated surface:

```python
async def test_loads_target_page(context):
    page = await context.new_page()
    resp = await page.goto("https://example.com")
    assert resp is not None and resp.ok
```

Every test that takes `context` shares one launched browser but never shares state. That
is the common case, and for a suite that just needs a real browser it is all you need.

## Asserting fingerprint realness inside a test

Because the test harness is Python and the browser is real, you can navigate to an allowed
open-source detector and assert on what it reports, in the same test file as everything
else. The rule from [how to test whether your browser is detected](how-to-test-bot-detection.md)
applies unchanged here: assert the presence of the right signal, not the absence of a
wrong one, because a blocked or empty result satisfies a negative assertion for free.

sannysoft is the cheap smoke test. Its `navigator.webdriver` row should read as a real
browser, not as an automated one:

```python
async def test_webdriver_row_reads_real(context):
    page = await context.new_page()
    await page.goto("https://bot.sannysoft.com")
    await page.wait_for_selector("#webdriver-result")

    verdict = await page.inner_text("#webdriver-result")
    # positive assertion: the cell must actually say "present" the way a
    # real browser does, not merely be non-empty and not merely "missing"
    assert "present" in verdict.lower()
    assert "false" not in verdict.lower()   # a forced false is its own tell
```

CreepJS asks a harder question - whether the browser is lying about itself - and returns a
trust score you can gate on. Give its probes time to finish before you read anything, and
treat a page that never settles as a failure rather than a pass:

```python
async def test_creepjs_trust_is_high(context):
    page = await context.new_page()
    await page.goto("https://abrahamjuliot.github.io/creepjs/")
    await page.wait_for_selector(".trust-score", timeout=30_000)

    text = await page.inner_text(".trust-score")     # e.g. "92% trust score"
    score = int("".join(ch for ch in text if ch.isdigit()))
    assert score >= 80
```

These pass because the fingerprint, the TLS handshake and the driver layer read as a
genuine Firefox rather than as an instrumented one. That is what the product is designed
to do, and it is why most in-page detection checks come back clean. The detectors
themselves have their own pages in this set - see
[how to test whether your browser is detected](how-to-test-bot-detection.md) for what each
one actually proves before you trust any single green cell.

## Per-test seed isolation, so a red test replays

A shared session seed makes the whole suite reproducible, which is usually what you want.
But sometimes you want each test to run as a distinct machine - a different GPU, screen and
audio device - so a fingerprint-dependent bug cannot pass on one identity and hide on the
next. The seed lives at launch, so a per-test identity means a per-test launch:

```python
import pytest_asyncio
from invisible_playwright.async_api import InvisiblePlaywright


@pytest_asyncio.fixture
async def seeded_browser(request):
    # deterministic per-test seed derived from the test's own name
    seed = abs(hash(request.node.name)) % 100_000_000
    sf = InvisiblePlaywright(seed=seed)
    async with sf as b:
        print("seed =", sf.seed)   # printed on failure so the run replays exactly
        yield b


async def test_with_its_own_identity(seeded_browser):
    page = await seeded_browser.new_page()
    await page.goto("https://example.com")
    ...
```

Deriving the seed from the test name keeps it stable: the same test always draws the same
machine, so a failure reproduces on the next run instead of vanishing into a new random
draw. When the assertion goes red, the printed seed is the exact identity to replay while
you debug. If you need to fix specific fields rather than a whole seed - a particular GPU
or screen size while the rest stays seed-derived - that is what
[pinning fingerprint fields](pinning.md) covers.

The trade is speed: a launch per test is slower than a context per test. Use the shared
session browser by default, and reach for the per-test launch only for the tests where a
distinct identity is the point.

## What a green fixture does not prove

Here is the honest limit, and it is the same false-green that runs through the rest of
these notes. A passing fingerprint assertion proves one thing: the browser looks like a
real browser. It says nothing about two factors that decide a real session and that no
in-page assertion can reach.

- **IP reputation.** Your CI runner and your production host have addresses, and a
  datacenter address on a known range loses with a perfect fingerprint. The fixture cannot
  see your exit, so it cannot fail on it. Supply a clean residential exit - the
  [configuration page](configuration.md) covers how the proxy is wired and how the timezone
  follows the egress IP.
- **Behaviour and cadence.** A suite that fires requests back to back at machine speed
  builds a velocity signal the fingerprint never touches. We have flagged our own product
  this way, and the flag belonged to the test harness, not the browser. Human pacing is
  something the reader supplies, not something a green assertion grants.

So a suite where every fingerprint test passes is necessary and not sufficient. It
confirms the browser layer is doing its job; it does not confirm the session will get
through. When a browser that passes every in-page check still gets the wrong page, the
ordered checklist in
[Playwright detected as a bot on one site](playwright-detected-as-bot.md) is written for
exactly that gap, and it puts the IP seventh for a reason.

## Conclusion

The fixture pair is the whole pattern: a session-scoped fixture that launches the stealth
browser once, a function-scoped fixture that hands each test a clean context, and a
per-test-seed variant for when you want a distinct, replayable identity per test. Assert
the presence of the right signal against an allowed open-source detector, print the seed
so a red test reproduces, and keep in mind that the green you get is a fingerprint green.
Pair it with a clean exit and human pacing, and the fixtures are measuring the part they
can actually see.

## Short answers to the questions that lead here

**Can I use invisible_playwright with pytest fixtures?** Yes. It returns a real Playwright
`Browser`, so a session-scoped fixture launches it once and a function-scoped fixture
yields a fresh context, exactly like ordinary pytest-asyncio code.

**Session-scoped or function-scoped for the browser?** Session-scoped, because launching is
the slow part. Isolate state with a function-scoped context off that one browser, and
relaunch per test only when you specifically want a distinct fingerprint each time.

**Why does my session-scoped async fixture error on the first test?** Almost always a
missing `loop_scope="session"`. A session-scoped async fixture needs a session-scoped
event loop, or pytest-asyncio tears the loop down under it.

**How do I give each test its own fingerprint?** The seed lives at launch, so use a
function-scoped fixture that constructs `InvisiblePlaywright(seed=...)` with a seed derived
from the test name. Same test, same machine, every run.

**If my fingerprint assertions pass, am I undetectable?** No, and no tool is. A passing
assertion proves the browser looks real. It does not prove your IP reputation or your
request cadence, and both fail independently of anything the fixture can assert.

**Which detectors can I assert against?** Open-source ones like sannysoft for a smoke test
or CreepJS for a trust score. Assert on a signal being present and correct, not merely on
the page not throwing.

## Sources

- The real invisible_playwright API as documented in [Quickstart](quickstart.md) and
  [Configuration](configuration.md): a two-line launch that returns a stock Playwright
  `Browser`, with a `seed` argument for a reproducible identity.
- Playwright's own docs on [`Browser.new_context()`](https://playwright.dev/python/docs/api/class-browser)
  and [browser contexts](https://playwright.dev/python/docs/browser-contexts): each context
  is an isolated, incognito-like session within one browser instance, which is the isolation
  the function-scoped context fixture relies on.
- This project's own testing notes on the false pass a negative assertion produces, and on
  the velocity flag that belonged to the harness rather than the browser.
- pytest-asyncio's fixture-scope and event-loop-scope behaviour, which is what makes the
  session-browser / function-context split work.

**See also:** [how to test whether your browser is detected](how-to-test-bot-detection.md)
for what each detector actually proves, and
[the checklist for being detected on one site](playwright-detected-as-bot.md) for the
IP-and-behaviour half a fixture cannot reach.

---

*Written while maintaining [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level driven by stock Playwright. The fixtures prove the
browser looks real; the clean exit and the human pacing are still yours to supply.*
