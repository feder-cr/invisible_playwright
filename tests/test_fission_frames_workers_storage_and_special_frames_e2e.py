"""Workers made in frames of other sites, what a frame of another site keeps, and the frames that have no address.

Three sites (see `_fission_frames.py`): the top is A (`127.0.0.1`), the frame B (`localhost`), the frame C
(`127.0.0.2`) nested in B. With Fission each site is a content process of its own.

* Identity inside workers. A dedicated Worker made in the top, in the frame of B and in the frame of C (blob and
  script-url workers) reports the same `navigator.userAgent`, `language`, `languages`, `hardwareConcurrency`,
  `platform` and time zone as the window of the top and as the window that made it: a worker lives in the process
  of the frame that made it, and its identity has to be declared there like the window's. The comparison reads an
  absence as a mismatch, never as an agreement. A SharedWorker and a ServiceWorker are made from the frame of B:
  the outcome asserted is what stock Firefox does there (see the docstrings).
* Cookies and storage. A cookie set by site B inside a frame of A appears in `context.cookies()` with B's domain;
  what `document.cookie`, `localStorage` and `sessionStorage` show to B-in-A is what stock Firefox shows, and B
  opened as a top page does not see them: storage is partitioned by top-level site, as in retail Firefox (stock
  Playwright Firefox has that protection off; see the docstrings).
* Frames with no address of their own, at the top level and inside the frame of B: `srcdoc`, `about:blank`, a
  `data:` url and a `sandbox`ed frame with and without `allow-scripts allow-same-origin`: they are in
  `page.frames`, `evaluate` works where scripts run, and `frame_locator` reaches into them (see the docstrings
  for what stock Firefox does with a sandbox that forbids scripts).

The first test proves the frames are in other processes, so the file cannot turn into a same-process suite.
"""
from __future__ import annotations

import json

import pytest

from _fission_frames import Sites, assert_other_process, launch, new_page, wait_for, wait_frame

pytestmark = pytest.mark.e2e

#: What a worker reads about the browser it runs in, as an object literal for its source.
IDENTITY = ("{userAgent: navigator.userAgent, language: navigator.language, languages: navigator.languages.join('|'),"
            " hardwareConcurrency: navigator.hardwareConcurrency, platform: navigator.platform,"
            " timeZone: Intl.DateTimeFormat().resolvedOptions().timeZone}")
FIELDS = ("userAgent", "language", "languages", "hardwareConcurrency", "platform", "timeZone")

#: Run in a frame: make a dedicated worker (from a blob, or from the script of the frame's site) and return what it
#: reports, or the error.
WORKER_JS = """(kind) => new Promise(resolve => {
  const source = 'postMessage(' + %s + ')';
  let worker;
  try {
    worker = kind === 'blob' ? new Worker(URL.createObjectURL(new Blob([source], {type: 'text/javascript'})))
                             : new Worker('/identity-worker.js');
  } catch (e) { resolve({error: String(e)}); return; }
  worker.onmessage = e => resolve(e.data);
  worker.onerror = e => resolve({error: 'worker error: ' + e.message});
  setTimeout(() => resolve({error: 'timeout'}), 10000);
})""" % json.dumps(IDENTITY)

WINDOW_JS = "() => (%s)" % IDENTITY


@pytest.fixture
def sites():
    with Sites() as s:
        yield s


@pytest.fixture(scope="module")
def browser(firefox_binary):
    with launch(firefox_binary) as browser:
        yield browser


@pytest.fixture
def page(browser):
    context, page = new_page(browser)
    yield page
    context.close()


def _absent(value):
    return value is None or value == "" or (isinstance(value, str) and value.startswith("error"))


def mismatches(reference, other, fields=FIELDS):
    """Every field in which ``other`` differs from ``reference``; an absence on either side is a mismatch."""
    problems = []
    for field in fields:
        if _absent(reference.get(field)):
            problems.append("reference has no %s: %r" % (field, reference.get(field)))
        elif _absent(other.get(field)):
            problems.append("%s is absent (%r), the reference reads %r" % (field, other.get(field), reference[field]))
        elif other[field] != reference[field]:
            problems.append("%s = %r, the reference reads %r" % (field, other[field], reference[field]))
    return problems


# ---------------------------------------------------------------------------
# identity inside workers
# ---------------------------------------------------------------------------


@pytest.fixture
def worker_pages(sites):
    sites.route("A", "/workers", "<!doctype html><title>top</title><iframe id='b' src='%s' width='600' height='300'>"
                "</iframe>" % sites.url("B", "/workers-b"))
    sites.route("B", "/workers-b", "<!doctype html><title>b</title><iframe id='c' src='%s' width='400' height='150'>"
                "</iframe>" % sites.url("C", "/workers-c"))
    sites.route("C", "/workers-c", "<!doctype html><title>c</title>")
    for site in ("A", "B", "C"):
        sites.route(site, "/identity-worker.js", "postMessage(%s)" % IDENTITY, ctype="text/javascript")
    return sites


