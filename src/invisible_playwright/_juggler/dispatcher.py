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

import contextvars
import itertools
import os
import threading
import time
from typing import Any, Callable, Dict, List, Optional

from . import perimeter, variants
# One class for a target that is gone, raised here for a disposed object and
# by the connection for a closed pipe. Defined there, the lower layer.
from invisible_core.juggler.connection import TargetClosedError  # noqa: F401 - re-exported on purpose




#: HOW MANY DISPATCHERS OF ONE BUCKET THE REGISTRY KEEPS, AS PLAYWRIGHT'S SERVER DOES
#: (1.62, `dispatchers/dispatcher.ts` `maxDispatchersForBucket`): a bucket is the object's
#: type - a Frame is 'MainFrame' or 'Frame' - and past its limit the oldest tenth of the
#: bucket is disposed with `__dispose__` {reason: "gc"}, which a client reads as "collected
#: to prevent unbounded heap growth". ONE VALUE PER TYPE, here, so it can be lowered in one
#: place; `INVPW_GC_LIMITS="Request=1000,Response=1000"` lowers some without editing code
#: ("default=N" changes the default).
#:
#: TWO DIFFERENCES FROM PLAYWRIGHT, and the only two. (1) Request, Response and Route are
#: 2000, not 10000: a context that lives through a hundred navigations of real pages
#: (~100 subresources each, ~200 objects per navigation) held 18,000 of them, and with real
#: headers that is 190 MB of process (measured on a real browser, 95 navigations, one
#: context, objects already light) against 70 MB with 2000. A client reads a request in the
#: callback of its event (the Go client keeps none beyond it) and 2000 is ten navigations.
#: (2) An object younger than GC_MIN_AGE_S is never collected, so what a client was just
#: handed is valid for the length of any callback however fast the bucket fills: Playwright
#: collects the oldest tenth whatever its age.
GC_LIMIT_DEFAULT = 10000
GC_LIMITS: Dict[str, int] = {"JSHandle": 100000, "ElementHandle": 100000,
                             "Request": 2000, "Response": 2000, "Route": 2000}
GC_MIN_AGE_S = 10.0


def gc_limits_from_env(environ: Optional[Dict[str, str]] = None) -> Dict[str, int]:
    """`GC_LIMITS` with the default under "default", and what `INVPW_GC_LIMITS` overrides."""
    environ = os.environ if environ is None else environ
    limits = dict(GC_LIMITS)
    limits["default"] = GC_LIMIT_DEFAULT
    for item in (environ.get("INVPW_GC_LIMITS") or "").split(","):
        name, _, value = item.partition("=")
        try:
            limits[name.strip()] = max(1, int(value))
        except ValueError:
            continue
    return limits


class ProtocolException(Exception):
    """A failure the client should see as a protocol error, with its reason.

    ⛔ Prefer this over a bare `Exception` for anything the caller could have
    caused: the message travels all the way to the user, and "Element is not
    visible" is a different day than "TypeError".
    """


class ExpectNotMet(ProtocolException):
    """An `expect()` whose condition did not hold within its timeout.

    `details` travels as the reply's `errorDetails`, which is what the client
    reads (`_frame.py` `_expect`) to build its AssertionError: `received` is
    the last value seen, serialized, and `timedOut` says the wait ran out."""

    def __init__(self, message: str, details: Dict,
                 log: Optional[List[str]] = None) -> None:
        super().__init__(message)
        self.details = details
        #: The call's log, which the reply carries as `log` ([B341]).
        self.log = list(log or [])


class CallLog:
    """What one call did, line by line: the log Playwright's server keeps per
    call (its progress, `metadata.log`) and sends with a failure, where the
    client prints it under "Call log:" ([B341]). `title` is the one the
    client sent in the call's metadata, as Playwright's `renderTitleForCall`
    reads it, or the method's name."""

    def __init__(self, title: str) -> None:
        self.title = title
        self.lines: List[str] = []

    def log(self, line: str) -> None:
        self.lines.append(line)


#: The call this thread is answering. Set by `Server.handle` around the
#: dispatch; each worker thread answers one call at a time.
_CURRENT_CALL: "contextvars.ContextVar[CallLog]" = contextvars.ContextVar("call")


