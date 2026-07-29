"""Backward-compat shim - spostato in invisible_core.config (alias completo)."""
import sys as _sys
from invisible_core import config as _mod
_sys.modules[__name__] = _mod
