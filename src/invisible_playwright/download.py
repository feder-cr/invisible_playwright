"""Backward-compat shim - spostato in invisible_core.download (alias completo)."""
import sys as _sys
from invisible_core import download as _mod
_sys.modules[__name__] = _mod