def test_the_frames_are_in_other_processes(page, worker_pages):
    page.goto(worker_pages.url("A", "/workers"), wait_until="load")
    b = wait_frame(page, worker_pages.url("B"), "/workers-b")
    assert_other_process(page.main_frame, b)
    assert_other_process(b, wait_frame(page, worker_pages.url("C"), "/workers-c"))


@pytest.mark.parametrize("kind", ["blob", "url"])
def test_a_dedicated_worker_reports_the_identity_of_the_window_in_every_frame(page, worker_pages, kind):
    page.goto(worker_pages.url("A", "/workers"), wait_until="load")
    frames = {"top": page.main_frame,
              "frame": wait_frame(page, worker_pages.url("B"), "/workers-b"),
              "nested": wait_frame(page, worker_pages.url("C"), "/workers-c")}
    reference = page.main_frame.evaluate(WINDOW_JS)
    assert not mismatches(reference, reference), reference
    problems = []
    for name, frame in frames.items():
        window = frame.evaluate(WINDOW_JS)
        worker = frame.evaluate(WORKER_JS, kind)
        problems += ["%s window: %s" % (name, p) for p in mismatches(reference, window)]
        problems += ["%s %s worker: %s" % (name, kind, p) for p in mismatches(reference, worker)]
        problems += ["%s worker against its own window: %s" % (name, p) for p in mismatches(window, worker)]
    assert not problems, "identity differs between the top window and the frames and workers:\n  " + "\n  ".join(problems)


SHARED_WORKER_JS = """() => new Promise(resolve => {
  try {
    const worker = new SharedWorker('/shared-worker.js');
    worker.port.onmessage = e => resolve({outcome: 'ok', identity: e.data});
    worker.onerror = e => resolve({outcome: 'error', message: String(e.message || e)});
  } catch (e) { resolve({outcome: 'throw', message: e.name + ': ' + e.message}); }
  setTimeout(() => resolve({outcome: 'timeout'}), 10000);
})"""

SERVICE_WORKER_JS = """() => new Promise(resolve => {
  try {
    navigator.serviceWorker.register('/service-worker.js').then(
      registration => navigator.serviceWorker.ready.then(ready => {
        navigator.serviceWorker.onmessage = e => resolve({outcome: 'registered', scope: registration.scope,
                                                          identity: e.data});
        (ready.active || registration.active).postMessage('who');
      }),
      e => resolve({outcome: 'rejected', message: e.name + ': ' + e.message}));
  } catch (e) { resolve({outcome: 'throw', message: e.name + ': ' + e.message}); }
  setTimeout(() => resolve({outcome: 'timeout'}), 15000);
})"""


def test_a_shared_worker_made_in_a_frame_of_another_site_reports_the_identity_of_the_top(page, worker_pages):
    """As stock Firefox does: a SharedWorker can be made from a frame of another site (its own site's worker
    script), starts, and answers. What it reports about the browser is what the top window reads."""
    worker_pages.route("B", "/shared-worker.js", "onconnect = e => { e.ports[0].postMessage(%s); };" % IDENTITY,
                       ctype="text/javascript")
    page.goto(worker_pages.url("A", "/workers"), wait_until="load")
    frame = wait_frame(page, worker_pages.url("B"), "/workers-b")
    result = frame.evaluate(SHARED_WORKER_JS)
    assert result["outcome"] == "ok", result
    assert not mismatches(page.main_frame.evaluate(WINDOW_JS), result["identity"]), result


def test_a_service_worker_registered_from_a_frame_of_another_site_reports_the_identity_of_the_top(page, worker_pages):
    """As stock Firefox does: a frame of another site registers a ServiceWorker of its own site (scope the site's
    root), it becomes active, and answers a message; what it reports about the browser is what the top window
    reads."""
    worker_pages.route("B", "/service-worker.js",
                       "self.addEventListener('install', e => self.skipWaiting());"
                       "self.addEventListener('activate', e => e.waitUntil(clients.claim()));"
                       "self.addEventListener('message', e => e.source.postMessage(%s));" % IDENTITY,
                       ctype="text/javascript", headers=[("Service-Worker-Allowed", "/")])
    page.goto(worker_pages.url("A", "/workers"), wait_until="load")
    frame = wait_frame(page, worker_pages.url("B"), "/workers-b")
    result = frame.evaluate(SERVICE_WORKER_JS)
    assert result["outcome"] == "registered", result
    assert result["scope"] == worker_pages.url("B", "/"), result
    assert not mismatches(page.main_frame.evaluate(WINDOW_JS), result["identity"]), result


