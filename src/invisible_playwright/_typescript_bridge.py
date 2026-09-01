"""Private bridge used by the TypeScript package.

This is not a user-facing CLI.  The Node wrapper keeps this process alive while
its Playwright context is open so a Linux virtual display and an ephemeral
Firefox profile have the same lifetime as that context.
"""
from __future__ import annotations

import json
import math
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any

from invisible_core import configure_proxy, prepare_session_geo, resolve_session_locale

from ._cursor import ENGINE_BINARY
from ._engine import resolve_executable
from ._juggler._profile import _write_user_js
from ._reaper import (
    SessionToken,
    TOKEN_VAR,
    alive,
    find_processes,
    guard_for,
    psutil,
    terminate,
)
from .launcher import InvisiblePlaywright

_OPTIONS = {
    "seed",
    "pin",
    "headless",
    "proxy",
    "extraArgs",
    "humanize",
    "locale",
    "timezone",
    "extraPrefs",
    "binaryPath",
    "profileDir",
    "showCursor",
}

_OWNER_PREFIX = "invisible-playwright-node-owner-"
_CLEANUP_PATH_VAR = "INVPW_TYPESCRIPT_CLEANUP_PATH"
_CLEANUP_NONCE_VAR = "INVPW_TYPESCRIPT_CLEANUP_NONCE"
_CLEANUP_FIELDS = {
    "version", "nonce", "profileDir", "removeProfile", "sessionToken",
    "virtualDisplayPid",
}


def _load_cleanup_metadata(metadata_path: Path, expected_nonce: str) -> dict[str, Any]:
    path = metadata_path.expanduser().resolve()
    owner = path.parent
    if path.name != "cleanup.json" or not owner.name.startswith(_OWNER_PREFIX):
        raise ValueError("cleanup metadata path is not wrapper-owned")
    if not re.fullmatch(r"[0-9a-f]{64}", expected_nonce):
        raise ValueError("cleanup nonce is invalid")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if type(payload) is not dict or set(payload) != _CLEANUP_FIELDS:
        raise ValueError("cleanup metadata has an incompatible shape")
    if payload["version"] != 1 or payload["nonce"] != expected_nonce:
        raise ValueError("cleanup metadata identity does not match its owner")
    token = payload["sessionToken"]
    if not isinstance(token, str) or (token and not re.fullmatch(r"[0-9a-f]{32}", token)):
        raise ValueError("cleanup session token is invalid")
    profile = payload["profileDir"]
    if not isinstance(profile, str) or not Path(profile).is_absolute():
        raise ValueError("cleanup profile path is invalid")
    if not isinstance(payload["removeProfile"], bool):
        raise ValueError("cleanup profile ownership is invalid")
    if payload["removeProfile"] and Path(profile).resolve() != owner / "profile":
        raise ValueError("refusing to remove a profile outside its wrapper owner")
    display_pid = payload["virtualDisplayPid"]
    if display_pid is not None and (
            not isinstance(display_pid, int) or isinstance(display_pid, bool) or
            display_pid <= 0):
        raise ValueError("cleanup virtual display pid is invalid")
    return payload


def _emergency_cleanup(metadata_path: Path, expected_nonce: str) -> None:
    payload = _load_cleanup_metadata(metadata_path, expected_nonce)
    token = SessionToken(payload["sessionToken"])
    if token:
        guard_for().reap(token)
        if find_processes(token):
            raise RuntimeError("session-token processes remain after emergency cleanup")
    display_pid = payload["virtualDisplayPid"]
    if display_pid is not None:
        if not token:
            raise RuntimeError("cannot confirm virtual display ownership without a token")
        try:
            display = psutil.Process(display_pid)
        except psutil.NoSuchProcess:
            display = None
        if display is not None:
            if not token.matches(display):
                raise RuntimeError("virtual display does not carry the cleanup token")
            terminate([display])
            if alive(display):
                raise RuntimeError("virtual display remains after emergency cleanup")
    if payload["removeProfile"]:
        profile = Path(payload["profileDir"])
        if profile.exists():
            shutil.rmtree(profile, ignore_errors=False)
        if profile.exists():
            raise RuntimeError("ephemeral profile remains after emergency cleanup")


