"""HUNT SCAFFOLDING, NEVER MERGED: the environment that starts the Gecko profiler from the first instruction of the parent process and makes it
write its profile when the process ends (tools/profiler/core/platform.cpp: MOZ_PROFILER_STARTUP, _INTERVAL, _ENTRIES, _FEATURES, _FILTERS, MOZ_PROFILER_SHUTDOWN).

  MOZ_PROFILER_STARTUP=1               start at startup
  MOZ_PROFILER_STARTUP_INTERVAL=1      one sample per millisecond
  MOZ_PROFILER_STARTUP_ENTRIES=8000000 buffer entries (8 bytes each: 64 MB per process); a typing window is a few thousand samples of a few dozen
                                       entries, and the whole life of a hunt browser is about ten seconds
  MOZ_PROFILER_STARTUP_FEATURES=js,stackwalk   JS frames named, native frames walked; NOT the startup extras (file I/O, IPC messages) nor screenshots / process CPU
  MOZ_PROFILER_STARTUP_FILTERS=GeckoMain       the main thread of each process only
  MOZ_PROFILER_SHUTDOWN=<file>-%p.json the profile of a process written when that process ends; "%p" is replaced by the process id of the WRITER
                                       (tools/profiler/core/platform.cpp, locked_profiler_save_profile_to_file). Content processes get the variable
                                       emptied by the profiler itself; every OTHER firefox.exe the parent starts inherits it

WHY "%p" (run 43551469616): ours' parent, when it shuts down, starts `firefox.exe --backgroundtask pingsender` (telemetry is on, as in a retail Firefox;
stock Playwright's build has it off). That child inherits MOZ_PROFILER_STARTUP and the SAME MOZ_PROFILER_SHUTDOWN, lives 1.4-2.5 s and writes ITS profile
(513-990 samples of TelemetrySend / BackgroundTask_pingsender) to the path the parent is about to write: the later write wins, or the two interleave
("Extra data" on parse). One path for two processes was the whole break; a path per writer pid (and the parent picked by its pid) cannot collide.

The core does NOT let a `MOZ_*` variable of the host reach the engine (invisible_core._engine_env.compose), so for our engine the variables are added
AFTER the core composed the environment (install_core_patch); stock Playwright is given them through `launch(env=...)`.
"""
import glob
import os
import re
import subprocess
import time

DEFAULT_ENTRIES = "8000000"


def profiler_env(shutdown_file):
    return {
        "MOZ_PROFILER_STARTUP": "1",
        "MOZ_PROFILER_STARTUP_INTERVAL": "1",
        "MOZ_PROFILER_STARTUP_ENTRIES": os.environ.get("HUNT_PROFILER_ENTRIES", DEFAULT_ENTRIES),
        "MOZ_PROFILER_STARTUP_FEATURES": "js,stackwalk",
        "MOZ_PROFILER_STARTUP_FILTERS": "GeckoMain",
        "MOZ_PROFILER_SHUTDOWN": shutdown_file,
    }


def profile_path(arm, n):
    """the TEMPLATE of one browser launch's profile files: arm, this python process (four run at once) and the run's number; the browser's own
    pid is put where "%p" is, by the browser, so each process that writes a profile writes its own file (see the top of this file)"""
    d = os.path.abspath(os.environ.get("HUNT_PROFILE_DIR", "prof"))
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, "%s-%d-%d-%%p.json" % (arm, os.getpid(), n))


def concrete(template, pid):
    """the file the process `pid` writes for `template`"""
    return template.replace("%p", str(int(pid)))


def files_of(template):
    """every file written for `template`, by whichever process: [(pid, path)] sorted by pid"""
    pre, _, post = template.partition("%p")
    out = []
    for f in glob.glob(glob.escape(pre) + "*" + glob.escape(post)):
        mid = f[len(pre):len(f) - len(post)] if post else f[len(pre):]
        if mid.isdigit():
            out.append((int(mid), f))
    return sorted(out)


_PARENT = re.compile(r"^parent (\d+) ")