# ---------------------------------------------------------------------------
# cookies and storage
# ---------------------------------------------------------------------------


@pytest.fixture
def storage_pages(sites):
    read = "window.__r = {cookie: document.cookie, ls: localStorage.getItem('k'), ss: sessionStorage.getItem('k')};"
    sites.route("A", "/storage", "<!doctype html><title>top</title><iframe id='b' src='%s'></iframe>"
                % sites.url("B", "/storage-b"))
    sites.route("B", "/storage-b", "<!doctype html><title>b</title><script>"
                "document.cookie = 'jscookie=2; path=/'; localStorage.setItem('k', 'frame-ls');"
                "sessionStorage.setItem('k', 'frame-ss'); %s</script>" % read,
                headers=[("Set-Cookie", "hdrcookie=1; Path=/")])
    sites.route("B", "/storage-own", "<!doctype html><title>own</title><script>%s</script>" % read)
    # B as a top page writes a cookie and an item of its own; B in a frame of A reads them (they must not be there)
    sites.route("B", "/storage-top-writes", "<!doctype html><title>top writes</title><script>"
                "document.cookie = 'topcookie=3; path=/'; localStorage.setItem('top-only', 'top-ls');"
                "window.__r = {cookie: document.cookie, top: localStorage.getItem('top-only')};</script>")
    sites.route("B", "/storage-read-b", "<!doctype html><title>reads</title><script>"
                "window.__r = {cookie: document.cookie, top: localStorage.getItem('top-only')};</script>")
    sites.route("A", "/storage-read", "<!doctype html><title>top</title><iframe id='b' src='%s'></iframe>"
                % sites.url("B", "/storage-read-b"))
    return sites


def test_what_a_frame_of_another_site_stores_is_seen_by_the_frame_and_by_the_context(page, storage_pages):
    """As stock Firefox does: a cookie set by site B inside a frame of A (by a header and by script) is in
    `context.cookies()` with B's domain, `document.cookie` of the frame shows both, and so do `localStorage` and
    `sessionStorage` of the frame what the frame wrote; a second page of the context with the same frame sees
    the same cookies and local storage, and the session storage of its own tab (the frame wrote it there too)."""
    context = page.context
    page.goto(storage_pages.url("A", "/storage"), wait_until="load")
    frame = wait_frame(page, storage_pages.url("B"), "/storage-b")
    assert frame.evaluate("window.__r") == {"cookie": "hdrcookie=1; jscookie=2", "ls": "frame-ls", "ss": "frame-ss"}
    assert sorted((c["name"], c["domain"]) for c in context.cookies()) == [("hdrcookie", "localhost"),
                                                                          ("jscookie", "localhost")]
    assert sorted(c["name"] for c in context.cookies(storage_pages.url("B"))) == ["hdrcookie", "jscookie"]
    assert context.cookies(storage_pages.url("A")) == []
    second = context.new_page()
    second.goto(storage_pages.url("A", "/storage"), wait_until="load")
    again = wait_frame(second, storage_pages.url("B"), "/storage-b")
    assert again.evaluate("window.__r") == {"cookie": "hdrcookie=1; jscookie=2", "ls": "frame-ls", "ss": "frame-ss"}


def test_storage_of_site_b_is_partitioned_between_a_frame_of_a_and_b_as_a_top_page(page, storage_pages):
    """The retail outcome, not stock Playwright's: cookies and web storage are partitioned by the top-level site.
    What site B wrote inside a frame of A is not visible to B opened as a top page: its `document.cookie` is empty
    and its `localStorage` item is null (`context.cookies()` still lists B's cookies, with B's domain, because it
    reads the whole jar). The reverse holds too: a cookie and a local-storage item that B wrote as a top page are
    not visible to B inside the frame of A. The session storage of the top page is empty in both cases.

    Why the reference here is the retail Firefox and not stock Playwright Firefox: the reference of this project
    is what a page reads in a Firefox a user installed, and retail Firefox partitions third-party cookies and
    storage by top-level site (Total Cookie Protection). Playwright's own Firefox ships with that protection off
    and shows the stored values to B as a top page (observed with Playwright 1.62: `document.cookie` is
    `hdrcookie=1; jscookie=2` and the item is `frame-ls`), which is not what a page sees in a real browser, so
    its answer is not the one asserted."""
    context = page.context
    page.goto(storage_pages.url("A", "/storage"), wait_until="load")
    wait_frame(page, storage_pages.url("B"), "/storage-b")
    own = context.new_page()
    own.goto(storage_pages.url("B", "/storage-own"), wait_until="load")
    assert own.evaluate("window.__r") == {"cookie": "", "ls": None, "ss": None}
    assert sorted(c["name"] for c in context.cookies(storage_pages.url("B"))) == ["hdrcookie", "jscookie"]
    # the reverse: B writes as a top page, then is framed by A and reads
    own.goto(storage_pages.url("B", "/storage-top-writes"), wait_until="load")
    assert own.evaluate("window.__r") == {"cookie": "topcookie=3", "top": "top-ls"}
    reader = context.new_page()
    reader.goto(storage_pages.url("A", "/storage-read"), wait_until="load")
    frame = wait_frame(reader, storage_pages.url("B"), "/storage-read-b")
    # B inside A reads its own partition: the two cookies the frame wrote above are there, what B wrote as a top page
    # (`topcookie`, the `top-only` item) is not.
    assert frame.evaluate("window.__r") == {"cookie": "hdrcookie=1; jscookie=2", "top": None}