class _CleanupOwner:
    def __init__(self, metadata_path: Path, nonce: str) -> None:
        self.metadata_path = metadata_path.expanduser().resolve()
        self.nonce = nonce
        self._payload = _load_cleanup_metadata(self.metadata_path, nonce)

    @property
    def profile_dir(self) -> Path:
        return Path(self._payload["profileDir"]).resolve()

    @property
    def remove_profile(self) -> bool:
        return self._payload["removeProfile"]

    def update(self, token: SessionToken, display_pid: int | None = None) -> None:
        self._payload["sessionToken"] = token.value
        self._payload["virtualDisplayPid"] = display_pid
        temporary = self.metadata_path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(self._payload, separators=(",", ":")),
            encoding="utf-8",
        )
        temporary.replace(self.metadata_path)

    def prepared_metadata(self) -> dict[str, Any]:
        prepared = {**self._payload, "metadataPath": str(self.metadata_path)}
        if prepared["virtualDisplayPid"] is None:
            del prepared["virtualDisplayPid"]
        return prepared


def _resolve_headless_with_cleanup(
        launcher: InvisiblePlaywright, cleanup_owner: _CleanupOwner | None) -> bool:
    if cleanup_owner is None:
        return launcher._resolve_headless()
    token = launcher._session_token
    cleanup_owner.update(token)
    previous = os.environ.get(TOKEN_VAR)
    os.environ[TOKEN_VAR] = token.value
    try:
        headless = launcher._resolve_headless()
    finally:
        if previous is None:
            os.environ.pop(TOKEN_VAR, None)
        else:
            os.environ[TOKEN_VAR] = previous
    display = launcher._virtual_display
    process = getattr(display, "_proc", None) if display is not None else None
    display_pid = getattr(process, "pid", None)
    if not isinstance(display_pid, int) or display_pid <= 0:
        display_pid = None
    cleanup_owner.update(token, display_pid)
    return headless


def _validate_options(options: dict[str, Any]) -> None:
    unknown = sorted(set(options) - _OPTIONS)
    if unknown:
        raise ValueError("unknown TypeScript option: " + ", ".join(unknown))

    seed = options.get("seed")
    if seed is not None and (not isinstance(seed, int) or isinstance(seed, bool)):
        raise TypeError("seed must be an integer or null")

    pin = options.get("pin")
    if pin is not None and type(pin) is not dict:
        raise TypeError("pin must be a plain object or null")

    proxy = options.get("proxy")
    if proxy is not None:
        if type(proxy) is not dict:
            raise TypeError("proxy must be a plain object or null")
        allowed_proxy_fields = {"server", "username", "password", "bypass"}
        extra_proxy_fields = sorted(set(proxy) - allowed_proxy_fields)
        if extra_proxy_fields:
            raise ValueError("proxy contains unknown fields: " + ", ".join(extra_proxy_fields))
        if not isinstance(proxy.get("server"), str):
            raise TypeError("proxy.server must be a string")
        for field in ("username", "password", "bypass"):
            if field in proxy and not isinstance(proxy[field], str):
                raise TypeError(f"proxy.{field} must be a string")

    extra_args = options.get("extraArgs")
    if extra_args is not None and (
            not isinstance(extra_args, list) or
            not all(isinstance(arg, str) for arg in extra_args)):
        raise TypeError("extraArgs must be an array of strings or null")

    humanize = options.get("humanize", True)
    valid_number = (
        isinstance(humanize, (int, float)) and
        not isinstance(humanize, bool) and
        math.isfinite(humanize) and
        humanize >= 0
    )
    if not isinstance(humanize, bool) and not valid_number:
        raise TypeError("humanize must be a boolean or a finite nonnegative number")

    for name in ("locale", "timezone", "binaryPath", "profileDir"):
        if name in options and not isinstance(options[name], str):
            raise TypeError(f"{name} must be a string")

    extra_prefs = options.get("extraPrefs")
    if extra_prefs is not None:
        if type(extra_prefs) is not dict:
            raise TypeError("extraPrefs must be a plain object or null")
        for key, value in extra_prefs.items():
            if not isinstance(key, str):
                raise TypeError("extraPrefs keys must be strings")
            scalar = value is None or isinstance(value, (str, bool, int, float))
            if not scalar or (isinstance(value, float) and not math.isfinite(value)):
                raise TypeError(f"extraPrefs.{key} must be a JSON scalar")

    if "headless" in options and not isinstance(options["headless"], bool):
        raise TypeError("headless must be a boolean")
    show_cursor = options.get("showCursor")
    if show_cursor is not None and not isinstance(show_cursor, bool):
        raise TypeError("showCursor must be a boolean or null")


