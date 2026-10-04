"""`new_context(locale=...)` is the context's language, not the launch one.

Measured on 0.25.11 with firefox-35: a session launched with ``locale="it-IT"``
opened ``browser.new_context(locale="de-DE")`` and every page of it reported
it-IT - ``navigator.language``, ``navigator.languages``, ``Intl`` and the
Accept-Language header alike. The option travelled to the engine as
``Browser.setLocaleOverride`` and landed on ``docShell.languageOverride``, which
nothing in Firefox reads, and on the realm that was current at that moment,
usually the initial ``about:blank``.

Two halves, one per side of the protocol:

* the ENGINE puts the context's language into the BrowsingContext's
  LanguageOverride field, the one Firefox reads for ``navigator.languages``,
  for the realm's default ``Intl`` locale and for the Accept-Language header.
  The launch locale already reaches pages through that field (seeded from
  ``juggler.locale.override``), so both arrive the same way;
* the SERVER sends the language LIST the core derives for the locale, not the
  bare tag. A German Firefox reports ``de-DE, de, en-US, en``; a context that
  sent ``"de-DE"`` would report one entry, and the default context - which
  receives the launch locale through the same defaults - would drop from the
  profile's four entries to one.

The header is compared by its tags only: firefox-35 sends the override list
raw, firefox-36 prepares it with q-values the way it prepares
``intl.accept_languages`` (``prfix/firefox-36-extra``).

Workers speak the context's language too. On firefox-35 a dedicated Worker
in the de-DE context still reported the launch locale, because its realm and
its ``navigator.languages`` were built from process-wide sources; from
firefox-36 a worker carries the language override of the document that made
it (a service worker, which has none, takes the one its context's pages
share). Retail 151 under its own ``emulation.setLocaleOverride`` leaves every
worker on the process language, so window and worker disagreeing there is a
gap of that override, not behaviour to copy.
"""
from __future__ import annotations

import http.server
import threading

import pytest

PAGE = b"<!doctype html><html><head><title>t</title></head><body>%s</body></html>"
OPENER = (b"<button id=pop onclick=\"window.open('/echo', '_blank')\" "
          b"style='width:200px;height:40px'>pop</button>")


# One script for the three kinds of worker: it answers with what its own
# realm reports, to a dedicated worker's `message`, a shared worker's port and
# a service worker's client.
WORKER = b"""
const read = () => ({languages: [...navigator.languages],
                     intl: Intl.DateTimeFormat().resolvedOptions().locale});
onmessage = (e) => (e.source || self).postMessage(read());
onconnect = (e) => e.ports[0].postMessage(read());
"""

WORKERS_JS = """async () => {
  const dedicated = await new Promise((ok) => {
    const w = new Worker('/w.js'); w.onmessage = (e) => ok(e.data); w.postMessage(0);
  });
  const shared = await new Promise((ok) => {
    const s = new SharedWorker('/w.js'); s.port.onmessage = (e) => ok(e.data);
  });
  const reg = await navigator.serviceWorker.register('/w.js');
  const sw = reg.active || reg.waiting || reg.installing;
  const service = await new Promise((ok) => {
    navigator.serviceWorker.onmessage = (e) => ok(e.data);
    const send = () => sw.postMessage(0);
    if (sw.state === 'activated') send();
    else sw.addEventListener('statechange', () => sw.state === 'activated' && send());
  });
  return {dedicated, shared, service};
}"""


def _serve():
    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path.startswith("/opener"):
                body = PAGE % OPENER
            elif self.path.startswith("/w.js"):
                self._send(WORKER, "text/javascript")
                return
            else:
                lang = self.headers.get("Accept-Language", "")
                body = PAGE % (b"<pre id=al>" + lang.encode() + b"</pre>")
            self._send(body, "text/html; charset=utf-8")

        def _send(self, body, kind):
            self.send_response(200)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, "http://127.0.0.1:%d" % srv.server_address[1]


def _tags(header):
    return [t.split(";")[0].strip() for t in header.split(",") if t.strip()]


def _read(page):
    return {
        "header": _tags(page.inner_text("#al")),
        "language": page.evaluate("navigator.language"),
        "languages": page.evaluate("[...navigator.languages]"),
        "intl": page.evaluate("Intl.DateTimeFormat().resolvedOptions().locale"),
    }


def _expected(locale):
    from invisible_core import decide_session_locale

    langs = list(decide_session_locale(locale).languages)
    return {"header": langs, "language": langs[0], "languages": langs,
            "intl": locale}


@pytest.mark.e2e
def test_a_context_locale_reaches_navigator_intl_and_the_header(firefox_binary):
    from invisible_playwright import InvisiblePlaywright

    srv, base = _serve()
    try:
        with InvisiblePlaywright(seed=5150, binary_path=firefox_binary,
                                 headless=True, locale="it-IT",
                                 timezone="Europe/Rome") as browser:
            ctx = browser.new_context()
            page = ctx.new_page()
            page.goto(base + "/echo", wait_until="load", timeout=20_000)
            assert _read(page) == _expected("it-IT"), (
                "the default context must keep the launch locale's full list")
            ctx.close()

            ctx = browser.new_context(locale="de-DE")
            page = ctx.new_page()
            page.goto(base + "/echo", wait_until="load", timeout=20_000)
            assert _read(page) == _expected("de-DE"), (
                "new_context(locale='de-DE') inside an it-IT session")
            want = {"languages": _expected("de-DE")["languages"], "intl": "de-DE"}
            workers = page.evaluate(WORKERS_JS)
            for kind, seen in workers.items():
                assert seen == want, "a %s worker of a de-DE context: %r" % (kind, seen)

            # A page the site opens belongs to the same context and speaks
            # its language from its first request.
            page.goto(base + "/opener", wait_until="load", timeout=20_000)
            with ctx.expect_page(timeout=15_000) as info:
                page.click("#pop")
            popup = info.value
            popup.wait_for_load_state("load", timeout=10_000)
            assert _read(popup) == _expected("de-DE"), "the popup of a de-DE context"
            ctx.close()
    finally:
        srv.shutdown()
