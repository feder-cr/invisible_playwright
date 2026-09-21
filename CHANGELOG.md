# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.25.3] - 2026-09-21

### Fixed
- **A transient gateway error on the engine download is asked again instead
  of ending the install.** Nothing in the package had ever asked twice: a
  `504 Gateway Time-out` from GitHub on the 262 MB archive came out as
  `error: 504` and that was the end of it, on a CI runner and on the machine
  of anyone whose first use of the package landed on one. The download now
  makes up to three attempts on the failures that mean "ask again" (408,
  429, 500, 502, 503, 504, dropped connections, read timeouts, a stream that
  dies mid-transfer), waits 2s and then 8s between them, honours a
  `Retry-After` the server sends up to a minute, and says on stderr that it
  is doing so. A permanent answer is unchanged and still comes out on the
  first ask: a 404 keeps reaching the message that explains a withdrawn tag,
  and the download deadline is never repeated, because retrying it would
  multiply the very wait it exists to cap.

### Requires
- `invisible-core` 34.27.0, which carries that change. Same engine
  (firefox-34), no change in this package beyond the pin.

## [0.25.2] - 2026-09-21

### Changed
- **One composer for the launch environment.** `_session.build_env` was a
  twin of the core's `launch.build_launch_env`, and the hidden surface's half
  of the contract (the variables `launch_env()` names, including the ones to
  remove) lived only in the twin. The wrapper now verifies the font manifest
  against the executable, which is the one thing that needs the executable,
  and delegates the composition to the core. Same environment on the wire.

### Requires
- `invisible-core` 34.26.0, where `build_launch_env` takes `display_env`.
  Same engine (firefox-34).

## [0.25.1] - 2026-09-21

### Fixed
- **A launch environment the caller names is the whole environment.** The
  in-process Juggler server added the named variables onto its own process
  environment, so a variable `build_env` had removed came back: measured on
  0.25.0 from the index, a `headless=True` session on Linux was born with
  `WAYLAND_DISPLAY` still set, and only `GDK_BACKEND=x11` kept it off the
  real desktop. The server now hands the engine exactly what was named;
  naming nothing (a bare launch) still inherits, because a browser with no
  `PATH` starts and then cannot reach the network.

## [0.25.0] - 2026-09-21

### Fixed
- **On Linux the Xvfb display no longer travels through `os.environ`.**
  `_LinuxVirtualDisplay.start()` used to write `DISPLAY`, `MOZ_ENABLE_WAYLAND`
  and `GDK_BACKEND` into the process environment and pop the five Wayland
  variables from it, restoring everything in `stop()`. Every session's
  environment is a copy of `os.environ`, so a `headless=False` session opened
  while an Xvfb session was still alive in the same process was born on the
  Xvfb and never appeared. The whole fact now travels in the surface's
  `launch_env()`, the same channel the Windows hidden desktop already used: the
  variables to set, and, named with `None`, the ones the browser must not
  carry; `build_env` applies both to the session's environment only.

### Requires
- `invisible-core` 34.25.0, where `launch_env()` gained the `None` half of the
  contract. Same engine (firefox-34).

## [0.24.0] - 2026-09-21

### Changed
- **`headless=True` on Windows creates the browser on a hidden Win32 desktop
  instead of asking the engine to cloak its window.** The spawner now calls
  `CreateProcessW` itself, naming a fresh desktop object in
  `STARTUPINFO.lpDesktop` - the one field `subprocess` cannot set - so the
  launcher, the parent, the GPU process and every content process are born
  off screen, out of the taskbar and out of alt-tab, with no cooperation from
  the binary. Linux is unchanged (Xvfb). The pref `zoom.stealth.cloak_windows`
  is no longer emitted and `invisible_core.cloak_prefs` is gone; the engine
  is stock on that surface, and the next engine release removes the dead
  hook.

  Measured on the sealed firefox-33 before shipping, same seed hidden against
  headed: `hasFocus`, `visibilityState`, `innerWidth/Height`,
  `outerWidth/Height`, `screenX/Y`, `screen.*`, `devicePixelRatio` and the
  WebGL renderer string identical, ANGLE D3D11 on both, one Firefox window on
  the session's desktop and none on the interactive one, three cross-origin
  navigations in a row without a content-process crash. The two sandbox keys
  that a hidden desktop needs (`security.sandbox.gpu.level=0`,
  `security.sandbox.content.level=4`, measured in 2026-05) ride only when the
  desktop was actually created, never from the platform name.

  Why: the owner chose a stock engine on this surface, and the same
  mechanism a mass-test harness has used since 2026-05 for dozens of parallel
  workers. What made it possible now and not in June is that the Node driver
  is gone: the process is ours to create.

### Requires
- **The firefox-34 engine, for the screencast of a hidden session.** On
  firefox-33 a `headless=True` session on Windows starts the window capture
  and never receives a frame: the engine's cropping capturer saw a visible,
  uncloaked window and cropped it from the SCREEN, which shows the input
  desktop only, and the screen capturer moved the capture thread there, after
  which the window capturer could not read the window either. firefox-34
  keeps a window that lives on another desktop on the window capturer, whose
  `PrintWindow` reads it in full from its own desktop. Measured 2026-09-20:
  0 frames in 15 s on firefox-33, both screencast tests green on the fixed
  build, hidden. `tests/test_hidden_desktop.py` now asks for a frame from the
  hidden session, so the engine's release gate proves it on every build.

