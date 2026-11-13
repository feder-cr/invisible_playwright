"""What the core did about the GeoIP database during a pytest run, written when the run ends.

Registered by tests/conftest.py when ``GEOIP_USED_OUT`` names a file (the CI legs set it; a developer's run does not). ``scripts/ci_lab_geoip.py
verify`` reads the file: the proof of a leg is not only the environment it was LAUNCHED in (``evidence``, written before pytest) but what the
process of the tests used: the value of ``STEALTHFOX_GEOIP_MMDB`` when the run ended, the sha256 of that file, and how many times the core went
past the override to ask GitHub for the latest database (``_resolve_latest_geoip_tag``) or to download one (``_download_geoip_tag``): none, in a leg
with the database of the pin. A test that removes the variable on purpose (the user-path e2e files do, in a throwaway environment of their own)
runs in another process and is not counted here.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path


class GeoipUsedRecorder:
    def __init__(self, path: str) -> None:
        self.path = Path(path)
        self.asked_github = 0
        self._undo = []

    def start(self) -> None:
        try:
            from invisible_core import _geoip_db
        except ImportError:        # a run without the core installed: the file says so
            return
        for name in ("_resolve_latest_geoip_tag", "_download_geoip_tag"):
            original = getattr(_geoip_db, name, None)
            if original is None:
                continue

            def counted(*args, __original=original, **kwargs):
                self.asked_github += 1
                return __original(*args, **kwargs)

            setattr(_geoip_db, name, counted)
            self._undo.append((_geoip_db, name, original))

    def stop(self) -> None:
        for module, name, original in reversed(self._undo):
            setattr(module, name, original)
        self._undo = []

    def pytest_sessionfinish(self, session, exitstatus) -> None:  # noqa: ARG002
        value = os.environ.get("STEALTHFOX_GEOIP_MMDB", "")
        digest = None
        if value and os.path.isfile(value):
            digest = hashlib.sha256(Path(value).read_bytes()).hexdigest()
        data = {"env": value, "exists": digest is not None, "sha256": digest, "asked_github": self.asked_github}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(data, indent=1), encoding="utf-8")
        self.stop()
