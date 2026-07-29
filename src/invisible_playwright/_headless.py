"""Backward-compat shim - spostato in invisible_core._headless (alias completo)."""
import sys as _sys
from invisible_core import _headless as _mod
_sys.modules[__name__] = _mod
