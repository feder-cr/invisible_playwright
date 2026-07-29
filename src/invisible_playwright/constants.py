"""Backward-compat shim - moved to invisible_core.constants (full alias)."""
import sys as _sys
from invisible_core import constants as _mod
_sys.modules[__name__] = _mod
