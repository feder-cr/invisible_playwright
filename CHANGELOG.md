# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.8.2] - 2026-08-31

### Changed

- **The pinned engine moves to firefox-25, which has no updater.** Until now
  every session showed an "Update available" badge in the toolbar shortly after
  start. No preference could remove it: `app.update.auto` stops the download
  rather than the notification, `app.update.suppressPrompts` only delays the
  doorhanger because the badge is shown immediately and unconditionally, and
  `app.update.disabledForTesting` is inert unless the browser is driven by
  Marionette or the remote agent, which this package is not.

  The engine is therefore built without the update machinery at all: the build
  options are gone rather than switched off, so nothing checks for an update,
  nothing can install one over the binary the seal pins, and the badge has no
  code left to draw it. The update channel is unchanged, because the
  application's remoting name derives from it.

  One consequence is worth stating: a retail Firefox contacts Mozilla's update
  service at startup and this build no longer does. No page can observe that -
  no API exposes the updater's state - but anything watching the connection
  can.

## [0.8.1] - 2026-08-30

### Fixed

- **`proxy=` with an `http` or `https` scheme was accepted and ignored.** The
  page went out on the host's address while the timezone, the locale and the
  WebRTC candidate had all been resolved through the proxy, so the session
  announced one country and connected from another. That is worse than having
  no proxy: it is the mismatch this package exists to avoid, produced by the
  package. SOCKS was unaffected, which is why it went unnoticed.

  The cause was three ways of expressing one thing, with the scheme choosing
  between them: SOCKS wrote `network.proxy.*` preferences, HTTP was handed to
  the Playwright driver to route per channel, and HTTP without a driver got
  different preferences again. Removing the Node driver removed the middle one
  and nothing said so. The other two are now gone rather than repaired: the
  endpoint is read once and routed by the engine command, for every scheme. A
  proxy that cannot be expressed refuses the launch instead of starting a
  browser without it. Reported from outside, with a 24-site case study behind
  it.
- **SOCKS proxies that require a username and password could not connect at
  all.** The credentials reached the engine's channel filter and stopped there,
  so the browser offered "no authentication" and the proxy closed the
  connection. The symptom was a connection refused, which reads like a dead
  proxy rather than a missing password. Needs the matching engine release.
- **`profile_dir=` raised `KeyError: 'browser'`.** A persistent context never
  opened: the reply carried the context and not the browser, and the client
  reads both.

### Changed

- Pins `invisible-core` 24.16.0, which seals the `firefox-24` engine. The proxy
  credentials fix lives in the engine, so on an older binary that one is inert.

## [0.8.0] - 2026-08-30

### Removed

- The Node driver. `invisible_playwright._driver` (6 MB of vendored JavaScript)
  and the `node.exe` downloader are gone, and the browser is now driven from
  Python over the Juggler protocol directly. The wheel goes from 1.6 MB to
  0.7 MB and the installed package from 11.2 MB to 6.5 MB, and a first install
  no longer downloads a 92 MB Node runtime - it fetches the browser and nothing
  else.
- `invisible-playwright show-trace`. The trace viewer is a Node application and
  left with the runtime that ran it. Traces themselves are unaffected: they are
  still recorded, and `playwright show-trace` from an ordinary Playwright
  install opens them.

### Changed

- Pins `invisible-core` 23.16.0, which seals the `firefox-23` engine. That is the
  engine carrying the two fixes below: both need it, and on an older binary the
  Python half of each is inert.
- The visible pointer overlay is ON by default. It draws the Windows arrow,
  with the package logo's green halo around it, in the browser's own chrome
  window - which the page cannot reach, so no site sees a difference either
  way. What it changes is what a person watching the screen sees. Pass
  `show_cursor=False` for the previous behaviour.

### Fixed

- A teardown that was cancelled left the browser running. `asyncio.CancelledError`
  inherits from `BaseException`, not from `Exception`, so the guard around each
  close step never caught it: cancel the task and teardown stopped wherever it
  had reached, skipping every later step including the one that reaps whatever
  the close did not. A cancellation is now caught, kept, and re-raised once every
  step has run, so it still propagates and nothing is left behind. Reported by
  DatGuy1 in #104.
- A browser that had opened a few hundred pages stopped delivering events
  entirely while still answering commands, so the next `new_page()` timed out
  waiting for a session that had already been announced. Event delivery was a
  chain of nested calls that grew by three per page and never shrank; past
  roughly 330 pages it crossed Python's recursion limit, and the failure was
  swallowed by the handler that keeps a bad callback from killing the
  connection. Delivery is now flat and subscribers are removed when their page
  closes.
- A closed page is now disposed, so neither the client's nor the server's
  object registry grows for the life of the browser.
- A CSS query on a page returned the browser's own form widgets. Firefox builds
  the controls inside `<input type=date|time>` in a shadow root it marks closed,
  and the engine handed those to automation along with the closed roots a page
  had authored, so the selector engine collected them as if the page had written
  them: `page.locator("button")` answered 2 on a document with one button, and
  the extra match was invisible. The damage was not the count. A click on the
  invisible match reported success and sent nothing, so a site that had not
  blocked anything looked like it had. Needs the matching engine release; the
  guard is `Element::GetShadowRootForBindings` refusing UA widgets, which is
  what the sibling API has done upstream since bug 2035665. Reported from
  outside, with a 24-site case study behind it.
- `Response.text()` and `Response.body()` read the body again. The command they
  rest on had been removed from Juggler while trimming it, and two more callers
  used it without saying so: traces and HARs recorded with embedded content were
  being written with every response body empty, and nothing raised. Bodies cost
  memory again, bounded as upstream bounds them: 100 MB per tab, 10 MB per
  response, oldest evicted first. Reading one from inside a `page.on("response")`
  handler now waits for the request to finish instead of racing it, which is why
  the main document used to fail where subresources did not.

## [0.7.4] - 2026-08-27

### Changed

- Pins `invisible-core` 21.16.0, which pins the `firefox-21` engine.
- The CI font gate writes its preferences into the profile instead of sending them
  over the protocol. From `firefox-21` the engine refuses `Browser.enable` with a
  `userPrefs` field rather than applying it late: preferences that arrive after
  startup mean the first launch initialises graphics and fonts with the defaults
  and the second with the stealth values, two different code paths. Nothing changes
  for users of this package - the vendored driver already writes them into the
  profile before startup.

