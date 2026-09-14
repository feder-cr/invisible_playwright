"""Protocol objects: guids, parentage, `__create__`, and method routing.

This is the other half of `transport.py`. The transport moves messages; this
decides what answers them.

**THE MODEL, WHICH IS SMALLER THAN IT LOOKS.** The Playwright protocol is not
RPC on a flat namespace: every message names a `guid`, and that guid is an
OBJECT with a type, a parent and an initializer. The client mirrors that tree
locally - `Connection._objects` - and builds a `ChannelOwner` for each one the
moment it sees a `__create__`. So a server that answers correctly has to do
three things and only three:

1. Hand out guids and remember what each one is.
2. Announce every new object with `__create__` BEFORE anything names it,
   parented to an object the client already knows.
3. Route `{guid, method, params}` to the object that owns it.

⛔ **ORDER IS PART OF THE PROTOCOL, NOT AN OPTIMISATION.** `__create__` for a
child must reach the client before the reply that returns a channel to it, and
before any event fired on it. `Connection.dispatch` looks the guid up in a plain
dict and raises `Cannot find object` if it is not there yet - so an out-of-order
create is not a race that usually works, it is a hard failure. Everything that
creates an object therefore emits the create first and returns the channel
after, in that order, from the same thread.

⛔ **AND A CHANNEL IS `{"guid": ...}`, NOT THE OBJECT.** `_connection.py`
replaces guids with channels on the way in and channels with guids on the way
out. A server that returns a bare guid string where a channel is expected does
not fail at the seam: the client stores a string where a `ChannelOwner` should
be, and the failure surfaces later, in a property access far from the cause.
"""
from __future__ import annotations

import itertools
import threading
from typing import Any, Callable, Dict, Optional

from . import perimeter
# One class for a target that is gone, raised here for a disposed object and
# by the connection for a closed pipe. Defined there, the lower layer.
from .connection import TargetClosedError  # noqa: F401 - re-exported on purpose




class ProtocolException(Exception):
    """A failure the client should see as a protocol error, with its reason.

    ⛔ Prefer this over a bare `Exception` for anything the caller could have
    caused: the message travels all the way to the user, and "Element is not
    visible" is a different day than "TypeError".
    """


class Dispatcher:
    """One protocol object.

    A subclass declares its type and the methods it answers. Methods are looked
    up by name in `METHODS`, which is explicit on purpose: routing by
    `getattr(self, method)` would expose every attribute of the object to
    anything that can put a string in a message.
    """

    #: The protocol type name, e.g. "Page". Must match what `_connection.py`'s
    #: object factory knows, or the client raises on `__create__`.
    TYPE = "Unknown"

    #: name -> bound-method-name. ⛔ EXPLICIT. See the class docstring.
    METHODS: Dict[str, str] = {}

    def __init__(self, server: "Server", parent: Optional["Dispatcher"],
                 initializer: Optional[Dict] = None,
                 guid: Optional[str] = None) -> None:
        self.server = server
        self.parent = parent
        self.initializer = initializer or {}
        self.guid = guid or server.new_guid(self.TYPE)
        self.disposed = False
        server.register(self)
        # ⛔ Announced BEFORE anyone can name it. See the module docstring.
        server.announce(self)

    # ── what the protocol needs ─────────────────────────────────────────────
    @property
    def channel(self) -> Dict[str, str]:
        """How this object is referred to inside a result or an initializer."""
        return {"guid": self.guid}

    def emit(self, method: str, params: Optional[Dict] = None) -> None:
        """Fire a protocol event on this object."""
        self.server.send_up({"guid": self.guid, "method": method,
                             "params": params or {}})

    def dispose(self, reason: Optional[str] = None) -> None:
        """Tell the client this object is gone, and forget its whole subtree.

        ⛔ ONE MESSAGE, MANY OBJECTS, AND THAT ASYMMETRY IS THE PROTOCOL. The
        client drops the children of a disposed object by itself - a
        `__dispose__` per child would be traffic saying what the guid tree
        already says, and the driver does not send it either: measured
        2026-08-28, it disposes Page, BrowserContext, Browser, ElementHandle
        and APIRequestContext, and never a Frame. So the children are dropped
        HERE, silently, and the wire stays identical.

        ⛔ FORGETTING THEM IS NOT OPTIONAL. Without the cascade this server's
        guid registry kept every frame of every closed page: measured at one
        `FrameDispatcher` per page, for the life of the browser. Nothing
        crashed, which is why it outlived the subscriber leak it was found
        beside - and why `test_a_long_session_does_not_accumulate_subscribers`
        now asserts on both registries rather than the noisy one.
        """
        if self.disposed:
            return
        self.disposed = True
        params = {"reason": reason} if reason else {}
        self.server.send_up({"guid": self.guid, "method": "__dispose__",
                             "params": params})
        self.server.unregister(self)
        for child in self.server.descendants_of(self):
            child.disposed = True
            self.server.unregister(child)

    # ── routing ─────────────────────────────────────────────────────────────
    def call(self, method: str, params: Dict) -> Any:
        name = self.METHODS.get(method)
        if name is None:
            # ⛔ NAME THE FEATURE, not just the missing method. "Page has no
            # method X" reads like a bug in this package; "X is part of HAR,
            # which is outside the perimeter by decision" is something the
            # caller can act on. The two cases are genuinely different and the
            # message has to say which one this is.
            outside = perimeter.refusal(method)
            raise ProtocolException(
                outside or
                ("%s has no method %r, and %r is INSIDE the perimeter this "
                 "package covers - so this is a gap, not a decision. See "
                 "32-stacco-da-playwright.md section 6.5."
                 % (self.TYPE, method, method)))
        return getattr(self, name)(params)


