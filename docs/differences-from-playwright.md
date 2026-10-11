---
title: "Differences from Playwright"
description: "Where invisible-playwright deliberately behaves differently from upstream Playwright, what it refuses, and why each one is a choice rather than a gap."
parent: "Documentation"
nav_order: 6
---


# Differences from Playwright

invisible-playwright adds nothing to Playwright's API: every method, option and
argument is Playwright's own. Where it cannot do what Playwright does without leaving
something a page can see, or without claiming to have done something it did not, it
says so with an error instead of pretending. This page lists those places.

## Objects that are not implemented

These exist only so the object tree is well formed. Every call on them raises an error
that names the reason.

| Object | Why |
|---|---|
| `Tracing` | It records a session for the trace viewer and drives none of it. `tracing.stop()` is accepted and does nothing, so code that always stops a trace keeps working. |
| `APIRequestContext` (`context.request`, `playwright.request`) | It sends HTTP from outside the page, without the browser's fingerprint, which is the opposite of what this package is for. |

## Calls that are refused

| Call | What happens | Why |
|---|---|---|
| `set_input_files()` with a folder | Error | The engine takes files. Pass the files inside the folder. |
| `set_input_files()` with an in-memory payload | Works, but `mimeType` is ignored | The payload is written to a file and handed over the way a user's pick is, so Firefox derives the type from the file name, as it does for a real pick. |
| `drop()` with an explicit data payload | Error | The engine builds the `DataTransfer` from a real pointer drag. Use `drag_and_drop()`. |
| `route_web_socket()` | Error | The engine reports WebSocket frames but cannot hold or rewrite one, so a route would look like it worked and pass everything through. |
| Screencast to a video file | Error | The engine streams JPEG frames and has no encoder. Pass `on_frame=` to receive the frames. |
| `register_selector_engine()` or `set_test_id_attribute()` after a page exists | Error | Both are built into the page's injected script when it is created. Call them before the first page. |
| `expose_binding()`, `expose_function()` | Error, not implemented yet | The page-facing half is easy; the reply path into the page's promise is not wired, and a binding that never answers would hang the page. |
| `set_storage_state()` with per-origin `localStorage` | Error, not implemented yet | Restoring only the cookies would look like success and leave half the session missing. |
| `response.body()` on a very large or old response | Error | The engine keeps at most 100 MB of response bodies per tab and 10 MB per response. Read the body sooner. |

## Behaviour that differs on purpose

- **`service_workers="block"`** is honoured by the engine itself, per context, with
  no page script: `register()` fails the way it fails when the worker's script is
  unreachable, and `navigator.serviceWorker` stays native. Playwright does it with a
  page script whose source names Playwright. On a persistent profile, turning the
  block on removes the service workers the profile had saved for that context, and
  they do not come back when the block is turned off.
- **Language.** `locale="auto"` (the default) declares the language of a Firefox
  build people in the proxy's country actually run. An explicit `locale` is
  `navigator.language`, followed by the fallbacks Firefox itself adds
  (`en-AU, en-US, en`).
- **Human input.** With `humanize=True` (the default) the pointer moves along a paced
  path, typing pauses before the first key, and a file chooser waits as a person
  looking for the file does. Calls take longer than in Playwright, and the page sees a
  person's timing.
- **The server speaks to this client only.** The bundled Playwright client and the
  bundled server are versioned together; a stock Playwright client cannot drive the
  server directly.
