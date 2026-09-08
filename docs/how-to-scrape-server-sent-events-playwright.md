---
title: "How to scrape server-sent events with Playwright"
description: "Scrape server-sent events with Playwright: the response event fires but body() never returns, so replace window.EventSource in an init script, wrap fetch, and parse text/event-stream yourself."
parent: "Scraping with Playwright"
grand_parent: "Guides"
nav_order: 117
---


# How to scrape server-sent events with Playwright

**To scrape server-sent events with Playwright, do not reach for a network event at all:
install a `page.add_init_script` that replaces `window.EventSource` and wraps `fetch`
before any of the page's own scripts run, pair it with `page.expose_function` so each
message crosses back into Python as it arrives, and stamp every row with a UTC receive
time and the last event id so a reconnect resumes instead of duplicating.** Playwright
exposes WebSocket frames on a first-class event and exposes no equivalent for SSE at any
level of the public API, so the hook that reads one is not the hook that reads the other.

SSE has a reputation as the easy one. One direction, plain text, a documented line format,
no handshake, no binary frames. All true of the protocol, and none of it true of the
tooling. For a Playwright scraper the two invert: a socket hands you `framereceived` with
the payload sitting in the argument, while an event stream hands you a request whose
`resource_type` is `eventsource` and then goes quiet for as long as the stream lives.

This page is what fills that gap. Why the obvious hook hangs instead of failing, why the
route everybody recommends is narrower than it looks, the instrumentation that works on
all three browsers, and a parser faithful enough to the spec that a comment line and an id
with no data both do the right thing.

## Playwright shows WebSocket frames and shows nothing for SSE

The asymmetry is worth stating precisely, because it is the whole reason this page exists.
`page.on("websocket")` fires once per socket and hands you a `WebSocket` object with
`framesent` and `framereceived` on it, each carrying the payload as an argument. That is a
message-level API, and
[scraping WebSocket streams](how-to-scrape-websocket-streams-playwright.md) is mostly a
matter of using it well.

There is no `page.on("eventsource")`. There is no message event on any object Playwright
hands you for a stream. What does exist is the classification: `eventsource` is a real
`resource_type`, sitting in the same list as `xhr`, `fetch`, `websocket` and the rest. So
you can see the stream open, count the streams a page holds, and block one with
`page.route`. You cannot read a single message of it. The support stops at the request.

## The response event fires, and then body() never returns

`page.on("response")` does fire for an SSE request, and it fires early. Playwright emits
that event when the status and headers arrive, not when the body does, which for an
ordinary document is a distinction nobody notices. Here it is the whole trap. The listener
runs, the URL is right, the content type says `text/event-stream`, and the capture looks
like it is working.

Then `response.body()` waits for a body that never completes, because a live stream not
completing is the point of it. Nothing raises. No timeout you configured applies. The
script sits there.

```python
from invisible_playwright import InvisiblePlaywright

def on_response(response):
    if response.request.resource_type != "eventsource":
        return
    print("stream seen:", response.status, response.headers.get("content-type"))
    # Do NOT do this on a live stream. The body never finishes, so the call
    # does not raise and does not time out. It just never comes back.
    # body = response.body()

with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    page.on("response", on_response)      # fires on headers, not on the body
    page.goto("https://example.com/live")
    page.wait_for_timeout(10_000)
```

The call that would fix this is `Response.bodyAsStream()`, and it does not exist.
Playwright issue 17199 asked for it on 2022-09-08. At the retrieval date it is still open
and labelled P3-collecting-feedback, so there is no supported way to read a response body
incrementally, and no partial read to fall back on.

One caution, stated plainly because I would rather not assert something I have not run.
The hang follows from `response.finished()` semantics, which resolve when the response
finishes, plus that open issue, and it is widely reported by people who hit it. It is a
strong inference and not a measurement of mine. Spend ten lines reproducing it against
your own stream before you build anything on the claim, and put a timeout around the
reproduction so confirming it costs a few seconds instead of an evening. For requests
that do end, [capturing XHR and API responses](how-to-capture-xhr-api-responses-playwright.md)
is the hook that works.

## CDP has the parsed message, on one browser, for one kind of client