class Server:
    """The guid registry, and the entry point the transport calls."""

    def __init__(self) -> None:
        self._objects: Dict[str, Dispatcher] = {}
        #: The guids this server has disposed, newest last.
        #:
        #: ⛔ REMEMBERED, BECAUSE FORGETTING THEM LOSES THE DIFFERENCE BETWEEN
        #: TWO ERRORS THAT ARE NOT THE SAME. A call naming a guid that was
        #: disposed is a race the client is built to swallow; a call naming a
        #: guid that never existed is a defect here, and answering both with
        #: the same message either hides the second or breaks the first.
        #:
        #: Bounded: past the cap the oldest are dropped and a very old guid
        #: reads as "never created" again. That is the right way round - the
        #: window that matters is the one between a dispose and the calls
        #: already in flight behind it.
        self._disposed: Dict[str, None] = {}
        self._counters: Dict[str, itertools.count] = {}
        self._lock = threading.Lock()
        self._transport: Any = None
        #: Guid "" is the Root the client creates on its own side. Nothing is
        #: registered for it here: it is only ever a PARENT.
        self.root_guid = ""
        self._on_shutdown: list = []
        #: guid -> the live impl-side object for it, or None until a
        #: transport binds it. See `bind_twins`.
        self._twins: Optional[Dict[str, Any]] = None
        #: The client's `Connection.deliver_event`, or None until bound. See
        #: `bind_twins` and `deliver`.
        self._deliver_event: Any = None

    # ── plumbing ────────────────────────────────────────────────────────────
    def attach(self, transport: Any) -> None:
        self._transport = transport

    def bind_twins(self, objects: Dict[str, Any], deliver_event: Any) -> None:
        """Give this server a live view of the client's guid -> object
        registry, and its event-delivery entry point. See
        `InProcessTransport.bind_impl_objects`, which calls this - the
        docstring for WHY lives there, next to its one caller."""
        self._twins = objects
        self._deliver_event = deliver_event

    def twin(self, guid: str) -> Any:
        """The live impl-side object for a guid this server already created,
        or None if nothing is bound yet or the guid is unknown."""
        return (self._twins or {}).get(guid)

    def deliver(self, fn: Any, *args: Any) -> None:
        """Call `fn(*args)` as an event, under the client's own rules for
        what that means (see `Connection.deliver_event`). MUST be called from
        the asyncio loop thread - use `call_soon` to get there first."""
        self._deliver_event(fn, *args)

    def run_blocking(self, fn: Any, *args: Any) -> Any:
        """A fused dispatcher's way to run a blocking call off the loop.
        Delegates to the transport, which owns the worker pool; see
        `InProcessTransport.run_blocking` for why this exists and what it
        guards against."""
        return self._transport.run_blocking(fn, *args)

    def call_soon(self, fn: Any, *args: Any) -> None:
        """A fused dispatcher's way to hand an EVENT to the asyncio loop from
        the connection's read-loop thread. See
        `InProcessTransport.call_soon`."""
        self._transport.call_soon(fn, *args)

    def send_up(self, message: Dict) -> None:
        if self._transport is not None:
            self._transport.emit_message(message)

    def new_guid(self, type_name: str) -> str:
        with self._lock:
            counter = self._counters.setdefault(type_name, itertools.count(1))
            return "%s@%d" % (type_name.lower(), next(counter))

    def register(self, obj: Dispatcher) -> None:
        with self._lock:
            self._objects[obj.guid] = obj

    #: How many disposed guids are remembered. Large enough for any burst of
    #: in-flight calls, small enough to be free.
    DISPOSED_MEMORY = 4096

    def unregister(self, obj: Dispatcher) -> None:
        with self._lock:
            self._objects.pop(obj.guid, None)
            self._disposed[obj.guid] = None
            while len(self._disposed) > self.DISPOSED_MEMORY:
                self._disposed.pop(next(iter(self._disposed)))

    def descendants_of(self, obj: Dispatcher) -> list:
        """Everything registered whose parent chain reaches `obj`.

        ⛔ WALKED RATHER THAN INDEXED, on purpose. A children list on every
        dispatcher is a second place that knows the tree, and the tree already
        lives in `parent`; keeping the two in step is precisely the kind of
        duplication that lets one of them go stale. The walk is over the live
        registry, which this cascade is what keeps small.
        """
        with self._lock:
            everything = list(self._objects.values())
        out = []
        for candidate in everything:
            walker = candidate.parent
            seen = set()
            while walker is not None and id(walker) not in seen:
                if walker is obj:
                    out.append(candidate)
                    break
                seen.add(id(walker))
                walker = walker.parent
        return out

    def announce(self, obj: Dispatcher) -> None:
        """Emit `__create__` for a new object, parented where the client can
        already find it."""
        parent_guid = obj.parent.guid if obj.parent is not None else self.root_guid
        self.send_up({
            "guid": parent_guid,
            "method": "__create__",
            "params": {"type": obj.TYPE, "guid": obj.guid,
                       "initializer": obj.initializer},
        })

    def on_shutdown(self, hook: Callable[[], None]) -> None:
        self._on_shutdown.append(hook)

    def shutdown(self) -> None:
        for hook in reversed(self._on_shutdown):
            try:
                hook()
            except Exception:
                # ⛔ One failing hook must not stop the others: this runs while
                # the session is already going away, and a browser left alive
                # here is a process nobody will ever kill.
                pass
        self._on_shutdown.clear()

    # ── the entry point ─────────────────────────────────────────────────────
    def handle(self, message: Dict) -> Any:
        guid = message.get("guid")
        method = message.get("method")
        params = message.get("params") or {}
        if guid == self.root_guid:
            return self.handle_root(method, params)
        with self._lock:
            obj = self._objects.get(guid)
        if obj is None:
            # ⛔ THE PERIMETER IS CHECKED FIRST, and this is the case the
            # refusal layer exists for. An out-of-perimeter call lands on a
            # guid that was never created, so without this the answer is `no
            # object 'artifact@3' to answer 'read'` - true, unreadable, and
            # indistinguishable from a bug here.
            outside = perimeter.refusal(method)
            if outside:
                raise ProtocolException(outside)
            with self._lock:
                gone = guid in self._disposed
            if gone:
                raise TargetClosedError(
                    "Target page, context or browser has been closed")
            raise ProtocolException(
                "no object %r to answer %r: it was never created"
                % (guid, method))
        return obj.call(method, params)

    def handle_root(self, method: str, params: Dict) -> Any:
        raise ProtocolException("the root has no method %r" % method)