### Removed
- `tests/test_cloak.py`, replaced by `tests/test_hidden_desktop.py`, which
  also carries the control arm the old guard lacked: a headed window is found
  on the interactive desktop before a hidden one is asserted absent from it.
- `build_prefs(headless=...)`: the argument selected the cloak layer and
  selects nothing now. `virtual_display` is the fact that remains.

## [0.23.0] - 2026-09-20

### Changed
- **The engine floor moves to firefox-33.** It is the first build where a drag
  gesture is delivered, where a gesture interrupted by a navigation does not
  leave the page dead to the pointer, and where the renderer acks every mouse
  event it handles - the ack this driver's questions about input rest on, and
  the one the engine had lost in its port to Firefox 150.

  On the drag: until now the pinned engine sent its mouse events through a
  door that could not dispatch `dragover` at all, and then lost the
  acknowledgement that upstream used to await, so a drag born in the gap
  between two calls went unnoticed. Measured against the previous engine, a
  humanised journey opened 0 drag sessions out of 20; against this one, 5 out
  of 5, and the dose-response on the pause between events went from 5
  deliveries out of 24 to 24 out of 24, which is the threshold disappearing
  rather than moving. Nothing in this package changed to get that: the pin
  carries it, and a consumer only ever runs the engine its seal names.

  The 0.22.3 that was prepared and never published pinned firefox-32, a build
  that delivered the drag and, under load, failed clicks more often than the
  engine it replaced - measured on the same runners, the same day: 0 red runs
  out of 6 before, 1 out of 4 with that engine alone. That is the defect the
  entry below closes, and it is why that version went out as this one instead.

### Fixed
- **Under load, a humanised movement no longer collapses into a jump.** The
  pacer drops an event whose successor is already due rather than sending it
  late, and that rule had no ceiling in space: on a loaded machine nearly every
  point was overtaken, the destination survived, and one event carried 89% of
  an 820 px journey with a timestamp exactly where the plan put it. Now an
  event is dropped only while the one after it lies within twice the plan's
  largest step of the last position sent; past that it goes out late, still no
  closer to the previous one than the 8 ms floor, and the movement overruns
  its budget by however much the machine is behind. The one overslept sample
  the Windows timer produces is still dropped, and the plan still ends on
  time. Both drivers now tell the pacer where the pointer starts, so the first
  drops are measured from where it really is.
- **A pointer action that did not reach its element no longer reports
  success.** `hover`, `click`, `check` and `uncheck` now ask the engine where
  each event actually landed, as recorded at dispatch by a privileged listener
  the page cannot see, and raise `ActionMissed` when the move, the press or
  the release fell on something else. Measured on a target that moves: `hover`
  returned normally 2 times out of 8 and `click` 8 times out of 8 with the
  page having seen no event at all. The miss is reported, not retried - the
  press happened, and repeating it is the defect the retry loop exists to
  prevent. `force` skips this as it skips the hit-target check. Requires an
  engine that answers `Page.pointerLanded` and returns an `eventId` from
  `Page.dispatchMouseEvent`: the question carries the id of the last event the
  action sent and the engine waits for the renderer's ack of it before it
  looks, because a `mousemove` is coalesced and dispatched at the next refresh
  tick, after a question sent right behind it.
- **The e2e test that asserted a `hover` reaches the page through a handle
  asserted an entrance, not a move.** It listened for `mouseover`, which fires
  only when the pointer enters the element, and did not control where the
  previous action had left the pointer; whether it passed depended on the
  humanised path of the action before it. It now says what it means. This was
  the red that appeared on runs whose whole diff was a data file, and it was
  the test, not the driver.

## [0.22.2] - 2026-09-18

### Fixed
- **`__version__` describes the code, not the install record.** It came from
  `importlib.metadata`, which answers about the distribution the installer put
  there. For a wheel that is the same artifact as the code. For
  `pip install -e` the metadata is written once while the code keeps moving,
  and pulling does not touch it. Measured minutes after the checkout had been
  brought to zero commits behind `origin/main`: the record said 0.16.2 while
  the tree declared 0.22.1, six releases apart.

  That matters here more than in most packages, because this is the one a
  measurement names. Read from the program, the X in "measured against
  invisible-playwright X" was the moment somebody ran `pip install -e`, not the
  code under test.

  The version is now the install record for a normal install, and for an
  editable one the version the source tree declares plus a `+editable` local
  segment, so a tree that can carry uncommitted work is never read as the
  published release of the same number. Which of the two an install is comes
  from what `pip` itself wrote in `direct_url.json` (PEP 610), not from a guess
  about `__file__`. The record is still reported, under
  `__install_record_version__`, the way `invisible_core` already names it.

- **Two tests were exercising whatever was installed rather than this code.**
  The `--version` and `-V` arms spawned a subprocess, and a subprocess does not
  inherit pytest's `pythonpath`. In CI the two coincide, because the workflow
  installs this repository editable; from a worktree they do not, and the arms
  went red against a checkout they were never meant to measure.

## [0.22.1] - 2026-09-18

