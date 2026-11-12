"""The Playwright server of this package, on a WebSocket: `launch_server()`.

Any standard Playwright client can `connect()` to what this serves, with the
shape of Playwright's own `launchServer` / `connect`: one browser, started
once, and a WebSocket whose messages are the protocol's JSON (`transport.py`
documents the four shapes; `wire.py` is the JSON boundary).

THE DESIGN, AND WHAT IT COSTS.

  * ONE BROWSER, ONE REGISTRY, MANY CONNECTIONS. `launch_server()` launches the
    browser once (the same launch the in-process client does: seed, persona,
    geo, prefs, env, display, all composed by `InvisiblePlaywright`'s own
    methods) into a single `JugglerServer` registry, and every WebSocket
    connection talks to THAT registry. The alternative - a registry per
    connection over the same Juggler connection - was refused: each
    `BrowserDispatcher` enables the browser itself and adopts EVERY page
    target it hears about, so two of them on one connection would each build a
    page for the other's tabs.
  * OWNERSHIP IS THE ISOLATION. An object made while answering a call from
    client C belongs to C (an object made on a thread of its own - a popup -
    belongs to its parent's owner). A message about an object goes only to its
    owner; an object with no owner (BrowserType, LocalUtils, the Browser) is
    shared and its messages go to every client, unless they name an owned
    object, which decides. A client naming an object that is not its own gets
    "never created", as if it did not exist.
  * A CLIENT LEAVING CLOSES ITS CONTEXTS AND NOT THE BROWSER. So does its
    `Browser.close`: the browser belongs to `launch_server()` and ends with
    `RemoteServer.close()` (or when the engine dies, see `on_engine_closed`).
  * `initialize` IS ANSWERED HERE, not by `JugglerServer.op_initialize`: it
    announces BrowserType, LocalUtils, the Browser and the Playwright root with
    `preLaunchedBrowser` (the shape of `launchServer`), to that client only.

THE IDENTITY STAYS THE SERVER'S. Seed, persona, prefs and env are composed
once, at launch. The handshake reads `x-playwright-browser`, `?browser=`,
`x-playwright-launch-options`, `?launch-options=` and `x-playwright-proxy`, and
keeps them on the client record (`RemoteClient.handshake`); none of them
changes the launch. There is no version check, no 428 and no limit of ours: any
client is accepted, whatever it says it is. What a client can still choose is
what Playwright lets a client choose per context (`newContext` options); the
server fills the ones it omitted from the session's profile (viewport, screen,
locale, timezone), and `context_options_override=True` makes the server's win.

LIMITS OF THIS FIRST VERSION, said instead of hidden.
  * `launchPersistentContext` and `profile_dir` are not served.
  * `x-playwright-proxy` (exposeNetwork) is recorded, not honoured.
  * Downloads, file choosers and other fused types follow the in-process
    client's path and are not carried to a remote client.
  * `RemoteServer.process` is the core's handle for the browser process
    (`Connection._process`, a private attribute of the core: pid / poll / wait).
  * Page numbers (`pageNumber`, which the in-process client reserves) are drawn
    here from one counter for the whole browser, for clients that do not send it.
"""
from __future__ import annotations

import asyncio
import collections
import contextvars
import itertools
import json
import queue
import secrets
import sys
import threading
import time
from typing import Any, Callable, Dict, List, Optional
from urllib.parse import parse_qs, urlsplit

from . import memprobe, wire
from .dispatcher import ProtocolException
from .server import (BrowserDispatcher, BrowserTypeDispatcher, JugglerServer,
                     LocalUtilsDispatcher, RefusingDispatcher)
from .transport import _error_payload

#: The client whose call a worker thread is answering.
CURRENT_CLIENT: "contextvars.ContextVar[Optional[RemoteClient]]" = (
    contextvars.ContextVar("invpw_remote_client", default=None))

DEFAULT_HOST = "127.0.0.1"


