"""A task that raced a long-lived future is not kept alive by it.

⛔ FOUND 2026-10-16, after the guid registry was bounded and memory still grew.
On Python 3.14 `asyncio.wait` adds the calling task to every future's
awaited-by set and removes it only from the futures that complete (CPython
gh-152569). Every protocol message races the transport's `on_error_future`,
which never completes while the connection is healthy, so every task that ever
sent a message - with its frame, its locals and whatever result it was holding -
stayed reachable until the browser closed: 178,299 finished tasks after ten
minutes of a page fetching in a loop.
"""
from __future__ import annotations

import asyncio
import gc
import http.server
import threading

import pytest

from invisible_playwright._pw._impl._helper import wait_released


def _finished_tasks(name):
    gc.collect()
    return sum(1 for o in gc.get_objects()
               if isinstance(o, asyncio.Task) and o.done()
               and o.get_coro().__qualname__.endswith(name))


@pytest.mark.unit
def test_racing_a_future_that_never_completes_keeps_no_task():
    async def main():
        forever = asyncio.get_running_loop().create_future()

        async def racer():
            quick = asyncio.get_running_loop().create_future()
            quick.set_result(None)
            await wait_released([forever, quick], return_when=asyncio.FIRST_COMPLETED)

        for _ in range(200):
            await asyncio.create_task(racer())
        held = _finished_tasks("racer")
        forever.cancel()
        return held

    # The last one is still referenced from main's own frame.
    assert asyncio.run(main()) <= 1


@pytest.mark.unit
def test_the_results_are_those_of_asyncio_wait():
    async def main():
        loop = asyncio.get_running_loop()
        done_one, pending_one = loop.create_future(), loop.create_future()
        done_one.set_result(1)
        done, pending = await wait_released({done_one, pending_one},
                                            return_when=asyncio.FIRST_COMPLETED)
        pending_one.cancel()
        return done == {done_one} and pending == {pending_one}

    assert asyncio.run(main())


class _Echo(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _answer(self):
        self.rfile.read(int(self.headers.get("content-length") or 0))
        document = self.path == "/"
        body = b"<!doctype html><title>wait</title>" if document else b'{"ok": true}'
        self.send_response(200)
        self.send_header("content-type", "text/html" if document else "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    do_GET = do_POST = _answer


@pytest.fixture
def echo_server():
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Echo)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield "http://127.0.0.1:%d" % server.server_address[1]
    server.shutdown()


@pytest.mark.e2e
def test_finished_protocol_tasks_are_released(firefox_binary, echo_server):
    """The route handler and the body read each send messages, so each raced
    `on_error_future`. Before the fix both were kept, one per request."""
    from invisible_playwright._pw.async_api import async_playwright

    async def main():
        async with async_playwright() as p:
            browser = await p.firefox.launch(headless=True, executable_path=firefox_binary)
            context = await browser.new_context()
            await context.route("**/*", lambda route: route.fallback())
            page = await context.new_page()
            reads = set()

            async def read(request):
                response = await request.response()
                if response:
                    await response.body()

            def finished(request):
                task = asyncio.create_task(read(request))
                reads.add(task)
                task.add_done_callback(reads.discard)

            page.on("requestfinished", finished)
            await page.goto(echo_server + "/")
            await page.evaluate("""async () => {
                for (let i = 0; i < 200; i++)
                    await fetch('/api?n=' + i, {method: 'POST', body: 'x'.repeat(2000)});
            }""")
            await asyncio.sleep(0.5)
            held = (_finished_tasks("_on_route"), _finished_tasks("read"))
            await browser.close()
            return held

    on_route, reads = asyncio.run(main())
    assert on_route < 20, "%d finished route handlers were kept" % on_route
    assert reads < 20, "%d finished body reads were kept" % reads