### Fixed
- **A click is delivered once, whatever the click does to the page.** One
  `click` could hand the page thirty-nine clicks and then report the action
  failed; on a control that stays put it did the action twice and reported
  success. Measured on 0.22.0, three buttons on one page differing only in
  what their handler does, thirty calls each: a button that does nothing and a
  button that moves itself were correct thirty times out of thirty, and a
  button that hides itself received 1068 clicks across 30 calls, all of which
  were then reported as failures.

  The discriminant is not movement. It is the target ceasing to be hittable by
  its OWN effect, which is what a modal's close, a cookie banner's accept, a
  menu item and a submit that becomes a spinner all do.

  The hit target was read before the action and again after it, and both reads
  raised `WrongHitTarget`, which the retry loop absorbs by starting over -
  and starting over presses again. The read afterwards cannot answer the
  question it is asked: once the event has gone out, "the point stopped
  belonging to the element" is the same observation for a click that missed
  and for a click that worked. It is gone rather than narrowed.

  What replaces it is one read, between the approach and the press. The read
  also moved after the approach, which is where the race is: a page that
  rearranges itself on hover was being judged in the layout from before the
  pointer arrived.

## [0.22.0] - 2026-09-17

### Fixed
- **`strict` now refuses an ambiguous selector instead of picking one.** The
  option was accepted across the public API, the injected script has always
  known how to raise on it, and nothing in between passed it on: the default
  stayed false all the way down. Every Locator method sends `strict=True`, so
  `page.locator("button").click()` on a page with two buttons clicked the
  FIRST one and reported success. That is not a missing error message. The
  automation acted on an element nobody had chosen, and said nothing about it.
- **`force` now bypasses the actionability checks it promises to bypass**, and
  both of them: the state checks and the hit-target check. "Receives events"
  is one of the checks the option names, and an overlay intercepting the
  pointer is the ordinary reason to pass it, so honouring only the first half
  would leave the option indistinguishable from doing nothing.

### Changed
- The server reads the per-action options in ONE place rather than one reader
  per option. A reader per option is the shape that lost these two: seven
  copies of `params.get("trial")` is how the eighth action gets written
  without one. A test derives the list of actions that can carry the options
  from the code and requires every operation reaching one of them to hand
  them over, so an action added without the wiring is red on the day it is
  written.
- `no_wait_after` is NOT in this list, and the note that grouped it with the
  other two was wrong. Upstream declares it deprecated and without effect, so
  a client that ignores it matches upstream rather than departing from it.

## [0.21.0] - 2026-09-17

### Added
- **`go_back()` and `go_forward()` are back**, and the cause 0.18.0 withdrew
  them for turned out to be here rather than in the engine. A restored
  document left this client holding the injected script it had cached PER
  FRAME, while that script is an object inside one execution CONTEXT, and a
  frame outlives its contexts: every navigation replaces them and so does a
  restore. The handle and the context it was minted in are stored together
  now, so "is this still mine" is decided from state rather than inferred
  from destruction events arriving complete and in order. They do not: on a
  restore the engine destroys worlds this client was never told existed.
- The end to end test walks back, forward and back again and READS THROUGH A
  LOCATOR each time. One movement was green against the defect, and so is
  asserting on the URL: both of the earlier tests did one of those.

### Changed
- The perimeter's `WITHDRAWN` set is empty again and stays declared, for the
  next operation that is written and cannot be trusted yet.

## [0.20.0] - 2026-09-16

### Added
- **A gate on what the injected script leaves on the page's NODES.** The half of
  this class that concerned the page's `window` was closed in 0.19.0; this is
  the other verb. `computeAriaRef` writes `element._ariaRef` while building the
  accessibility tree, reachable only through `mode="ai"`, and the question
  underneath had never been answered: does an assignment made through an Xray
  stay in the sandbox, or reach the page's node?

  Measured against a page served from `127.0.0.1` that watches its own elements:
  it stays. With `mode="ai"` the snapshot comes back carrying refs and the
  sandbox sees the expando, while the page sees nothing at all. Nothing was
  broken and nothing needed fixing - but the day that confinement stops holding,
  every interactable element would carry a property any page can enumerate, and
  nothing would have said so. The test drives the writing branch deliberately
  and fails if it ever stops being reached, so it cannot go quietly green.

## [0.19.0] - 2026-09-16

### Changed
- **A click has a duration now, and a double click has an interval.**
  `mousedown` and `mouseup` used to leave together, so what a page measured as
  the hold was **8.0 ms** against the 60-120 ms a finger takes; and the two
  presses of a double click had nothing between them, yet were still delivered
  as a `dblclick`, because that event is born from `clickCount` rather than from
  the interval - so a page received a double click no operating system would
  have accepted as one. Both now come from the session's `PointerPersona`, and
  `delay=`, which was dropped here along with `button` and `modifiers` before
  it, works again as the override Playwright documents it to be.
  `humanize=False` keeps the old behaviour.

- **Typing now takes the time a hand takes, and that is a real slowdown you
  should know about before upgrading.** `page.type`, `locator.press_sequentially`,
  `keyboard.type` and the typing half of `fill` used to emit keys as fast as the
  protocol allows: a `keydown` and its `keyup` measured **4.0 ms** apart on a
  real page, against the 60-120 ms a finger takes, and eleven characters went by
  in about 50 ms. A site does not have to probe for that, it only has to listen,
  and a password field is where that listening is already installed. The rhythm
  now comes from a `TypingPersona` drawn from the session seed - dwell, gaps,
  the digram structure that makes alternating hands faster than one hand, and
  occasional hesitations - so `password123` takes about 1.8 s instead of 50 ms.
  Same seed still types the same way. `humanize=False` keeps the old speed, and
  the `delay=` you pass still wins over the persona for that call.

