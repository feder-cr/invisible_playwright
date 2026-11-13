---
title: "How to scrape WebSocket streams with Playwright"
description: "Scrape WebSocket streams with Playwright: subscribe with page.on('websocket'), classify each frame before parsing, apply the deltas to a snapshot, and stamp every row with a UTC receive time."
parent: "Scraping with Playwright"
grand_parent: "Guides"
nav_order: 109
---


# How to scrape WebSocket streams with Playwright

**To scrape a WebSocket stream with Playwright, subscribe with `page.on("websocket")`,
attach a `framereceived` handler to every socket it reports, classify each frame before
you parse it, apply the data frames as deltas against the snapshot the stream opened
with, and write one row per applied update carrying a UTC receive timestamp, a connection
id and the protocol's own sequence number.** `page.on("response")` never fires for socket
traffic, so the capture recipe that works for XHR finds nothing at all here.

A socket is not a slower XHR. An XHR is a request and a document: you ask, you get one
complete thing, you parse it. A socket is an open pipe: a first message describing the
world, then a long run of small corrections to it. Nothing you click causes any
particular frame. Nothing announces that the stream is finished. And data, heartbeats and
bookkeeping all share the single channel, so the first `json.loads` you write throws on a
payload that says `ping`.

This page is the recorder that survives that: the hook, the type check before the parser,
the delta application that turns patches back into state, the timestamp taken inside the
handler, and the reconnect detection without which a long capture stores two series under
one set of sequence numbers.

## The hook is page.on("websocket"), not page.on("response")

Playwright reports sockets on an event of their own. `page.on("websocket", handler)`
fires once per socket, and the handler receives a `WebSocket` object carrying the URL and
its own events: `framesent`, `framereceived`, `socketerror` and `close`. Frames are not
HTTP responses, so they never reach a `response` listener and they are not what
`page.route` intercepts. Register the page listener before `goto`, or the socket opens
during load and you lose the subscribe frame and the snapshot.

```python
from invisible_playwright import InvisiblePlaywright

def attach(ws):
    print("socket opened:", ws.url)
    ws.on("framesent", lambda payload: print("out:", payload[:120]))
    ws.on("framereceived", lambda payload: print("in :", payload[:120]))
    ws.on("socketerror", lambda err: print("socket error:", err))
    ws.on("close", lambda _: print("socket closed:", ws.url))

with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    page.on("websocket", attach)      # register BEFORE goto or you miss the open
    page.goto("https://example.com/live")
    page.wait_for_timeout(15_000)
```

The outbound side is worth listening to as well. `framesent` carries the subscribe
message the page's own JavaScript sends, which names the channels being requested and
often the field the server will sequence on. One outbound frame is cheaper schema
documentation than an afternoon spent reading the bundle. Recent Playwright versions also
expose `page.route_web_socket()`, which mocks and rewrites socket traffic. For reading a
live stream you want the observation hook above, which changes nothing on the wire. For
the initial state the page fetches over HTTP before the socket opens,
[capturing XHR and API responses](how-to-capture-xhr-api-responses-playwright.md) covers
the response side.

## A socket sends deltas, not documents

The first frames are usually a snapshot and everything after is a patch, so a scraper
that stores frames is storing instructions rather than data. Apply the patches to a state
dictionary as they arrive and store the result.

Three shapes cover nearly every stream. Full replacement, where each frame is the whole
current object, which is rare and easy. Snapshot plus delta, where one early frame
carries everything and the rest carry only what changed, which is the common case. And an
event log, where each frame is an independent fact and there is no state to maintain. Log
twenty raw frames and read them before writing a line of parsing: the three need
different code and look alike from a distance.

```python
def apply_frame(book, msg):
    kind = msg.get("type")
    if kind == "snapshot":
        book.clear()                                  # a snapshot replaces, never merges
        book.update({row["id"]: row for row in msg.get("data", [])})
    elif kind == "update":
        for row in msg.get("data", []):
            if row.get("deleted"):
                book.pop(row["id"], None)
            else:
                book.setdefault(row["id"], {}).update(row)
    return book
```

