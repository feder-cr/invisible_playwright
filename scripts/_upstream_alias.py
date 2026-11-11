
"""Loaded with -p, before the suite's conftest, so the redirection is in place
by the time anything imports `playwright`."""
import sys, pathlib

_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "src"))

import invisible_playwright._pw as _pw

_NAMES = ['playwright', 'playwright._impl._assertions', 'playwright._impl._browser', 'playwright._impl._driver', 'playwright._impl._errors', 'playwright._impl._glob', 'playwright._impl._helper', 'playwright._impl._network', 'playwright._impl._page', 'playwright._impl._path_utils', 'playwright._impl._selectors', 'playwright.async_api', 'playwright.async_api._generated', 'playwright.sync_api']


class _Absent:
    """A module our client does not have, that says so when touched."""

    def __init__(self, name, why):
        self.__name__ = name
        self._why = why

    def __getattr__(self, attr):
        # ⛔ IT HANDS BACK A CALLABLE AND RAISES ON THE CALL, not on the
        # attribute. `from playwright._impl._driver import
        # compute_driver_executable` is a module-level line in the suite's
        # conftest, so raising here kills COLLECTION and takes all 657 tests
        # with it - for a name used inside exactly one fixture. Failing at the
        # call means the baseline loses only the tests that actually reach it,
        # which is the honest count.
        why = self._why
        name = self.__name__

        def gone(*a, **k):
            raise RuntimeError(
                "%s.%s does not exist in invisible_playwright: %s. This suite "
                "is Playwright's own and tests components this package "
                "removed - the Node driver went on 2026-08-28 - so a test that "
                "reaches this name is testing something we deliberately do not "
                "ship, not something broken." % (name, attr, why))

        gone.__name__ = attr
        return gone


def _absent(name, why):
    return _Absent(name, why)

sys.modules["playwright"] = _pw
for _name in _NAMES:
    if _name == "playwright":
        continue
    _tail = _name.split(".", 1)[1]
    try:
        _mod = __import__("invisible_playwright._pw." + _tail,
                          fromlist=["_"])
    except ImportError as _failure:
        # ⛔ A STAND-IN THAT EXPLAINS, not a silent skip and not a crash at
        # collection. Our client genuinely lacks some modules - `_impl._driver`
        # went with the Node driver - and the suite imports them at the top of
        # its conftest while using them in ONE fixture. Refusing here would
        # lose all 657 tests to a trace-viewer path; falling back to the real
        # `playwright` would measure Microsoft's client and call it ours. So
        # the name exists, and touching it says exactly what was removed.
        _mod = _absent(_name, str(_failure))
    sys.modules[_name] = _mod


def _pin_launch():
    """Give every launch OUR engine.

    The suite calls `browser_type.launch(**launch_arguments)` and those
    arguments carry only `headless`: upstream downloads its browsers, we pin
    one. Our launch REFUSES without an `executable_path` - correctly, it is the
    seal's whole job - so the binary is injected here rather than by editing
    the reference copy.

    ⛔ AT MODULE LEVEL, NOT ONLY IN A PYTEST HOOK, and the binary comes from
    the environment. A test that starts a child with `multiprocessing` gets a
    fresh interpreter on Windows (spawn, not fork): nothing of pytest's state
    is in it, so a redirect installed only by a hook is missing there, and the
    child imports the stock `playwright` and looks for its own Firefox. Every
    interpreter of the job imports this module first (`upstream_site/
    sitecustomize.py`), so the redirect holds in the children too. Idempotent:
    the pytest hook calls it again and it installs nothing twice.
    """
    import os
    binary = os.environ.get("INVPW_BINARY_PATH")
    if not binary:
        return
    from invisible_playwright._pw._impl import _browser_type as _bt

    if getattr(_bt.BrowserType.launch, "_invpw_pinned", False):
        return
    original = _bt.BrowserType.launch

    async def launch(self, *args, **kwargs):
        # ⛔ `executablePath`, camelCase: the impl layer takes the wire
        # spelling, and `executable_path` would be swallowed by nothing and
        # silently ignored - the launch would refuse exactly as before, and
        # the patch would look installed while doing nothing.
        if not kwargs.get("executablePath"):
            kwargs["executablePath"] = binary
        return await original(self, *args, **kwargs)

    launch._invpw_pinned = True
    _bt.BrowserType.launch = launch
    print("[baseline] every launch pinned to %s" % binary)


_pin_launch()


def pytest_sessionstart(session):
    _pin_launch()