def _channel_guids(value: Any, depth: int = 4):
    """The guids named inside a message's params (`{"guid": g}` channels)."""
    if depth < 0:
        return
    if isinstance(value, dict):
        if set(value) == {"guid"} and isinstance(value["guid"], str):
            yield value["guid"]
            return
        for item in value.values():
            yield from _channel_guids(item, depth - 1)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _channel_guids(item, depth - 1)


class RemoteClient:
    """One WebSocket connection."""

    def __init__(self, endpoint: "RemoteEndpoint", ws: Any,
                 loop: asyncio.AbstractEventLoop, handshake: Dict) -> None:
        self.endpoint = endpoint
        self.ws = ws
        self.loop = loop
        self.handshake = handshake
        self.alive = True
        self.initialized = False
        self._outbox: "asyncio.Queue" = asyncio.Queue()

    def post(self, text: str) -> None:
        """Queue one text for the socket, from any thread. FIFO: the writer
        task sends them in the order they were posted, which is what keeps
        `__create__` before the reply that names it."""
        if not self.alive:
            return
        try:
            self.loop.call_soon_threadsafe(self._outbox.put_nowait, text)
        except RuntimeError:
            pass  # the loop is closed: nobody is left to read it

    def post_message(self, message: Dict) -> None:
        try:
            self.post(wire.dumps(message))
        except wire.WireError as problem:
            self.endpoint.wire_errors.append(problem)
            msg_id = message.get("id") if isinstance(message, dict) else None
            if msg_id is not None and "error" not in message:
                self.post(json.dumps({"id": msg_id, "error": {"error": {
                    "name": "Error", "stack": "",
                    "message": "the reply is not JSON: %s" % problem}}}))

    async def pump(self) -> None:
        while True:
            text = await self._outbox.get()
            if text is None:
                return
            try:
                await self.ws.send(text)
            except Exception:
                return
            trim = self.endpoint.trim
            if trim is not None and len(text) >= memprobe.TRIM_BYTES:
                del text  # the reply's text goes back to the allocator first
                self._trim(trim)

    @staticmethod
    def _trim(trim: Callable[[], None]) -> None:
        try:
            trim()
        except Exception:
            pass

    def stop_pump(self) -> None:
        self.alive = False
        try:
            self.loop.call_soon_threadsafe(self._outbox.put_nowait, None)
        except RuntimeError:
            pass


class _SharedServer(JugglerServer):
    """The registry of a served browser: it knows who owns what."""

    def __init__(self) -> None:
        super().__init__()
        self.endpoint: Optional["RemoteEndpoint"] = None

    def register(self, obj: Any) -> None:
        obj._owner = CURRENT_CLIENT.get(None) or getattr(
            getattr(obj, "parent", None), "_owner", None)
        super().register(obj)

    def send_up(self, message: Dict) -> None:
        if self.endpoint is not None:
            self.endpoint.route(message)

    def unregister(self, obj: Any) -> None:
        super().unregister(obj)
        if obj.TYPE == "BrowserContext" and self.endpoint is not None:
            self.endpoint.context_closed()


class SharedBrowserDispatcher(BrowserDispatcher):
    """The browser of a served launch: a client's `close` ends ITS contexts."""

    def op_close(self, params: Dict) -> Any:
        client = CURRENT_CLIENT.get(None)
        for context in list(self.contexts):
            if not context.disposed and getattr(context, "_owner", None) is client:
                try:
                    context.op_close({})
                except Exception:
                    pass
        return None


class SharedBrowserType(BrowserTypeDispatcher):
    browser_class = SharedBrowserDispatcher


class UnservedBrowserType(RefusingDispatcher):
    """chromium and webkit: in the Playwright initializer because the protocol requires every
    browser type there (a client's constructor reads all three and does not check for a missing
    one), and every call on them refuses with the reason. Only firefox is served."""

    TYPE = "BrowserType"
    REASON = "this server serves one browser, the firefox engine, and not %s"

    def __init__(self, server: Any, name: str) -> None:
        super().__init__(server, None, {"executablePath": "", "name": name})
        self.REASON = self.REASON % name


