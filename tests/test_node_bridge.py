from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.unit


def test_node_bridge_prepares_a_profile_without_launching_a_browser(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    import invisible_playwright._typescript_bridge as bridge
    from invisible_playwright._typescript_bridge import PreparedSession

    binary = tmp_path / "firefox"
    binary.write_text("not launched", encoding="utf-8")
    binary.chmod(0o755)
    profile = tmp_path / "profile"
    monkeypatch.setattr(bridge, "resolve_executable", lambda path: Path(path))
    monkeypatch.setattr(bridge.InvisiblePlaywright, "_build_env", lambda self, prefs: {})

    session = PreparedSession.from_options({
        "seed": 42,
        "headless": False,
        "locale": "en-US",
        "timezone": "UTC",
        "binaryPath": str(binary),
        "profileDir": str(profile),
        "humanize": True,
    })
    try:
        config = session.config
        assert config["seed"] == 42
        assert config["executablePath"] == str(binary)
        assert config["profileDir"] == str(profile)
        assert config["headless"] is False
        assert "proxy" not in config
        assert config["context"]["locale"] == "en-US"
        assert config["context"]["timezoneId"] == "UTC"
        assert config["context"]["screen"] == {"width": 1920, "height": 1080}
        assert config["context"]["viewport"] == {"width": 1920, "height": 947}
        user_js = (profile / "user.js").read_text(encoding="utf-8")
        assert 'user_pref("general.useragent.override"' in user_js
        assert 'user_pref("stealthfox.humanize", true);' in user_js
        json.dumps(config)
    finally:
        session.close()


def test_private_cleanup_mode_runs_the_bounded_owner_cleanup(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    import invisible_playwright._typescript_bridge as bridge

    metadata = tmp_path / "cleanup.json"
    calls = []
    monkeypatch.setattr(
        bridge,
        "_emergency_cleanup",
        lambda path, nonce: calls.append((path, nonce)),
    )

    assert bridge.main(["--cleanup", str(metadata), "e" * 64]) == 0
    assert calls == [(metadata, "e" * 64)]


def test_virtual_display_is_token_stamped_before_spawn_and_recorded(
        monkeypatch: pytest.MonkeyPatch):
    import invisible_playwright._typescript_bridge as bridge
    from invisible_playwright._reaper import SessionToken, TOKEN_VAR

    token = SessionToken.mint()
    seen_tokens = []
    updates = []
    launcher = SimpleNamespace(
        _session_token=token,
        _virtual_display=None,
    )

    def resolve_headless():
        seen_tokens.append(os.environ.get(TOKEN_VAR))
        launcher._virtual_display = SimpleNamespace(
            _proc=SimpleNamespace(pid=9876),
        )
        return False

    launcher._resolve_headless = resolve_headless
    owner = SimpleNamespace(update=lambda value, pid=None: updates.append((value, pid)))
    monkeypatch.delenv(TOKEN_VAR, raising=False)

    assert bridge._resolve_headless_with_cleanup(launcher, owner) is False
    assert seen_tokens == [token.value]
    assert TOKEN_VAR not in os.environ
    assert updates == [(token, None), (token, 9876)]


def test_prepared_session_publishes_validated_cleanup_identity(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    import invisible_playwright._typescript_bridge as bridge

    owner_dir = tmp_path / "invisible-playwright-node-owner-test"
    owner_dir.mkdir()
    profile = owner_dir / "profile"
    metadata = owner_dir / "cleanup.json"
    nonce = "d" * 64
    metadata.write_text(json.dumps({
        "version": 1,
        "nonce": nonce,
        "profileDir": str(profile),
        "removeProfile": True,
        "sessionToken": "",
        "virtualDisplayPid": None,
    }), encoding="utf-8")
    binary = tmp_path / "firefox"
    binary.write_text("binary", encoding="utf-8")
    binary.chmod(0o755)
    monkeypatch.setattr(bridge, "resolve_executable", lambda path: Path(path))
    monkeypatch.setattr(bridge.InvisiblePlaywright, "_build_env", lambda self, prefs: {})
    cleanup_owner = bridge._CleanupOwner(metadata, nonce)

    session = bridge.PreparedSession.from_options(
        {"binaryPath": str(binary), "headless": False},
        cleanup_owner=cleanup_owner,
    )
    try:
        cleanup = session.config["cleanup"]
        assert cleanup["metadataPath"] == str(metadata.resolve())
        assert cleanup["nonce"] == nonce
        assert cleanup["profileDir"] == str(profile.resolve())
        assert cleanup["removeProfile"] is True
        assert len(cleanup["sessionToken"]) == 32
    finally:
        session.close()


def test_emergency_cleanup_reaps_token_and_removes_owned_ephemeral_profile(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    import invisible_playwright._typescript_bridge as bridge
    from invisible_playwright._reaper import SessionToken

    owner = tmp_path / "invisible-playwright-node-owner-test"
    owner.mkdir()
    profile = owner / "profile"
    profile.mkdir()
    metadata = owner / "cleanup.json"
    nonce = "a" * 64
    token = SessionToken.mint()
    metadata.write_text(json.dumps({
        "version": 1,
        "nonce": nonce,
        "profileDir": str(profile),
        "removeProfile": True,
        "sessionToken": token.value,
        "virtualDisplayPid": None,
    }), encoding="utf-8")
    reaped = []
    monkeypatch.setattr(bridge, "guard_for", lambda: SimpleNamespace(reap=reaped.append))
    monkeypatch.setattr(bridge, "find_processes", lambda _token: [])

    bridge._emergency_cleanup(metadata, nonce)

    assert reaped == [token]
    assert not profile.exists()


def test_emergency_cleanup_never_removes_a_supplied_persistent_profile(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    import invisible_playwright._typescript_bridge as bridge

    owner = tmp_path / "invisible-playwright-node-owner-test"
    owner.mkdir()
    profile = tmp_path / "persistent"
    profile.mkdir()
    marker = profile / "keep"
    marker.write_text("persistent", encoding="utf-8")
    metadata = owner / "cleanup.json"
    nonce = "b" * 64
    metadata.write_text(json.dumps({
        "version": 1,
        "nonce": nonce,
        "profileDir": str(profile),
        "removeProfile": False,
        "sessionToken": "",
        "virtualDisplayPid": None,
    }), encoding="utf-8")
    monkeypatch.setattr(bridge, "find_processes", lambda _token: [])

    bridge._emergency_cleanup(metadata, nonce)

    assert marker.read_text(encoding="utf-8") == "persistent"


def test_emergency_cleanup_terminates_a_token_owned_virtual_display(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    import invisible_playwright._typescript_bridge as bridge
    from invisible_playwright._reaper import SessionToken, TOKEN_VAR

    owner = tmp_path / "invisible-playwright-node-owner-test"
    owner.mkdir()
    profile = owner / "profile"
    profile.mkdir()
    metadata = owner / "cleanup.json"
    nonce = "c" * 64
    token = SessionToken.mint()
    metadata.write_text(json.dumps({
        "version": 1,
        "nonce": nonce,
        "profileDir": str(profile),
        "removeProfile": True,
        "sessionToken": token.value,
        "virtualDisplayPid": 4321,
    }), encoding="utf-8")
    process = SimpleNamespace(pid=4321, environ=lambda: {TOKEN_VAR: token.value})
    terminated = []
    monkeypatch.setattr(bridge, "guard_for", lambda: SimpleNamespace(reap=lambda _token: 0))
    monkeypatch.setattr(bridge, "find_processes", lambda _token: [])
    monkeypatch.setattr(bridge.psutil, "Process", lambda _pid: process)
    monkeypatch.setattr(bridge, "terminate", lambda processes: terminated.extend(processes))
    monkeypatch.setattr(bridge, "alive", lambda _process: False)

    bridge._emergency_cleanup(metadata, nonce)

    assert terminated == [process]


def test_node_bridge_rejects_unknown_options():
    from invisible_playwright._typescript_bridge import PreparedSession

    with pytest.raises(ValueError, match="unknown TypeScript option: typo"):
        PreparedSession.from_options({"typo": True})


def test_node_bridge_rejects_non_boolean_headless():
    from invisible_playwright._typescript_bridge import PreparedSession

    with pytest.raises(TypeError, match="headless must be a boolean"):
        PreparedSession.from_options({"headless": "false"})


def test_node_bridge_rejects_non_array_extra_args():
    from invisible_playwright._typescript_bridge import PreparedSession

    with pytest.raises(TypeError, match="extraArgs must be an array of strings or null"):
        PreparedSession.from_options({"extraArgs": "--private"})


@pytest.mark.parametrize(("option", "value", "message"), [
    ("seed", True, "seed must be an integer or null"),
    ("seed", 1.5, "seed must be an integer or null"),
    ("pin", [], "pin must be a plain object or null"),
    ("proxy", [], "proxy must be a plain object or null"),
    ("proxy", {}, "proxy.server must be a string"),
    ("proxy", {"server": 7}, "proxy.server must be a string"),
    ("proxy", {"server": "http://proxy", "username": False},
     "proxy.username must be a string"),
    ("proxy", {"server": "http://proxy", "extra": "no"},
     "proxy contains unknown fields: extra"),
    ("extraArgs", ["--ok", 1], "extraArgs must be an array of strings or null"),
    ("humanize", "false", "humanize must be a boolean or a finite nonnegative number"),
    ("humanize", -0.1, "humanize must be a boolean or a finite nonnegative number"),
    ("humanize", float("inf"), "humanize must be a boolean or a finite nonnegative number"),
    ("locale", False, "locale must be a string"),
    ("timezone", 1, "timezone must be a string"),
    ("binaryPath", [], "binaryPath must be a string"),
    ("profileDir", False, "profileDir must be a string"),
    ("extraPrefs", [], "extraPrefs must be a plain object or null"),
    ("extraPrefs", {"pref": []}, "extraPrefs.pref must be a JSON scalar"),
    ("extraPrefs", {"pref": float("nan")}, "extraPrefs.pref must be a JSON scalar"),
    ("headless", "false", "headless must be a boolean"),
    ("showCursor", "false", "showCursor must be a boolean or null"),
], ids=lambda value: repr(value))
def test_node_bridge_rejects_erased_javascript_option_types(
        option: str, value: object, message: str):
    from invisible_playwright._typescript_bridge import PreparedSession

    with pytest.raises((TypeError, ValueError), match=message.replace(".", r"\.")):
        PreparedSession.from_options({option: value})


def test_node_bridge_accepts_nullable_option_values():
    import invisible_playwright._typescript_bridge as bridge

    validate = getattr(bridge, "_validate_options", None)
    assert validate is not None, "the bridge must validate options before creating resources"
    validate({
        "seed": None,
        "pin": None,
        "proxy": None,
        "extraArgs": None,
        "extraPrefs": None,
        "showCursor": None,
    })


def test_ephemeral_profile_is_removed_when_launcher_construction_fails(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    import invisible_playwright._typescript_bridge as bridge

    profile = tmp_path / "ephemeral"

    def make_profile(**_kwargs):
        profile.mkdir()
        return str(profile)

    monkeypatch.setattr(bridge.tempfile, "mkdtemp", make_profile)

    def fail_constructor(**_kwargs):
        raise RuntimeError("constructor failed")

    monkeypatch.setattr(bridge, "InvisiblePlaywright", fail_constructor)

    with pytest.raises(RuntimeError, match="constructor failed"):
        bridge.PreparedSession.from_options({})

    assert not profile.exists()


def test_prepared_session_surfaces_reaper_failure_and_finishes_other_cleanup(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    import invisible_playwright._typescript_bridge as bridge
    from invisible_playwright._reaper import SessionToken

    profile = tmp_path / "ephemeral"
    profile.mkdir()
    (profile / "marker").write_text("remove me", encoding="utf-8")
    display_stops = []
    token = SessionToken.mint()
    launcher = SimpleNamespace(
        _virtual_display=SimpleNamespace(stop=lambda: display_stops.append(True)),
        _session_token=token,
    )

    def fail_reap(_token):
        raise RuntimeError("reaper failed")

    monkeypatch.setattr(bridge, "guard_for", lambda: SimpleNamespace(reap=fail_reap))
    session = bridge.PreparedSession(launcher, {}, profile, True)

    with pytest.raises(RuntimeError, match="reaper failed"):
        session.close()

    assert launcher._session_token == SessionToken()
    assert display_stops == [True]
    assert not profile.exists()


def test_prepared_session_reaps_browser_processes_with_its_session_token(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    import invisible_playwright._typescript_bridge as bridge
    from invisible_playwright._reaper import SessionToken

    token = SessionToken.mint()
    launcher = SimpleNamespace(_virtual_display=None, _session_token=token)
    reaped = []
    monkeypatch.setattr(
        bridge,
        "guard_for",
        lambda: SimpleNamespace(reap=reaped.append),
        raising=False,
    )
    session = bridge.PreparedSession(launcher, {}, tmp_path, False)

    session.close()
    session.close()

    assert reaped == [token]
