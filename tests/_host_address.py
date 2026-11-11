"""The address of this host that a relay or a server of an e2e test binds to: ONE helper for every test that needs it."""
import socket

import pytest


def this_host_address() -> str:
    """The IPv4 address this host sends from (the one of its default route, as a real gateway's relay has a routable one:
    a loopback relay only suits a client whose UDP socket is not bound to an interface, and an engine's ICE sockets are).
    No packet is sent.

    A host with no default route (a container or a CI runner without a network, a laptop offline) has no such address: the
    test cannot pass by design there, so it SKIPS. Call it BEFORE starting any server of the test, so that nothing is left
    listening when it skips."""
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect(("192.0.2.1", 9))
        return probe.getsockname()[0]
    except OSError:
        pytest.skip("no default route: no LAN address to bind the relay")
    finally:
        probe.close()