class UnservedAndroid(RefusingDispatcher):
    """Required by the 1.62 Playwright initializer; Android is outside what is served."""

    TYPE = "Android"
    REASON = "Android devices are not served"

    def __init__(self, server: Any) -> None:
        super().__init__(server, None, {})


class UnservedElectron(RefusingDispatcher):
    """Required by the 1.62 Playwright initializer; Electron is outside what is served."""

    TYPE = "Electron"
    REASON = "Electron is not served"

    def __init__(self, server: Any) -> None:
        super().__init__(server, None, {})


class RemoteEndpoint:
    """The WebSocket side: serves an already-launched browser's registry.

    Knows nothing of how the browser was launched (`launch_server` does that),
    so the unit tests give it a registry with a stand-in Browser.
    """

    WORKERS = 8

    def __init__(self, server: _SharedServer, browser_type: BrowserTypeDispatcher,
                 browser: Any, *, host: str = DEFAULT_HOST, port: int = 0,
                 path: Optional[str] = None,
                 context_defaults: Optional[Dict] = None,
                 context_options_override: bool = False) -> None:
        self.server = server
        self.browser_type = browser_type
        self.browser = browser
        self.host = host
        self.requested_port = port
        self.token = path or secrets.token_urlsafe(18)
        self.context_defaults = dict(context_defaults or {})
        self.context_options_override = context_options_override
        self.clients: List[RemoteClient] = []
        #: The last few: each one holds its traceback, and a client that keeps
        #: asking for what is not JSON must not grow it for the life of the browser.
        self.wire_errors: "collections.deque" = collections.deque(maxlen=32)
        self.port: Optional[int] = None
        self._pages = itertools.count(0)
        self._jobs: "queue.Queue" = queue.Queue()
        self._workers: List[threading.Thread] = []
        self._thread: Optional[threading.Thread] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._stop: Optional[asyncio.Event] = None
        self._ready = threading.Event()
        self._start_error: Optional[BaseException] = None
        self._lock = threading.Lock()
        self._closed = False
        #: The two opt-in switches (`memprobe.py`): None unless the environment asks.
        self.probe = memprobe.probe_from_env(self)
        self.trim = memprobe.trimmer_from_env()
        #: The rest of what the Playwright initializer must name (spec 1.62: chromium,
        #: firefox, webkit, android, electron are all required), created once.
        self.chromium = UnservedBrowserType(server, "chromium")
        self.webkit = UnservedBrowserType(server, "webkit")
        self.android = UnservedAndroid(server)
        self.electron = UnservedElectron(server)
        server.endpoint = self

    # ── where it listens ────────────────────────────────────────────────────
    @property
    def ws_endpoint(self) -> str:
        host = self.host
        if host in ("0.0.0.0", "", "::"):
            host = "127.0.0.1"
        if ":" in host and not host.startswith("["):
            host = "[%s]" % host
        return "ws://%s:%d/%s" % (host, self.port or 0, self.token)

    def start(self) -> None:
        for i in range(self.WORKERS):
            t = threading.Thread(target=self._work, daemon=True,
                                 name="invpw-remote-%d" % i)
            t.start()
            self._workers.append(t)
        self._thread = threading.Thread(target=self._serve, daemon=True,
                                        name="invpw-remote-ws")
        self._thread.start()
        self._ready.wait()
        if self._start_error is not None:
            self.close()
            raise self._start_error
        if self.probe is not None:
            self.probe.start()

    def context_closed(self) -> None:
        """A context left the registry: the probe takes a line, the allocator a trim."""
        if self.probe is not None:
            self.probe.write("context_closed")
        if self.trim is not None:
            RemoteClient._trim(self.trim)

    def _serve(self) -> None:
        loop = asyncio.new_event_loop()
        self._loop = loop
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self._main())
        except BaseException as failure:  # noqa: BLE001
            self._start_error = self._start_error or failure
        finally:
            self._ready.set()
            try:
                loop.close()
            except Exception:
                pass

    async def _main(self) -> None:
        try:
            from websockets.asyncio.server import serve
        except ImportError as failure:  # pragma: no cover
            # Spelled by the tool that installed this package, as `_engine.py` does.
            from invisible_core.installer import install
            from invisible_core.pin import format_command
            raise RuntimeError(
                "serving a browser needs the `websockets` package: "
                + format_command(install("invisible-playwright", "websockets"))) from failure
        self._stop = asyncio.Event()
        try:
            async with serve(self._handler, self.host, self.requested_port,
                             process_request=self._process_request,
                             max_size=None, ping_interval=None,
                             compression=None) as listener:
                self.port = listener.sockets[0].getsockname()[1]
                self._ready.set()
                await self._stop.wait()
                for client in list(self.clients):
                    try:
                        await client.ws.close(1001, "the server is closing")
                    except Exception:
                        pass
        except BaseException as failure:  # noqa: BLE001
            self._start_error = failure
            raise

    # ── the handshake ───────────────────────────────────────────────────────
    def _process_request(self, connection: Any, request: Any) -> Any:
        """Refuse a wrong path with a readable reason, and read what a client
        says about itself. Never raises: a failure is an HTTP answer."""
        from http import HTTPStatus
        try:
            split = urlsplit(request.path)
            if split.path.strip("/") != self.token:
                return connection.respond(
                    HTTPStatus.FORBIDDEN,
                    "this path is not served here: the endpoint is the one "
                    "launch_server() returned, secret included\n")
            query = parse_qs(split.query)
            headers = request.headers
            handshake: Dict[str, Any] = {
                "browser": (headers.get("x-playwright-browser")
                            or (query.get("browser") or [None])[0]),
                "proxy": headers.get("x-playwright-proxy"),
                "launch_options": None,
            }
            raw = (headers.get("x-playwright-launch-options")
                   or (query.get("launch-options") or [None])[0])
            if raw:
                try:
                    handshake["launch_options"] = json.loads(raw)
                except ValueError as bad:
                    return connection.respond(
                        HTTPStatus.BAD_REQUEST,
                        "launch-options is not JSON (%s); the server's launch "
                        "is fixed anyway, so send valid JSON or none\n" % bad)
            connection.invpw_handshake = handshake
            return None
        except Exception as failure:  # noqa: BLE001 - never crash a handshake
            return connection.respond(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                "the handshake failed: %s\n" % failure)

    # ── one connection ──────────────────────────────────────────────────────
    async def _handler(self, ws: Any) -> None:
        loop = asyncio.get_running_loop()
        client = RemoteClient(self, ws, loop,
                              getattr(ws, "invpw_handshake", None) or {})
        with self._lock:
            self.clients.append(client)
        pump = asyncio.create_task(client.pump())
        try:
            async for raw in ws:
                try:
                    text = raw.decode("utf-8") if isinstance(raw, bytes) else raw
                    message = json.loads(text)
                    if not isinstance(message, dict):
                        raise ValueError("a message is a JSON object, got %s"
                                         % type(message).__name__)
                except ValueError as bad:
                    await ws.close(1003, ("not a protocol message: %s" % bad)[:120])
                    break
                self._jobs.put((client, message))
        except Exception:
            pass
        finally:
            client.stop_pump()
            with self._lock:
                if client in self.clients:
                    self.clients.remove(client)
            pump.cancel()
            self._jobs.put((client, None))  # the leaving, in the pool

    # ── the worker side ─────────────────────────────────────────────────────
    def _work(self) -> None:
        while True:
            item = self._jobs.get()
            if item is None:
                return
            client, message = item
            token = CURRENT_CLIENT.set(client)
            try:
                if message is None:
                    self._client_gone(client)
                else:
                    self._answer(client, message)
            except Exception:
                pass
            finally:
                CURRENT_CLIENT.reset(token)

    def _answer(self, client: RemoteClient, message: Dict) -> None:
        msg_id = message.get("id")
        try:
            result = self._dispatch(client, message)
        except Exception as failure:  # noqa: BLE001
            self._reply_error(client, msg_id, failure)
        else:
            self._reply(client, msg_id, result)

    def _dispatch(self, client: RemoteClient, message: Dict) -> Any:
        guid = message.get("guid")
        method = message.get("method")
        if guid == self.server.root_guid and method == "initialize":
            return self._initialize(client)
        obj = self.server._objects.get(guid)
        owner = getattr(obj, "_owner", None)
        if obj is not None and owner is not None and owner is not client:
            raise ProtocolException(
                "no object %r to answer %r: it was never created"
                % (guid, method))
        return self.server.handle(self._prepared(message))

    def _prepared(self, message: Dict) -> Dict:
        """What a standard client does not say and this server needs: the
        page's number, and the session's defaults for a context."""
        method = message.get("method")
        if method not in ("newPage", "newContext"):
            return message
        params = dict(message.get("params") or {})
        if method == "newPage" and "pageNumber" not in params:
            params["pageNumber"] = next(self._pages)
        if message.get("guid") == self.browser.guid:
            for key, value in self.context_defaults.items():
                if key in ("viewport", "screen") and params.get("noDefaultViewport"):
                    continue
                if self.context_options_override or key not in params:
                    params[key] = value
        return dict(message, params=params)

    def _initialize(self, client: RemoteClient) -> Dict:
        """The announce a launched server makes to a client, to it alone and
        in the order that names nothing the client has not seen."""
        client.initialized = True
        for obj in (self.browser_type, self.chromium, self.webkit, self.android,
                    self.electron, self._utils(), self.browser):
            parent = obj.parent.guid if obj.parent is not None else self.server.root_guid
            client.post_message({"guid": parent, "method": "__create__",
                                 "params": {"type": obj.TYPE, "guid": obj.guid,
                                            "initializer": obj.initializer}})
        client.post_message({"guid": self.server.root_guid, "method": "__create__",
                             "params": {"type": "Playwright", "guid": "Playwright",
                                        "initializer": self._playwright_initializer()}})
        return {"playwright": {"guid": "Playwright"}}

    def _utils(self) -> Any:
        for obj in list(self.server._objects.values()):
            if isinstance(obj, LocalUtilsDispatcher):
                return obj
        raise ProtocolException("this server has no LocalUtils")

    def _playwright_initializer(self) -> Dict:
        return {"chromium": self.chromium.channel,
                "firefox": self.browser_type.channel,
                "webkit": self.webkit.channel,
                "android": self.android.channel,
                "electron": self.electron.channel,
                "utils": self._utils().channel,
                "preLaunchedBrowser": self.browser.channel}

    def _reply(self, client: RemoteClient, msg_id: Any, result: Any) -> None:
        if msg_id is None:
            return
        out: Dict[str, Any] = {"id": msg_id}
        if result is not None:
            out["result"] = result
        client.post_message(out)

    def _reply_error(self, client: RemoteClient, msg_id: Any,
                     failure: BaseException) -> None:
        if msg_id is None:
            return
        out: Dict[str, Any] = {"id": msg_id,
                               "error": {"error": _error_payload(failure)}}
        details = getattr(failure, "details", None)
        if details is not None:
            out["errorDetails"] = details
        log = getattr(failure, "log", None)
        if log:
            out["log"] = list(log)
        client.post_message(out)

    # ── the server's messages, to whoever they are for ──────────────────────
    def route(self, message: Dict) -> None:
        for client in self._targets(message):
            client.post_message(message)

    def _owner_of(self, guid: Any) -> Any:
        return getattr(self.server._objects.get(guid), "_owner", None)

    def _targets(self, message: Dict) -> List[RemoteClient]:
        params = message.get("params") or {}
        subjects = [params.get("guid") if message.get("method") in
                    ("__create__", "__adopt__") else None, message.get("guid")]
        owner = None
        for guid in subjects:
            owner = self._owner_of(guid)
            if owner is not None:
                break
        if owner is None:
            for guid in _channel_guids(params):
                owner = self._owner_of(guid)
                if owner is not None:
                    break
        if owner is not None:
            return [owner] if owner.alive else []
        with self._lock:
            return [c for c in self.clients if c.alive and c.initialized]

    def _client_gone(self, client: RemoteClient) -> None:
        """A connection ended: its contexts end with it, the browser does not."""
        for obj in list(self.server._objects.values()):
            if (getattr(obj, "_owner", None) is client
                    and obj.TYPE == "BrowserContext" and not obj.disposed):
                try:
                    obj.op_close({})
                except Exception:
                    pass

    # ── the end ─────────────────────────────────────────────────────────────
    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
        if self.probe is not None:
            self.probe.stop()
        loop, stop = self._loop, self._stop
        if loop is not None and stop is not None:
            try:
                loop.call_soon_threadsafe(stop.set)
            except RuntimeError:
                pass
        if self._thread is not None and self._thread is not threading.current_thread():
            self._thread.join(timeout=10)
        for _ in self._workers:
            self._jobs.put(None)
        for t in self._workers:
            if t is not threading.current_thread():
                t.join(timeout=5)


