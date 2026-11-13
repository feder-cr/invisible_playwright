"""The FakeDoh tells a certificate the client refused from a connection the client dropped (no browser).

Windows census of 12/11 (run 43333574229): test_resolver_race_e2e read a ConnectionAbortedError [WinError 10053], through the
gateway of a context that HAS the certificate exception, as "a context without the FakeDoh certificate exception used the DoH".
A dropped connection (a speculative or duplicate DoH connection the browser abandons) says nothing about the certificate; only
a TLS alert from the client does. Known-bad: FakeDoh before this change has no certificate_refusals, and the e2e asserted
`tls_failures == []`, which a dropped connection alone fails.
"""
from __future__ import annotations

import pytest

from _doh_servers import FakeDoh

pytestmark = pytest.mark.unit

ABORTED_ON_WINDOWS = ("gw-a", "ConnectionAbortedError: [WinError 10053] An established connection was aborted by the software in your host machine")
RESET = ("gw-1", "ConnectionResetError: [Errno 104] Connection reset by peer")
EOF = ("gw-1", "SSLEOFError: EOF occurred in violation of protocol (_ssl.c:1006)")
UNKNOWN_CA = ("gw-1", "SSLError: [SSL: TLSV1_ALERT_UNKNOWN_CA] tlsv1 alert unknown ca (_ssl.c:1006)")
BAD_CERTIFICATE = ("direct", "SSLError: [SSL: SSLV3_ALERT_BAD_CERTIFICATE] sslv3 alert bad certificate (_ssl.c:1006)")


@pytest.fixture
def doh():
    server = FakeDoh({})
    yield server
    server.close()


def test_a_dropped_connection_is_not_a_refused_certificate(doh):
    doh.tls_failures.extend([ABORTED_ON_WINDOWS, RESET, EOF])
    assert doh.certificate_refusals() == []
    assert len(doh.tls_failures) == 3, "the failures stay listed for the message of a failed assertion"


def test_a_tls_alert_from_the_client_is_a_refused_certificate(doh):
    doh.tls_failures.extend([ABORTED_ON_WINDOWS, UNKNOWN_CA, BAD_CERTIFICATE])
    assert doh.certificate_refusals() == [UNKNOWN_CA, BAD_CERTIFICATE]