def parent_pid(stamp_lines):
    """the pid of the parent process, from the patched Juggler's stamps ("parent <pid> <seq> <tag> ..."), None if none yet"""
    for line in list(stamp_lines):
        m = _PARENT.match(line)
        if m:
            return int(m.group(1))
    return None


_ANCHOR = re.compile(r"anchor=([0-9.]+)")


def anchor_of(stamp_lines, pid):
    """the anchor (wall ms of ChromeUtils.now() == 0, i.e. of process creation) the parent `pid` calibrated, from its A0 stamps; the Juggler.js one first"""
    best = None
    for line in list(stamp_lines):
        if line.startswith("parent %d " % pid) and " A0 " in line:
            m = _ANCHOR.search(line)
            if m:
                if "module=Juggler.js" in line:
                    return float(m.group(1))
                best = best if best is not None else float(m.group(1))
    return best


def pid_alive(pid):
    if os.name == "nt":
        import ctypes
        k32 = ctypes.windll.kernel32
        k32.OpenProcess.restype = ctypes.c_void_p
        h = k32.OpenProcess(0x00100000 | 0x1000, False, int(pid))  # SYNCHRONIZE | PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return False
        try:
            return k32.WaitForSingleObject(ctypes.c_void_p(h), 0) == 0x102  # WAIT_TIMEOUT: still running
        finally:
            k32.CloseHandle(ctypes.c_void_p(h))
    try:
        os.kill(int(pid), 0)
        return True
    except OSError:
        return False


def kill_tree(pid):
    if os.name == "nt":
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(int(pid))], capture_output=True, timeout=60)
    else:
        try:
            os.kill(int(pid), 9)
        except OSError:
            pass


def wait_for_parent(stamp_lines, template, find_timeout=60.0, exit_timeout=180.0):
    """A CLEAN CLOSE THAT LETS THE PROFILE BE WRITTEN. Called after the core's close() (the pipe is shut: Juggler quits the browser). What `close()` waited
    for is the Popen pid, which on Windows is the LAUNCHER stub (exits after about a second: invisible_core.juggler.connection.Connection.close), so the
    browser's own shutdown, and the profile it writes at the very end of it, are NOT waited for by anybody. Here: find the parent's pid in the stamps (the
    browser dumps them while it ends), wait for THAT pid to be gone (the profile is complete when the process is), and only then return the file.
    Returns (pid or None, file or None, problems [str], seconds waited)."""
    t0 = time.time()
    problems = []
    pid = None
    while time.time() - t0 < find_timeout:
        pid = parent_pid(stamp_lines)
        if pid:
            break
        time.sleep(0.2)
    if pid is None:
        return None, None, ["no parent stamp in %.0f s: the parent pid is unknown" % find_timeout], time.time() - t0
    while pid_alive(pid) and time.time() - t0 < find_timeout + exit_timeout:
        time.sleep(0.2)
    if pid_alive(pid):
        problems.append("parent %d still alive %.0f s after the close: killed (its profile cannot be complete)" % (pid, time.time() - t0))
        kill_tree(pid)
        time.sleep(1.0)
    path = concrete(template, pid)
    for _ in range(50):  # the file is closed with the process; this only covers a slow disk
        if os.path.exists(path) and os.path.getsize(path) > 0:
            break
        time.sleep(0.1)
    else:
        problems.append("no profile file %s for the parent %d" % (os.path.basename(path), pid))
        path = None
    return pid, path, problems, time.time() - t0


def install_core_patch(get_file):
    """After the core composed the engine's environment, add the profiler's variables (`get_file()` = the TEMPLATE of the browser being launched).
    The core's `close` is NOT patched any more: its wait is on the launcher stub, so a longer wait never waited for the browser (wait_for_parent does)."""
    from invisible_core import _engine_env
    real = _engine_env.compose

    def compose(env, locale_tag=None):
        out = real(env, locale_tag)
        out.update(profiler_env(get_file()))
        return out

    _engine_env.compose = compose
