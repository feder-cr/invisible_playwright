"""`prefers-color-scheme` is the persona's, whatever the host's theme is.

The core declares the theme (`ui.systemUsesDarkTheme` and the
`prefers-color-scheme` content override, both from `profile.dark_theme`). A page
must read exactly that: a light persona on a dark PC reads light, a dark persona
on a light PC reads dark. A value that follows the host is a value the persona
did not declare, and one that is the same for every seed is a value that links
them.

Two browsers over two tests, one per persona, so that whichever theme this host
has, one of the two disagrees with it and fails if the page follows the host.

The context is opened the way the Go client opens it, with `colorScheme` set to
"no-override" (Playwright's "not overridden"): before this file the server
passed that on as `Browser.setColorScheme` and the engine refused the context,
so the test could not even read a value.

Known-bad, run before this file was trusted: on the unfixed server the context
is refused (`Expected "<root>.colorScheme" to be one of [dark, light,
no-preference]`).
"""
from __future__ import annotations

import asyncio
import json

import pytest

pytest.importorskip("websockets")
from websockets.asyncio.client import connect  # noqa: E402

pytestmark = pytest.mark.e2e

READ = ("({dark: matchMedia('(prefers-color-scheme: dark)').matches,"
        "light: matchMedia('(prefers-color-scheme: light)').matches})")


def _seed_with(dark):
    from invisible_core._fpforge import generate_profile
    for seed in range(1, 200):
        if bool(generate_profile(seed).dark_theme) is dark:
            return seed
    raise AssertionError("no seed in 1..199 has dark_theme=%s" % dark)


def _unwrap(value):
    """The protocol's tagged value to a plain dict of booleans."""
    if isinstance(value, list):
        return {item["k"]: item["v"]["b"] for item in value}
    return value


async def _read_scheme(endpoint):
    objects = {}
    async with connect(endpoint, max_size=None) as ws:
        counter = [0]

        async def call(guid, method, params=None, metadata=None):
            counter[0] += 1
            await ws.send(json.dumps({"id": counter[0], "guid": guid, "method": method,
                                      "params": params or {}, "metadata": metadata or {}}))
            while True:
                m = json.loads(await ws.recv())
                if m.get("method") == "__create__":
                    objects[m["params"]["guid"]] = m["params"]
                if m.get("id") == counter[0]:
                    assert "error" not in m, m
                    return m.get("result")

        await call("", "initialize", {"sdkLanguage": "javascript"})
        browser = [g for g, p in objects.items() if p["type"] == "Browser"][0]
        ctx = (await call(browser, "newContext", {
            "colorScheme": "no-override", "reducedMotion": "no-override",
            "forcedColors": "no-override", "contrast": "no-override"}))["context"]["guid"]
        page = (await call(ctx, "newPage", {}))["page"]["guid"]
        frame = objects[page]["initializer"]["mainFrame"]["guid"]
        await call(frame, "goto", {"url": "data:text/html,<title>t</title>",
                                   "waitUntil": "load"}, {"timeout": 30000})
        out = await call(frame, "evaluateExpression", {
            "expression": READ, "isFunction": False,
            "arg": {"value": {"v": "undefined"}, "handles": []}}, {})
        await call(ctx, "close", {})
        return _unwrap(out["value"]["o"])


@pytest.mark.parametrize("dark", [False, True], ids=["light-persona", "dark-persona"])
def test_a_page_reads_the_personas_scheme(firefox_binary, dark):
    from invisible_playwright._juggler.remote import launch_server
    seed = _seed_with(dark)
    server = launch_server(seed=seed, headless=True, virtual_display=False,
                           binary_path=firefox_binary, locale="en-US",
                           timezone="Europe/Rome")
    try:
        read = asyncio.run(asyncio.wait_for(_read_scheme(server.ws_endpoint), 120))
    finally:
        server.close()
    assert server.process.poll() is not None
    assert read == {"dark": dark, "light": not dark}, (
        "seed %d declares dark_theme=%s and the page read %s" % (seed, dark, read))