The Chrome DevTools Protocol carries the answer people find first.
`Network.eventSourceMessageReceived` fires with exactly five fields: requestId, timestamp,
eventName, eventId and data. That is the parsed frame, already split at the colon and
already stripped, delivered without any parsing work on your side. It is a good event, and
recommending it flatly is still wrong, because it is restricted twice.

The first restriction is the browser. Playwright states that CDP sessions are only
supported on Chromium-based browsers, so the route does not exist on Firefox or WebKit at
all, which is one more entry on
[the ledger between BiDi and CDP](bidi-vs-cdp-detection.md).

The second restriction is the one that hurts. The event only fires for real `EventSource`
objects, and Chromium issue 40659493 is titled "DevTools: XHR and fetch to
text/event-stream resources don't show events". A large share of modern SSE traffic never
constructs an `EventSource`, because `EventSource` cannot send a custom header and `fetch`
can, so anything needing an authorization header reads its stream with fetch and a
`ReadableStream`. Chat interfaces that stream tokens are almost all in that group. On
those pages CDP reports nothing, and reports it silently.

## Replace EventSource before the page's own scripts run

The portable route is instrumentation rather than interception, and it rests on two
documented guarantees. `page.add_init_script` is evaluated after the document has been
created but before any of the page's scripts have run, which is early enough to swap
`window.EventSource` before page code takes a reference to it. `page.expose_function` adds
a function on the window object of every frame, and that binding outlives every page load
in the frame, so each message crosses into Python without polling.

```python
from datetime import datetime, timezone
from invisible_playwright import InvisiblePlaywright

EVENTSOURCE_HOOK = r"""
(() => {
  const Native = window.EventSource;
  if (!Native) return;

  function Wrapped(url, init) {
    const es = new Native(url, init);
    const add = EventTarget.prototype.addEventListener.bind(es);
    const mirrored = new Set();

    const watch = (type) => {
      if (mirrored.has(type)) return;
      mirrored.add(type);
      add(type, (ev) => window.__sse({
        url: String(url),
        event: type,
        data: ev.data,
        last_event_id: ev.lastEventId || "",
      }));
    };

    // es.onmessage = fn never passes through addEventListener, so subscribe to
    // the default type here instead of waiting for the page to ask for it.
    watch("message");

    // then mirror every custom type, at the moment the page subscribes to it
    es.addEventListener = function (type, fn, opts) {
      watch(type);
      return add(type, fn, opts);
    };
    return es;
  }

  Wrapped.prototype = Native.prototype;
  Wrapped.CONNECTING = 0; Wrapped.OPEN = 1; Wrapped.CLOSED = 2;
  window.EventSource = Wrapped;
})();
"""

def on_message(msg):
    msg["received_at"] = datetime.now(timezone.utc).isoformat(timespec="milliseconds")
    print(msg)

with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    page.expose_function("__sse", on_message)   # binding outlives page loads
    page.add_init_script(EVENTSOURCE_HOOK)      # runs before the page's scripts
    page.goto("https://example.com/stream")
    page.wait_for_timeout(30_000)
```

Both registrations go before `goto`, or the page opens its stream against the native
constructor and the hook mirrors nothing. Two lines in that script are the difference
between catching everything and catching half. Subscribing to `message` up front covers
the pages that assign `onmessage` directly, which never reaches `addEventListener`.
Wrapping `addEventListener` covers the custom event names, which you cannot know in
advance and which carry most of the interesting payloads.

Say the cost out loud: this replaces a window global, so a page that inspects
`EventSource` finds something whose `toString` does not report native code.

## Wrap fetch as well, or you miss the streams worth reading

The fetch path needs a different shape, because there is no object to replace. What there
is instead is a response body you can copy. `ReadableStream.tee()` splits it into two
branches: read one, hand the other back to the page, and the page behaves as though
nothing happened.