def current_call() -> CallLog:
    """The log of the call being answered, or a fresh one outside a call."""
    return _CURRENT_CALL.get(None) or CallLog("")


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
        self.announced()
        # And only then, as Playwright does: past the limit of its bucket the oldest are let go.
        server.collect_stale(self._gc_bucket)

    def announced(self) -> None:
        """The `__create__` has gone: where an object lets go of what only the
        announcement needed (a leaf's headers, a POST body). Runs once, from
        the constructor."""

    def gc_bucket(self) -> str:
        """The bucket this object is counted in (`GC_LIMITS`): its type."""
        return self.TYPE

    # ── what the protocol needs ─────────────────────────────────────────────
    @property
    def channel(self) -> Dict[str, str]:
        """How this object is referred to inside a result or an initializer."""
        return {"guid": self.guid}

    def emit(self, method: str, params: Optional[Dict] = None) -> None:
        """Fire a protocol event on this object. One that is already disposed says
        nothing: the client has dropped the guid, and naming it is an error there
        (Playwright's server ignores it too, outside its own tests)."""
        if self.disposed:
            return
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
        self.released()
        for child in self.server.descendants_of(self):
            child.disposed = True
            self.server.unregister(child)
            child.released()

    def released(self) -> None:
        """The object has left the registry, by its own `dispose` or by an
        ancestor's cascade (which does not call `dispose` on a descendant):
        where it lets go of what OTHER objects hold of it - an index, a list,
        a file. Runs once."""

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
        #: bucket -> the guids of its live objects, oldest first (a dict keeps the order).
        self._buckets: Dict[str, Dict[str, None]] = {}
        self.gc_limits = gc_limits_from_env()
        self.gc_min_age = GC_MIN_AGE_S
        self._lock = threading.Lock()
        self._transport: Any = None
        #: Guid "" is the Root the client creates on its own side. Nothing is
        #: registered for it here: it is only ever a PARENT.
        self.root_guid = ""
        self._on_shutdown: list = []
        #: What `shutdown` runs after every hook. See `after_shutdown`.
        self._after_shutdown: list = []

    # ── plumbing ────────────────────────────────────────────────────────────
    def attach(self, transport: Any) -> None:
        self._transport = transport

    def send_up(self, message: Dict) -> None:
        if self._transport is not None:
            self._transport.emit_message(message)

    def new_guid(self, type_name: str) -> str:
        with self._lock:
            counter = self._counters.setdefault(type_name, itertools.count(1))
            return "%s@%d" % (type_name.lower(), next(counter))

    def register(self, obj: Dispatcher) -> None:
        obj._born = time.monotonic()
        bucket = obj._gc_bucket = getattr(obj, "TYPE", "Unknown")
        if hasattr(obj, "gc_bucket"):
            bucket = obj._gc_bucket = obj.gc_bucket()
        with self._lock:
            self._objects[obj.guid] = obj
            self._buckets.setdefault(bucket, {})[obj.guid] = None

    #: How many disposed guids are remembered. Large enough for any burst of
    #: in-flight calls, small enough to be free.
    DISPOSED_MEMORY = 4096

    def unregister(self, obj: Dispatcher) -> None:
        with self._lock:
            self._objects.pop(obj.guid, None)
            members = self._buckets.get(getattr(obj, "_gc_bucket", None))
            if members is not None:
                members.pop(obj.guid, None)
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

    def gc_limit(self, bucket: str) -> int:
        return self.gc_limits.get(bucket, self.gc_limits["default"])

    def collect_stale(self, bucket: str) -> None:
        """Playwright's `maybeDisposeStaleDispatchers`: a bucket past its limit loses its
        oldest tenth, each with `__dispose__` {reason: "gc"} and the whole subtree silently
        (a client drops the children of a disposed object by itself)."""
        limit = self.gc_limit(bucket)
        with self._lock:
            members = self._buckets.get(bucket)
            if not members or len(members) <= limit:
                return
            oldest = list(itertools.islice(members, max(1, limit // 10)))
            young = time.monotonic() - self.gc_min_age
            victims = []
            for guid in oldest:
                obj = self._objects.get(guid)
                if obj is None:
                    continue
                if obj._born > young:
                    break  # the bucket is in birth order: the rest are younger
                victims.append(obj)
        self.dispose_many(victims, "gc")

    def dispose_many(self, victims: list, reason: Optional[str]) -> None:
        """Dispose objects and their subtrees with ONE walk of the registry (a walk per
        object would be quadratic in a bucket of thousands). A victim under another victim
        is dropped by the cascade and says nothing of its own: the client would have no
        guid for it."""
        ids = {id(v) for v in victims}
        params = {"reason": reason} if reason else {}
        for victim in victims:
            walker, covered = victim.parent, False
            while walker is not None:
                if id(walker) in ids:
                    covered = True
                    break
                walker = walker.parent
            if covered or victim.disposed:
                continue
            victim.disposed = True
            self.send_up({"guid": victim.guid, "method": "__dispose__", "params": params})
            self.unregister(victim)
            victim.released()
        with self._lock:
            everything = list(self._objects.values())
        for candidate in everything:
            walker, seen = candidate.parent, set()
            while walker is not None and id(walker) not in seen:
                if id(walker) in ids:
                    candidate.disposed = True
                    self.unregister(candidate)
                    candidate.released()
                    break
                seen.add(id(walker))
                walker = walker.parent

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

    def after_shutdown(self, step: Callable[[], None]) -> None:
        """Run ``step`` when the session ends, after every hook, in the order
        registered.

        ⛔ FOR WHAT THE BROWSER READS, and that is why it is not a hook: the
        hooks run in reverse, and the browser is the reader of the session's
        files. The profile is made BEFORE the browser starts and the upload
        directory after it, so as hooks the profile went while the browser
        still ran - and the comment beside it said the opposite (B223,
        measured on Windows: 6,091 profiles left in one %TEMP%). A step here
        runs once nothing registered as a hook is running any more, whenever
        it was registered.
        """
        self._after_shutdown.append(step)

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
        for step in self._after_shutdown:
            try:
                step()
            except Exception:
                pass
        self._after_shutdown.clear()

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
        metadata = message.get("metadata") or {}
        # ⛔ THE VERSIONS OF THE PROTOCOL ARE FOLDED HERE, once, before any
        # operation reads a parameter: see `variants.py`. A refusal of a
        # command this server does not serve is raised from there.
        params = variants.to_internal(method, params, metadata)
        token = _CURRENT_CALL.set(CallLog(metadata.get("title") or method or ""))
        try:
            return obj.call(method, params)
        finally:
            _CURRENT_CALL.reset(token)

    def handle_root(self, method: str, params: Dict) -> Any:
        raise ProtocolException("the root has no method %r" % method)
