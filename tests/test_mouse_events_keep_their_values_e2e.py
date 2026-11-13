"""Mouse events reach the page and an iframe of the same process with the values they have always had.

The mouse of the engine is sent through the asynchronous (APZ) route from firefox-62 (a click on an iframe of
another site needs the hit test; see automation/input), which is also the route of the events of the page and of the
iframes in its own process. They must not change: their ORDER, whether they are trusted, the pressure, the pointer id
and type, the buttons and button, and the click count (`detail`). The sequences below are what the engine delivers (a move with a button held carries buttons=1 and pressure 0.5, as Pointer Events and stock Playwright do),
recorded with the same script (coordinates are left out: they depend on the screen's scale). The test passes on
firefox-61 and has to pass on the engine with the APZ route.

A drag is the same question for the drag session (the flag "async synthesized for tests" would send it to the native
drag loop) and is guarded by tests/test_drag_motion.py, which this test does not replace.
"""
from __future__ import annotations

import http.server
import threading
import time

import pytest

pytestmark = pytest.mark.e2e

FRAME = (b"<!doctype html><title>f</title><body style='margin:0'><button id=b "
         b"style='position:absolute;left:10px;top:10px;width:120px;height:50px'>b</button>")
TOP = ("<!doctype html><title>t</title><body style='margin:0'><button id=t "
       "style='position:absolute;left:10px;top:10px;width:120px;height:50px'>t</button>"
       "<iframe id=f src='/frame' style='position:absolute;left:200px;top:100px;width:300px;height:200px;border:0'></iframe>"
       "<script>window.__log=[];const rec=(d)=>e=>__log.push([d,e.type,e.isTrusted,"
       "e.pressure===undefined?null:e.pressure,e.pointerId===undefined?null:e.pointerId,e.pointerType||null,"
       "e.buttons,e.button,e.detail,e.timeStamp]);"
       "const types=['pointermove','mousemove','pointerdown','mousedown','pointerup','mouseup','click','dblclick',"
       "'contextmenu','auxclick'];"
       "for(const t of types)document.addEventListener(t,rec('top'),true);"
       "document.getElementById('f').addEventListener('load',()=>{const w=document.getElementById('f').contentWindow;"
       "w.__log=window.__log;for(const t of types)w.document.addEventListener(t,rec('frame'),true);});</script>").encode()

