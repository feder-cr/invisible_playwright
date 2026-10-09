"""The frame lifecycle against a real browser: the four states on a real page.

The lifecycle itself is the core's (`invisible_core.juggler.lifecycle`) and
its unit tests are there. This one needs the engine, and the engine is where
this package's e2e job is: the core's CI has no browser, so a browser test
moved there with the module would never run.
"""
from __future__ import annotations

import tempfile
import threading
import time

import pytest

from invisible_core.juggler.lifecycle import Lifecycle


@pytest.mark.e2e
def test_the_four_states_are_reached_on_a_real_page(firefox_binary):
    import http.server
    import socketserver

    from invisible_core.launch import build_launch_plan
    from invisible_core.juggler import connection as conn

    PAGE = (b"<!doctype html><html><head><title>t</title></head><body>"
            b"<h1>hi</h1><iframe src='/inside'></iframe></body></html>")

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            body = (b"<html><body>child</body></html>"
                    if self.path == "/inside" else PAGE)
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    profile_dir = tempfile.mkdtemp(prefix="lifecycle_e2e_")
    plan = build_launch_plan(11, profile_dir=profile_dir, binary_path=firefox_binary, timezone="UTC",
                              locale="en-US")

    with socketserver.TCPServer(("127.0.0.1", 0), H) as srv:
        port = srv.server_address[1]
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        c = conn.launch(firefox_binary, profile_dir, headless=True,
                         env=plan.env)
        try:
            sessions: dict = {}
            c.add_listener(lambda m, p, s: (
                sessions.__setitem__(
                    p["targetInfo"]["targetId"], p["sessionId"])
                if m == "Browser.attachedToTarget" else None))
            c.send("Browser.enable", {"attachToDefaultContext": True})
            ctx = c.send("Browser.createBrowserContext",
                         {"removeOnDetach": True})
            page = c.send("Browser.newPage",
                          {"browserContextId": ctx["browserContextId"]})
            deadline = time.time() + 15
            while page["targetId"] not in sessions and time.time() < deadline:
                time.sleep(0.02)
            v = Lifecycle(c, sessions[page["targetId"]])
            time.sleep(0.5)

            result = v.goto("http://127.0.0.1:%d/" % port,
                            until="load", timeout=30)
            assert result["navigationId"], result
            # ⛔ The defect the unit test reproduces, verified here too:
            # after `commit` the URL must NOT be about:blank.
            assert result["url"].startswith("http://127.0.0.1:"), result["url"]

            v.wait_for_state(v.main_frame, "networkidle", timeout=30)
            assert v.inflight == 0

            tree = v.frame_tree()
            children = [d for d in tree.values() if d["parent"]]
            assert len(children) == 1, tree
            assert "load" in tree[v.main_frame]["states"]
        finally:
            c.close()
        srv.shutdown()