class PreparedSession:
    """A prepared Firefox profile plus any display process that supports it."""

    def __init__(self, launcher: InvisiblePlaywright, config: dict[str, Any],
                 profile_dir: Path, remove_profile: bool) -> None:
        self._launcher = launcher
        self.config = config
        self._profile_dir = profile_dir
        self._remove_profile = remove_profile
        self._closed = False

    @classmethod
    def from_options(
            cls, options: dict[str, Any], *,
            cleanup_owner: _CleanupOwner | None = None) -> PreparedSession:
        _validate_options(options)

        supplied_profile = options.get("profileDir")
        profile_dir: Path | None = None
        launcher: InvisiblePlaywright | None = None
        remove_profile = cleanup_owner.remove_profile if cleanup_owner else not bool(supplied_profile)

        try:
            if cleanup_owner is not None:
                profile_dir = cleanup_owner.profile_dir
                expected_remove = not bool(supplied_profile)
                expected_profile = (
                    Path(supplied_profile).expanduser().resolve()
                    if supplied_profile else cleanup_owner.metadata_path.parent / "profile"
                )
                if remove_profile != expected_remove or profile_dir != expected_profile:
                    raise ValueError("cleanup metadata does not match profile ownership")
            else:
                profile_dir = (Path(supplied_profile).expanduser().resolve()
                               if supplied_profile else
                               Path(tempfile.mkdtemp(prefix="invisible-playwright-node-")))
            launcher = InvisiblePlaywright(
                seed=options.get("seed"),
                pin=options.get("pin"),
                headless=options.get("headless", False),
                proxy=options.get("proxy"),
                extra_args=options.get("extraArgs"),
                humanize=options.get("humanize", True),
                locale=options.get("locale", "auto"),
                timezone=options.get("timezone", ""),
                extra_prefs=options.get("extraPrefs"),
                binary_path=options.get("binaryPath"),
                profile_dir=profile_dir,
                show_cursor=options.get("showCursor"),
            )
            # Node cannot run the Python-side cursor generator. The patched browser
            # implements the same ordinary Playwright pointer API, so select that
            # engine explicitly instead of writing a pref that disables motion.
            launcher._cursor_engine = ENGINE_BINARY

            geo = prepare_session_geo(launcher._timezone, launcher._proxy)
            launcher._timezone = geo.timezone
            launcher._webrtc_egress_ip = geo.egress_ip
            launcher._srflx_dichiarato = geo.srflx_da_dichiarare()
            if (launcher._locale or "").strip().lower() == "auto":
                launcher._locale = resolve_session_locale(
                    geo.egress_ip, launcher._proxy)

            launcher._session_token = SessionToken.mint()
            executable = resolve_executable(launcher._binary_path)
            playwright_headless = _resolve_headless_with_cleanup(launcher, cleanup_owner)
            prefs = launcher._build_prefs()
            playwright_proxy = configure_proxy(launcher._proxy, prefs)
            profile_dir.mkdir(parents=True, exist_ok=True)
            _write_user_js(str(profile_dir), prefs)

            complete_env = launcher._build_env(prefs)
            env_delta = {
                key: value for key, value in complete_env.items()
                if os.environ.get(key) != value
            }
            context = launcher._default_context_kwargs()
            context_config: dict[str, Any] = {
                "viewport": context["viewport"],
                "screen": context["screen"],
            }
            if "locale" in context:
                context_config["locale"] = context["locale"]
            if "timezone_id" in context:
                context_config["timezoneId"] = context["timezone_id"]

            config: dict[str, Any] = {
                "seed": launcher.seed,
                "executablePath": str(executable),
                "profileDir": str(profile_dir),
                "headless": playwright_headless,
                "args": launcher._extra_args,
                "env": env_delta,
                "context": context_config,
            }
            if playwright_proxy is not None:
                config["proxy"] = playwright_proxy
            if cleanup_owner is not None:
                config["cleanup"] = cleanup_owner.prepared_metadata()
            return cls(launcher, config, profile_dir, remove_profile)
        except BaseException:
            cls._clean(launcher, profile_dir, remove_profile)
            raise

    @staticmethod
    def _clean(launcher: InvisiblePlaywright | None, profile_dir: Path | None,
               remove_profile: bool) -> None:
        cleanup_failure: Exception | None = None
        if launcher is not None:
            token = launcher._session_token
            if token:
                try:
                    guard_for().reap(token)
                except Exception as failure:  # noqa: BLE001 - cleanup must continue
                    cleanup_failure = failure
                launcher._session_token = SessionToken()
            display = launcher._virtual_display
            launcher._virtual_display = None
            if display is not None:
                try:
                    display.stop()
                except Exception as failure:  # noqa: BLE001 - cleanup must continue
                    if cleanup_failure is None:
                        cleanup_failure = failure
        if remove_profile and profile_dir is not None:
            shutil.rmtree(profile_dir, ignore_errors=True)
        if cleanup_failure is not None:
            raise cleanup_failure

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._clean(self._launcher, self._profile_dir, self._remove_profile)


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if args:
        if len(args) != 3 or args[0] != "--cleanup":
            sys.stderr.write("invisible_playwright TypeScript bridge: invalid private mode\n")
            return 2
        try:
            _emergency_cleanup(Path(args[1]), args[2])
            return 0
        except BaseException as failure:  # noqa: BLE001 - process boundary
            sys.stderr.write(
                f"invisible_playwright TypeScript emergency cleanup: {failure}\n")
            return 1

    session: PreparedSession | None = None
    try:
        cleanup_owner = _CleanupOwner(
            Path(os.environ[_CLEANUP_PATH_VAR]),
            os.environ[_CLEANUP_NONCE_VAR],
        )
        line = sys.stdin.readline()
        if not line:
            raise RuntimeError("the TypeScript bridge received no launch options")
        payload = json.loads(line)
        if not isinstance(payload, dict):
            raise TypeError("the TypeScript bridge options must be a JSON object")
        session = PreparedSession.from_options(payload, cleanup_owner=cleanup_owner)
        sys.stdout.write(json.dumps(session.config, separators=(",", ":")) + "\n")
        sys.stdout.flush()
        # Node closes stdin when the Playwright context closes. Keeping this
        # process alive keeps Xvfb alive too; EOF is the ownership boundary.
        while sys.stdin.buffer.read(8192):
            pass
        return 0
    except BaseException as failure:  # noqa: BLE001 - process boundary
        sys.stderr.write(f"invisible_playwright TypeScript bridge: {failure}\n")
        return 1
    finally:
        if session is not None:
            session.close()


if __name__ == "__main__":
    raise SystemExit(main())
