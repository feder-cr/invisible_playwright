"""HUNT SCAFFOLDING, NEVER MERGED: the environment that starts the Gecko profiler from the first instruction of the parent process and makes it
write its profile when the process ends (tools/profiler/core/platform.cpp: MOZ_PROFILER_STARTUP, _INTERVAL, _ENTRIES, _FEATURES, _FILTERS, MOZ_PROFILER_SHUTDOWN).

  MOZ_PROFILER_STARTUP=1               start at startup
  MOZ_PROFILER_STARTUP_INTERVAL=1      one sample per millisecond
  MOZ_PROFILER_STARTUP_ENTRIES=8000000 buffer entries (8 bytes each: 64 MB per process); a typing window is a few thousand samples of a few dozen
                                       entries, and the whole life of a hunt browser is about ten seconds
  MOZ_PROFILER_STARTUP_FEATURES=js,stackwalk   JS frames named, native frames walked; NOT the startup extras (file I/O, IPC messages) nor screenshots / process CPU
  MOZ_PROFILER_STARTUP_FILTERS=GeckoMain       the main thread of each process only
  MOZ_PROFILER_SHUTDOWN=<file>         the profile of the parent (children's variable is emptied by the profiler itself) written at exit

The core does NOT let a `MOZ_*` variable of the host reach the engine (invisible_core._engine_env.compose), so for our engine the variables are added
AFTER the core composed the environment (install_core_patch); stock Playwright is given them through `launch(env=...)`.
"""
import os

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
    """one file per browser process: arm, this python process (four run at once) and the run's number"""
    d = os.path.abspath(os.environ.get("HUNT_PROFILE_DIR", "prof"))
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, "%s-%d-%d.json" % (arm, os.getpid(), n))


def install_core_patch(get_file):
    """After the core composed the engine's environment, add the profiler's variables (`get_file()` = the file of the browser being launched);
    the core's `close` waits up to 5 s for the browser to end before it terminates it, and a profile written at exit may take longer: the wait is 90 s."""
    from invisible_core import _engine_env
    from invisible_core.juggler import connection
    real = _engine_env.compose

    def compose(env, locale_tag=None):
        out = real(env, locale_tag)
        out.update(profiler_env(get_file()))
        return out

    _engine_env.compose = compose
    real_close = connection.Connection.close

    def close(self, timeout=5.0):
        return real_close(self, max(timeout, 90.0))

    connection.Connection.close = close
