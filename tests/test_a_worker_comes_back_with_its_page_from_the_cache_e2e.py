"""A live worker leaves with its page for the back-forward cache and comes back with it (Fission strategy 1).

A document starts a worker that answers (`postMessage('tick')` -> 1, 2, ...). The top goes forward to another
page, which puts the document in the back-forward cache with its worker frozen, and `go_back()` restores it
(`pageshow.persisted` is read: without the cache there is no restore and the test would prove nothing). The
client sees what a user's page has, read from the protocol events it receives (the client has no `worker` event
yet), in order:

1. `Page.workerCreated(w)` when the worker starts;
2. `Page.workerDestroyed(w)` when the top goes forward, before the commit of the new page;
3. at `go_back()`, after the contexts of the restored document are cleared (`Runtime.executionContextsCleared`,
   and `Page.frameDocumentRestored` when the engine sends it): `Page.workerCreated(w)` again, with the SAME
   `workerId`, once, and no `Page.workerDestroyed(w)` after the restore;
4. one live worker in the end (created minus destroyed), and it is the page's: it answers 3, not 1.

The worker lives in the top (a root that is the main one) or in a frame of another site (an out-of-process root
that is not the main one, announced again when it is reattached). The top goes forward to the same site (it keeps
its process: only the back-forward cache can end the worker, which is the case Juggler a95bbc6808 fixed) or to
another site (with strategy 1 the top changes process), by `page.goto` (Page.navigate gives the top a new browsing
context even on the same site) or by the page's own `location.href` (the same browsing context). Hosts a.test / b.test / c.test resolve to one local server
(`network.dns.localDomains`): two hosts are two sites, one port.

THE PRECONDITION IS FIREFOX'S, NOT OURS. Whether a document goes into the back-forward cache is Firefox's decision, taken when the
next navigation starts (nsDocShell::InternalLoad -> Document::CanSavePresentation, CanonicalBrowsingContext::AllowedInBFCache), and
a document that is refused is refused for good: it is loaded again on the way back. What refuses it is anything in flight in the
document's load group at that moment (a dedicated worker's script fetch is one: a worker that has not started yet makes the page
non-restorable, measured 0 of 8 restored when leaving right after `load`, 8 of 8 after one round trip to the worker) and the
page's `pageshow` not yet run (it clears the loading mark). So the test leaves only when every document it holds has had its
`pageshow` and its worker has answered, and a run whose page was still loaded again is not a result of ours: the gesture is
repeated on a fresh browser, up to ATTEMPTS times, and only ATTEMPTS refusals in a row fail, loudly.
"""
from __future__ import annotations

import threading
import time
from http.server import BaseHTTPRequestHandler

import pytest

from _servers import LocalServer

#: How many times the whole gesture may be started again because Firefox did not put the page in the cache (see above).
ATTEMPTS = 3


class _NotRestored(Exception):
    """Firefox loaded the page again instead of restoring it: the precondition of the test, not a verdict on it."""


STRATEGY_1 = {
    "fission.autostart": True,
    "fission.webContentIsolationStrategy": 1,
    "dom.ipc.processCount.webIsolated": 4,
    "network.dns.localDomains": "a.test,b.test,c.test",
}

# What a document with a worker runs: the worker answers every message with its count.
OWNER = """<script>
  window.__shown = [];
  addEventListener('pageshow', e => window.__shown.push(e.persisted));
  window.__w = new Worker('/worker.js');
  window.__ask = () => new Promise(resolve => {
    window.__w.onmessage = e => { if (e.data !== 'started') resolve(e.data); };
    window.__w.postMessage('tick');
  });
</script>"""
WORKER = "let n = 0; postMessage('started'); onmessage = () => postMessage(++n);"