Store the affected entry after the patch, not the patch itself, unless the change log is
what you actually want. Stored patches make every later question a replay from the
snapshot, and if the capture began after that snapshot went past, some questions have no
answer at all.

## Text, binary, and the compression that is already gone

`framereceived` hands you a `str` for a text frame and `bytes` for a binary one, and
permessage-deflate compression has already been undone by the browser before the payload
reaches your handler. What is left is application-level encoding, which the browser will
not unwrap for you.

So the type check comes first, ahead of any `.strip()` or `json.loads`. After it, a
binary payload is often self-describing in its first byte or two. A gzip stream starts
with 0x1F 0x8B. A zlib stream starts with 0x78, usually followed by 0x01, 0x9C or 0xDA. A
payload that opens with a brace is JSON somebody sent as a binary frame.

```python
import gzip
import zlib

GZIP_MAGIC = bytes([0x1F, 0x8B])   # every gzip stream begins with these two bytes
ZLIB_FIRST = 0x78                  # 0x78 0x01, 0x78 0x9C and 0x78 0xDA are all zlib

def decode_binary(payload: bytes):
    if payload[:2] == GZIP_MAGIC:
        return gzip.decompress(payload).decode("utf-8")
    if payload[:1] and payload[0] == ZLIB_FIRST:
        return zlib.decompress(payload).decode("utf-8")
    return None      # not self-describing: this one needs the site's own decoder
```

Here is where the approach stops helping. If the payload is protobuf, msgpack or a
length-prefixed encoding the site invented, nothing in the frame carries the schema, and
recovering it means reading the page's own JavaScript decoder. In that case the rendered
DOM, flicker and all, is the honest source, and the socket route is a dead end worth
abandoning on day one.

## Classify every frame before you parse it

Data, heartbeats, subscription acknowledgements and errors arrive on the same channel, so
classification has to happen before parsing. A heartbeat is often a bare word, a single
digit, or an empty frame, and `json.loads` throws on all three. Wrapping the parse in a
bare `except` is not classification: the frames still disappear, they just disappear
quietly.

```python
import json

def classify(payload):
    """Return (kind, parsed). Never raises."""
    if isinstance(payload, (bytes, bytearray)):
        text = decode_binary(bytes(payload))
        if text is None:
            return "binary", payload           # needs a site-specific decoder
    else:
        text = payload
    text = text.strip()
    if text in ("", "ping", "pong", "h", "2", "3"):    # heartbeats are site-specific
        return "heartbeat", None
    if not text.startswith(("{", "[")):
        return "opaque", text                  # framed protocols prefix with a digit
    try:
        msg = json.loads(text)
    except ValueError:
        return "unparsed", text
    if isinstance(msg, dict):
        if msg.get("type") in ("subscribed", "ack", "welcome"):
            return "ack", msg
        if msg.get("error") or msg.get("type") == "error":
            return "error", msg
    return "data", msg
```

Count the kinds and print the counter at the end of every run. A capture that ends with
four thousand heartbeats and three data frames says the subscription never took, and that
is indistinguishable from a quiet feed until the kinds are counted apart. Write the
`unparsed` bucket to a side file instead of dropping it: that bucket is where a protocol
change shows up first, one frame at a time, before it becomes an empty dataset.

## Stamp the row on arrival, in UTC, with the sequence number

Frames arrive out of band from anything you click, so the only ordering you own is the
moment of receipt. Take it inside the handler with `datetime.now(timezone.utc)`, and
record the protocol's sequence number beside it when there is one: the two answer
different questions.

The sequence number is the server's order and is authoritative for order. The receive
timestamp is your clock and is authoritative for latency, and for what the data looked
like at a given minute. They disagree routinely: two frames can share a server timestamp
and arrive milliseconds apart, and one burst can deliver several sequence numbers inside
a single receive millisecond. Use UTC, not local time: a capture that crosses a
daylight-saving change with local stamps contains an hour that repeats and cannot be
sorted afterwards. Record the socket URL too, since a page often opens several and one of
them is analytics.

```python
from datetime import datetime, timezone

def make_row(ws_url, conn_id, kind, msg):
    return {
        "received_at": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
        "socket": ws_url,
        "conn": conn_id,        # which connection: see the reconnect section
        "seq": msg.get("seq") if isinstance(msg, dict) else None,
        "kind": kind,
        "payload": msg,
    }
```

