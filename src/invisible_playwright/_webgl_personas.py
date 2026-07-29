"""Backward-compat shim - spostato in invisible_core._webgl_personas (alias completo)."""
import sys as _sys
from invisible_core import _webgl_personas as _mod
_sys.modules[__name__] = _mod
