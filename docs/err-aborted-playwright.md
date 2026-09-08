---
title: "net::ERR_ABORTED in Playwright"
description: "net::ERR_ABORTED usually means a navigation was cancelled on purpose, by a redirect, a download, route.abort(), or a second goto(), not that anything actually failed. When it is benign and when it signals a real bug."
parent: "Network, Proxy and WebRTC"
grand_parent: "Guides"
nav_order: 33
---


# net::ERR_ABORTED in Playwright

`net::ERR_ABORTED` means a request was cancelled before it finished, not that it
failed. Chromium's own network error list defines it as "an operation was aborted
(due to user action)," and the cancelling "action" is very often something your own
page, your own script, or Playwright's own navigation handling did on purpose. This
is the one error in the `net::ERR_` family that is frequently not a problem at
all. [ERR_CONNECTION_RESET](err-connection-reset-playwright.md) and
[ERR_HTTP2_PROTOCOL_ERROR](err-http2-protocol-error-playwright.md) report a
connection that genuinely broke; this one usually reports one that was closed on
purpose, and treating every occurrence as a bug wastes time chasing something that
already worked.

## The causes that are usually fine

**A download started instead of a page load.** When a navigation resolves to a file
download instead of an HTML response, the browser aborts the page-level navigation
in favor of handing the response to the download machinery, and Playwright surfaces
that abort as `net::ERR_ABORTED` even though the file saves correctly.
[microsoft/playwright-java#541](https://github.com/microsoft/playwright-java/issues/541)
shows exactly this: navigating to a URL that returns only a JSON payload logs
`net::ERR_ABORTED` on the response, reproduced across Chromium, Firefox and WebKit
alike, with the download itself completing successfully regardless. The fix is not
suppressing the error, it is not using `page.goto()` to wait on a navigation that was
never going to become a page: use `page.expect_download()` around the action that
triggers it instead.

**A redirect chain moved the page somewhere else mid-flight.** Authentication flows
in particular chain several redirects, and Chromium's request lifecycle can report
an intermediate hop as aborted, since it was superseded before completing, without
the overall navigation actually failing.

**Two navigations fired in quick succession.** Calling `page.goto()` a second time
before the first has resolved cancels the first, and the cancelled one reports
`net::ERR_ABORTED`. This is standard browser behavior, not a Playwright defect, and
it is intentional the moment your own code causes it.

**An SPA replaced the frame during navigation.** "Maybe frame was detached?" appended
to the error, seen in reports like
[microsoft/playwright#13071](https://github.com/microsoft/playwright/issues/13071)
and [#34889](https://github.com/microsoft/playwright/issues/34889), points at a
single-page app tearing down and rebuilding its root frame while a navigation was in
flight, ordinary behavior for that class of app rather than a network failure.

## The causes that are a real problem

**Your own `page.route()` handler called `route.abort()`.** If a route handler is
deliberately aborting requests, matched by URL pattern, to speed up a test or block
unwanted third-party calls, and the pattern is broader than intended, it can abort a
request the test actually needed. [microsoft/playwright#21451](https://github.com/microsoft/playwright/issues/21451)
is exactly this shape: a route handler aborting a specific unwanted domain pattern
threw `net::ERR_ABORTED` on the next `page.goto()` because the abort landed on
in-flight navigation rather than the intended third-party request.

**`page.close()` was called while a navigation was still in flight.** Closing the
page tears down whatever requests it had open; if that happens mid-navigation on
purpose or by an unrelated timeout, the abort is real and the navigation that was
racing it never gets a result. When the teardown came from the browser or the
context disappearing under you, the exception that surfaces first is usually
[TargetClosedError](playwright-targetclosederror-causes.md), and the abort is the
second thing you see.

**A route handler that never resolves.** A `page.route()` interceptor that neither
calls `route.continue()`, `route.fulfill()`, nor `route.abort()` leaves a request
hanging, and it can be aborted during teardown rather than completing, which reads
identically to an intentional abort but traces back to a handler bug instead.

## Telling the two apart

Read what triggered the abort, not just the string. A download-triggering URL,
a redirect chain, or a second `goto()` you called yourself all explain the abort on
their own and need no fix. A route handler you wrote, a `page.close()` call your own
code or test framework issued, or a hanging interceptor are bugs in the automation
code, and the fix is in that code, not in retrying the navigation or raising a
timeout.

```bash
DEBUG=pw:api python your_script.py 2> pw.log
```

Reading which call issued immediately before the abort, a second `goto()`, a
`route.abort()`, a `page.close()`, separates the benign cases from the real ones
faster than guessing from the exception alone.

## The honest boundary

`net::ERR_ABORTED` is not a fingerprint or network-identity failure, and no amount
of browser realness changes whether it fires. It is a navigation-lifecycle signal
that fires identically on a stock Playwright Chromium, a stock Firefox, and this
project's patched build, because the cancellation is decided by request-handling
logic in the engine, on your own code's instruction, not by anything about how real
the browser's identity looks to a remote site. The error that does carry that
second meaning is a different one:
["Execution context was destroyed" can be a navigation you did not ask for](execution-context-destroyed.md),
which is worth reading before deciding an abort was a block.

## Short answers to the questions that lead here

**What does net::ERR_ABORTED mean?** A request or navigation was cancelled before
completing, most often on purpose, by a download, a redirect, a second navigation, or
a `route.abort()` call, rather than by a network or server failure.

**Is net::ERR_ABORTED always a bug?** No, and most of the time it is not. A download
replacing a page load, a redirect chain, and overlapping navigations you triggered
yourself are all expected causes with no fix required.

**How do I stop it from firing on a download?** Do not wait on `page.goto()` to
resolve a URL that becomes a download. Wrap the triggering action in
`page.expect_download()` instead.

**My own route.abort() call seems to be the cause. How do I fix that?** Narrow the
URL pattern your handler matches so it only intercepts the requests you actually
intend to block, not in-flight navigation traffic that happens to match too broadly.

**Does invisible_playwright cause or fix this error?** Neither. It is a
navigation-lifecycle event decided by request handling and your own automation
code, entirely independent of the browser's fingerprint or identity layer.

## Sources

- Chromium's [`net/base/net_error_list.h`](https://chromium.googlesource.com/chromium/src/+/main/net/base/net_error_list.h),
  for `ERR_ABORTED` (-3), retrieved 2026-08-30.
- [microsoft/playwright-java#541](https://github.com/microsoft/playwright-java/issues/541),
  `net::ERR_ABORTED` logged when navigating to a URL that only returns a downloadable
  payload, reproduced across all three engines.
- [microsoft/playwright#21451](https://github.com/microsoft/playwright/issues/21451),
  a `page.route()` handler's `route.abort()` call surfacing as `net::ERR_ABORTED` on
  a subsequent navigation.
- [microsoft/playwright-python#2131](https://github.com/microsoft/playwright-python/issues/2131),
  an intermittent `net::ERR_ABORTED` report during ordinary navigation.
- [microsoft/playwright#13071](https://github.com/microsoft/playwright/issues/13071)
  and [#34889](https://github.com/microsoft/playwright/issues/34889), the
  "maybe frame was detached?" variant tied to SPA frame replacement during
  navigation.
- Playwright's own [`Route` API reference](https://playwright.dev/python/docs/api/class-route),
  for the `route.abort()` method and its optional error-code argument.

**See also:** [Playwright TargetClosedError: the causes and the fixes](playwright-targetclosederror-causes.md)
for another error whose real cause lives in the lines logged before it rather than
in the exception itself, [ERR_CONNECTION_RESET in Playwright](err-connection-reset-playwright.md)
for a genuinely network-level failure to contrast against, and
[ERR_HTTP2_PROTOCOL_ERROR in Playwright](err-http2-protocol-error-playwright.md) for
a case where a server deliberately resetting a stream is easy to mistake for an
accident.

---

*From the notes of [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level driven by stock Playwright. `net::ERR_ABORTED`
tracks a navigation-lifecycle decision, most often your own code's, and has nothing
to do with how the browser's identity reads to a remote site.*
