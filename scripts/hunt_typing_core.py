"""HUNT SCAFFOLDING, NEVER MERGED: print which invisible-core is installed (version, where it came from, whether its keyboard sends on
absolute deadlines), so the step summary says which arm ran what."""
import importlib.metadata as m
import json
import pathlib

import invisible_core
from invisible_core.juggler import keyboard

d = m.distribution("invisible-core")
direct = pathlib.Path(str(d._path)) / "direct_url.json"
info = json.loads(direct.read_text()) if direct.exists() else None
print("invisible-core", d.version, "from", invisible_core.__file__)
print("source:", json.dumps(info["vcs_info"] if info and info.get("vcs_info") else "index"))
print("keyboard sends on absolute deadlines (_due):", hasattr(keyboard.Keyboard, "_due"))
