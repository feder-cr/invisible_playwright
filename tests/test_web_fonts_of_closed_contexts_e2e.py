"""The web fonts of contexts that were closed leave WebRender's shared archive, on a real engine.

⛔ THE KNOWN-BAD INPUT, measured on firefox-60: every page of a context has its own WebRender window,
and a window that is closed is unregistered on the render backend's direct channel while the clearing of
its namespace goes through the low-priority scene builder queue. The clearing then found no window, did
nothing, and the web fonts of the namespace stayed in the archive every window of the pool shares:
`explicit/gfx/webrender/resource-cache/fonts` grew by the size of every distinct web font a closed context
had loaded (10 / 20 / 40 contexts with a font of their own: 10.4 / 19.8 / 40.7 MB kept, none released by a minimize or a GC).
A retail Firefox holds none of them once the page is gone.

From firefox-61 a namespace's fonts are taken out of the shared archive whatever the order of the two
messages. Here: 20 contexts, one at a time with a few open together, each loads a web font whose bytes are
its own; after all of them are closed the archive holds (about) what it held before.

The engine that does it is not published when this file is written: `engine_at_least` makes the test wait
for it, and on an older engine it is skipped, never passed.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.e2e

CONTEXTS = 20
# The retention measured on firefox-60 is ~1 MB per context (20 contexts: 19.8 MB); the fixed archive is
# expected to be back at what it held before, within the allocator's slack.
SLACK_MB = 3.0


def test_the_web_fonts_of_closed_contexts_leave_the_shared_archive(firefox_binary, engine_at_least):
    engine_at_least("firefox-61")
    import webfont_probe

    result = webfont_probe.run(firefox_binary, contexts=CONTEXTS)

    # the probe measured what it says: every page loaded its font, the server served one per context
    assert result["loaded"] == CONTEXTS, result
    assert result["served"] == CONTEXTS, result
    # and the archive does show the fonts while contexts that use them are open (a probe that never
    # sees a font would pass on any engine)
    assert result["open_mb"] > result["before_mb"], result
    # all closed: what is left is the engine's own, not the fonts of 20 contexts
    assert result["after_mb"] <= result["before_mb"] + SLACK_MB, result
