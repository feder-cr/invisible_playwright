"""HUNT SCAFFOLDING, NEVER MERGED: the member names of the omni.ja files next to an engine's executable (omni.ja and browser/omni.ja), as JSON on stdout.

Usage: python scripts/hunt_jar_names.py ENGINE_EXE > evidence/jar_names_<arm>.json
The summary of the profile hunt takes the members that only ours has (by base name) as our files, next to scripts/hunt_ours_list.json from the source tree.
"""
import json
import os
import sys

from invisible_core.jar import open_jar

root = os.path.dirname(os.path.abspath(sys.argv[1]))
names = []
for rel in ("omni.ja", os.path.join("browser", "omni.ja")):
    p = os.path.join(root, rel)
    if os.path.exists(p):
        names += [rel.replace("\\", "/") + ":" + n for n in sorted(open_jar(p).names())]
json.dump(names, sys.stdout)