## [0.7.3] - 2026-08-26

### Changed

- The Playwright client is now vendored inside the package. `invisible_playwright._pw`
  (client) and `_driver` (Node driver) ship with the wheel, and `playwright` is no
  longer a dependency: the code that runs is the code we ship. The vendored copy
  carries four changes over upstream 1.61.1 - `set_content` waits for load the way a
  driven page needs, ~643 KB of unused subsystems removed (android, electron, bidi,
  recorder, chromium, webkit), `_exposeConsoleApi` neutralised, and `console.debug`
  dropped from injected code. It is Apache-2.0 inside an otherwise MIT package;
  `pyproject.toml` declares `MIT AND Apache-2.0` and `THIRD_PARTY_FORK.md` records
  the provenance.
- Pins `invisible-core` 20.16.0.

### Removed

- **macOS is no longer supported.** Releases stopped at `firefox-20`; the CI builds
  Windows and Linux only. On a Mac the package now refuses at launch with a message
  that says why, instead of trying to download a binary that will not exist. Already
  published macOS assets are untouched.

### Fixed

- Without a proxy the egress IP was discovered twice before the browser started -
  two identical requests to an external service, from the real address, where a real
  user makes none. It is discovered once now.
- `pyee` and `greenlet` are declared explicitly. They were transitive dependencies of
  `playwright`; removing that dependency removed them too, while the vendored client
  still imports them, so the wheel installed and then failed to import.

## [0.7.2] - 2026-08-18

### Fixed

- Pins `invisible-core` 20.15.0, which carries a fix for a defect that lived in
  17 published core versions. An `http://` or `https://` proxy handed to
  `build_launch_plan` produced NO proxy preference at all: `configure_proxy`
  returns a non-SOCKS endpoint to its caller, because only Playwright can answer
  a proxy's 407, and that path launches the binary with `subprocess` and had
  nowhere to put it. The browser then went out on the machine's own address
  while the geo layer had already resolved timezone, locale and egress THROUGH
  the proxy, so the session announced one country and connected from another.

  **This wrapper was never affected and needs no change on your side.** It hands
  the endpoint to Playwright, and that was measured: the same exit IP as curl
  through the same proxy, in http and in socks5. The fix matters if you also use
  `invisible_core.launch.build_launch_plan` directly.

### Removed

- The `browser launches` badge. The counter behind it was a release asset hosted
  on a repository that was deleted on 2026-08-18, so the series is frozen on its
  last real value and the renderer would have redrawn that number every morning.
  A frozen figure presented as current is worse than no figure. The history is
  untouched and the SVG on the `badges` branch stays, because already-published
  PyPI pages are serving it.

## [0.7.1] - 2026-08-17

### Changed

- Pins `invisible-core` 20.14.0, which seals firefox-20. The engine release is
  the memory one: the same fingerprint at the same seed, the same detector
  verdicts, less RAM and less CPU. The font faces stopped being copied twice on
  their way into the shadow list, the bundle is opened by path instead of being
  held in the heap, and the `.ttc` accounting now counts the faces we DECLARE
  rather than every face the file happens to carry.

### Fixed

- This entry itself was missing until 2026-08-18. 0.7.1 shipped to the index on
  2026-08-17 and the changelog stopped at 0.7.0, so for a day the released
  version was undocumented. It was not caught by the release: the guard that
  compares the index against this file, `test_the_changelog_documents_every_
  version_it_claims_to_cover` in `tests/test_release_e2e.py`, is marked `e2e`
  and the default selection deselects it. It fired on the first full e2e run
  after the release, which is the run that happens for a reason unrelated to
  releasing.

## [0.7.0] - 2026-08-11

### Changed

- Pins `invisible-core` 19.14.0, which seals firefox-19. The engine release
  carries a `seal.json` asset for the first time, so the seal a client verifies
  and the seal the build produced are the same bytes rather than two things
  that agree.
- `session_kwargs` in the test bench no longer pins the proxy exit. It used a
  literal session id, and the providers are sticky on it, so every realness
  measurement this project made left through the same address: one distinct IP
  in twenty-four hours, eleven events on it in one hour, and the detector had
  classified it as a datacenter. The same binary on fresh exits scores 0, 3 and
  0 where it scored 14. Consistency runs still pin one exit for their pair,
  because that comparison is about our own determinism and a rotating exit puts
  the network inside it.

### Fixed

- `scripts/ci_font_gate.py` asked a question whose answer could not mean
  anything. It launched the engine raw, which since the generic-family map
  became a declaration means launching it without one - and the engine does not
  invent declarations. And it inferred "did this face load" from a line height
  that we declare: it worked while the fallback measured 91 and the families 95,
  and stopped the day the metrics became declared and the fallback landed on 95.
  It now hands in the one declaration it needs and asserts which families SHARE
  a face, measured from the ink box. Green on this build, on the previous
  release, and on both platforms.

### Added

- Two e2e tests for failures the suite could not see: a session that dies under
  heavy text shaping, and a frame reporting its parent's screen origin instead
  of its own. Both validated by reintroducing the real defect in the engine and
  rebuilding, not by reasoning about them.

## [0.6.1] - 2026-08-05

### Changed

- Pins `invisible-core` 18.13.0, which puts an upper bound in wall-clock time on the engine download. A `requests` timeout is per socket operation, so a connection delivering a byte every 59 seconds satisfied it forever and the transfer had no total limit; `INVISIBLE_DOWNLOAD_DEADLINE` (default 1800s) bounds it, and the refusal names the deadline, the elapsed time and the bytes received. Set it to 0 on a genuinely slow link.
- README: the Telegram invitation moved to the top and the status badges to the bottom.

## [0.6.0] - 2026-08-01

