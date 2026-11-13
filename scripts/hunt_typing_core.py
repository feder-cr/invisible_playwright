"""HUNT SCAFFOLDING, NEVER MERGED: print which invisible-core and which invisible-playwright are installed (version, where each came from,
whether the keyboard sends on absolute deadlines), so the step summary says which arm ran what. Run with the interpreter of the arm."""
import importlib.metadata as m
import json
import pathlib

import invisible_core
import invisible_playwright
from invisible_core.juggler import keyboard


def source(name):
    d = m.distribution(name)
    direct = pathlib.Path(str(d._path)) / "direct_url.json"
    info = json.loads(direct.read_text()) if direct.exists() else None
    if info and info.get("vcs_info"):
        return d.version, info["vcs_info"]
    return d.version, ("local dir " + info["url"]) if info else "index"


v, src = source("invisible-core")
print("invisible-core", v, "from", invisible_core.__file__)
print("source:", json.dumps(src))
v, src = source("invisible-playwright")
print("invisible-playwright", v, "from", invisible_playwright.__file__)
print("wrapper source:", json.dumps(src))
print("keyboard sends on absolute deadlines (_due):", hasattr(keyboard.Keyboard, "_due"))
