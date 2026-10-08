"""A request's `resource_type` comes from Juggler's cause, as the driver reads it.

⛔ EVERY REQUEST WAS `other`. Juggler sends `cause` as the `nsIContentPolicy`
constant's name (`TYPE_DOCUMENT`, `TYPE_XMLHTTPREQUEST`, `TYPE_FETCH`), and the
table it was looked up in was keyed by `document`, `xmlhttprequest`, `fetch`.
Nothing matched, so a page load, a fetch and an XHR were indistinguishable
on the `request` event. Seen on 2026-10-08 against firefox-36 while recording
a site's XHR/fetch calls from `context.on("request")`.
"""
from __future__ import annotations

import pytest

from invisible_playwright._juggler._marshal import _resource_type


@pytest.mark.parametrize("cause, expected", [
    ("TYPE_DOCUMENT", "document"),
    ("TYPE_SUBDOCUMENT", "document"),
    ("TYPE_XMLHTTPREQUEST", "xhr"),
    ("TYPE_FETCH", "fetch"),
    ("TYPE_SCRIPT", "script"),
    ("TYPE_STYLESHEET", "stylesheet"),
    ("TYPE_IMAGE", "image"),
    ("TYPE_IMAGESET", "image"),
    ("TYPE_FONT", "font"),
    ("TYPE_MEDIA", "media"),
    ("TYPE_WEBSOCKET", "websocket"),
    ("TYPE_WEB_MANIFEST", "manifest"),
    ("TYPE_BEACON", "other"),
    ("TYPE_OTHER", "other"),
])
def test_the_cause_juggler_sends_names_the_resource(cause, expected):
    assert _resource_type({"cause": cause, "internalCause": "TYPE_OTHER"}) == expected


def test_an_event_source_is_named_by_its_internal_cause():
    assert _resource_type({"cause": "TYPE_OTHER",
                           "internalCause": "TYPE_INTERNAL_EVENTSOURCE"}) == "eventsource"


@pytest.mark.parametrize("params", [{}, {"cause": None}, {"cause": "TYPE_SOMETHING_NEW"},
                                    {"cause": "document"}])
def test_anything_else_is_other(params):
    assert _resource_type(params) == "other"