- **A drag travels now, instead of arriving.** The whole of `drag_and_drop` was
  five events, and the two carrying the journey were identical and 7 ms apart -
  a pair no device can report, since a move event is born from moving. They were
  deliberate: one jump alone did not reliably start the drag. Measured on a
  480-pixel drag, that journey now takes 27 events over 26 distinct points, on a
  path drawn by the same generator and delivered under the same pacing rules as
  every other pointer movement this package makes. The approach to the source is
  a path too. `humanize=False` keeps a single event, and `humanize=<seconds>`
  now caps this movement as it already capped every other one.

### Fixed
- **`drag_and_drop` raised on a `draggable` element, and left the mouse button
  down.** Gecko started a native drag session off that single 480-pixel jump,
  `dispatchDragEvent` then failed with `NS_ERROR_FAILURE`, and the `mouseup`
  never went out - so the `buttons` field of every event after it said
  "pressed", including the recovery `mouseup` in the error path, which failed
  the same way. The whole of HTML5 drag and drop was therefore unusable and
  nothing said so, because there was no end-to-end test of a drag at all. With a
  real path the drag is born the way it is for a hand and the gesture completes.
  It is the same change as the entry above: one absence, three symptoms.
- **`delay=` on typing was measured in milliseconds and slept in seconds.** The
  public API documents it in milliseconds; the one server path that forwarded it
  handed the number straight to `time.sleep`, so
  `element_handle.type("abc", delay=100)` waited five minutes instead of 0.3
  seconds. The six paths that dropped the value were accidentally protected from
  the bug the seventh had. All seven honour it now, and the unit is in the name.
- **`trial=True` no longer performs the action.** It is accepted on 31 public
  signatures - click, dblclick, hover, tap, check, uncheck, set_checked and drag
  across `Page`, `Frame`, `Locator` and `ElementHandle` - and the word appeared
  nowhere in the engine client, so it reached the wire and fell off it: a call
  whose whole purpose was not to click clicked. The single place that did read it
  made the outcome worse rather than better, skipping the humanised pointer
  approach and then running the action anyway, so the click went out in the most
  recognisable shape a click has. It is honoured now in the retry loop, which is
  the only thing that knows what actionable means: the checks run, the hit target
  is verified, and nothing is dispatched. A drag checks both ends.

- **A mis-aimed click is retried instead of blocked, and the injected script
  no longer touches the page's window at all.** The hit-target interceptor
  installed capture listeners there for the duration of every action and
  called `preventDefault` on the events that landed on the wrong element.
  Both halves are gone. The listeners were the last thing this package left
  on the page, and the blocking was the worse half: a real `mousedown` that
  disappears is not something an input stack produces, so the defence
  announced itself exactly when it worked. The check is now a pure DOM read
  run before the action and again after it, so a target that moves is caught
  and the action retried. What a page sees is a click that landed where the
  thing it was aimed at used to be, which is what a hand produces when a
  layout shifts. The guarantee narrows in one case: an element that moves
  between the check and the event now receives that one event before the
  retry, where before it received none.
## [0.18.0] - 2026-09-16

### Removed
- **`go_back()` and `go_forward()` now refuse.** 0.17.0 made them work for one
  step, and one step is the problem. The engine restores the document and
  reports the navigation, but a restored page leaves the driver holding a stale
  handle for that page's world: go forward, or go back a second time, and every
  locator raises `Cannot find object with id` while `evaluate()` and
  `content()` keep answering from the right document. `reload()` clears it.
  A method that is right once and wrong afterwards puts the failure somewhere
  else, where it reads as a broken selector on a healthy page, so both refuse
  with a sentence naming what would have happened. Navigate to the URL instead;
  `reload()` is unaffected, and a page moving its own history with
  `history.back()` still restores from the cache.

### Changed
- The perimeter gained a fifth category, `WITHDRAWN`, for an operation that is
  written and held back by a defect rather than missing. The four that existed
  would each have said something untrue about these two, and an inventory that
  lies is worse than one with a gap.
- The README stops claiming all methods. It never covered tracing, HAR, CDP or
  the API request context, and every refusal has said so for a while; the
  headline had not caught up.
## [0.17.0] - 2026-09-16

### Fixed
- **An authenticated HTTP or HTTPS proxy routes the page again.** It was
  accepted and then bypassed, silently, on every engine from firefox-24 to
  firefox-30: the credentials went to the proxy endpoint, where Gecko
  implements them for SOCKS only, so the call failed inside the channel filter
  and the connection went out directly over the host's own address with
  nothing raised anywhere. SOCKS was unaffected, which is why it lived through
  seven releases. HTTP proxy auth answers the 407 challenge instead, and that
  path was already wired. Reported from the outside, not caught here. There is
  now an end to end test that drives a browser through an authenticated HTTP
  proxy, on both the launch road and the context road, which is the case
  nothing covered.
- **`go_back()` and `go_forward()` report the navigation they performed.** The
  engine went back all along; Juggler had no bookkeeping for a document
  restored from the back forward cache, so the client was never told the
  navigation had committed. The test asserts that going back RESTORES the
  document rather than fetching it again, because asserting on the URL alone
  passes against a browser that silently refetches.

### Changed
- Pinned engine moves to **firefox-31**, via `invisible-core` 31.23.0.
- The pinning page's worked examples now run as written. Two of them named a
  GPU the validated pool cannot present and raised when copied.
- The ledger step realigns this file's heading date to the date the index
  reports, so a heading typed hours before the upload it describes cannot
  disagree with the gate that checks it.