### Fixed
- `browser.new_page()` now gives the page the same context `browser.new_context()` does: the profile's viewport, screen, DPR, colour scheme, locale and timezone. It did not, because Playwright's `Browser.new_page` forwards to the IMPLEMENTATION object, whose own `new_page` calls `new_context` on itself, so a wrapper installed on the api object was never consulted. Measured in a real browser with the seed the e2e uses: `new_page` gave `innerWidth` 1280, Playwright's stock viewport, against a fingerprint reporting `screenWidth` 1920, while `new_context` gave 1906; a dark-pinned profile came back light on the same path. `new_page` is the call in this package's README, in the class docstring and in every example it ships, which is why this is a minor bump and not a patch.
- The engine-mismatch message told you to run `fetch --force`, three days after that flag was removed with four of the six subcommands. A test pinned the old string, so both were wrong together.
- Four tests marked `unit` drove `__enter__` with Playwright mocked and made two real network calls each, then sat out the lifetime guard's full 10s deadline waiting for a browser tree a mock never produces. 45.6s to 0.34s for that file. CI-only.

### Changed
- Requires `invisible-core==18.12.0`, which carries one prefs composition for all three entry points (`compose_session_prefs`), a `proxy=` argument on `get_default_stealth_prefs` that had no way to reach `configure_proxy` before, a proxy endpoint with no port refused rather than dropped silently, and a process scan that no longer asks psutil for the parent of every process on the machine.
- `build_prefs` delegates to that composition, so the three entry points that used to stack layers on top of `translate_profile_to_prefs` in their own order now agree, with the difference asserted as an exact set. What stays here is this path's delivery and its two decisions: the cloak, which only Windows and macOS need, and which generator draws the pointer path. `max_seconds_for` is applied here rather than passing `humanize` through, because `humanize=0` with the binary engine selected is a cap of nothing, not a request to disable motion, and passing it straight to the core would make it falsy and switch the generator off.
- Eight imports in `launcher.py` and `async_api.py` were unused and are gone. The ninth, `IANA_TO_POSIX_TZ` in `_session.py`, is not dead: the import IS the probe that turns an old core into a message about a version instead of a message about a symbol, so it keeps a `noqa` carrying the reason.
- `datetime.utcfromtimestamp` is replaced in `_recaptcha_seed.py`. It returns a naive datetime and has been deprecated since 3.12, which matters now that the matrix reaches 3.14.
- CI runs 3.11, 3.12, 3.13 and 3.14 on Ubuntu and Windows, every version `requires-python` promises; two of them had never run here. ruff selects F601 beside F821 and F811, because a dict literal with a repeated key silently keeps only the last one and Python does not warn.
- ruff is declared in the dev extra instead of being pip-installed unpinned on every run, so the tool that gates every push cannot change under us between two pushes. `pytest-mock` and `responses` were declared and used by no test in this package, and are dropped.
- Publishing is a workflow on a tag push using PyPI trusted publishing (OIDC, environment `pypi`), so there is no long-lived token on a machine or in a secret. The upload sits behind a gate that checks three things a person had to remember: the tag names the version being built, the `invisible-core==` pin is already on the index, and the suite passes, since a tag push does not trigger the ordinary test workflow. It needs a GitHub publisher registered on PyPI before its first run; until that exists the upload fails with `invalid-publisher`, which is the correct failure.

## [0.5.0] - 2026-08-01

### Changed
- The CLI is two commands, `fetch` and `version`. `path`, `clear-cache`, `doctor` and `fetch --force` are gone, and so is the tag argument. **Breaking for anybody scripting the four that were removed** - they fail loudly rather than being ignored, and a test asserts that. None of the behaviour is gone.
- `doctor` runs inside `fetch` now, on every run and before the download rather than after. That ordering is the point: a cached tree that no longer matches the seal is the case worth catching, and it is invisible to a "download if missing" that only looks at whether a file exists. It was the thing most worth doing and the thing least likely to be typed.
- `--force` is unnecessary once every run verifies: a tree is replaced because it does not match the seal, not because a flag was passed.
- `path` is the last line of `fetch`'s stdout, so `$(invisible-playwright fetch)` is the scripting form, and unlike `path` it guarantees the thing it names exists and matches. The mismatch report goes to stderr so it can never end up inside a captured path.
- `clear-cache` is deliberately NOT folded in. The cache root is shared with `invisible_firefox`, so pruning trees no seal points at would delete the other product's engine on a machine running both. `version` prints the location instead.
- The tag argument went with them: the seal decides which engine a build runs and `verify_engine` refuses anything else, so a tag on the command line could only ever name something that would then be rejected.
- The surface is asserted as an EXACT set, not a subset. A subset check passes while the CLI grows back one convenience at a time, which is how it reached six.

## [0.4.9] - 2026-08-01

### Changed
- Requires `invisible-core==18.11.0`. 18.10.0 classifies a 404 on the engine archive instead of raising a bare `HTTPError`, which is issue #51, and 18.11.0 removed the part of it that ran `pip install --upgrade` on the caller's environment: a library that installs things while it is running mutates an environment nobody asked it to touch, ignores whatever lockfile chose that version, and inside a container rewrites an image layer at runtime. What reaches a user of this package is that a 404 on the engine now says whether the release was retired on purpose, in which case no retry will find it and the message carries the upgrade command, or whether the tag is current and the fault is ours. Nothing in this package's behaviour changed.

### Fixed
- The README said the sampler draws "~400 fields". Measured against a real profile: 197 leaves on the most generous count, which treats every bundled font name as a field, and 155 prefs emitted. Corrected to ~200. `tests/test_readme_claims.py` now gates the engine version, the platform list, the download size, the documented subcommands in both directions, and every fenced Python example on the page - four claims that had nothing checking them.
- A publish was racing its own verification: the install e2e runs on `release:`, which fires seconds after the upload, and the index does not serve a new version to pip immediately, so the job reported a forgotten publish that had happened two minutes earlier. A bounded five-minute poll on the per-version endpoint, scoped to the release event. CI-only.

## [0.4.8] - 2026-07-28

### Changed
- Requires `invisible-core==18.9.0`, which carries a clearer engine refusal (it used to locate a missing juggler inside an `omni.ja` the tree does not have) and a publish gate that no longer reports a mistyped command with the same exit code as a broken gate. An exact pin means those reach a user only when this package's pin moves, so this release is what delivers them.
- CI runs ruff with F821 and F811 selected, those two only: they catch a name that cannot resolve, which is not a style question and not findable by running tests. Nothing here had ever run a linter, and there are zero violations once the three below are fixed.