EXPECTED = {
    'top click': [
        ('top', 'pointermove', True, 0, 0, 'mouse', 0, -1, 0),
        ('top', 'mousemove', True, None, None, None, 0, 0, 0),
        ('top', 'pointerdown', True, 0.5, 0, 'mouse', 1, 0, 0),
        ('top', 'mousedown', True, None, None, None, 1, 0, 1),
        ('top', 'pointerup', True, 0, 0, 'mouse', 0, 0, 0),
        ('top', 'mouseup', True, None, None, None, 0, 0, 1),
        ('top', 'click', True, 0, 0, 'mouse', 0, 0, 1),
    ],
    'frame click': [
        ('frame', 'pointermove', True, 0, 0, 'mouse', 0, -1, 0),
        ('frame', 'mousemove', True, None, None, None, 0, 0, 0),
        ('frame', 'pointerdown', True, 0.5, 0, 'mouse', 1, 0, 0),
        ('frame', 'mousedown', True, None, None, None, 1, 0, 1),
        ('frame', 'pointerup', True, 0, 0, 'mouse', 0, 0, 0),
        ('frame', 'mouseup', True, None, None, None, 0, 0, 1),
        ('frame', 'click', True, 0, 0, 'mouse', 0, 0, 1),
    ],
    'top double click': [
        ('top', 'pointermove', True, 0, 0, 'mouse', 0, -1, 0),
        ('top', 'mousemove', True, None, None, None, 0, 0, 0),
        ('top', 'pointermove', True, 0, 0, 'mouse', 0, -1, 0),
        ('top', 'mousemove', True, None, None, None, 0, 0, 0),
        ('top', 'pointerdown', True, 0.5, 0, 'mouse', 1, 0, 0),
        ('top', 'mousedown', True, None, None, None, 1, 0, 1),
        ('top', 'pointerup', True, 0, 0, 'mouse', 0, 0, 0),
        ('top', 'mouseup', True, None, None, None, 0, 0, 1),
        ('top', 'click', True, 0, 0, 'mouse', 0, 0, 1),
        ('top', 'pointerdown', True, 0.5, 0, 'mouse', 1, 0, 0),
        ('top', 'mousedown', True, None, None, None, 1, 0, 2),
        ('top', 'pointerup', True, 0, 0, 'mouse', 0, 0, 0),
        ('top', 'mouseup', True, None, None, None, 0, 0, 2),
        ('top', 'click', True, 0, 0, 'mouse', 0, 0, 2),
        ('top', 'dblclick', True, None, None, None, 0, 0, 2),
    ],
    'frame right click': [
        ('frame', 'pointermove', True, 0, 0, 'mouse', 0, -1, 0),
        ('frame', 'mousemove', True, None, None, None, 0, 0, 0),
        ('frame', 'pointermove', True, 0, 0, 'mouse', 0, -1, 0),
        ('frame', 'mousemove', True, None, None, None, 0, 0, 0),
        ('frame', 'pointerdown', True, 0.5, 0, 'mouse', 2, 2, 0),
        ('frame', 'mousedown', True, None, None, None, 2, 2, 1),
        ('frame', 'contextmenu', True, 0.5, 0, 'mouse', 2, 2, 1),
        ('frame', 'pointerup', True, 0, 0, 'mouse', 0, 2, 0),
        ('frame', 'mouseup', True, None, None, None, 0, 2, 1),
        ('frame', 'auxclick', True, 0, 0, 'mouse', 0, 2, 1),
    ],
    'move with button down': [
        ('frame', 'pointermove', True, 0, 0, 'mouse', 0, -1, 0),
        ('frame', 'mousemove', True, None, None, None, 0, 0, 0),
        ('frame', 'pointerdown', True, 0.5, 0, 'mouse', 1, 0, 0),
        ('frame', 'mousedown', True, None, None, None, 1, 0, 1),
        ('frame', 'pointermove', True, 0.5, 0, 'mouse', 1, -1, 0),
        ('frame', 'mousemove', True, None, None, None, 1, 0, 0),
        ('frame', 'pointermove', True, 0.5, 0, 'mouse', 1, -1, 0),
        ('frame', 'mousemove', True, None, None, None, 1, 0, 0),
        ('frame', 'pointerup', True, 0, 0, 'mouse', 0, 0, 0),
        ('frame', 'mouseup', True, None, None, None, 0, 0, 1),
        ('frame', 'click', True, 0, 0, 'mouse', 0, 0, 1),
    ],
}


class _Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        body = FRAME if self.path.startswith("/frame") else TOP
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


def _record(page, action):
    page.evaluate("window.__log.length = 0")
    action()
    time.sleep(0.4)
    return page.evaluate("window.__log.slice()")


def test_the_events_of_the_page_and_of_an_iframe_of_its_process_keep_their_order_and_values(firefox_binary):
    from invisible_playwright import InvisiblePlaywright

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        with InvisiblePlaywright(binary_path=firefox_binary, headless=True, humanize=False) as browser:
            page = browser.new_context().new_page()
            page.goto("http://127.0.0.1:%d/" % server.server_address[1], wait_until="load")
            page.wait_for_function("window.__log && document.getElementById('f').contentDocument.readyState === 'complete'")
            time.sleep(0.5)
            actions = {
                "top click": lambda: (page.mouse.move(60, 35), page.mouse.down(), page.mouse.up()),
                "frame click": lambda: (page.mouse.move(260, 150), page.mouse.down(), page.mouse.up()),
                "top double click": lambda: (page.mouse.move(60, 35), page.mouse.click(60, 35, click_count=2)),
                "frame right click": lambda: (page.mouse.move(260, 150), page.mouse.click(260, 150, button="right")),
                "move with button down": lambda: (page.mouse.move(300, 200), page.mouse.down(),
                                                  page.mouse.move(310, 205), page.mouse.move(330, 215), page.mouse.up()),
            }
            got = {}
            for name, action in actions.items():
                log = _record(page, action)
                stamps = [row[-1] for row in log]
                assert stamps == sorted(stamps), "%s: the time stamps go backwards" % name
                assert all(stamp <= page.evaluate("performance.now()") for stamp in stamps), "%s: a time stamp in the future" % name
                got[name] = [tuple(row[:-1]) for row in log]
    finally:
        server.shutdown()

    for name, expected in EXPECTED.items():
        assert got[name] == expected, "%s: expected %s, got %s" % (name, expected, got[name])