## [0.16.2] - 2026-09-15

### Changed
- **The language gate comes from the core.** The check that this repository
  is English used to be a script here and a copy in AIHawk, and the copies
  drifted: this one never gained the `.js`, `.css` and `.html` coverage the
  other did, so the front end was outside its sight. It now runs as
  `invisible_core.english` from the pinned core, `invisible-core` 30.23.0,
  and what this repository exempts is declared once in
  `[tool.invisible.english]` in `pyproject.toml`. An exclusion that names
  nothing is refused, which is the check the copies did not have.
- **Three names cross into English with the core.** `srflx_to_declare()`,
  the `srflx_declared` keyword of `build_launch_env` and the attribute that
  carries the decision follow the core's rename in the same release, because
  either half alone breaks the launch. Nothing the browser receives changes:
  the emitted prefs and the launch environment are byte-identical to 0.16.1.

## [0.16.1] - 2026-09-15

### Fixed
- **Setting real files on a file input works.** Both doors ended in
  `InvalidStateError: An attempt was made to use an object that is not, or is
  no longer, usable`, a sentence naming no file, no API and no preference. The
  refusal is in the parent process: it declines to build a `File` for any
  content process whose remote type is not `file`. The preference the engine's
  own gate calls the "or for testing" escape ships from `invisible-core`
  30.22.0, which this release pins, and the two tests that covered the defect
  are hard assertions again for the first time since it was opened.
- **`FileChooser.set_files()` could not upload anything, and said so.** A
  chooser already holds the input element, so the client asks the
  `ElementHandle` for `setInputFiles` rather than going through a selector, and
  this package had no such method: the whole listening half of the file-chooser
  feature led to a dispatcher that correctly reported a gap rather than a
  decision. The element handle now performs the same upload the frame does,
  through the same action and the same reader of the request, so there is one
  upload path and not two. The engine-side half of file uploads is a preference
  that belongs to `invisible-core` and arrives when the pin moves: the two
  tests covering it stay expected-red until then, and are now strict so they
  turn red the day they start passing.
- **`locator.scroll_into_view_if_needed()` works. It never had.** It sent the
  engine command `Page.scrollIntoViewIfNeeded`, whose handler calls
  `scrollRectIntoViewIfNeeded` - a method declared in no binding of `Element`
  anywhere in the engine, so it was `undefined` for every caller and the call
  threw every time. Measured at four positions, including an element already in
  view: a timeout on all four, while `bounding_box()` on the same element
  answered correctly. The package already knew how to scroll, in the one place
  that mattered: every click has scrolled through the injected script since a
  click below the fold was found to miss. The public method now calls that same
  helper, so there is one scroll and not two. It centres the element rather than
  scrolling the minimum, and a `rect` argument is not honoured.

### Added
- **Every operation that refuses now says so in `_juggler/perimeter.py`, and a
  test keeps it that way.** Ten operations raised `ProtocolException` inline
  with a good reason beside the code and nothing anywhere that recorded they
  existed, so there was no way to tell a deliberate refusal from a gap without
  reading the dispatcher end to end. They are declared in three sets that keep
  the distinction: the engine offers no command (`drop`, `setOffline`,
  `setWebSocketInterceptionPatterns`), the answer is fixed when the injected
  script is built (`registerSelectorEngine`, `setTestIdAttributeName`), or it is
  simply not written yet (`exposeBinding`, `frameElement`, `resolve`, `reject`,
  `setExtraHTTPHeaders`). The test fails both ways: a new inline refusal nobody
  declares, and a declared name that has since started working.

## [0.16.0] - 2026-09-15

### Changed
- **A GPU pin now selects a validated persona instead of setting a label, and an
  impossible value is refused rather than ignored.** `pin={"gpu.renderer": ...}`
  used to change the `Profile` object and leave the browser reporting the seed's
  own GPU: measured on seed 1561645783, the profile said AMD RX 7900 XTX while
  `zoom.stealth.webgl.renderer` stayed the seed's NVIDIA GTX 980. The same held
  for `gpu.vendor` and `gpu.class_tier`, so all three keys were decorative with
  respect to the page. A renderer string cannot travel alone - around 81
  `getParameter` values, the shader precisions and the extension list belong to
  the same GPU - so a pin picks one of the validated personas, and a renderer,
  vendor or class the pool cannot present raises with the available values named.
  Sessions that pin nothing are unaffected: 404 seeds emit byte-identical
  preferences before and after.
- **`webgl.msaa_samples` is no longer a pin key.** The engine now emits the same
  MSAA sample count on Windows and Linux, so a pin has nothing left to move.
  Before this, the value was honoured on Linux and held at 4 on Windows, and the
  two builds emitted a different `gl.SAMPLES` for the same seed on seven of eight
  measured seeds.

### Fixed
- **`docs/pinning.md` described behaviour the code did not have.** It said
  `gpu.renderer` "lands verbatim in `UNMASKED_RENDERER_WEBGL`" and "does not
  condition other fields"; both were the opposite of true. The README example
  pinned a card no validated persona carries, so the snippet a reader copied did
  nothing.

- **The pinning surface loses three keys that could not work.** `screen.tier`
  could not condition the screen it named, because pins are applied after the
  sampler has drawn. `screen.avail_width` and `screen.avail_height` were
  page-contradicting: the engine derives the available rect from `width`,
  `height` and `taskbar_px`, so pinning either moved a label on the profile and
  nothing a page reads. Pin `screen.width`, `screen.height` and
  `screen.taskbar_px`; the available rect follows from them.
