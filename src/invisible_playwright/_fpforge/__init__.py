"""Backward-compat shim - spostato in invisible_core._fpforge (alias completo).

Aliasa il package E i suoi submodule agli stessi oggetti del core, cosi'
``from invisible_playwright._fpforge.profile import Profile`` e
``from invisible_core._fpforge.profile import Profile`` sono la STESSA classe
(isinstance funziona) e i nomi privati restano accessibili.
"""
import sys as _sys
import invisible_core._fpforge as _pkg
from invisible_core._fpforge import profile as _profile
from invisible_core._fpforge import _sampler as _sampler_mod
from invisible_core._fpforge import _network as _network_mod

_sys.modules[__name__] = _pkg
_sys.modules[__name__ + ".profile"] = _profile
_sys.modules[__name__ + "._sampler"] = _sampler_mod
_sys.modules[__name__ + "._network"] = _network_mod