Build the row inside the handler. A handler that appends raw payloads to a list for a
later loop to stamp gives every row the time the loop got there: one clustered timestamp
that looks plausible, sorts fine and is wrong. Append rows as JSON Lines so a crash keeps
everything written up to that point, the reason
[writing to JSON Lines](how-to-scrape-to-json-lines-playwright.md) suits any long capture.

## The stream never ends, so choose the stopping condition

A socket keeps delivering after the page looks idle, so there is no natural end, and
`wait_until="networkidle"` never settles: an open feed keeps the network busy and the
call times out like a broken page. That trap is the subject of
[waiting for the page to load](how-to-wait-for-page-load-playwright.md). Choose the exit
yourself, from three kinds.

A duration is the honest default for an open-ended feed: capture two minutes, or an hour,
and say which. A row count fits when you want a fixed number of updates and do not care
how long that takes. A data condition fits when the protocol has a terminal frame or a
completion flag. Those are three different experiments, and the choice belongs in the
code, not in whenever somebody pressed Ctrl-C.

Write the reason for stopping into the output, as a manifest line or in the file name. A
capture that ended on its deadline and one that died on a dropped connection produce
byte-identical files apart from that fact. It is the same bookkeeping that makes
[an interrupted scrape resumable](how-to-resume-an-interrupted-scrape-playwright.md).

## A reconnect resets the sequence and corrupts the series

When the socket drops and the page reopens it, `page.on("websocket")` fires again with a
new `WebSocket` object, the server replays a fresh snapshot, and the sequence numbers
start over at zero or one. A file keyed on sequence alone now holds two different series
wearing the same numbers, and nothing raises.

The damage is quiet in both directions. Deduplicating on sequence discards the whole
second connection as though it were a repeat. Sorting by sequence interleaves two runs
into a series that never happened. The fix is one field: allocate a connection id when
the socket attaches, put it in every row, and sort on connection and sequence together.

Reconnection also resets your state, and that is the half people miss. The new snapshot
is a replacement, not an update. Keep patching into the dictionary from the previous
connection and every entry removed while you were disconnected stays in your data
forever, correct-looking and stale. Clear the state when a socket attaches. A gap inside
one connection is a different fault: sequence 41 following 39 means a frame was lost and
the state you hold is wrong. Restart from a fresh snapshot rather than patch a book you
know is broken.

Reconnecting under the same seed presents the same device each time, so a feed that drops
and resumes reads as one returning subscriber rather than a churn of new clients.
[Scraping live price feeds](how-to-scrape-cryptocurrency-prices-playwright.md) covers why
a held-open socket is metered differently from a page visit.

## The recorder, assembled

The pieces fit into one class: a connection id per socket, a state dictionary cleared on
attach, counters for kinds and gaps, a stamped row per data frame, and a deadline.

```python
import collections
import itertools
import json
import time
from invisible_playwright import InvisiblePlaywright

class Recorder:
    def __init__(self, path, max_seconds=120, max_rows=None):
        self.out = open(path, "a", encoding="utf-8")
        self.deadline = time.monotonic() + max_seconds
        self.max_rows, self.rows = max_rows, 0
        self.conn_ids = itertools.count(1)
        self.kinds = collections.Counter()
        self.book, self.last_seq = {}, {}

    def attach(self, ws):
        conn = next(self.conn_ids)   # a fresh id for every reconnect
        self.book.clear()            # a reconnect resets state, it does not extend it

        def on_frame(payload):
            kind, msg = classify(payload)
            self.kinds[kind] += 1
            if kind != "data":
                return
            seq, prev = msg.get("seq"), self.last_seq.get(conn)
            if seq is not None and prev is not None and seq != prev + 1:
                self.kinds["gap"] += 1     # a real hole, inside one connection
            self.last_seq[conn] = seq
            apply_frame(self.book, msg)
            print(json.dumps(make_row(ws.url, conn, kind, msg)), file=self.out, flush=True)
            self.rows += 1

        ws.on("framereceived", on_frame)

    def should_stop(self):
        if time.monotonic() > self.deadline:
            return True
        return self.max_rows is not None and self.rows >= self.max_rows

with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    rec = Recorder("frames.jsonl", max_seconds=120)
    page.on("websocket", rec.attach)
    page.goto("https://example.com/live")
    while not rec.should_stop():
        page.wait_for_timeout(500)
    print(dict(rec.kinds))
```