```python
FETCH_HOOK = r"""
(() => {
  const nativeFetch = window.fetch;
  window.fetch = async function (input, init) {
    const res = await nativeFetch(input, init);
    const ctype = res.headers.get("content-type") || "";
    if (!res.body || !ctype.includes("text/event-stream")) return res;

    const url = typeof input === "string" ? input : (input.url || String(input));
    const [ours, theirs] = res.body.tee();     // one branch each
    const reader = ours.getReader();
    const decoder = new TextDecoder();

    (async () => {
      for (;;) {
        const { value, done } = await reader.read();
        if (done) { window.__sseRaw({ url: url, chunk: "", done: true }); return; }
        // drain promptly: tee buffers whatever the slower branch has not taken
        window.__sseRaw({
          url: url,
          chunk: decoder.decode(value, { stream: true }),
          done: false,
        });
      }
    })();

    return new Response(theirs, {
      status: res.status, statusText: res.statusText, headers: res.headers,
    });
  };
})();
"""
```

Two limits, both worth knowing before you ship it. A tee holds whatever the slower branch
has not consumed, so a reader that stalls turns into memory growth on a long stream, which
is why the loop above forwards each chunk and does no work of its own. And the
reconstructed `Response` is not the original: it reports an empty `url`, loses
`redirected`, and its `type` is `default`. A page that reads any of those will notice. When
that happens, keep the EventSource hook and drop the fetch one rather than fighting it.

## Parse the wire format the way the spec parses it

The fetch branch gives you bytes, so the parsing is yours, and the format has six rules
that each cost you data when skipped. Lines split at the first colon, not at every colon,
so a `data` line holding JSON survives. Exactly one leading space is removed from the
value, not all whitespace, so a payload that begins with two spaces keeps one. A line
starting with `:` is a comment and is ignored, which is how servers send keepalives, and a
parser that treats it as data fills your file with blanks. A `data` field appends its
value and then a single LF, so consecutive data lines concatenate with newlines between
them. An `id` sets the last event ID unless the value contains a NULL, in which case the
whole line is ignored. A `retry` sets the reconnection time only if the value is entirely
ASCII digits, and unknown field names are ignored outright.

```python
import re

LINE_BREAK = re.compile(r"\r\n|\r|\n")   # the three the spec allows, and only these

def parse_sse(chunk, state):
    """Yield one dict per dispatched event. state carries across chunks:
    {"buf": "", "data": "", "type": "", "id": None, "retry": None}"""
    buf, hold = state["buf"] + chunk, ""
    if buf.endswith("\r"):
        buf, hold = buf[:-1], "\r"       # may be the first half of a CRLF pair
    lines = LINE_BREAK.split(buf)
    state["buf"] = lines.pop() + hold    # the tail may be half a line

    for line in lines:
        if line == "":                                   # blank line dispatches
            if state["data"] == "":                      # empty buffer: no event,
                state["type"] = ""                       # but the id stays set
                continue
            payload = state["data"]
            if payload.endswith("\n"):
                payload = payload[:-1]                   # remove exactly one LF
            yield {"event": state["type"] or "message",  # default type
                   "data": payload,
                   "id": state["id"],
                   "retry_ms": state["retry"]}
            state["data"], state["type"] = "", ""
            continue
        if line.startswith(":"):                         # comment: the keepalive
            continue
        field, _, value = line.partition(":")            # first colon only
        if value.startswith(" "):
            value = value[1:]                            # one space, not lstrip()
        if field == "data":
            state["data"] += value + "\n"                # value, then a single LF
        elif field == "event":
            state["type"] = value
        elif field == "id":
            if "\x00" not in value:                      # a NULL voids the line
                state["id"] = value
        elif field == "retry":
            if value.isascii() and value.isdigit():      # ASCII digits or nothing
                state["retry"] = int(value)
        # every other field name is ignored
```

Two shapes in there are not decoration. `str.splitlines()` also breaks on form feed and on
U+2028, both of which can appear inside a JSON payload, so the split is a regex over the
three terminators the spec names. And an empty data buffer returns without dispatching,
while the last event ID is set before that return, so an `id` line arriving with no data
still moves your resumption point forward even though no message reaches the page.

## Reconnects, Last-Event-ID, and the stream that will not come back

Reconnection is automatic and it is where a naive capture doubles its rows. When the
browser reconnects it sends a `Last-Event-ID` header carrying the last id it saw, and a
well-behaved server replays from there. Those replayed messages are real messages with
real ids, so the deduplication key is the id, not the arrival order and not the payload
hash.