# ---------------------------------------------------------------------------
# frames with no address of their own
# ---------------------------------------------------------------------------

INNER = "&lt;p id=x&gt;%s&lt;/p&gt;&lt;script&gt;window.v=1&lt;/script&gt;"
SPECIAL = ('<iframe id="srcdoc" srcdoc="%s"></iframe>' % (INNER % "in srcdoc")
           + '<iframe id="blank"></iframe>'
           + '<iframe id="data" src="data:text/html,%s"></iframe>' % (INNER % "in data")
           + '<iframe id="sb0" sandbox srcdoc="%s"></iframe>' % (INNER % "sb0")
           + '<iframe id="sb1" sandbox="allow-scripts allow-same-origin" srcdoc="%s"></iframe>' % (INNER % "sb1")
           + '<iframe id="sb2" sandbox="allow-scripts" srcdoc="%s"></iframe>' % (INNER % "sb2"))

#: id -> (url of the frame, `window.v` in it, text of #x). `window.v` is 1 where the document's script ran;
#: a sandbox without `allow-scripts` runs none, but `evaluate` still works there and answers undefined.
SPECIAL_FRAMES = {
    "srcdoc": ("about:srcdoc", 1, "in srcdoc"),
    "blank": ("about:blank", None, None),
    "data": (None, 1, "in data"),
    "sb0": ("about:srcdoc", None, "sb0"),
    "sb1": ("about:srcdoc", 1, "sb1"),
    "sb2": ("about:srcdoc", 1, "sb2"),
}


@pytest.fixture
def special_pages(sites):
    sites.route("A", "/special", "<!doctype html><title>special</title>" + SPECIAL)
    sites.route("B", "/special", "<!doctype html><title>special b</title>" + SPECIAL)
    sites.route("A", "/special-in-b", "<!doctype html><title>top</title><iframe id='b' src='%s'></iframe>"
                % sites.url("B", "/special"))
    return sites


@pytest.mark.parametrize("where", ["top", "inside_b"])
def test_special_frames_are_listed_evaluated_and_reached_by_frame_locator(page, special_pages, where):
    """As stock Firefox does: `srcdoc` and every `sandbox`ed srcdoc frame is `about:srcdoc`, the empty one is
    `about:blank`, the `data:` one keeps its url; all are in `page.frames` under their parent, named by their id;
    `evaluate` works in all of them and finds `window.v` where the document's script could run (not in the
    sandbox without `allow-scripts`, not in the empty frame); `frame_locator` reaches the text inside each, in
    the empty frame once something is written there; the same at the top level and inside a frame of B."""
    path = "/special" if where == "top" else "/special-in-b"
    page.goto(special_pages.url("A", path), wait_until="load")
    owner_site = "A" if where == "top" else "B"
    owner = wait_frame(page, special_pages.url(owner_site), "/special")
    if where == "inside_b":
        assert_other_process(page.main_frame, owner)
    expected_count = 1 + (where == "inside_b") + len(SPECIAL_FRAMES)
    assert wait_for(page, lambda: len(page.frames) == expected_count), [(f.name, f.url) for f in page.frames]
    inner = {f.name: f for f in page.frames if f.parent_frame is owner}
    assert sorted(inner) == sorted(SPECIAL_FRAMES), [(f.name, f.url) for f in page.frames]
    scope = page.frame_locator("#b") if where == "inside_b" else page
    for frame_id, (url, value, text) in SPECIAL_FRAMES.items():
        frame = inner[frame_id]
        if url is None:
            assert frame.url.startswith("data:text/html,"), frame.url
        else:
            assert frame.url == url, (frame_id, frame.url)
        assert frame.evaluate("window.v") == value, frame_id
        if frame_id == "blank":
            frame.evaluate("document.body.innerHTML = '<p id=x>written</p>'")
            text = "written"
        assert scope.frame_locator("#" + frame_id).locator("#x").inner_text() == text, frame_id