@pytest.fixture
def sites():
    routes = {}

    class Handler(BaseHTTPRequestHandler):
        timeout = 5

        def log_message(self, *args):
            pass

        def do_GET(self):
            path = self.path.split("?")[0]
            if path not in routes:
                self.send_response(404)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            ctype, body = routes[path]
            body = body.encode()
            self.send_response(200)
            self.send_header("Content-Type", ctype + "; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = LocalServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    port = server.server_address[1]

    def at(host, path):
        return "http://%s:%d%s" % (host, port, path)

    routes["/worker.js"] = ("text/javascript", WORKER)
    routes["/top-worker"] = ("text/html", "<!doctype html><title>top</title>" + OWNER)
    routes["/frame-worker"] = ("text/html", "<!doctype html><title>frame</title>" + OWNER)
    routes["/top-frame"] = ("text/html", "<!doctype html><title>top</title>"
                            "<script>window.__shown = []; addEventListener('pageshow', e => window.__shown.push(e.persisted));"
                            "</script><iframe id=f src='%s' width=300 height=150></iframe>" % at("b.test", "/frame-worker"))
    routes["/other"] = ("text/html", "<!doctype html><title>other</title><p>other")
    try:
        yield {"top": {"page": at("a.test", "/top-worker")}, "oop-frame": {"page": at("a.test", "/top-frame")},
               "same-site": at("a.test", "/other"), "other-site": at("c.test", "/other")}
    finally:
        server.shutdown()
        server.server_close()


@pytest.fixture
def events(monkeypatch):
    """Every protocol event the client receives, in order."""
    from invisible_core.juggler.connection import EventListeners

    seen = []
    dispatch = EventListeners.dispatch_event

    def record(self, method, params, session):
        seen.append((method, params))
        return dispatch(self, method, params, session)

    monkeypatch.setattr(EventListeners, "dispatch_event", record)
    return seen


def _wait(check, seconds=10.0):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if check():
            return True
        time.sleep(0.1)
    return bool(check())


def _index(events, start, method, pred=lambda p: True):
    for i in range(start, len(events)):
        if events[i][0] == method and pred(events[i][1]):
            return i
    return None


def _wait_cacheable(page):
    """Until every document of the page that records `pageshow` has had it: a document whose `load` handlers are still running,
    or whose `pageshow` has not run, is still marked as loading and Firefox will not cache it (Document::OnPageShow clears
    the mark). The worker's own start is the round trips the caller made before."""
    def shown():
        return all(frame.evaluate("(window.__shown || [0]).length >= 1") for frame in page.frames)
    assert _wait(shown, 10.0), "a document of the page never ran its pageshow: %r" % [f.url for f in page.frames]


def _ask(page, where):
    target = page if where == "top" else next(f for f in page.frames if f.url.endswith("/frame-worker"))
    return target.evaluate("window.__ask()")


@pytest.mark.e2e
@pytest.mark.parametrize("how", ["goto", "location"])
@pytest.mark.parametrize("away", ["same-site", "other-site"])
@pytest.mark.parametrize("where", ["top", "oop-frame"])
def test_a_worker_leaves_with_its_page_for_the_cache_and_comes_back_the_same(firefox_binary, sites, events, where,
                                                                             away, how, engine_at_least):
    engine_at_least("firefox-62")

    def attempt():
        del events[:]                      # the events of this attempt only: the ids and the order are read from them
        _gesture(firefox_binary, sites, events, where, away, how)

    _until_restored(attempt)


def _until_restored(attempt, attempts=ATTEMPTS):
    """Run `attempt` until Firefox has put the page in the cache. A verdict of ours (any other failure) ends it at once."""
    refused = []
    for _ in range(attempts):
        try:
            attempt()
            return
        except _NotRestored as why:
            refused.append(str(why))
    raise AssertionError(
        "Firefox did not put the page in the back-forward cache in %d attempts out of %d, so this test proves nothing: %r"
        % (len(refused), attempts, refused))


def test_a_refused_page_is_tried_again_and_only_attempts_refusals_in_a_row_fail():
    calls = []

    def refused_twice():
        calls.append(1)
        if len(calls) < ATTEMPTS:
            raise _NotRestored("loaded again")

    _until_restored(refused_twice)
    assert len(calls) == ATTEMPTS

    calls.clear()
    with pytest.raises(AssertionError, match="proves nothing"):
        _until_restored(lambda: (calls.append(1), (_ for _ in ()).throw(_NotRestored("loaded again"))))
    assert len(calls) == ATTEMPTS

    calls.clear()                                   # a verdict of ours is not retried
    with pytest.raises(ValueError):
        _until_restored(lambda: (calls.append(1), (_ for _ in ()).throw(ValueError("ours"))))
    assert len(calls) == 1


def _gesture(firefox_binary, sites, events, where, away, how):
    from invisible_playwright import InvisiblePlaywright

    with InvisiblePlaywright(seed=42, binary_path=firefox_binary, humanize=False,
                             headless=True, extra_prefs=STRATEGY_1) as browser:
        page = browser.new_context().new_page()
        page.set_default_timeout(10000)
        page.goto(sites[where]["page"], wait_until="load")

        # 1. the worker starts and is told
        assert _wait(lambda: _index(events, 0, "Page.workerCreated", lambda p: p["url"].endswith("/worker.js")) is not None), \
            "the worker was never announced"
        created = events[_index(events, 0, "Page.workerCreated", lambda p: p["url"].endswith("/worker.js"))][1]
        wid = created["workerId"]
        if where == "oop-frame":
            assert _wait(lambda: any(f.url.endswith("/frame-worker") for f in page.frames)), (
                "the frame of the worker is not listed with its url: %r" % [f.url for f in page.frames])
        assert _ask(page, where) == 1
        assert _ask(page, where) == 2
        _wait_cacheable(page)

        # 2. the top goes forward: the worker ends for the client, before the new page commits
        forward = len(events)
        if how == "goto":
            page.goto(sites[away], wait_until="load")   # Page.navigate: a new browsing context for the top
        else:
            with page.expect_navigation(wait_until="load"):   # the page's own navigation: the same browsing context
                page.evaluate("url => { location.href = url; }", sites[away])
        committed = _index(events, forward, "Page.navigationCommitted", lambda p: p.get("url", "").endswith("/other"))
        assert committed is not None, "no commit of the new page was seen"
        ended = _index(events, forward, "Page.workerDestroyed", lambda p: p["workerId"] == wid)
        assert ended is not None, "the worker was not told ended when its page left: %r" % _methods(events, forward)
        assert ended < committed, "the worker was told ended only after the new page committed: %r" % _methods(events, forward)

        # 3. back from the cache: the same worker, told once, after the restored document's contexts are cleared
        back = len(events)
        page.go_back(wait_until="load")
        if page.evaluate("window.__shown") != [False, True]:
            raise _NotRestored("the page was loaded again instead of restored: __shown = %r" % page.evaluate("window.__shown"))
        if not _wait(lambda: _index(events, back, "Page.workerCreated", lambda p: p["workerId"] == wid) is not None):
            other = [p["workerId"] for m, p in events[back:] if m == "Page.workerCreated"]
            raise AssertionError(("the restored worker was announced under a NEW id %s, not %s: " % (other, wid)
                                  if other else "the restored document did not announce its worker again: ")
                                 + repr(_methods(events, back)))
        again = _index(events, back, "Page.workerCreated", lambda p: p["workerId"] == wid)
        cleared = _index(events, back, "Runtime.executionContextsCleared")
        if cleared is not None:
            assert cleared < again, "the worker was announced before the contexts were cleared: %r" % _methods(events, back)
        restored = _index(events, back, "Page.frameDocumentRestored")
        if restored is not None:
            assert restored < again, "the worker was announced before the document was restored: %r" % _methods(events, back)
        time.sleep(1.0)  # what comes in excess comes now
        after = events[back:]
        assert not [p for m, p in after if m == "Page.workerDestroyed" and p["workerId"] == wid], (
            "the restored worker was told ended: %r" % _methods(events, back))
        assert len([p for m, p in after if m == "Page.workerCreated" and p["workerId"] == wid]) == 1, (
            "the restored worker was announced more than once: %r" % _methods(events, back))

        # 4. one live worker, the page's, with its count
        live = {}
        for m, p in events:
            if m == "Page.workerCreated":
                live[p["workerId"]] = p
            elif m == "Page.workerDestroyed":
                live.pop(p["workerId"], None)
        assert list(live) == [wid], "live workers after the restore: %r" % list(live.values())
        assert _ask(page, where) == 3, "the worker that came back is not the page's worker with its state"


def _methods(events, start):
    return [m + (" " + p.get("workerId", "") if m.startswith("Page.worker") else "")
            for m, p in events[start:] if m.startswith(("Page.worker", "Page.navigationCommitted", "Page.frameDocument",
                                                        "Runtime.executionContextsCleared", "Page.frameDetached",
                                                        "Page.frameAttached"))]