class RemoteServer:
    """What `launch_server()` returns: the shape of Playwright's BrowserServer
    (`ws_endpoint`, `close()`, `process`) plus what the session decided."""

    def __init__(self, endpoint: RemoteEndpoint, *, session: Any = None,
                 browser: Any = None, on_engine_closed: Optional[Callable[[], None]] = None,
                 notes: Optional[List[str]] = None) -> None:
        self._endpoint = endpoint
        self._session = session
        self._browser = browser
        self._on_engine_closed = on_engine_closed
        self._closing = threading.Event()
        self._closed = False
        self._close_lock = threading.Lock()
        #: Options that were asked for and could not be honoured, with why.
        self.notes: List[str] = list(notes or [])
        self._monitor: Optional[threading.Thread] = None

    # ── BrowserServer ───────────────────────────────────────────────────────
    @property
    def ws_endpoint(self) -> str:
        return self._endpoint.ws_endpoint

    @property
    def process(self) -> Any:
        """The browser process (pid / poll / wait), or None for a registry with
        no process of its own."""
        conn = getattr(self._browser, "conn", None)
        return getattr(conn, "_process", None)

    @property
    def seed(self) -> Optional[int]:
        return getattr(self._session, "seed", None)

    @property
    def exit_ip(self) -> Optional[str]:
        """The egress address measured at launch (through the proxy, if any)."""
        return getattr(self._session, "_webrtc_egress_ip", None)

    @property
    def host(self) -> str:
        return self._endpoint.host

    @property
    def port(self) -> Optional[int]:
        return self._endpoint.port

    def _watch_engine(self) -> None:
        process = self.process
        if process is None or self._on_engine_closed is None:
            return

        def watch() -> None:
            while not self._closing.is_set():
                try:
                    dead = process.poll() is not None
                except Exception:
                    dead = True
                if dead:
                    if not self._closing.is_set():
                        try:
                            self._on_engine_closed()
                        except Exception:
                            pass
                    return
                time.sleep(0.25)

        self._monitor = threading.Thread(target=watch, daemon=True,
                                         name="invpw-engine-watch")
        self._monitor.start()

    def close(self) -> None:
        """Everything: the sockets, the browser and its tree, the hidden
        display and the threads. Idempotent."""
        with self._close_lock:
            if self._closed:
                return
            self._closed = True
        self._closing.set()
        self._endpoint.close()
        try:
            self._endpoint.server.shutdown()
        except Exception:
            pass
        if self._session is not None:
            try:
                self._session._teardown()
            except Exception:
                pass
        if self._monitor is not None and self._monitor is not threading.current_thread():
            self._monitor.join(timeout=5)

    kill = close

    def __enter__(self) -> "RemoteServer":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    async def aclose(self) -> None:
        await asyncio.get_running_loop().run_in_executor(None, self.close)


