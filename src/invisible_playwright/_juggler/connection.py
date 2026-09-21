"""The connection to Juggler: the pipe, with no Node in between.

THE CONTRACT, read from `juggler/pipe/nsRemoteDebuggingPipe.cpp` and not
guessed:

  - messages are JSON delimited by a ZERO BYTE, not a newline
    (`ReaderLoop` accumulates until it finds `'\\0'`);
  - on POSIX the descriptors are **hardwired to 3 and 4**: `const int
    readFD = 3; const int writeFD = 4;`. They are not negotiated;
  - on Windows there are NO descriptors: they are HANDLEs read from the
    environment, `GetEnvironmentVariableA("PW_PIPE_READ", ...)` plus
    `atoi`, so the value must be passed in DECIMAL and the handle must be
    made inheritable;
  - the names are from the BROWSER's point of view: its `PW_PIPE_READ` is
    what IT reads from, i.e. where WE write.

⛔ And the flag `-juggler-pipe` must appear on the command line, or on
Windows the handles never get armed at all and the pipe breaks at the
launcher -> parent transition.

⛔ THE READINESS SIGNAL DOES NOT TRAVEL OVER THE PIPE. The browser prints
`Juggler listening to the pipe` on stdout, and that line comes out of a
`dump()` that a `MOZILLA_OFFICIAL` build disables: a disabled `dump()`
RETURNS SUCCESSFULLY without writing. The fix lives in the Firefox source
(`30-upstream-playwright-patches.md`), not here. Anyone reading a long
timeout at launch should look there first.

State: first piece. Opens the connection, sends commands, receives
responses and events. Not yet a client: no lifecycle, no frames, no
actionability.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from typing import Any, Callable, Optional

NUL = b"\x00"
_READY = "Juggler listening to the pipe"


class ProtocolError(RuntimeError):
    """The browser refused a command. Carries its message, not ours.

    ⛔ `checkScheme` is CLOSED WORLD: an undeclared field is not ignored,
    it is rejected, and it happens at RUNTIME. The browser's message is
    the only thing that says which field.
    """


class TargetClosedError(Exception):
    """The thing a call was aimed at is gone: a disposed object, or the
    browser itself, whose pipe has closed.

    ⛔ THE CLASS NAME IS THE CONTRACT, not decoration. `reply_error` puts
    `type(failure).__name__` in the error payload, and `_helper.parse_error`
    turns that exact string into the fork's `TargetClosedError` on the client
    - which is the ONLY thing `_page.py`'s `close()` swallows:

        except Exception as e:
            if is_target_closed_error(e): return

    Anything else propagates. So closing a page whose context was closed first
    - `context.close()` cascades, then a fixture teardown calls `page.close()`,
    which is an ordinary shape and not a misuse - raised a hard error here on
    2026-08-28, the day closed pages started being disposed at all.

    ⛔ AND IT LIVES HERE, ONE LAYER DOWN FROM WHERE IT WAS BORN, BECAUSE A
    CLOSED PIPE IS A CLOSED TARGET TOO. Until 0.15.0 the dispatcher raised
    this for a disposed guid, while the connection answered a pipe that had
    closed with a nameless error - `the pipe closed`, `the pipe is closed` -
    which `parse_error` turned into the fork's plain `Error`. The same fact,
    the browser is gone, reached a caller as two different classes, and the
    caller that had to tell "the page refused" from "the browser died" (the
    AIHawk server's retry) was reduced to matching the sentences. Measured
    2026-09-14. One class, raised by both layers, and exported from the
    public API so a caller can catch the TYPE.
    """


class EventListeners:
    """Who is subscribed to a connection's events, and how one is delivered.

    ⛔ ONE IMPLEMENTATION, USED BY THE REAL CONNECTION AND BY THE FAKES. The
    tests stand a stub connection in front of `Lifecycle` and `InjectedScript`,
    and if that stub carried its own idea of what subscribing means, the tests
    would be measuring the stub. Inheriting it is what makes a green test say
    something about the code that ships.
    """

    def __init__(self) -> None:
        #: The subscribers, in the order they registered.
        #:
        #: ⛔ A LIST, BECAUSE THE CHAIN OF CLOSURES IT REPLACES WAS RECURSIVE
        #: AND UNBOUNDED. Every subscriber used to capture the previous
        #: `on_event` and install itself, so delivering one event called the
        #: whole chain nested: the Python stack depth WAS the number of
        #: subscribers. Nothing ever unsubscribed either, and a page registers
        #: three (Lifecycle, InjectedScript, PageDispatcher), so the chain grew
        #: by 3 per page for the life of the browser. Measured 2026-08-28: page
        #: 0 sat at 4 links, page 325 at 979, and just past there the default
        #: recursion limit of 1000 was crossed and EVERY event began raising
        #: RecursionError. The read loop correctly refuses to die on a raising
        #: handler, so the failure was silent: commands still got their
        #: replies, `Browser.attachedToTarget` was never recorded, and
        #: `new_page` timed out after 20s waiting for a session, forever after.
        #: Playwright's own suite hit it at 63% and every one of the ~150 tests
        #: past that point failed identically.
        self._listeners: list = []
        self._listeners_lock = threading.Lock()
        #: Every exception a listener raised, as "method: message". Bounded at
        #: 32 so a handler that raises on every event cannot eat memory.
        #: ⛔ READ THIS IN A TEST. An empty event list plus an empty
        #: `handler_errors` means the browser sent nothing; an empty event list
        #: with entries HERE means your handler is broken, and those are two
        #: completely different bugs that used to look identical.
        self.handler_errors: list = []

    def add_listener(self, fn) -> None:
        """Subscribe to every event. Registering twice is a no-op, not two
        deliveries: a handler called twice for one event announces a frame
        twice, and that reads as the browser having sent it twice."""
        with self._listeners_lock:
            if fn not in self._listeners:
                self._listeners.append(fn)

    def remove_listener(self, fn) -> None:
        """Unsubscribe. ⛔ CALLING THIS IS NOT OPTIONAL HOUSEKEEPING: it is
        the half of the fix that keeps the list from growing without bound,
        and a subscriber that forgets it leaks for the life of the browser -
        which is the defect this registry exists to close, reintroduced."""
        with self._listeners_lock:
            try:
                self._listeners.remove(fn)
            except ValueError:
                pass

    def dispatch_event(self, method: str, params: dict,
                       session: Optional[str]) -> None:
        """Deliver one event to every subscriber, iteratively.

        ⛔ ONE LISTENER'S FAILURE MUST NOT COST THE OTHERS THEIR EVENT. In the
        chain this replaces, a subscriber that raised took every subscriber
        BELOW it down with the same event, because the delivery to the rest
        was the last statement of the one that raised. Here each call is
        isolated: the failure is recorded and the loop carries on.
        """
        with self._listeners_lock:
            subscribers = list(self._listeners)
        for fn in subscribers:
            try:
                fn(method, params, session)
            except Exception as failure:
                if len(self.handler_errors) < 32:
                    self.handler_errors.append("%s: %s" % (method, failure))


class Connection(EventListeners):
    """A pipe to an already-launched Firefox."""

    def __init__(self, to_browser, from_browser, process=None):
        super().__init__()
        self._to_browser = to_browser      # where WE write
        self._from_browser = from_browser  # where WE read
        self._process = process
        self._next_id = 0
        self._pending: dict[int, list] = {}
        self._lock = threading.Lock()
        # ⛔ ONE WRITER AT A TIME ON THE PIPE. `send` used to write outside
        # `_lock`, which was fine while every command came from one thread.
        # The screencast acknowledges frames from the READER thread (see
        # `post`) while the caller's thread is dispatching input, and two
        # `os.write` calls interleaving on one pipe splice two JSON messages
        # into one unparseable line - a failure that shows up as the browser
        # ignoring a command, not as an error here.
        self._write_lock = threading.Lock()
        self._closed = False
        self._error: Optional[BaseException] = None
        self._reader = threading.Thread(target=self._read_loop, daemon=True)
        self._reader.start()

    # ── reading ─────────────────────────────────────────────────────────────
    def _read_loop(self) -> None:
        buffer = b""
        try:
            while not self._closed:
                chunk = os.read(self._from_browser, 65536)
                if not chunk:
                    break
                buffer += chunk
                while NUL in buffer:
                    raw, buffer = buffer.split(NUL, 1)
                    if raw:
                        self._deliver(raw)
        except OSError as e:
            self._error = e
        finally:
            self._closed = True
            # ⛔ Whoever is waiting must be WOKEN UP, not left to the
            # timeout: a closed pipe is information, and delivering it
            # thirty seconds later as "no response" hides what happened.
            with self._lock:
                pending = list(self._pending.values())
                self._pending.clear()
            for ready, box in pending:
                # Named, so `send` raises the closed-target class and the
                # client sees the browser is GONE rather than a refusal.
                box.append({"error": {"name": "TargetClosedError",
                                      "message": "the pipe closed"}})
                ready.set()

    def _deliver(self, raw: bytes) -> None:
        try:
            msg = json.loads(raw.decode("utf-8"))
        except Exception:
            return
        msg_id = msg.get("id")
        if msg_id is None:
            method = msg.get("method")
            if method:
                # ⛔ The `sessionId` must be delivered together with the
                # event. Without it, the events of TWO pages open at the
                # same time are indistinguishable, and whoever is
                # waiting for a `load` gets the other tab's. It is not a
                # rare case: it is what happens on the second
                # `new_page()`.
                # ⛔ SWALLOWING A HANDLER'S FAILURE IS RIGHT, LOSING IT IS
                # NOT. `dispatch_event` isolates each subscriber and records
                # what it raised in `handler_errors`, because a handler that
                # raises must not kill the read loop - one bad callback would
                # take the whole connection down - while a bare `pass` makes
                # the failure INVISIBLE. That is not a theory twice over:
                # `test_python_talks_to_juggler_without_node` installed a
                # two-argument lambda where this call passes three, so every
                # delivery raised TypeError and the event list stayed empty;
                # and the recursion the listener registry replaced was found
                # only by running Playwright's suite, because it too landed
                # here and was recorded into a list nobody read.
                self.dispatch_event(method, msg.get("params") or {},
                                    msg.get("sessionId"))
            return
        with self._lock:
            entry = self._pending.pop(msg_id, None)
        if entry is not None:
            ready, box = entry
            box.append(msg)
            ready.set()

    # ── writing ─────────────────────────────────────────────────────────────
    def send(self, method: str, params: Optional[dict] = None,
             session: Optional[str] = None, timeout: float = 30.0) -> Any:
        if self._closed:
            raise TargetClosedError("the pipe is closed: %s" % (self._error or ""))
        with self._lock:
            self._next_id += 1
            msg_id = self._next_id
            # An EVENT, not a `sleep` loop: the wakeup comes from the
            # reader thread when the response is there, instead of from
            # the next tick of a `time.sleep(0.002)`.
            #
            # ⛔ And what this line did NOT fix also belongs here,
            # because the first draft of this comment cited a false
            # measurement. It said "a BARE command cost 26.8 ms": that
            # command was `Heap.collectGarbage`, **which really does
            # collect garbage**, and it still costs 23.1 ms even after
            # the change. The real latency of the pipe, measured on
            # 2026-08-27 on commands that do no work, is **2.4 ms** for
            # `Runtime.evaluate("1")` and 4.3 ms for
            # `Page.getContentQuads`. Picking an expensive command as
            # the sample of "bare" attributes the browser's own time to
            # the transport.
            box: list = []
            ready = threading.Event()
            self._pending[msg_id] = (ready, box)
        msg: dict = {"id": msg_id, "method": method, "params": params or {}}
        if session:
            msg["sessionId"] = session
        self._write(msg)

        if not ready.wait(timeout):
            with self._lock:
                self._pending.pop(msg_id, None)
            raise ProtocolError(
                "%s: no response in %.0fs. If this is the FIRST command, "
                "look at the readiness signal before the pipe."
                % (method, timeout))
        response = box[0]
        if "error" in response:
            e = response["error"]
            if e.get("name") == "TargetClosedError":
                raise TargetClosedError("%s: %s" % (method, e.get("message", e)))
            raise ProtocolError("%s: %s" % (method, e.get("message", e)))
        return response.get("result")

    def post(self, method: str, params: Optional[dict] = None,
             session: Optional[str] = None) -> None:
        """Send a command and do NOT wait for its reply.

        ⛔ THIS IS THE ONLY WAY TO SEND FROM INSIDE AN EVENT HANDLER. Handlers
        run on the reader thread, and `send` blocks until the reader thread
        delivers the reply - so a `send` from a handler waits for a message
        that only the waiting thread could receive. That is a deadlock, not a
        slowdown, and the file-chooser path already pays for it with a helper
        thread. A screencast acknowledges every frame from the handler that
        received it; a thread per frame would be absurd, and a queue would be
        a second reader. So the ack goes out here, with an id the browser
        will answer and nobody will wait for: `_deliver` drops a reply whose
        id is not pending.

        It is not for commands whose RESULT matters, and not for commands
        whose FAILURE matters either: an error reply is dropped with the rest.
        """
        if self._closed:
            return
        with self._lock:
            self._next_id += 1
            msg_id = self._next_id
        msg: dict = {"id": msg_id, "method": method, "params": params or {}}
        if session:
            msg["sessionId"] = session
        self._write(msg)

    def _write(self, msg: dict) -> None:
        data = json.dumps(msg).encode("utf-8") + NUL
        with self._write_lock:
            os.write(self._to_browser, data)

    def close(self, timeout: float = 5.0) -> None:
        """Closes the pipe and waits for the browser to exit on its own.

        ⛔ Do NOT start from `terminate()`, and the reason is measured in
        this project: on Windows the pid that `Popen` returns is the
        LAUNCHER stub, which exits after about a second, so by the time
        of the kill the browser's tree is no longer its child and
        survives. Counted in one day: 88 orphaned processes.

        The clean path goes through the contract:
        `nsRemoteDebuggingPipe::ReaderLoop` calls `Disconnected` when the
        read returns zero, and Juggler shuts the browser down. Closing
        the pipe IS the exit command. `terminate()` remains only as a
        last resort, for whatever did not die on its own.
        """
        self._closed = True
        for fd in (self._to_browser, self._from_browser):
            try:
                os.close(fd)
            except OSError:
                pass
        p = self._process
        if not p:
            return
        deadline = time.monotonic() + timeout
        while p.poll() is None and time.monotonic() < deadline:
            time.sleep(0.05)
        if p.poll() is None:
            try:
                p.terminate()
            except OSError:
                pass


# ── launch ──────────────────────────────────────────────────────────────────

class _WindowsProcess:
    """The four things `launch` and `Connection.close` ask of a process,
    over a raw process handle.

    It exists because `subprocess.Popen` cannot name the DESKTOP a process is
    created on: CPython's `STARTUPINFO` carries `dwFlags`, `wShowWindow`, the
    three std handles and `lpAttributeList`, and nothing else - `lpDesktop`
    is not reachable from it. The hidden-desktop launch needs exactly that
    field, so the spawn below calls `CreateProcessW` itself and this is the
    handle it gives back, with the same surface the Popen used to have:
    `pid`, `poll()`, `returncode`, `wait()`, `terminate()`, `stdout`.
    """

    STILL_ACTIVE = 259

    def __init__(self, handle: int, pid: int, stdout) -> None:
        self._handle = handle
        self.pid = pid
        self.stdout = stdout
        self.returncode: Optional[int] = None

    def _kernel32(self):
        import ctypes
        return ctypes.WinDLL("kernel32", use_last_error=True)

    def poll(self) -> Optional[int]:
        if self.returncode is None and self._handle:
            import ctypes
            from ctypes import wintypes
            code = wintypes.DWORD(0)
            k32 = self._kernel32()
            if k32.GetExitCodeProcess(wintypes.HANDLE(self._handle),
                                      ctypes.byref(code)) and \
                    code.value != self.STILL_ACTIVE:
                self.returncode = int(code.value)
                k32.CloseHandle(wintypes.HANDLE(self._handle))
                self._handle = 0
        return self.returncode

    def wait(self, timeout: Optional[float] = None) -> int:
        from ctypes import wintypes
        if self.returncode is None and self._handle:
            ms = 0xFFFFFFFF if timeout is None else int(timeout * 1000)
            self._kernel32().WaitForSingleObject(wintypes.HANDLE(self._handle),
                                                 wintypes.DWORD(ms))
        code = self.poll()
        if code is None:
            raise subprocess.TimeoutExpired(str(self.pid), timeout or 0)
        return code

    def terminate(self) -> None:
        if self.poll() is None and self._handle:
            from ctypes import wintypes
            self._kernel32().TerminateProcess(wintypes.HANDLE(self._handle),
                                              wintypes.UINT(1))

    kill = terminate


def _spawn_windows(executable, argv, env):
    """`CreateProcessW` by hand, for the one field `subprocess` cannot set.

    The browser is created on the desktop named by `INVPW_DESKTOP` when the
    session asked to be hidden (`invisible_core._headless`), and on the
    caller's own desktop otherwise. That variable is consumed HERE and does
    not reach the browser: the engine never reads it.

    Everything else is what the `Popen` call used to do, made explicit:
    exactly five handles are inherited - the two Juggler pipe ends, the
    child's end of the stdout pipe, a second handle to that same end for
    stderr, and the read end of a pipe for stdin - through
    `PROC_THREAD_ATTRIBUTE_HANDLE_LIST`, so a second session starting in the
    same instant cannot hand its pipe ends to this browser and lose the
    ability to notice its own closing. `bInheritHandles=TRUE` with that list
    inherits those five and nothing else.

    ⛔ THE THREE STD HANDLES ARE SHAPED FOR FIREFOX'S LAUNCHER, and the first
    draft of this function got two of them wrong. `firefox.exe` runs first as
    a launcher that creates the real browser with a handle list of its own,
    built from its stdin, stdout and stderr plus the Juggler pipe
    (`browser/app/winlauncher/LauncherProcessWin.cpp`, `ProcThreadAttributes.h`).
    That list takes only DISK and PIPE handles, silently drops anything else
    while still naming it as a std handle, and is never de-duplicated - and
    Windows refuses a handle list with the same handle twice. A NUL device for
    stdin (a CHAR handle) and one handle for both stdout and stderr made the
    launcher's `CreateProcessW` fail with ERROR_INVALID_PARAMETER (Event Log,
    `LauncherProcessWin.cpp:580`); the launcher then disabled itself for that
    path in the registry (`|Browser` = 0) and ran on as the browser itself,
    so the session worked and `tests/test_first_launch_from_a_new_path.py`
    was the only thing that saw it. `Popen` never tripped it because CPython
    duplicates every std handle separately. So: stdin is the read end of a
    pipe whose write end is closed at once (EOF, and a PIPE type), and stderr
    is its own duplicate of the stdout pipe.
    """
    import ctypes
    import msvcrt
    import _winapi
    from ctypes import wintypes

    from invisible_core import DESKTOP_ENV

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)

    def inheritable(h):
        """⛔ CPython's `_winapi.CreatePipe` calls Windows' CreatePipe with
        NULL security attributes, so the handles are NOT inheritable.
        Passing them in the handle list as they were made `CreateProcess`
        fail with `WinError 87 - The parameter is incorrect`, which does
        not name the cause. They get duplicated asking for inheritance,
        and the original is closed."""
        current_process = _winapi.GetCurrentProcess()
        new = _winapi.DuplicateHandle(current_process, h, current_process,
                                      0, True, _winapi.DUPLICATE_SAME_ACCESS)
        _winapi.CloseHandle(h)
        return new

    # Two pipes. The names follow the BROWSER's point of view, like the
    # environment it will read: "read" is what it reads from, so where
    # WE write.
    its_read, our_write = _winapi.CreatePipe(0, 0)
    our_read, its_write = _winapi.CreatePipe(0, 0)
    its_read, its_write = inheritable(its_read), inheritable(its_write)
    # stdout and stderr merged on one pipe, as before: readiness and the
    # last words of a browser that dies at startup are both read from it.
    # Two HANDLES to it, one per std slot: the launcher's list must not hold
    # the same handle twice (see the docstring).
    out_read, out_write = _winapi.CreatePipe(0, 0)
    out_write = inheritable(out_write)
    current_process = _winapi.GetCurrentProcess()
    err_write = _winapi.DuplicateHandle(current_process, out_write,
                                        current_process, 0, True,
                                        _winapi.DUPLICATE_SAME_ACCESS)
    # stdin from a pipe with no writer: the browser reads EOF, never the
    # caller's console, and the launcher can forward a PIPE handle where it
    # drops a NUL device.
    in_read, in_write = _winapi.CreatePipe(0, 0)
    in_read = inheritable(in_read)
    _winapi.CloseHandle(in_write)

    env = dict(env)
    desktop = env.pop(DESKTOP_ENV, None)
    # `atoi` on the C++ side: the value must be DECIMAL.
    env["PW_PIPE_READ"] = str(int(its_read))
    env["PW_PIPE_WRITE"] = str(int(its_write))

    class STARTUPINFOW(ctypes.Structure):
        _fields_ = [("cb", wintypes.DWORD), ("lpReserved", wintypes.LPWSTR),
                    ("lpDesktop", wintypes.LPWSTR), ("lpTitle", wintypes.LPWSTR),
                    ("dwX", wintypes.DWORD), ("dwY", wintypes.DWORD),
                    ("dwXSize", wintypes.DWORD), ("dwYSize", wintypes.DWORD),
                    ("dwXCountChars", wintypes.DWORD),
                    ("dwYCountChars", wintypes.DWORD),
                    ("dwFillAttribute", wintypes.DWORD),
                    ("dwFlags", wintypes.DWORD), ("wShowWindow", wintypes.WORD),
                    ("cbReserved2", wintypes.WORD),
                    ("lpReserved2", ctypes.c_void_p),
                    ("hStdInput", wintypes.HANDLE),
                    ("hStdOutput", wintypes.HANDLE),
                    ("hStdError", wintypes.HANDLE)]

    class STARTUPINFOEXW(ctypes.Structure):
        _fields_ = [("StartupInfo", STARTUPINFOW),
                    ("lpAttributeList", ctypes.c_void_p)]

    class PROCESS_INFORMATION(ctypes.Structure):
        _fields_ = [("hProcess", wintypes.HANDLE), ("hThread", wintypes.HANDLE),
                    ("dwProcessId", wintypes.DWORD),
                    ("dwThreadId", wintypes.DWORD)]

    k32.InitializeProcThreadAttributeList.argtypes = (
        ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD,
        ctypes.POINTER(ctypes.c_size_t))
    k32.UpdateProcThreadAttribute.argtypes = (
        ctypes.c_void_p, wintypes.DWORD, ctypes.c_size_t, ctypes.c_void_p,
        ctypes.c_size_t, ctypes.c_void_p, ctypes.c_void_p)
    k32.CreateProcessW.argtypes = (
        wintypes.LPCWSTR, wintypes.LPWSTR, ctypes.c_void_p, ctypes.c_void_p,
        wintypes.BOOL, wintypes.DWORD, ctypes.c_void_p, wintypes.LPCWSTR,
        ctypes.c_void_p, ctypes.POINTER(PROCESS_INFORMATION))

    inherited = (wintypes.HANDLE * 5)(its_read, its_write, out_write,
                                      err_write, in_read)
    size = ctypes.c_size_t(0)
    k32.InitializeProcThreadAttributeList(None, 1, 0, ctypes.byref(size))
    attrs = ctypes.create_string_buffer(size.value)
    if not k32.InitializeProcThreadAttributeList(attrs, 1, 0, ctypes.byref(size)):
        raise ctypes.WinError(ctypes.get_last_error())
    proc_thread_attribute_handle_list = 0x20002
    if not k32.UpdateProcThreadAttribute(
            attrs, 0, proc_thread_attribute_handle_list, inherited,
            ctypes.sizeof(inherited), None, None):
        raise ctypes.WinError(ctypes.get_last_error())

    si = STARTUPINFOEXW()
    si.StartupInfo.cb = ctypes.sizeof(STARTUPINFOEXW)
    si.StartupInfo.dwFlags = 0x100  # STARTF_USESTDHANDLES
    si.StartupInfo.hStdInput = in_read
    si.StartupInfo.hStdOutput = out_write
    si.StartupInfo.hStdError = err_write
    if desktop:
        si.StartupInfo.lpDesktop = desktop
    si.lpAttributeList = ctypes.cast(attrs, ctypes.c_void_p)

    # Sorted case-insensitively, which is the order Windows keeps its own
    # block in; `CREATE_UNICODE_ENVIRONMENT` says it is wide. Each entry ends
    # in a NUL and the block in a second one: spelled with chr() because a
    # control character inside a literal is what the workbench's literal gate
    # hunts (a backslash escape in a Windows path), and here it is deliberate.
    nul = chr(0)
    block = nul.join("%s=%s" % kv for kv in
                     sorted(env.items(), key=lambda kv: kv[0].upper())) + nul + nul
    env_block = ctypes.create_unicode_buffer(block, len(block) + 1)
    # `CreateProcessW` may write into the command line: a mutable buffer.
    cmdline = ctypes.create_unicode_buffer(
        subprocess.list2cmdline([executable] + argv))
    create_unicode_environment, extended_startupinfo_present = 0x400, 0x80000
    pi = PROCESS_INFORMATION()
    ok = k32.CreateProcessW(
        None, cmdline, None, None, True,
        create_unicode_environment | extended_startupinfo_present,
        env_block, None, ctypes.byref(si), ctypes.byref(pi))
    err = ctypes.get_last_error()
    k32.DeleteProcThreadAttributeList(attrs)
    # The child's ends no longer serve us: keeping them open would
    # prevent us from noticing that the browser has closed.
    for h in (its_read, its_write, out_write, err_write, in_read):
        _winapi.CloseHandle(h)
    if not ok:
        for h in (our_read, our_write, out_read):
            _winapi.CloseHandle(h)
        raise OSError(err, "CreateProcessW failed for %s%s: %s" % (
            executable, (" on desktop %r" % desktop) if desktop else "",
            ctypes.FormatError(err).strip()))
    k32.CloseHandle(pi.hThread)
    stdout = os.fdopen(msvcrt.open_osfhandle(out_read, os.O_RDONLY), "rb")
    p = _WindowsProcess(int(pi.hProcess), int(pi.dwProcessId), stdout)
    return (msvcrt.open_osfhandle(our_write, 0),
            msvcrt.open_osfhandle(our_read, os.O_RDONLY), p)


def _spawn_posix(executable, argv, env):
    """⛔ IT WAS BROKEN, AND THE DOCSTRING HAD GUESSED WHICH HALF.

    This leg was written from `nsRemoteDebuggingPipe.cpp` and never executed
    until 2026-08-28. The symptom on the first run: the browser starts, prints
    `Juggler listening to the pipe`, stays alive - and the very first write
    raises `BrokenPipeError`. A live process with no reader on the other end.

    ⛔ THE CAUSE, MEASURED RATHER THAN REASONED. CPython's close-all-fds
    sweep runs AFTER `preexec_fn`, so the two descriptors this function creates
    with `dup2` are closed again before `exec` unless they are in the keep
    list. A four-line experiment reported it exactly:

        with preexec_fn + pass_fds(its_read, its_write) ->  alive=[3]
        with 3 and 4 also kept                          ->  alive=[3, 4]

    Fd 3 survived only by accident: the first `os.pipe()` in the parent
    happened to return 3, so it was already in the keep list and the `dup2`
    onto it was a no-op. Fd 4 was created by `dup2` and swept away - which is
    why the browser could ANNOUNCE itself (it had a read end on 3) and never
    receive anything (nothing owned the read end of OUR pipe once 4 died).

    ⛔ The remedy is to name 3 and 4 in `pass_fds` as well. They are not open
    in the parent and do not have to be: that list is a set of NUMBERS the
    child's sweep spares, not a set of parent handles.
    """
    its_read, our_write = os.pipe()
    our_read, its_write = os.pipe()

    def fix_descriptors():
        # On POSIX the numbers are HARDWIRED in the C++: 3 for reading,
        # 4 for writing. `nsRemoteDebuggingPipe.cpp` does not look them up.
        #
        # ⛔ AND 3 AND 4 *ARE* IN `pass_fds`. This comment used to say they
        # could not be, on the reasoning that the list names fds the parent
        # hands down. That reasoning was wrong: the list is a set of NUMBERS
        # the child spares during its close sweep, and the sweep runs AFTER
        # `preexec_fn` - measured, not assumed. Without them the browser starts
        # with nothing on 4 and the first write gets EPIPE from a live
        # process.
        #
        # `dup2` clears CLOEXEC on the NEW descriptor, which is what saves this
        # in the ordinary case - but only when the source and target differ.
        # `dup2(fd, fd)` is documented to be a no-op that does NOT clear it, so
        # a pipe that happens to land on 3 or 4 would be closed at exec while
        # every other run works. The explicit `set_inheritable` below removes
        # that difference instead of relying on which fds the OS handed out.
        for source, target in ((its_read, 3), (its_write, 4)):
            if source != target:
                os.dup2(source, target)
            os.set_inheritable(target, True)

    p = subprocess.Popen([executable] + argv, env=env,
                         preexec_fn=fix_descriptors,
                         # ⛔ 3 and 4 ARE IN THIS LIST, see the docstring.
                         # Without them the sweep that runs after `preexec_fn`
                         # closes exactly the two descriptors this whole
                         # function exists to create.
                         pass_fds=(its_read, its_write, 3, 4),
                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    os.close(its_read)
    os.close(its_write)
    return our_write, our_read, p


def launch(executable: str, profile_dir: str, *, headless: bool = True,
          argv_extra: Optional[list] = None, env: Optional[dict] = None,
          ready_timeout: float = 60.0) -> Connection:
    """Launches Firefox over the pipe and returns an already-ready
    connection."""
    argv = ["-no-remote"]
    if headless:
        argv.append("-headless")
    else:
        argv += ["-wait-for-browser", "-foreground"]
    argv += ["-profile", profile_dir, "-juggler-pipe"]
    argv += list(argv_extra or [])
    argv.append("-silent")

    full_env = dict(os.environ if env is None else env)
    spawn = _spawn_windows if sys.platform == "win32" else _spawn_posix
    to_browser, from_browser, p = spawn(executable, argv, full_env)

    # ⛔ Readiness is read on stdout, not on the pipe, and the line may
    # not come out at all on a MOZILLA_OFFICIAL build without the fix in
    # Juggler. It waits for the line, but it does NOT die if it does not
    # arrive: it tries to talk anyway, so the failure mode is a protocol
    # error naming the command instead of a silent timeout.
    seen, detto = _wait_until_ready(p, ready_timeout)
    # ⛔ A BROWSER THAT EXITED SAYS SO HERE, WHERE ITS OUTPUT STILL EXISTS.
    #
    # `stderr` is merged into `stdout` two functions up and read line by line
    # while waiting for readiness - and it used to be thrown away. When the
    # process had already died, this returned a Connection over a closed pipe
    # and the caller got `BrowserType.launch: the pipe is closed`, which names
    # neither the exit code nor the one thing the browser printed before going.
    #
    # Measured 2026-08-30: a CI guard went red with exactly that sentence, and
    # deciding whether the cause was the engine or the client took a rebuilt
    # binary, four workflow runs and a controlled comparison against the
    # PREVIOUS engine - all to recover information the browser had already
    # written and nobody had kept.
    if p.poll() is not None:
        coda = "\n".join(("    " + r) for r in detto[-15:]) or "    (nothing)"
        raise RuntimeError(
            "the browser exited during startup, before the protocol could be "
            "used.\n  exit code : %s\n  command   : %s\n  it printed:\n%s"
            % (p.returncode, " ".join([executable] + argv), coda))
    c = Connection(to_browser, from_browser, p)
    c.ready_seen = seen
    return c


def _wait_until_ready(p, timeout: float):
    """(readiness seen, what the browser printed) - the second half is new.

    The lines are KEPT rather than tested and dropped: they are the only
    account of a startup that fails, and they cost a list.
    """
    detto = []
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if p.poll() is not None:
            # Drain what is still buffered: the last line before the exit is
            # usually the one that says why.
            try:
                resto = p.stdout.read() or b""
            except Exception:
                resto = b""
            detto += [r for r in resto.decode("utf-8", "replace").split("\n") if r.strip()]
            return False, detto
        line = p.stdout.readline()
        if not line:
            time.sleep(0.01)
            continue
        testo = line.decode("utf-8", "replace").rstrip("\r\n")
        if testo.strip():
            detto.append(testo)
        if _READY in testo:
            return True, detto
    return False, detto
