"""Synchronous API - re-exports InvisiblePlaywright for parity with async_api."""
from .launcher import InvisiblePlaywright
# The same three error classes `async_api` exports; see the note there.
from invisible_playwright._pw._impl._errors import Error, TargetClosedError, TimeoutError

__all__ = ["InvisiblePlaywright", "Error", "TargetClosedError", "TimeoutError"]
