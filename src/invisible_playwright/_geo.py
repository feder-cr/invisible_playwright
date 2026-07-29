"""Backward-compat shim - spostato in invisible_core._geo (alias completo)."""
import sys as _sys
from invisible_core import _geo as _mod
_sys.modules[__name__] = _mod