### Fixed
- `Renderer` was used as an annotation six times in `_behaviour.py` and defined in no module in the repository. It survives only because `from __future__ import annotations` never evaluates an annotation, so the six were strings that looked like a type and a reader had no way to learn what the parameter accepts. It is a real alias now.
- `prof: Any` in `_motion.py`, with `Any` itself unimported. Importing it would have widened that module's deliberately tiny allowlist, which one of its own tests enforces, and there was no need: `prof` is the cumulative distance table `_profile_table` returns and `_profile_at` consumes, both annotated `list[float]` a few lines away.
- The install e2e imported the venv helpers from `invisible_core.testing`, which the runner does not have on purpose, so the job went red at collection having tested nothing. The helpers are local to those two files again, with the reason above them, and the core's suite now parses every file the user-install workflows name and refuses the import. CI-only.
- CI arms the hooks, because the suite asserts they are armed. The assertion moved into `invisible_core.testing` and all three repos started making it, but only the core's workflow ran `install_hooks.py`, so this repo went red on every push with a message about an unset config that reads like a developer's mistake rather than a missing CI step. CI-only.

## [0.4.7] - 2026-07-28

### Changed
- Requires `invisible-core==18.8.0`, which carries the work of the last two days: the prefs builder split into fourteen named steps with its output hash-identical across 400 profiles, the GeoIP fetch moved out of `download`, `invisible_core.pin` as a public module, and a publish gate whose index cross-check finally asks about the package it is gating.
- Nothing in this package's behaviour changed. The imports moved: it asks `invisible_core` for what that package exports instead of reaching into its private modules, and its own back-compat shims are for users again rather than for its own source.

### Fixed
- The suite could not tell a collapsed selection from a pass in one place and the install e2e imported the package it tests the install of in another; both are CI-only and neither reached a user.

## [0.4.6] - 2026-07-27

### Changed
- This package can no longer be published without a gate. A release used to be a bare `twine upload` of whatever happened to be in a directory - which is how 0.4.4 reached the index built from a tree that predated the fix it was meant to carry. The gate is `invisible_core.release`, one implementation for all three packages, and the pre-push hook runs it on a release tag. What makes it work is that `publish` builds and uploads *what it just built*, so a stale directory cannot be what ships.
- `PUBLISHED.json` records what actually reached the index, back-filled from the artifacts themselves.
- Requires `invisible-core==18.6.0`.

## [0.4.5] - 2026-07-27

### Fixed
- An environment holding an older `invisible-core` failed on the browser launch path with `ImportError: cannot import name 'IANA_TO_POSIX_TZ' from 'invisible_core'` - a symbol name, from a package whose version the reader did not choose. It now states which core version this build needs, keeps the original error inside the message for whoever is debugging, and ends with the command that fixes it. **0.4.4 has this bug and cannot be corrected in place; a PyPI filename is never re-uploaded. Use 0.4.5.**

## [0.4.4] - 2026-07-27

### Changed
- The process-lifetime machinery moved into `invisible-core`, shared with the profile manager. Both packages launch the same browser and both had the same leak; the manager had it a day longer because the fix lived here. `invisible_playwright._reaper` stays as a re-export, so nothing you import changes.
- The IANA-to-POSIX timezone table is the core's now. This package carried a byte-identical copy of all ten entries, including the Arizona row that exists because mapping it to `MST7MDT` made libc apply DST and an identification service deduce a Denver origin - and the core's own copy carried a comment admitting it had been copied from here. Two copies with a documented keep-in-sync obligation and nothing enforcing it.

### Fixed
- The lifetime guard counted an attempted job assignment as a successful one, so a process it had failed to adopt was never retried and the count it reported was a number of tries. Measured on the sibling package, that reported eight processes held while eight survived the kill. This package shipped the same accounting.

## [0.4.3] - 2026-07-27

### Fixed
- `async with InvisiblePlaywright(...)` no longer leaves browsers behind when the runner is killed. The lifetime guard added in 0.4.0 reached the sync entry point only, so every async user kept the entire leak while this file said it was closed. If you drive this package with `async with` on Windows, upgrading is the fix.
- The guard itself stopped adopting the process tree after the first process it found, so part of the tree stayed outside the kernel job even on the sync path. Found by killing the runner mid-session and counting the browsers still carrying that session's token: sync 0/0/0/0, async 2/0/0/2 - the same code on both, differing only in timing, which is what made an intermittent race look like a working feature. After the fix: ten sessions, zero survivors on both.
- `INVPW_TRUE_HEADLESS` now works on both entry points. It was read in the async class alone, so a documented environment variable applied or did not depending on whether you wrote `with` or `async with`.

### Changed
- The two entry points share their session logic instead of restating it: prefs, the launch environment and the IANA-to-POSIX timezone table live in one module both call. 80.6% of the async class was the sync class retyped, which is how a fix reaches one of them and a release note describes both.
- Requires `invisible-core==18.3.0`.

## [0.4.2] - 2026-07-26

### Changed
- The lifetime guard is a strategy object instead of a module of flags. `SessionToken` (a value object), `find_processes` (the only place psutil appears) and `LifetimeGuard` with a `JobObjectGuard` / `NullGuard` pair: `os.name` is tested in exactly one place, so no launcher code branches on the platform, and the Null implementation reports that it guarantees nothing rather than doing nothing while looking successful. Behaviour unchanged.
- The stroke planner is six named stages instead of one 203-line function, and the idle planner is a table of episodes instead of an if/elif chain with the weights written as bare literals in the branch conditions. Both verified byte-identical against recorded output, which caught two float-associativity regressions that no other test could see - `a * b * c` and `a * (b * c)` differ in the last bit, and that is enough to change a rounded pixel and every draw after it. A permanent fingerprint test now covers 576 cases and 16135 waypoints.

## [0.4.1] - 2026-07-26

### Changed
- Three long functions became named stages, with their output pinned before and after so the refactor could not change it in silence.
- The lifetime guard became a strategy rather than a module of flags.
- The forbidden-name scan no longer blocks a clone that has no word list.

## [0.4.0] - 2026-07-26

