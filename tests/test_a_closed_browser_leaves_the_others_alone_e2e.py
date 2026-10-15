"""A browser closed twice leaves the other browsers of the process alone.

The known-bad input, reported 2026-10-15: the driver closes a browser's pipe
from `Browser.close` and again from the session's shutdown hook. Through
invisible-core 47.39.0 each call closed the pipe's descriptor NUMBERS, and a
released number goes to the next pipe the process opens, so when one process
drives several sessions the second call closed another browser's pipe: its
launch died with EISDIR, or its next command found the pipe gone.

Two sessions in one event loop make the order deterministic: the first closes
its browser (its numbers are released), the second launches (and is given
them), the first shuts down (the second close), and the second must still
drive its page. Measured on Windows against 47.39.0 and firefox-47: the run
does not fail, it HANGS - the second close lands on a number the other
browser's output reader is blocked on, and Windows holds that close until the
read returns - so what turns it red is the suite's per-test timeout
(`run_e2e.py`). Against 47.40.0 it passes in under 20 s.
"""
from __future__ import annotations

import asyncio

import pytest

pytestmark = pytest.mark.e2e


def test_a_session_shutting_down_does_not_close_another_sessions_pipe(firefox_binary):
    from invisible_playwright.async_api import InvisiblePlaywright

    async def run():
        first = InvisiblePlaywright(seed=7001, binary_path=firefox_binary, headless=True)
        browser = await first.__aenter__()
        try:
            await browser.close()
            async with InvisiblePlaywright(seed=7002, binary_path=firefox_binary,
                                           headless=True) as other:
                page = await other.new_page()
                await page.set_content("<p>still here</p>")
                await first.__aexit__(None, None, None)
                first = None
                assert await page.text_content("p") == "still here"
                page = await other.new_page()
                await page.set_content("<p>and again</p>")
                assert await page.text_content("p") == "and again"
        finally:
            if first is not None:
                await first.__aexit__(None, None, None)

    asyncio.run(asyncio.wait_for(run(), 180))