The loop polls `should_stop` rather than sleeping once for the whole duration, so a
count-based or condition-based exit fires when it happens. The counter printed at the end
is the first thing to read: it says whether you captured a feed or a heartbeat.

## Conclusion

A WebSocket stream punishes every habit that works on documents. Frames are patches, not
records, so what you maintain and store is the state, not the instructions. The channel
is shared, so classification comes before parsing and a bare `except` is not
classification. Payloads are sometimes bytes, the browser has already unwrapped the
transport compression, and what remains is either self-describing in its first byte or
needs the site's own decoder. Ordering is yours to record: a UTC stamp taken inside the
handler, a sequence number when one exists, and a connection id so a reconnect cannot
merge two series into one. Then pick an ending on purpose, and write down which ending it
was.

## Short answers to the questions that lead here

**How do I read WebSocket frames in Playwright?** `page.on("websocket", handler)` fires
once per socket, and inside the handler `ws.on("framereceived", ...)` fires for every
inbound frame with a `str` or `bytes` payload. Register the page listener before `goto`
or you miss the opening frames.

**Why does page.on("response") not see my socket data?** Because frames are not HTTP
responses. Once a socket is open its traffic is frames, reported only on that socket's own
events, and `page.route` does not touch them either.

**Why does json.loads throw on some frames?** Because heartbeats, acknowledgements and
errors share the channel with data, and a heartbeat is often a bare word, a digit or an
empty frame. Classify first, parse only the kinds you recognise.

**Should I store the frames or the data?** The data. Most streams send a snapshot and
then patches, so a file of frames is a file of instructions that only means something
when replayed from a snapshot you may never have captured.

**When do I stop capturing?** When you decide: a duration, a row count, or a data
condition such as a terminal message. A socket does not stop on its own, and
`networkidle` never settles while one is open.

**My series has duplicate sequence numbers. What happened?** The socket dropped and
reconnected, and the server restarted its counter. Give every connection an id, put it in
each row, and clear your applied state whenever a new socket attaches.

## Sources

- Playwright's [WebSocket class](https://playwright.dev/python/docs/api/class-websocket),
  for `framesent`, `framereceived`, `socketerror`, `close`, `url` and `is_closed`, and for
  the payload arriving as `str` or `bytes`. Retrieved 2026-08-28.
- Playwright's [network guide](https://playwright.dev/python/docs/network), for the
  `page.on("websocket")` event and where it sits relative to the request and response
  events. Retrieved 2026-08-28.
- Playwright's [page events](https://playwright.dev/python/docs/api/class-page#page-event-web-socket),
  including `page.route_web_socket()`, which mocks socket traffic and is the opposite of
  the read-only hook used above. Retrieved 2026-08-28.
- RFC 7692, the permessage-deflate extension, negotiated at the protocol level and
  unwrapped by the browser, which is why a compressed stream reaches the handler as
  ordinary text.
- This project's own behaviour: the object the library returns is a real Playwright
  `Browser`, so every call above is the upstream API.

**See also:** [capturing XHR and API responses](how-to-capture-xhr-api-responses-playwright.md)
for the snapshot that precedes the socket,
[writing to JSON Lines](how-to-scrape-to-json-lines-playwright.md) for the append-safe row
file, [scraping live price feeds](how-to-scrape-cryptocurrency-prices-playwright.md) for a
stream that never pauses, and
[resuming an interrupted scrape](how-to-resume-an-interrupted-scrape-playwright.md) for
how a capture ends.

---

*Written while maintaining [invisible_playwright](https://github.com/oss-invisible/playwright),
a Firefox patched at the C++ level driven by stock Playwright. The connection id is the
field that was missing the first time: a six-hour capture crossed two silent reconnects,
looked complete, and held three overlapping sequence ranges nobody saw until it was
sorted.*