The delay before that reconnect has no specified value. The spec calls it
implementation-defined and describes it only as probably in the region of a few seconds,
so a test that asserts a number is asserting your browser's build rather than the
protocol. A `retry` field overrides it, and only when the value is all ASCII digits.

The ways a stream stops are worth telling apart, because from Python they look identical.
`readyState` is CONNECTING at 0, OPEN at 1 and CLOSED at 2. If the status is not 200, or
the content type is not exactly `text/event-stream`, the connection fails, and once the
user agent has failed the connection it does not attempt to reconnect. HTTP 204 is the
documented way for a server to tell a client to stop reconnecting, so a capture that goes
quiet may have been dismissed rather than broken. Record the status and the last id you
held, and the three cases separate themselves, which is the same bookkeeping that makes
[an interrupted scrape resumable](how-to-resume-an-interrupted-scrape-playwright.md).

## The recorder, and the reason it stopped

The pieces assemble into one object: a parser state per stream, a seen set keyed on stream
and id, a counter per event type, a deadline, and a recorded reason for stopping.

```python
import collections, json, time
from datetime import datetime, timezone
from invisible_playwright import InvisiblePlaywright

class StreamRecorder:
    def __init__(self, path, max_seconds=120):
        self.out = open(path, "a", encoding="utf-8")
        self.deadline = time.monotonic() + max_seconds
        self.state, self.seen = {}, set()
        self.kinds = collections.Counter()
        self.reason = "running"

    def write(self, url, event, data, event_id):
        if event_id and (url, event_id) in self.seen:   # replayed after a reconnect
            self.kinds["replayed"] += 1
            return
        if event_id:
            self.seen.add((url, event_id))
        self.kinds[event] += 1
        row = {"received_at": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
               "stream": url, "event": event, "id": event_id or None, "data": data}
        print(json.dumps(row), file=self.out, flush=True)

    def on_message(self, msg):        # from the EventSource wrapper, already parsed
        self.write(msg["url"], msg["event"], msg["data"], msg["last_event_id"])

    def on_raw(self, msg):            # from the fetch wrapper, still raw text
        if msg["done"]:
            self.reason = "stream closed"
            return
        st = self.state.setdefault(msg["url"],
            {"buf": "", "data": "", "type": "", "id": None, "retry": None})
        for ev in parse_sse(msg["chunk"], st):
            self.write(msg["url"], ev["event"], ev["data"], ev["id"] or "")

    def should_stop(self):
        if self.reason != "running":
            return True
        if time.monotonic() > self.deadline:
            self.reason = "deadline"
        return self.reason != "running"

with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    rec = StreamRecorder("events.jsonl", max_seconds=120)
    page.expose_function("__sse", rec.on_message)
    page.expose_function("__sseRaw", rec.on_raw)
    page.add_init_script(EVENTSOURCE_HOOK)
    page.add_init_script(FETCH_HOOK)
    page.goto("https://example.com/stream", wait_until="domcontentloaded")
    while not rec.should_stop():
        page.wait_for_timeout(500)
    print(rec.reason, dict(rec.kinds))
```

`wait_until="networkidle"` is the wrong wait here and will time out like a broken page,
because an open stream keeps the network busy forever, which is one of the traps in
[waiting for the page to load](how-to-wait-for-page-load-playwright.md). Rows go out as
JSON Lines so a killed run keeps everything written up to that moment, the same reason
[writing to JSON Lines](how-to-scrape-to-json-lines-playwright.md) suits any open-ended
capture. Read the counter first: a run that ends with a thousand keepalives and four
messages is telling you the subscription never took.

## Conclusion

Server-sent events are simpler on the wire and harder in Playwright, and the gap is
entirely in the tooling. There is no frame event, so the response hook that looks right
hangs on a body that never completes, and the CDP event that carries the parsed message is
limited to Chromium and to pages that build a real `EventSource`. What survives all three
browsers is instrumentation: replace the constructor and wrap fetch in an init script that
runs before page code, bring each message back through an exposed function, and parse the
format the way the spec parses it, colon by colon. Then key your deduplication on the
event id, because a reconnect will replay and the browser will not tell you it happened.

## Short answers to the questions that lead here