# ── the launch ──────────────────────────────────────────────────────────────

def _context_defaults(session: Any, downloads: Optional[str]) -> Dict:
    """The session's own defaults for a context, in the protocol's spelling:
    what the wrapper's `_default_context_kwargs` gives its in-process users."""
    kwargs = session._default_context_kwargs()
    out: Dict[str, Any] = {}
    for key, value in kwargs.items():
        out["timezoneId" if key == "timezone_id" else key] = value
    if downloads:
        out["acceptDownloads"] = downloads
    return out


def _launch_params(session: Any, *, virtual_display: bool, headless: bool,
                   timeout_ms: Optional[int], notes: List[str]) -> Dict:
    """`InvisiblePlaywright.__enter__` up to the launch, as protocol params.

    The same calls in the same order, so the served browser is the one the
    wrapper would have launched: geo (egress, timezone, locale), the
    hidden display, the prefs, the proxy, the session token and the env."""
    from invisible_core import configure_proxy, prepare_session_geo
    from invisible_core import make_virtual_display
    from invisible_playwright._engine import resolve_executable
    from invisible_playwright._reaper import SessionToken

    geo = prepare_session_geo(session._timezone, session._proxy,
                              session._locale_requested, seed=session.seed,
                              udp_stun_servers=session._udp_stun_servers)
    session._timezone = geo.timezone
    session._locale = geo.locale
    session._webrtc_egress_ip = geo.egress_ip
    session._geolocation = geo.geolocation_override()
    executable = resolve_executable(session._binary_path)
    pw_headless = bool(headless)
    if not headless and virtual_display:
        if sys.platform == "win32":
            notes.append("virtual_display ignored: Windows has no virtual "
                         "display in this server (the browser runs on the "
                         "caller's desktop)")
        else:
            vd = make_virtual_display()
            if vd is None:
                notes.append("virtual_display ignored: no display server found")
            else:
                vd.start()
                session._virtual_display = vd
    prefs = session._build_prefs()
    playwright_proxy = configure_proxy(session._proxy, prefs)
    session._session_token = SessionToken.mint()
    env = session._build_env(prefs)
    params: Dict[str, Any] = {
        "executablePath": str(executable),
        "headless": pw_headless,
        "firefoxUserPrefs": prefs,
        "args": list(session._extra_args),
        "env": [{"name": k, "value": str(v)} for k, v in env.items()],
    }
    if playwright_proxy:
        params["proxy"] = playwright_proxy
    if timeout_ms is not None:
        params["timeout"] = timeout_ms
    return params


