"""Stand-ins for what the server's pointer talks to: the page's engine side
(`Actions`), the page's script (`InjectedScript`) and a clock.

Nothing launches a browser. `make_pointer` builds a real `Pointer` over them, the
way `PageDispatcher._build` does, and the page records what the engine would
receive.
"""
from __future__ import annotations

from invisible_playwright._juggler import pointer as pointer_mod
from invisible_playwright._juggler.pointer import Pointer

# A 100x40 control at (400, 300): centre exactly (450, 320).
BOX = {"x": 400.0, "y": 300.0, "width": 100.0, "height": 40.0}


class VirtualClock:
    """A clock that only advances when something sleeps.

    Plumbing assertions must not be at the mercy of what the machine running
    them was doing at the time. The dispatcher drops waypoints it cannot
    deliver on time; against a clock that never falls behind nothing is dropped
    and the behaviour is exactly reproducible."""

    def __init__(self) -> None:
        self.t = 0.0

    def now(self) -> float:
        return self.t

    def sleep(self, seconds: float) -> None:
        self.t += max(0.0, seconds)


class LateClock(VirtualClock):
    """A platform that always oversleeps by a fixed amount: Windows with its
    default 15.6 ms timer tick, in a form that can be asserted on exactly."""

    def __init__(self, overshoot: float = 0.006) -> None:
        super().__init__()
        self.overshoot = overshoot

    def sleep(self, seconds: float) -> None:
        self.t += max(0.0, seconds) + self.overshoot


class FakeActions:
    def __init__(self, page) -> None:
        self._page = page
        self.position = (0.0, 0.0)

    def move(self, x, y, steps=1):
        self._page.moves.append((round(x, 4), round(y, 4)))
        self.position = (x, y)

    def wheel(self, dx, dy):
        self._page.wheels.append((dx, dy))

    def hit_target(self, frame_id, element, point):
        self._page.hit_asked.append((frame_id, element, point))
        return "done" if self._page.hits else "other"


class FakeInjected:
    def __init__(self, page) -> None:
        self._page = page

    def query_selector(self, frame_id, selector, strict=False):
        self._page.queried.append((frame_id, selector))
        return "el" if self._page.box is not None else None

    def bounding_box(self, frame_id, element):
        return dict(self._page.box) if self._page.box else None

    def dispose(self, frame_id, element):
        self._page.disposed.append(element)


class FakePage:
    def __init__(self, width=1280, height=720, box=BOX, hits=True) -> None:
        self.viewport_size = {"width": width, "height": height}
        self.box = box
        self.hits = hits
        self.moves = []
        self.wheels = []
        self.hit_asked = []
        self.queried = []
        self.disposed = []
        self.actions = FakeActions(self)
        self.injected = FakeInjected(self)


def make_pointer(seed=4242, max_seconds=1.5, *, number=0, width=1280,
                 height=720, box=BOX, hits=True, draws=True):
    """A real `Pointer` on a fake page. Returns `(page, pointer)`."""
    page = FakePage(width, height, box, hits)
    pointer = Pointer(page.actions, page.injected, number, seed, max_seconds,
                      lambda: page.viewport_size, draws=draws)
    return page, pointer


def use_clock(monkeypatch, clock) -> None:
    monkeypatch.setattr(pointer_mod, "_CLOCK", clock)