### Added
- Pointer movement is generated in this package, from the session seed, instead of by the engine. Existing code gets it with no edit: the six internal funnels every pointer action already goes through are wrapped, so a `page.click` written a year ago moves through the new generator. A plain `sync_playwright()` browser in the same process is untouched.
- Movement between actions: drift while reading, motion while the wheel turns, overshoot and correction, and movements that end on nothing. If every movement ends on something clickable, the set of endpoints is itself a signature however good each path is.
- `INVPW_CURSOR_ENGINE` selects the generator: `python` (default), `binary` (the previous behaviour) or `off`.
- The browser process tree is tied to this process's lifetime on Windows, so it cannot outlive a runner that was killed. Measured: eight survivors on the first attempt and twelve on the second before, zero after. `psutil` becomes a dependency for this - an optional reaper is absent exactly on the machines that need it, and silently. **This reached the SYNC entry point only. `async with` kept the whole leak through 0.4.2 - see 0.4.3.**

### Changed
- Install is now `pip install invisible-playwright`, from the index. The git URL is gone from the README, the CLI docs and the generated release notes. Nothing about the package changes for someone who was already installing it from git, except that pip can now see what it is holding.
- `invisible-core` is declared as an exact version specifier (`invisible-core== an exact version`) instead of a `git+https://...` direct reference. A direct reference carries no version, so there is nothing for `pip check` to compare and a broken environment reports clean; a real specifier is reported, exit 1, and a plain reinstall of this package repairs it. The cost is that a binary bump is now a release of every package that pins the core, which is deliberate.
- Requires `invisible-core>=18.2.0`, which bounds the timezone lookup as a step rather than only per request.

### Fixed
- `hover()` no longer fails intermittently on Windows. The approach now completes before the automation layer's hit-target check is installed, so the only event inside its window is that layer's own move, on target. Measured on a page whose target needs a scroll: 3 failures out of 3 before, 0 out of 3 after.

## [0.3.6] - 2026-07-25

### Fixed
- The browser tree now dies with this process even when the process is KILLED. An exception out of the `with` block was never the leak - `__exit__` runs and Playwright cleans up, measured over an interleaved A/B with zero survivors. The leak is the killed-runner path, where `__exit__` never executes at all: launch, kill the runner, and eight processes were still alive, twelve on the second attempt.

### Changed
- Declared as `invisible_playwright` on the index, pinning `invisible-core==18.1.0`.

## [0.3.5] - 2026-07-25

### Changed
- Playwright pin is back to `>=1.55,<=1.61.0`: the floor drop to `1.40` shipped in 0.3.4 is reverted. The conservative CI-tested floor (1.55) is kept together with the tested upper cap (1.61.0).

## [0.3.4] - 2026-07-24

### Changed
- Playwright floor lowered to `>=1.40,<=1.61.0`. The `1.55` floor was never a real compatibility bound - it was only the single minor that 0.3.1 had pinned. The `firefox-18` binary was smoke-tested (launch, `new_context`, navigate, evaluate, click) against 1.40, 1.45, 1.50 and 1.54 and passed on all four, so anyone on an older client keeps working; the upper cap stays at the tested 1.61.0. Reverted in 0.3.5.

## [0.3.3] - 2026-07-24

### Changed
- Playwright pin is now `>=1.55,<=1.61.0` (was `>=1.55,<1.62`). The open upper bound would let an untested version below 1.62 install and break a fresh setup the way 1.61 first did; the cap is now the exact version validated against this binary, 1.61.0. It moves forward on purpose, once a newer Playwright is tested against the binary.
- The expected Firefox version in the tests is derived from the constant instead of a hardcoded literal, so a binary bump no longer needs the test edited alongside it.

## [0.3.2] - 2026-07-24

### Changed
- Playwright pin moved to `>=1.55,<1.62` (was `>=1.55,<1.56`). The `firefox-18` binary rebases onto Firefox 151, which is what the latest Playwright (1.61) pairs with, so 1.61 now drives the binary natively - no more pinning to an older client. Both ends of the range were tested against the new binary (full browser suite on 1.61; drift-free protocol on 1.55). `scripts/playwright_pin.txt` -> 1.61.0.
- README badges are served as SVGs from the repo instead of a third-party badge service, so the header no longer depends on an external endpoint; the dynamic Firefox-version badge, which was the flaky one, is now static.

### Fixed
- The CI font gate checks that the whitelisted faces actually LOAD, not only that the family enumerates. Enumerating a font is not loading it, so the previous check could pass on a build that could name a family it could not render.

## [0.3.1] - 2026-07-13

