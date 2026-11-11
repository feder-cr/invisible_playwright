"""The permission names a caller uses become the engine's, and a name outside the table is refused.

The table is the one of the Playwright client for this browser (playwright-core `ffBrowser.js`,
`doGrantPermissions`): geolocation -> geo, notifications -> desktop-notification, the other three as they are.
"""
import pytest

from invisible_playwright._juggler.dispatcher import ProtocolException
from invisible_playwright._juggler.server import _engine_permissions

pytestmark = pytest.mark.unit


def test_the_five_names_of_the_table():
    assert _engine_permissions(
        ["geolocation", "persistent-storage", "push", "notifications", "screen-wake-lock"]
    ) == ["geo", "persistent-storage", "push", "desktop-notification", "screen-wake-lock"]


def test_the_order_is_the_callers_and_nothing_is_dropped():
    assert _engine_permissions(["notifications", "geolocation"]) == ["desktop-notification", "geo"]
    assert _engine_permissions([]) == []


@pytest.mark.parametrize("name", ["camera", "microphone", "clipboard-read", "geo", "desktop-notification", ""])
def test_a_name_outside_the_table_is_an_error(name):
    with pytest.raises(ProtocolException, match="Unknown permission: %s" % name):
        _engine_permissions(["geolocation", name])
