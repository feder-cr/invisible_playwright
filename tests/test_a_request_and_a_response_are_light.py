"""A request and its response weigh what they hold, and let go of what the wire carried.

Measured on a real browser, one context, 95 navigations of pages with ~100 subresources
(registry 18,000 objects): a Request was 4.5 KB and a Response 6.0 KB (headers as a list of
dicts, kept twice - in the initializer and beside it - and the POST body in the initializer),
and the process grew from 37 to 149 MB. Kept as interned pairs, with the initializer reduced
to what is asked again, they are 0.84 KB each and the same run ends at 73 MB.

Known-bad: with the headers kept as the list of dicts the size test fails; with the headers
dropped from the initializer BEFORE the message is sent the wire test fails; with
`rawRequestHeaders` answering from the initializer the answer test fails.
"""
from __future__ import annotations

import sys

import pytest

from test_a_closed_context_leaves_nothing_in_the_registry import (  # noqa: F401
    ScriptedJuggler, browser)

pytestmark = pytest.mark.unit

HEADERS = [{"name": "Host", "value": "example.com"},
           {"name": "User-Agent", "value": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:159.0) Gecko/20100101 Firefox/159.0"},
           {"name": "Accept", "value": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"},
           {"name": "Accept-Language", "value": "en-US,en;q=0.5"},
           {"name": "Accept-Encoding", "value": "gzip, deflate, br"},
           {"name": "Connection", "value": "keep-alive"},
           {"name": "Cookie", "value": "sid=" + "c" * 200},
           {"name": "Sec-Fetch-Dest", "value": "document"},
           {"name": "Sec-Fetch-Mode", "value": "navigate"},
           {"name": "Sec-Fetch-Site", "value": "none"}]
DETAILS = {"protocol": "TLS 1.3", "subjectName": "example.com", "issuer": "CA",
           "validFrom": 1, "validTo": 2}


def deep(obj, seen=None):
    seen = set() if seen is None else seen
    if id(obj) in seen:
        return 0
    seen.add(id(obj))
    size = sys.getsizeof(obj)
    if isinstance(obj, dict):
        size += sum(deep(k, seen) + deep(v, seen) for k, v in obj.items())
    elif isinstance(obj, (list, tuple)):
        size += sum(deep(i, seen) for i in obj)
    return size


def page_of(browser):
    server = browser.server
    context = server.object(server.handle(
        {"guid": browser.guid, "method": "newContext", "params": {}})["context"]["guid"])
    return context, server.object(server.handle(
        {"guid": context.guid, "method": "newPage", "params": {"pageNumber": 0}})["page"]["guid"])


def traffic(page, n, post=None, details=None):
    conn = page.conn
    rid = "R%d" % n
    request = {"requestId": rid, "url": "https://example.com/%d" % n, "method": "POST" if post else "GET",
               "headers": HEADERS, "frameId": None, "navigationId": None, "resourceType": "Script"}
    if post:
        request["postData"] = post
    conn.dispatch_event("Network.requestWillBeSent", request, page.session)
    conn.dispatch_event("Network.responseReceived", {
        "requestId": rid, "status": 200, "statusText": "OK", "headers": HEADERS,
        "remoteIPAddress": "93.184.216.34", "remotePort": 443,
        "securityDetails": dict(details) if details else None}, page.session)
    conn.dispatch_event("Network.requestFinished", {"requestId": rid, "protocolVersion": "h2"},
                        page.session)
    return page._request_log[-1]


def test_the_announcement_carries_the_headers_and_the_body_and_the_object_keeps_neither(browser):
    context, page = page_of(browser)
    sent = []
    real = browser.server.send_up
    browser.server.send_up = lambda m: (sent.append(m), real(m))[1]
    request = traffic(page, 1, post="p" * 1000)
    created = {m["params"]["type"]: m["params"]["initializer"] for m in sent
               if m["method"] == "__create__" and m["params"]["type"] in ("Request", "Response")}
    assert created["Request"]["headers"] == HEADERS
    assert created["Request"]["postData"] == "p" * 1000
    assert created["Response"]["headers"] == HEADERS
    assert "timing" in created["Response"]
    for kept in (request.initializer, request.response.initializer):
        assert "headers" not in kept and "postData" not in kept and "timing" not in kept
    assert request.initializer["url"] == "https://example.com/1"
    assert request.response.initializer["status"] == 200


def test_the_raw_headers_are_still_answered_as_they_were_sent(browser):
    context, page = page_of(browser)
    request = traffic(page, 1, details=DETAILS)
    assert request.op_raw_request_headers({}) == {"headers": HEADERS}
    assert request.response.op_raw_response_headers({}) == {"headers": HEADERS}
    assert request.response.op_server_addr({}) == {"value": {"ipAddress": "93.184.216.34", "port": 443}}
    assert request.response.op_security_details({}) == {"value": DETAILS}


def test_a_request_with_ten_headers_is_a_fraction_of_what_it_was(browser):
    """What a request costs ON TOP of the one before it: a site repeats most of its headers."""
    context, page = page_of(browser)
    skip = ("server", "parent", "page", "response", "_owner")
    previous = traffic(page, 1)
    seen: set = set()
    deep({k: v for k, v in vars(previous).items() if k not in skip}, seen)
    request = traffic(page, 2)
    marginal = deep({k: v for k, v in vars(request).items() if k not in skip}, seen)
    assert marginal < 2000, marginal      # 4.5 KB with the list of dicts, kept twice


def test_headers_and_certificates_are_shared_between_responses(browser):
    context, page = page_of(browser)
    a = traffic(page, 1, details=DETAILS)
    b = traffic(page, 2, details=DETAILS)
    assert a.response.security_details is b.response.security_details
    assert a._headers[0][1] is b._headers[0][1]


def test_what_a_request_held_is_let_go_when_it_is_disposed(browser):
    context, page = page_of(browser)
    request = traffic(page, 1)
    response = request.response
    browser.server.handle({"guid": page.guid, "method": "close", "params": {}})
    assert request._headers == () and request.response is None
    assert response._headers == () and response.security_details is None