### Fixed
- [#48](https://github.com/feder-cr/invisible_playwright/issues/48): Playwright 1.61 adds an `isMobile` field to the `Browser.setDefaultViewport` Juggler command that the FF150 binary does not accept, which kills the session. The range narrows from `>=1.40,<1.61` to the CI-tested 1.55.x (`>=1.55,<1.56`, single-sourced from `scripts/playwright_pin.txt`) so a fresh install always gets a compatible client. Widened again in 0.3.2, once `firefox-18` rebased onto Firefox 151.

### Changed
- The patched-Firefox source repo is now `feder-cr/firefox_antidetect_patch` (it was `feder-cr/invisible_firefox`, a name the profile manager took over). Release notes, CI references and the release URLs the download tests mock all point at the new name; the binaries themselves are unchanged.
- The wrapper no longer injects the font-list / system-UI environment variables at launch: the binary ships its own font bundle and is self-contained on that front. The integration tests were adapted and the font-sampling tests dropped.

### Added
- `scripts/ci_font_gate.py`: asserts the Windows font persona on every OS, so a Linux or macOS runner catches a font regression that would otherwise only show up on Windows.

## [0.3.0] - 2026-07-03

### Changed
- Pure config (seed -> fingerprint -> prefs, binary download, proxy, geo) is split out into a standalone `invisible-core` package with zero Playwright, so a profile manager can reuse it without pulling Playwright in. The wrapper depends on it and replaces the moved modules with full-alias shims: existing imports (public API, submodules, private names) and `isinstance` checks keep working unchanged. `tests/test_backcompat.py` locks that contract with 6 guards.
- `BINARY_VERSION` walks `firefox-7` -> `firefox-13` across this cycle: `firefox-8`, then `firefox-9` (`firefox-8` was found broken and is refused outright, not merely superseded), `firefox-10`, `firefox-11`, `firefox-12` (the cross-OS render-parity build) and `firefox-13` (geo-aware locale/Intl + the Windows font bundle + the audio gate).
- Playwright is capped at `<1.61` (`>=1.40,<1.61`) and the pin is single-sourced from `scripts/playwright_pin.txt` instead of being written in two places.
- WebGL personas: only the GPU buckets that survive the tampering checks are shipped, and the render-noise seed is decoupled from the persona seed.
- The dead `zoom.stealth.normalize_date_now` baseline pref is dropped.
- New runtime dependencies: `requests[socks]` (SOCKS egress lookup), `maxminddb` (mmdb reader), `tzdata` (IANA database for `zoneinfo`, which Windows lacks). After the split they arrive transitively through `invisible-core`.

### Added
- `timezone="auto"`: the browser timezone is auto-derived from the egress IP. By default (no explicit timezone) it ALWAYS resolves - from the proxy egress when a proxy is set, otherwise from the host's own public IP - so the zone can never disagree with the IP (the classic `timezone_mismatch` signal). An explicit `"Area/City"` is the only way to force a specific zone. On failure: with a proxy the launch raises (no silent host-TZ fallback behind a foreign proxy); without a proxy it falls back to the host TZ so a transient lookup can't break the launch.
- The egress IP is mapped to its IANA zone with an offline mmdb (`daijro/geoip-all-in-one`). It always tracks the upstream weekly rebuild: on every launch the current latest release tag is resolved from the `releases/latest/download` permalink (no GitHub API → no rate limit) and pulled only if newer than the cache, older copies pruned. Offline → the cached copy is reused; never a pinned tag (daijro prunes old releases, so a pin eventually 404s). `STEALTHFOX_GEOIP_MMDB` points at your own `.mmdb` to skip the download.
- `resolve_session_timezone(timezone, proxy)` and `ensure_geoip_mmdb()` re-exported at the package root (plus `GeoTimezoneError`) so integrations that own their launch can reproduce the resolution.
- `tests/test_geo.py` (37) + `tests/test_geoip_update.py` (freshness / auto-update / offline fallback) unit tests.
- Cross-OS render parity (needs `firefox-12`): the same font/canvas/WebGL fingerprint now renders consistently on Windows, Linux and macOS, so a Windows persona looks identical regardless of the host the binary runs on. Each whitelisted font renders a distinct canvas image (font-detection probes that dedup by rendered image keep every name), the standard Windows fonts (Calibri, Franklin Gothic, Gadugi, Javanese Text, Myanmar Text) are always present so the detected font set matches a real Windows install, and the per-seed render-noise leaves a solid-colour reference render byte-exact while still varying real fingerprint renders.
- GPU persona applied on every platform: Linux/macOS hosts now present a coherent Windows GPU (renderer + WebGL parameters) instead of the host's real adapter; pool re-rooted on a real-device GPU mix.
- `tests/test_canvas_render_stealth.py`, `tests/test_webgl_noise_active.py` and new `tests/test_sampler.py` cases: regression guards for per-font canvas distinctness, solid-readback purity under render-noise, and the always-present standard-font invariant.
- macOS support in the wrapper (x86_64 + arm64): the release pipeline builds five targets and the wrapper resolves the archive for the host it runs on.
- Headless is cloaked on Windows and macOS and runs under Xvfb on Linux, instead of the flagged headless mode; CI carries a cloak guard and a WebGL-masking guard. The cloak applies on the async path too.
- The e2e suite runs the real detectors offline (BotD, FingerprintJS, fpscanner, CreepJS, vendored under `tests/vendor/`) and a hermetic SOCKS5 auth + routing e2e, all on CI on every push. The 15 hand-rolled BotD imitations are dropped now that the real one runs.
- `fetch --force` on the CLI, to re-download an archive over a cached one.

### Fixed
- WebRTC behind a proxy ships the validated realness configuration, with CI guards: a fully blocked WebRTC is itself a tell, so the guard asserts the positive form and not merely the absence of a leak.
- The TLS ClientHello matches stock Firefox again: cipher `0xC009` was being offered and stock Firefox does not offer it.
- The humanize prefs were written under the wrong namespace.
- The font pool uses the real Windows 11 family name `franklin gothic medium`; the previous name does not exist on a real install.

## [0.2.0] - 2026-05-28

### Added
- Public config helpers in `invisible_playwright.config`: `get_default_stealth_prefs(seed, *, pin, locale, timezone, extra_prefs, humanize, virtual_display)` returns a complete `firefox_user_prefs` dict; `get_default_args()` returns the baseline CLI args list (currently empty). Both also re-exported at the package root.
- `invisible_playwright.ensure_binary` re-exported at the package root for parity with the `cloakbrowser.download.ensure_binary` integration pattern that downstream projects (Skyvern, Crawlee, agno) already expect.
- These helpers let third-party fetchers (changedetection.io plugins, Crawlee `BrowserPool` subclasses, agno toolkits) drive `playwright.firefox.launch(executable_path=..., firefox_user_prefs=...)` themselves without depending on the `InvisiblePlaywright` context manager owning the lifecycle.
- `tests/unit/test_config_public.py`: 14 unit tests covering deterministic seed, locale / timezone / pin / extra_prefs / humanize variations, and round-trip via the public namespace.

### Unchanged
- `InvisiblePlaywright` context manager surface is identical (backwards compatible).
- `BINARY_VERSION` stays at `firefox-7`. Python-only release; no new Firefox build.

## [0.1.8] - 2026-05-23

### Fixed
- [#20](https://github.com/feder-cr/invisible_playwright/issues/20): cross-origin iframes were unreachable from Playwright. `element_handle.content_frame()` returned `None`, `frame.evaluate()` threw cross-origin SOP errors, and `frame_locator(...).click()` timed out even with `force=True`. Root cause: FF150 defaults `fission.webContentIsolationStrategy=1` (`IsolateEverything`), which site-isolates every cross-origin iframe into a separate `webIsolated` content process even when `fission.autostart=False`. The parent's Juggler FrameTree then has a Frame placeholder with no docShell and no URL - every protocol op that needs to enter the iframe fails. Fix: pin `fission.webContentIsolationStrategy=0` (`IsolateNothing`) in the baseline prefs. The setting can be flipped back per session via `extra_prefs={"fission.webContentIsolationStrategy": 1}`.

### Added
- `tests/test_cross_origin_iframe.py`: 4 unit + 5 e2e regression sentinels for cross-origin iframe interaction. The e2e layer runs entirely offline against two local HTTP servers on `127.0.0.1` (two ports = two SOP origins) and covers `page.frames` URL tracking, `content_frame()`, `frame.evaluate()`, `frame_locator(...).locator(...)`, and end-to-end `dispatch_event("click")` for plain, sandboxed and titled iframes. A future FF upgrade or fingerprint A/B that flips the pref back to `1` will fail the suite before shipping.

### Unchanged
- `BINARY_VERSION` stays at `firefox-7`. Python-only release; no new Firefox build was needed.

## [0.1.7] - 2026-05-21

### Fixed
- [#18](https://github.com/feder-cr/invisible_playwright/issues/18): Tab crash when running with `headless=True` on Windows on pages that trigger cross-process navigation. Two separate bugs that only manifested together: (1) the Chromium content sandbox at default level 6 puts content processes on `kAlternateWinstation`, but the wrapper hides the browser window on its own alt-desktop (`CreateDesktop` for headless on Windows). Mismatched desktops → cross-process navigations couldn't reparent windows → content process exits cleanly and Playwright fires `page.on('crash')`. (2) The canvas2d `getImageData` stealth spoof wrote to a read-only mapped `DataSourceSurface`. On GPU-backed canvases that memory is write-protected → segfault during the final `getImageData` at page unload. Wrapper now sets `security.sandbox.content.level=4` in the alt-desktop workaround set, and `firefox-7` ships the source fix that moves the noise to the JS array's writable backing buffer.

### Changed
- `BINARY_VERSION` bumped from `firefox-5` to `firefox-7`. `firefox-6` was rolled back when its partial fix turned out to be wrong (the iframe-burst hypothesis was a dead end; bisection in the evening found the real two-bug cause documented above).

## [0.1.6] - 2026-05-21

### Added
- `profile_dir=` kwarg on `InvisiblePlaywright` (sync + async). When set, the session uses `firefox.launch_persistent_context()` so cookies, localStorage, sessionStorage, extensions, cache and prefs are kept on disk between runs. `__enter__` returns a `BrowserContext` directly: `with InvisiblePlaywright(profile_dir=p) as ctx: ctx.new_page()`. Pair with a stable `seed=` to also pin the fingerprint identity across runs. First run creates the dir; subsequent runs reuse it.

### Fixed
- `launch_persistent_context(timezone_id="…")` no longer times out at 180s. Root cause: `juggler/content/main.js` calls `docShell.overrideTimezone(...)` on every navigation; the patched Firefox up to firefox-4 didn't expose that IDL method on `nsIDocShell`, so the call threw `TypeError: docShell.overrideTimezone is not a function`. On the non-persistent path the error fired *after* launch and was harmless; on the persistent path it blocked the launch handshake. `firefox-5` ships the C++ method (see `patch.md` section 19); this release removes the firefox-4 era Python workaround that was filtering `locale`/`timezone_id` out of the persistent context kwargs.

### Changed
- `BINARY_VERSION` bumped from `firefox-4` to `firefox-5`. The Python source delta is JS/Python only; the new Firefox build adds 50 lines of C++ in `docshell/base/nsIDocShell.idl` + `nsDocShell.cpp`.

## [0.1.5] - 2026-05-20

### Fixed
- [#15](https://github.com/feder-cr/invisible_playwright/pull/15): `python -m invisible_playwright fetch` raised `RuntimeError: no SHA256 for firefox-150.0.1-stealth-linux-x86_64.tar.gz in checksums.txt` for every user because the parser kept the `*` binary-mode prefix that `sha256sum` writes in front of filenames. Now `.lstrip("*")` is applied to the key. Reporter + patch: [@LostBoxArt](https://github.com/LostBoxArt). Unrelated to the `firefox-N` binary; existing caches still work, only first-time fetches were broken.

## [0.1.4] - 2026-05-20

### Fixed
- [#13](https://github.com/feder-cr/invisible_playwright/issues/13): every page that threw an uncaught JS error (e.g. bunny.net) crashed the Playwright client with `TypeError: Cannot read properties of undefined (reading 'url')`. Root cause: upstream Playwright Juggler added a required `location` field to the `Page.uncaughtError` event in the 2026-05-07 roll ([microsoft/playwright@c8604ec](https://github.com/microsoft/playwright/commit/c8604ecd97)); our fork was carrying the pre-roll schema in every `firefox-N` build. Fix matches upstream - Runtime.js builds the `errorLocation`, PageAgent.js forwards it on both worker and runtime error paths, Protocol.js declares the schema field. Reporter: [@dionorgua](https://github.com/dionorgua).

### Changed
- `BINARY_VERSION` bumped from `firefox-3` to `firefox-4`. JS-only change inside `chrome/juggler/`; `xul.dll` and `firefox.exe` are byte-identical to `firefox-3`.

## [0.1.3] - 2026-05-19

### Changed
- `BINARY_VERSION` bumped from `firefox-2` to `firefox-3`. The new archives on both Windows and Linux are built from a clean clone of [feder-cr/firefox_antidetect_patch#stealth/150](https://github.com/feder-cr/firefox_antidetect_patch/tree/stealth/150) - the consolidated source-of-truth fork (renamed from `feder-cr/firefox`; the companion `feder-cr/firefox-stealth` patches repo was deleted, all patches now live as commits on top of `mozilla-firefox/firefox`).
- The patched Firefox archive now ships the **proper C++ implementation** of `windowUtils.jugglerSendMouseEvent`, replacing the JS shim from 0.1.2.

### C++ fixes landed in this release
- **C1+C2**: `setDownloadInterceptor` IDL + cpp (re-landed for FF150).
- **C4**: 5 `nsIDocShell` stealth attributes (`fileInputInterceptionEnabled`, `overrideHasFocus`, `bypassCSPEnabled`, `forceActiveState`, `disallowBFCache`).
- **C5**: `LauncherProcessWin.cpp` + `nsWindowsWMain.cpp` juggler-pipe handle inheritance - without this, the Playwright pipe disconnects immediately on launch.
- **C6**: `juggler-navigation-started-renderer` / `-browser` observer notifications in `nsDocShell.cpp` and `CanonicalBrowsingContext.cpp` - without these, `Page.ready` never fires and `ctx.new_page()` hangs.
- **C7 (partial)**: storage stub for `nsIDocShell.languageOverride`. Workaround `InvisiblePlaywright(locale="")` recommended until full BC FIELD port lands.

### Verified
- Both archives built from same source: feder-cr/firefox_antidetect_patch commit `68906f1f9c55`.
- Windows + Linux smoke suite green: launch, `ctx.new_page()`, `page.mouse.{move,down,up,click,wheel}`, `navigator.webdriver=false`, sannysoft 32/33 PASS.
- SHA256 published in `checksums.txt` on the `firefox-3` release.

### Notes
- This is the first release with a native Linux build of the patched binary (previous `firefox-3` draft mentioned shipping the Linux firefox-2 archive byte-for-byte; that no longer applies - Linux now has the full C++ patch series).

## [0.1.2] - 2026-05-18

### Changed
- `BINARY_VERSION` bumped from `firefox-1` to `firefox-2`. The patched Firefox archive on GitHub Releases now contains the JS fix from 0.1.1 (every `page.mouse.*` / `page.click()` / `locator.click()` / `mouse.wheel()` failure on the FF150 binary). Users on 0.1.1 must run `python -m invisible_playwright clear-cache && python -m invisible_playwright fetch` to pick up the new archive.

### Verified
- Archive integrity tests on both platforms: Windows zip extracted + booted via Playwright (`mouse.move + click + page.click(selector)` all succeed end-to-end), Linux tarball file-level checks (firefox/libxul.so sizes, byte-identity of patched JS files against Windows source). 21/21 assertions pass.
- SHA256 published in `checksums.txt` on the `firefox-2` release.

## [0.1.1] - 2026-05-18

### Fixed
- **Critical**: every `page.mouse.*`, `page.click(selector)`, `locator.click()`, `page.hover()`, `mouse.wheel()` failed on the patched Firefox 150 binary with `win.windowUtils.jugglerSendMouseEvent is not a function`. The Juggler JS was porting calls to a Playwright-specific C++ method that was never landed in the FF146→FF150 port; replaced with the Mozilla chrome-scope `win.synthesizeMouseEvent` helper which is present in FF150. Six call sites patched across `juggler/protocol/PageHandler.js` and `juggler/content/PageAgent.js`. Reporter: [@trob9](https://github.com/trob9) - [#9](https://github.com/feder-cr/invisible_playwright/issues/9).
- `_linkedBrowser.scrollRectIntoViewIfNeeded()` is now guarded at both call sites in `PageHandler.js` (`dispatchMouseEvent` and `dispatchWheelEvent`) - the method is not present on the shipped FF150 `<browser>` element, so the unguarded call threw before the mouse event was dispatched.

### Added
- `tests/test_mouse.py`: 12-case regression suite covering every patched code path (mouse.move/click/dblclick/right-click, modifiers, locator.click/hover, wheel, manual mousedown+up, off-viewport move, humanize intermediate moves, scroll-and-click on offscreen element). Test cases inspired by `microsoft/playwright-python/tests/async/test_click.py`.
- Community standards: `CODE_OF_CONDUCT.md`, `CONTRIBUTING.md`, `SECURITY.md`, `.github/ISSUE_TEMPLATE/*`, `.github/PULL_REQUEST_TEMPLATE.md`.

### Notes
- The Stealthfox humanize Bezier expansion continues to fire intermediate `mousemove` events; the swap to `synthesizeMouseEvent` does not change the human-trajectory behavior (verified by test).
- The reCAPTCHA v3 score (0.90) and FingerprintPro / CreepJS results documented in the README are unaffected - `synthesizeMouseEvent` is a legitimate Mozilla helper that does not increase the anti-detect surface.
- A binary refresh of the patched Firefox archive on GitHub Releases is required for users to receive this fix (the Juggler JS is shipped inside the archive). The `BINARY_VERSION` will be bumped to `firefox-2` in that release.

## [0.1.0] - 2026-05-13

### Added
- Initial public release.
- `InvisiblePlaywright` sync and async context managers - drop-in replacement for `playwright.sync_api.Browser` / `async_api.Browser`.
- StealthFox humanize hook: Bezier-curve mouse trajectories enabled by default.
- `_fpforge` Bayesian fingerprint sampler with ~400 fields per session.
- CLI: `invisible-playwright fetch | path | version | clear-cache`.
- Pinnable fingerprint fields via `pin={...}` (see `docs/pinning.md`).
- SOCKS5 / SOCKS4 / HTTP / HTTPS proxy support with auth.
- Linux x86_64 and Windows x86_64 binary support.

[0.4.3]: https://github.com/feder-cr/invisible_playwright/releases/tag/v0.4.3
> The two oldest entries point at a COMMIT, not a tag: 0.1.0 and 0.1.1
> predate arriving on PyPI (the index starts at 0.3.5) and never had
> a tag. Creating one today is not harmless: `publish.yml` triggers
> on `v*`, and for a version the index does not serve, `already-published`
> does not short-circuit, so a new tag could kick off a publish
> attempt. The commit is the same information without that risk.

[0.1.1]: https://github.com/feder-cr/invisible_playwright/compare/7a983e99c53fa1ec1a443651a4dd9de42258dc61...589c848e07a67c459969a2ddfb79851f48b10eff
[0.1.0]: https://github.com/feder-cr/invisible_playwright/commit/7a983e99c53fa1ec1a443651a4dd9de42258dc61
[0.4.4]: https://github.com/feder-cr/invisible_playwright/releases/tag/v0.4.4
[0.4.5]: https://github.com/feder-cr/invisible_playwright/releases/tag/v0.4.5
[0.4.6]: https://github.com/feder-cr/invisible_playwright/releases/tag/v0.4.6