- **`codec.webspeech_synth` is gone from the profile.**
  `media.webspeech.synth.enabled` is emitted as a constant `true`, which is what
  retail Firefox does on desktop, so the sampled field only ever made the profile
  disagree with the browser - on about 12% of seeds.

### Internal
- Requires `invisible-core==30.21.0`.
- `_cursor.humanize_prefs` is deleted. It was a second implementation of the
  `stealthfox.humanize*` contract with no caller in `src/`, and it disagreed with
  the live one: it never emitted `stealthfox.humanize.stepMs`. The two properties
  its tests asserted now run through `_session.build_prefs`, which is the
  function both front doors actually call.

## [0.15.2] - 2026-09-14

### Fixed
- **`evaluate` no longer rewrites the JavaScript you passed it.** The argument
  was placed by searching the built script for the placeholder `ARG` and
  replacing it - over a string that already held your expression - so those
  three uppercase letters were rewritten to the argument's JSON anywhere they
  appeared in your own code. `'CARGO'.length` answered 6 instead of 5, a
  selector `[data-role='TARGET']` matched nothing, `/ARGH/` became `/nullH/`,
  and `const ARG = 5` was a syntax error. The three letters only had to appear
  inside a longer word, and three of those four failures were silent. The
  argument is now placed in the same formatting pass that inserts the
  expression, so your code is never scanned. `page.evaluate`,
  `page.evaluate_handle` and `page.wait_for_function` were affected;
  `element_handle.evaluate` and `eval_on_selector` already built their wrapper
  this way and were not.
- The script the page actually runs is unchanged, character for character, for
  every expression that did not spell the placeholder, and a test pins it:
  `evaluate` is the one call that deliberately runs in the main world, so the
  shape of that wrapper is something a site can look at.

## [0.15.1] - 2026-09-14

### Changed
- **The engine is `firefox-30`, through `invisible-core 30.19.0`.** The
  screencast capture no longer ends for good when a headed window is
  minimised for a moment: the engine listens for its capture module ending,
  starts another one a second later for as long as the window cannot be
  captured, and repeats the last frame every 250 ms while the module is
  quiet, so the age of a frame means one thing to a client that watches it.
  Nothing else moves: the pin follows the seal.

## [0.15.0] - 2026-09-14

### Changed
- **A closed pipe is a closed target.** A call made after the browser's pipe
  closed, or one that was waiting when it closed, now raises
  `TargetClosedError` - the same class a call on a disposed page, context or
  browser raises - instead of a plain `Error` carrying `the pipe closed`. The
  browser being gone is one fact and reaches a caller as one type.
- `invisible_playwright.async_api` and `invisible_playwright.sync_api` export
  `Error`, `TimeoutError` and `TargetClosedError`, so a caller can catch the
  type without importing a private path.

## [0.14.0] - 2026-09-08

### Added
- **`page.screencast.start(fps=...)` sets the frame rate, and the default
  stays at ten.** The engine has taken a frame rate all along, and the wrapper
  passed its own constant to it whatever the caller asked, so a live view could
  not go past ten frames a second no matter what it did. Measured on the same
  page, interleaved arms: asking for 10 delivers 9.6-9.8 fps at 257 KB/s,
  asking for 25 delivers 23.8-24.0 at 629 KB/s. The default does not move,
  which is the whole shape of the change: raising it would put two and a half
  times the bandwidth on every consumer to serve the one that is watching a
  window, so the caller decides and the constant is what happens when nobody
  does. That is how `quality` and `size` on the same call already work.

## [0.13.2] - 2026-09-07

### Fixed
- **`page.goto()` now answers with the Response, instead of always `None`.**
  The navigation itself was never broken - the page loaded, the `response`
  events carried the right statuses and `expect_response` worked - but the
  return value was hardcoded to `None`, so `page.goto(url).status` was not
  reachable. `page.reload()` answers with a Response too, and so does history
  navigation, which shares that code path but is still held back by a separate
  engine defect of its own. A redirect chain answers with the FINAL response,
  and a same-document navigation still answers `None`, as upstream does.
  Crawlee treats a `None` here as a failed load, so a `PlaywrightCrawler`
  driven by this package failed every request; it now finishes them.

## [0.13.1] - 2026-09-06

### Changed
- **The engine is `firefox-29`, through `invisible-core 29.19.0`.** It carries
  the screencast that `page.screencast.start(on_frame=...)` needs, so the
  feature shipped in 0.13.0 now has an engine that answers it, and the
  full-window frame no longer carries the Windows resize border. Nothing
  else moves: the pin follows the seal.

## [0.13.0] - 2026-09-05

### Added
- **`page.screencast.start(on_frame=...)` streams JPEG frames of the browser
  WINDOW.** `page.screenshot()` gives the content viewport and can never show
  the pointer, which is drawn in the chrome document precisely so that no page
  can see it. The screencast captures the window through the operating system
  in the parent process - tab strip, address bar and pointer included, nothing
  injected into the page - and hands each frame to `on_frame` as JPEG bytes
  with the captured window's size. Needs an engine from `firefox-28` on;
  `size=` bounds the frame (scaled down to fit, never up, default 1280x800)
  and `quality=` the JPEG quality (default 80). `path=` (a video file) is
  refused by name: the engine has no video encoder.