def launch_server(host: str = DEFAULT_HOST, port: int = 0, *,
                  path: Optional[str] = None,
                  seed: Optional[int] = None,
                  pin: Optional[Dict[str, Any]] = None,
                  proxy: Optional[Dict[str, str]] = None,
                  headless: bool = False,
                  virtual_display: bool = True,
                  humanize: Any = False,
                  motion_budget_s: Optional[float] = None,
                  show_cursor: Optional[bool] = None,
                  preload_newtab: Optional[bool] = None,
                  locale: str = "auto",
                  timezone: str = "",
                  extra_args: Optional[List[str]] = None,
                  extra_prefs: Optional[Dict[str, Any]] = None,
                  binary_path: Optional[str] = None,
                  downloads: Optional[str] = None,
                  context_options: Optional[Dict[str, Any]] = None,
                  context_options_override: bool = False,
                  launch_timeout_ms: Optional[int] = 60000,
                  on_engine_closed: Optional[Callable[[], None]] = None,
                  udp_stun_servers: Optional[List[str]] = None,
                  ) -> RemoteServer:
    """Launch ONE browser and serve it on a WebSocket; return when it listens
    and the browser is ready.

    host / port / path  where it listens; `127.0.0.1`, a random port and a
                        random secret path by default. The caller chooses an
                        external address.
    seed                None draws a new one; `server.seed` reads it back.
    pin                 fingerprint fields forced (see docs/pinning.md).
    proxy               `{"server", "username", "password"}`, fixed after the
                        launch; the egress (exit IP -> timezone, locale
                        "auto"; the GeoIP database offline from
                        STEALTHFOX_GEOIP_MMDB) is measured here through it.
    headless            True = true headless; False = headed.
    virtual_display     with headless=False: a private virtual display (Xvfb
                        with its own launch env). Windows: ignored, with the
                        reason in `server.notes`.
    humanize            False (default) / True / seconds; `motion_budget_s`
                        gives the cap in seconds (with the seed it rides in
                        SESSION_SEED_PREF / MOTION_BUDGET_PREF).
    downloads           "deny" cancels every download of every context.
    context_options     protocol-spelled options (`viewport`, `locale`, ...)
                        applied to every context a client creates, on top of
                        the profile's geometry, timezone and locale; filled
                        where the client says nothing, unless
                        `context_options_override=True`.
    on_engine_closed    called, from another thread, when the engine dies
                        (not when `close()` ends it).
    udp_stun_servers    where a SOCKS5 proxy's UDP exit is measured from: "ip:port" IPv4 literals, None for the
                        two public defaults. Kept on the session, so the launch and every context proxy a client
                        later gives measure from the same list. Python only: nothing reaches the browser.
    The engine is the sealed one (`ensure_binary`, INVISIBLE_PLAYWRIGHT_ENGINE_DIR)
    and the launch FAILS if the version it reports is not the seal's.
    """
    from invisible_playwright import InvisiblePlaywright
    from invisible_playwright._engine import assert_wire_version

    if humanize and motion_budget_s:
        humanize = float(motion_budget_s)
    session = InvisiblePlaywright(
        seed, pin=pin, headless=headless, proxy=proxy, extra_args=extra_args,
        humanize=humanize, locale=locale, timezone=timezone,
        extra_prefs=extra_prefs, binary_path=binary_path,
        show_cursor=show_cursor, udp_stun_servers=udp_stun_servers,
        preload_newtab=preload_newtab)
    notes: List[str] = []
    server = _SharedServer()
    endpoint: Optional[RemoteEndpoint] = None
    try:
        params = _launch_params(session, virtual_display=virtual_display,
                                headless=headless, timeout_ms=launch_timeout_ms,
                                notes=notes)
        browser_type = SharedBrowserType(server)
        LocalUtilsDispatcher(server)
        browser = server.object(
            browser_type.op_launch(params)["browser"]["guid"])
        session._bind_process_tree()

        class _Reported:  # what assert_wire_version reads off a Browser
            version = browser.initializer.get("version", "")
        assert_wire_version(_Reported)

        defaults = _context_defaults(session, downloads)
        defaults.update(context_options or {})
        endpoint = RemoteEndpoint(
            server, browser_type, browser, host=host, port=port, path=path,
            context_defaults=defaults,
            context_options_override=context_options_override)
        endpoint.start()
    except BaseException:
        if endpoint is not None:
            endpoint.close()
        try:
            server.shutdown()
        except Exception:
            pass
        try:
            session._teardown()
        except Exception:
            pass
        raise
    result = RemoteServer(endpoint, session=session, browser=browser,
                          on_engine_closed=on_engine_closed, notes=notes)
    result._watch_engine()
    return result


async def launch_server_async(*args: Any, **options: Any) -> RemoteServer:
    """`launch_server`, off the loop: the launch blocks for seconds."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(
        None, lambda: launch_server(*args, **options))