**Does page.on("response") work for server-sent events?** It fires, which is the trap.
Playwright emits the response event when status and headers arrive, so the stream shows up
immediately and the capture looks correct. `response.body()` then waits for a body that
never completes.

**Why is there no framereceived event for SSE?** Because Playwright models SSE as an HTTP
response rather than as a message-carrying connection. WebSocket has its own class with
`framesent` and `framereceived`; for SSE, `eventsource` is only a value of
`request.resource_type`.

**Can I read the messages over CDP?** On Chromium, for a page that constructs a real
`EventSource`, yes: `Network.eventSourceMessageReceived` gives requestId, timestamp,
eventName, eventId and data. Not on Firefox or WebKit, and not when the page reads its
stream with fetch.

**How do I catch a stream opened with fetch?** Wrap `window.fetch` in an init script,
check for `text/event-stream` in the content type, and `tee()` the body so one branch is
yours and one goes back to the page. Drain your branch, since a tee buffers the difference.

**Why does my parser lose the later lines of a multi-line message?** Because each `data`
field appends its value and then a single LF, so consecutive data lines join with newlines
between them and only the final LF is removed at dispatch. Concatenating without that LF
collapses them into one line.

**The stream stopped and nothing raised. What happened?** Three candidates that look
alike from Python. The server may have answered 204, the documented way to say stop
reconnecting. The response may not have been 200 or not `text/event-stream`, which fails
the connection with no retry. Or the connection dropped and the browser is inside its
reconnection delay.

## Sources

- The WHATWG HTML standard,
  [server-sent events](https://html.spec.whatwg.org/multipage/server-sent-events.html),
  for every parsing rule on this page: the comment line, the first-colon split, the single
  stripped space, the data buffer and its trailing LF, the NULL rule on `id`, the
  ASCII-digits rule on `retry`, `Last-Event-ID`, the `readyState` values, the 204 case and
  the rule that a failed connection is not retried. Retrieved 2026-08-28.
- Playwright's [Request class](https://playwright.dev/python/docs/api/class-request), for
  `resource_type` and the `eventsource` value in its list. Retrieved 2026-08-28.
- Playwright's [Response class](https://playwright.dev/python/docs/api/class-response),
  for `body()` and `finished()`, which is where the hang argument comes from.
  Retrieved 2026-08-28.
- Playwright's [BrowserContext class](https://playwright.dev/python/docs/api/class-browsercontext),
  for `add_init_script` running before the page's own scripts, for `expose_function`
  adding a binding on every frame that outlives page loads, and for the statement that CDP
  sessions are only supported on Chromium-based browsers. Retrieved 2026-08-28.
- Playwright's [WebSocket class](https://playwright.dev/python/docs/api/class-websocket),
  for the `framesent` and `framereceived` events this page contrasts against.
  Retrieved 2026-08-28.
- Playwright issue [17199](https://github.com/microsoft/playwright/issues/17199),
  requesting `Response.bodyAsStream()`, opened 2022-09-08, open and labelled
  P3-collecting-feedback. Retrieved 2026-08-28.
- Chromium issue [40659493](https://issues.chromium.org/issues/40659493), "DevTools: XHR
  and fetch to text/event-stream resources don't show events". Retrieved 2026-08-28.
- The Chrome DevTools Protocol
  [Network domain](https://chromedevtools.github.io/devtools-protocol/tot/Network/#event-eventSourceMessageReceived),
  for the five parameters of `Network.eventSourceMessageReceived`. Retrieved 2026-08-28.

**See also:** [scraping WebSocket streams](how-to-scrape-websocket-streams-playwright.md)
for the protocol Playwright does expose at message level,
[capturing XHR and API responses](how-to-capture-xhr-api-responses-playwright.md) for the
requests that actually finish,
[writing to JSON Lines](how-to-scrape-to-json-lines-playwright.md) for the append-safe row
file a long capture needs, and
[resuming an interrupted scrape](how-to-resume-an-interrupted-scrape-playwright.md) for
picking up from the last id you held.

---

*Written while maintaining [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level driven by stock Playwright. The CDP event was the first
answer tried here and it is the one every thread recommends, and it reported nothing on a
page that read its stream with fetch: not a broken hook, just a hook waiting for an
EventSource that was never constructed.*