### Fixed
- **Two threads writing the Juggler pipe could splice two commands into one
  line.** Frames are acknowledged from the reader thread while the caller's
  thread dispatches input; the pipe now has a write lock, and a
  `Connection.post` that sends without waiting for the reply.

## [0.12.3] - 2026-09-05

### Fixed
- **Orphaned `.tmp-<tag>-<pid>` trees in the engine cache are swept.** Through
  `invisible-core 27.19.0`: a download killed during `verifying` or
  `extracting` used to leave up to the whole extracted tree behind, because
  only a fresh download by the same pid ever removed it. `ensure_binary` now
  removes the trees whose process is gone, on every call, and leaves alone
  this process's own tree, a live process's tree and anything not named after
  a pid. The MCP server 0.13.0 starts the download with the process, which made
  a killed download an ordinary event rather than a Ctrl-C.

## [0.12.2] - 2026-09-05

### Changed
- **The launch counter lives on the engine's repository.** Every session now
  declares `invisible_firefox.usage_ping.url` through `invisible-core 27.18.0`,
  pointing at the `usage-counter` release of `firefox_antidetect_patch`, the
  repository that builds and ships the engine. The engine's compiled default
  says the same from the next build. Engines older than firefox-21 do not read
  the pref and are not counted. To opt out of the ping, set
  `invisible_firefox.usage_ping.enabled` to `False` in `extra_prefs`.
- **The `browser launches` badge is back.** It was removed in 0.7.2 because the
  counter behind it lived on a repository that had been deleted, so the badge
  would have redrawn a frozen number every morning. The counter has lived on
  this repository since 2026-08-19 and is counting again, so the figure the
  badge shows is live. It is cumulative, and it undercounts: engines before
  firefox-21 still ask the old address, and they rejoin only as they upgrade.

## [0.12.1] - 2026-09-05

### Fixed
- **A persistent profile keeps its cookies and storage for the next session.**
  Since 0.8.0, `profile_dir=` kept nothing: the first session on a profile set a
  cookie and a localStorage entry and read both back, the second session on the
  same profile read an empty jar and `None`, and on disk `cookies.sqlite` held
  zero rows. The persistent launch handed back an ordinary context, a Juggler
  container with its own userContextId, and Juggler deletes a container's
  cookies and storage together with the identity when the context is removed;
  everything a session wrote lived in a container that died with the session.
  The persistent context is now the browser's default one, the one whose state
  is the profile's, addressed by omitting `browserContextId` and never removed;
  closing it closes the browser, as upstream does. Found by a clean-environment
  check of 0.12.0, on the day after the docs page promised "logging in once
  instead of every run".

### Added
- **A test that a persistent profile keeps state**, e2e: two sessions on one
  profile, cookie and localStorage written in the first and read in the second,
  and the cookie row read from `cookies.sqlite` in between, so a regression
  cannot pass by keeping the data where the page sees it and the disk does not.
  Against 0.12.0 it fails on the on-disk read.

## [0.12.0] - 2026-09-04

