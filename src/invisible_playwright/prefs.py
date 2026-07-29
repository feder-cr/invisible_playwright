"""Backward-compat shim - spostato in invisible_core.prefs (alias completo)."""
import sys as _sys
from invisible_core import prefs as _mod
_sys.modules[__name__] = _mod
