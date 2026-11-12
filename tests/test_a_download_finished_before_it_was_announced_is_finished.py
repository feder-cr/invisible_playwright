"""A download the engine finished BEFORE it announced it is finished once it is announced.

`BrowserDispatcher._on_download_finished` keeps the error of a download whose artifact does not exist yet, and
`_on_download_created` hands it to the artifact later. A download that succeeded has no error (`None`), and the old
code read that as "nothing was kept": the artifact was never marked finished, so `save_as`, `path` and `failure`
waited forever on a complete file (seen on tiny downloads, where the transfer ends before the announcement is handled).

No browser and no socket: the two real methods run on a stand-in that carries the attributes they read, and the
artifact is a fake that records what it was told.

Known-bad input: the old code -> (a) and (d) are red.
"""
from __future__ import annotations

import threading
import types

import pytest

from invisible_playwright._juggler import server as juggler_server
from invisible_playwright._juggler.server import BrowserDispatcher

pytestmark = pytest.mark.unit

CONTEXT = "ctx-1"


class FakeArtifact:
    """What `ArtifactDispatcher` is to the two methods: built with (server, context, uuid, path), told once."""

    def __init__(self, server, context, uuid, path):
        self.uuid = uuid
        self.path = path
        self.channel = {"guid": "artifact@" + uuid}
        self.finished = threading.Event()
        self.reports = []

    def report_finished(self, error):
        self.reports.append(error)
        self.finished.set()


@pytest.fixture
def browser(monkeypatch, tmp_path):
    monkeypatch.setattr(juggler_server, "ArtifactDispatcher", FakeArtifact)
    return types.SimpleNamespace(
        contexts=[types.SimpleNamespace(context_id=CONTEXT)],
        _targets_lock=threading.Lock(),
        _page_targets={},
        _adoptions={},
        _gone_openers={},
        _artifacts={},
        _finished_early={},
        server=object(),
        _downloads_dir=lambda context_id: str(tmp_path),
    )


def _created(browser, uuid, context=CONTEXT):
    BrowserDispatcher._on_download_created(
        browser, {"uuid": uuid, "browserContextId": context, "pageTargetId": "t1",
                  "url": "http://x/f", "suggestedFileName": "f.bin"})


def _finished(browser, uuid, **extra):
    BrowserDispatcher._on_download_finished(browser, dict({"uuid": uuid}, **extra))


def test_a_download_that_succeeded_before_it_was_announced_is_finished(browser):
    _finished(browser, "u1")
    _created(browser, "u1")
    artifact = browser._artifacts["u1"]
    assert artifact.finished.wait(2), "the artifact was never marked finished: save_as would wait forever"
    assert artifact.reports == [None]
    assert browser._finished_early == {}


def test_a_download_canceled_before_it_was_announced_is_finished_with_the_reason(browser):
    _finished(browser, "u2", canceled=True)
    _created(browser, "u2")
    artifact = browser._artifacts["u2"]
    assert artifact.finished.wait(2)
    assert artifact.reports == ["canceled"]
    assert browser._finished_early == {}


def test_a_download_that_failed_before_it_was_announced_keeps_its_error(browser):
    _finished(browser, "u3", error="NS_ERROR_NET_RESET")
    _created(browser, "u3")
    assert browser._artifacts["u3"].reports == ["NS_ERROR_NET_RESET"]


def test_a_download_announced_and_then_finished_is_finished(browser):
    _created(browser, "u4")
    artifact = browser._artifacts["u4"]
    assert not artifact.finished.is_set()
    _finished(browser, "u4")
    assert artifact.finished.wait(2)
    assert artifact.reports == [None]
    assert browser._finished_early == {}


def test_a_download_announced_and_not_finished_stays_open(browser):
    _created(browser, "u5")
    assert not browser._artifacts["u5"].finished.is_set()
    assert browser._finished_early == {}


def test_an_early_finish_of_a_context_that_does_not_exist_is_not_kept(browser):
    _finished(browser, "u6")
    assert "u6" in browser._finished_early
    _created(browser, "u6", context="gone")
    assert "u6" not in browser._artifacts
    assert browser._finished_early == {}