### Fixed
- **The first launch on Windows works.** On a machine that had never run the
  engine from that path, the first `InvisiblePlaywright(...)` exited with
  `3221226505` (0xC0000409) before the protocol came up, and the second one
  worked. It was Firefox's launcher process, not the browser: on a new path
  `firefox.exe` runs as the launcher first and forwards the Juggler pipe to the
  real browser, and it read that pipe from CRT descriptors 3 and 4, which the
  Node.js driver provides and this client does not, since it names the handles
  in `PW_PIPE_READ` / `PW_PIPE_WRITE`. The launcher died on `_get_osfhandle(3)`;
  the next launch found a launcher timestamp with no browser timestamp in the
  registry and disabled the launcher for that path, which is why it failed
  exactly once per machine and why nothing about the cache directory mattered.
  The engine is firefox-27, where the launcher forwards the handles it is
  handed. Every Windows first launch since 0.8.0, the release where this client
  replaced the Node driver, had this; the daily Windows install check had been
  red since 2026-08-31. (#144)

### Added
- **A test for the first launch from a path Firefox has never run from**,
  Windows only, e2e. It removes the launcher's registry values for the binary
  under test, launches once, and checks both that the page answers and that the
  launcher recorded a start followed by the browser's, so a success with the
  launcher disabled, the path that always worked, does not pass. Against
  firefox-26 it fails with the exit code above.

### Internal
- Engine pin moved to firefox-27 through `invisible-core 27.17.0`.

## [0.11.0] - 2026-09-04

### Fixed
- **A screenshot photographs where the page is.** The clip was
  `{x: 0, y: 0, width: innerWidth, height: innerHeight}`, and that clip is in
  document coordinates: not the window, but the top corner of the page at the
  window's size. An agent that scrolled and looked got the same image every
  time, and nothing in the image says so. `full_page` was not read at all:
  800x600 came back where 800x4000 was asked for. (#136)
- **Two manual workflows stop calling the removed `path` command.** Both
  called `python -m invisible_playwright path`, a subcommand removed when the
  CLI became `fetch` plus `version`. The Windows launch matrix failed in all
  three cells before reaching its launch probe, and the WebRTC gate had not run
  since the removal. The existing parser gate scanned Python tests only, so
  neither call site was in its perimeter; there is now one that reads the
  workflows too. Thanks to @SamadhiFire for finding and fixing it. (#141)

### Added
- **A check that asks the index whether the version a change proposes is
  still free**, on pull requests. This release exists partly because that
  check found its own repository in the state it exists to prevent: 0.10.0 was
  on the index while main sat on 0.10.0 with fifteen commits on top of the
  tag, the screenshot fix among them. A release from there would have
  published nothing and reported success, because the publish workflow treats
  an already-present version as a deliberate no-op, which is right for a
  re-pushed tag and indistinguishable from somebody forgetting to bump. (#142)

### Documentation
- Twelve pages stop shipping code that cannot run as pasted, tool metadata is
  out of the shipped screenshots, and the front page names the agent layer
  that sits on this engine. (#137, #138, #139, #140)

## [0.10.0] - 2026-09-03

### Added
- **Nine acting methods on `ElementHandle`.** `click`, `hover`, `fill`, `type`,
  `press`, `focus`, `check`, `uncheck` and `select_option` now work on a handle,
  so `page.query_selector("#go").click()` does what it does in Playwright. Until
  now the driver served fourteen handle methods, every one of them a read, and
  every action answered `ElementHandle has no method 'click'`.

  They go through the same humanised path as the selector versions - approach,
  hover, press, release, with the hit-target check and the actionability retry -
  rather than a second, simpler one. Two click paths would be two fingerprints.
  What differs is only what should: the node is not looked up again, and the
  handle is still usable afterwards.
### Documentation
- **Two pages said there is no MCP server for this project.** Both were written
  on 2026-07-27 and the server was published on 2026-08-19: true when written,
  and afterwards they steered readers to Microsoft's server, which cannot carry
  the seeded profile, for a problem ours solves. The integration page now opens
  with ours and is what it actually is - how to point Microsoft's playwright-mcp
  at this engine if you are already on it, and what that costs.

- **The README never mentioned either sibling.** A reader who would rather prompt
  than script had one hand-off, to the page above, which told them no server
  existed. There is now a short block after Install with both one-liners, and the
  Related projects section lists the three siblings before the third-party ones.

- The Python floor was stated only as a badge image about 200 lines below the
  install block, and the first-run download size disagreed across three READMEs,
  all three wrong against the packaged seal. One home for it, here.

- Four wiki pages edited in a working tree on 2026-08-30, rescued onto a branch
  and then lost a second time when the branch was never merged. Landed.
### Internal
- The English-only gate was written for this repository on 2026-08-27 and wired
  nowhere: not into tests.yml, not into the pre-push hook. Both sibling packages
  copied the script and the CI step; this one kept only the script. Run here for
  the first time it found four Italian test files, now translated, and it runs in
  CI from now on.

## [0.9.0] - 2026-09-02

### Fixed

- **`wait_for_timeout` has never waited.** A request for 2000ms returned in
  1ms, in both the sync and the async API and in every caller of either, for as
  long as this package has had its own driver. Nothing raised, so nothing
  showed.

  The defect was in `_juggler/server.py`, the driver written here to replace the
  Node one: `op_wait_for_timeout` read `params.get("timeout")`, a key the client
  never sends for this call, so the sleep was always zero. The client sends
  `waitTimeout`, which looks like a typo and is not - upstream Playwright
  declares this one parameter under its own name because `timeout` is the
  reserved key on that channel, carrying the per-call action timeout for every
  other call.

  ⛔ The first attempt patched the vendored client instead, to make it send
  the key the server happened to read. That is a fix at the symptom: it leaves
  the defect in the code this project owns, the next re-vendor of upstream would
  silently undo it, and it committed a false claim about the protocol into a
  comment, this changelog and a test. Review caught it before it shipped.

  What it cost, where it was found. A corpus run against real sites reported
  three of them as blocked by three different anti-bot products, one with the
  challenge markup sitting in the document; all three serve their real page once
  the wait exists, so those verdicts were artefacts of the tool rather than
  measurements of anything. A fourth site was reported as never loading and
  loads in twenty seconds. In the MCP package a polling loop of sixty
  one-second waits ran to completion instantly, so a test with a sixty-second
  budget failed in fourteen for no reason anybody could see.

  ⛔ **This changes timings for existing callers**, which is why it is a
  minor bump and not a patch. Code that called `wait_for_timeout` and returned
  instantly will now take the time it asked for. That is the documented
  behaviour of the API and the old behaviour was silently wrong, but a script
  that waited in a loop will get slower rather than faster.


## [0.8.3] - 2026-08-31

### Changed

- **Pins `invisible-core` 26.17.0, which seals firefox-26.** That engine moves
  every input handler in the visible-pointer overlay out of input delivery.
  Until now only the `mousemove` one had been deferred; the press handler was
  still adding a class, CREATING a DOM node, attaching a listener to it and
  appending it - which starts a CSS animation - synchronously in the parent
  process while a `mousedown` was in flight. That is more work than what had
  been removed, and on the click path rather than the move path.

  The move handler's cost was measured: with the overlay on, the median gap
  between the `mousemove` events THE PAGE receives moved by a whole clamp step,
  18 ms against 17, p = 0.0005. The press path was never measured, because the
  bench that found the first one injects and listens for `mousemove` only - it
  was watching the branch that had already been fixed. The remedy here is the
  same rule applied to all four handlers rather than a second special case: a
  handler records numbers and asks for a frame, and one place draws.

  ⛔ The timing gain is NOT measured. A press-path bench now exists and refuses
  to run on a loaded machine, which is all that has been available. This is a
  fix with a mechanism and a measured precedent, not a number.

- The core it pins also stops the pre-push hook from naming one cause for every
  kind of publish-gate refusal.

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
