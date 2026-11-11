"""A child interpreter of the upstream job is redirected to our client too.

`test_call_sync_method_after_playwright_close_with_own_loop` starts a child with
`multiprocessing`. On Linux that is a fork, which inherits what the pytest hook
installed; on Windows it is a spawn, a fresh interpreter with none of pytest's
state, which imported the stock `playwright` and looked for Playwright's own
Firefox. The redirect is now installed by `scripts/_upstream_alias.py` at import
and every interpreter of the job imports it at startup, through the
`sitecustomize.py` of `scripts/upstream_site/`, which `upstream_baseline.job_env`
puts first on PYTHONPATH.

The children here are made with the `spawn` start method on every OS, so the
bug is reproduced on Linux and on a developer's PC too.
"""
from __future__ import annotations

import ast
import multiprocessing
import os
import re
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import upstream_baseline as base  # noqa: E402

PROBE = '''
import os, sys


def _state():
    pw = sys.modules.get("playwright")
    bt = sys.modules.get("invisible_playwright._pw._impl._browser_type")
    return {
        "playwright": getattr(pw, "__name__", None),
        "binary": os.environ.get("INVPW_BINARY_PATH"),
        "pinned": bool(bt and getattr(bt.BrowserType.launch, "_invpw_pinned", False)),
    }


def at_startup(queue):
    queue.put(_state())


def installed_twice(queue):
    import scripts._upstream_alias as alias
    from invisible_playwright._pw._impl import _browser_type as bt
    before = bt.BrowserType.launch
    alias._pin_launch()
    alias.pytest_sessionstart(None)
    out = _state()
    out["same_launch"] = bt.BrowserType.launch is before
    queue.put(out)
'''


def _child(tmp_path, monkeypatch, env: dict, target: str) -> dict:
    """What a `spawn` child sees at startup, with `env` as its environment."""
    (tmp_path / "upstream_probe.py").write_text(PROBE, encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))
    for key in ("PYTHONPATH", "INVPW_BINARY_PATH"):
        if key in env:
            monkeypatch.setenv(key, env[key])
        else:
            monkeypatch.delenv(key, raising=False)
    import upstream_probe
    ctx = multiprocessing.get_context("spawn")
    queue = ctx.Queue()
    proc = ctx.Process(target=getattr(upstream_probe, target), args=(queue,))
    proc.start()
    try:
        got = queue.get(timeout=120)
    finally:
        proc.join(timeout=60)
        if proc.is_alive():
            proc.kill()
    assert proc.exitcode == 0
    return got


BINARY = str(Path(sys.executable))      # any path: it is never launched


def test_a_spawned_child_is_redirected_to_our_client_with_the_jobs_environment(
        tmp_path, monkeypatch):
    env = base.job_env(BINARY, {})
    got = _child(tmp_path, monkeypatch, env, "at_startup")
    assert got["playwright"] == "invisible_playwright._pw"
    assert got["binary"] == BINARY
    assert got["pinned"] is True


def test_without_the_sitecustomize_folder_the_child_is_not_redirected(
        tmp_path, monkeypatch):
    # the bug, reproduced: the same environment minus the folder
    env = base.job_env(BINARY, {})
    parts = [p for p in env["PYTHONPATH"].split(os.pathsep)
             if Path(p) != base.SITE]
    assert len(parts) == len(env["PYTHONPATH"].split(os.pathsep)) - 1
    env["PYTHONPATH"] = os.pathsep.join(parts)
    got = _child(tmp_path, monkeypatch, env, "at_startup")
    assert got["playwright"] is None
    assert got["pinned"] is False
    assert got["binary"] == BINARY      # the variable alone is not the redirect


def test_the_redirect_waits_for_the_job_to_ask_for_it(tmp_path, monkeypatch):
    env = base.job_env(BINARY, {})
    del env["INVPW_BINARY_PATH"]
    got = _child(tmp_path, monkeypatch, env, "at_startup")
    assert got["playwright"] is None and got["pinned"] is False


def test_the_pytest_hook_does_not_install_the_launch_patch_a_second_time(
        tmp_path, monkeypatch):
    env = base.job_env(BINARY, {})
    got = _child(tmp_path, monkeypatch, env, "installed_twice")
    assert got["pinned"] is True and got["same_launch"] is True


def test_the_job_environment_has_this_checkouts_folders_in_order():
    env = base.job_env("ff", {"PYTHONPATH": "/somewhere/else", "KEEP": "1"})
    assert env["PYTHONPATH"].split(os.pathsep) == [
        str(base.SITE), str(ROOT), str(ROOT / "src")]
    assert env["INVPW_BINARY_PATH"] == "ff" and env["KEEP"] == "1"
    assert (base.SITE / "sitecustomize.py").is_file()


def test_the_committed_alias_module_is_the_one_the_script_generates():
    # CI regenerates it with the suite's own module list; the committed copy
    # (the one the children above import) must be the same text with a list.
    text = (ROOT / "scripts" / "_upstream_alias.py").read_text(encoding="utf-8")
    names = ast.literal_eval(re.search(r"^_NAMES = (\[.*\])$", text,
                                       re.MULTILINE).group(1))
    assert names and "playwright" in names
    assert text == base.PLUGIN.replace("__NAMES__", repr(names))
