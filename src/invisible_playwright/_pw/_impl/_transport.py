# Copyright (c) Microsoft Corporation.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import asyncio
import io
import json
import os
import subprocess
import sys
from abc import ABC, abstractmethod
from typing import Callable, Dict, Optional, Union

from invisible_playwright._pw._impl._helper import ParsedMessagePayload


# Sourced from: https://github.com/pytest-dev/pytest/blob/da01ee0a4bb0af780167ecd228ab3ad249511302/src/_pytest/faulthandler.py#L69-L77
def _get_stderr_fileno() -> Optional[int]:
    try:
        # when using pythonw, sys.stderr is None.
        # when Pyinstaller is used, there is no closed attribute because Pyinstaller monkey-patches it with a NullWriter class
        if sys.stderr is None or not hasattr(sys.stderr, "closed"):
            return None
        if sys.stderr.closed:
            return None

        return sys.stderr.fileno()
    except (NotImplementedError, AttributeError, io.UnsupportedOperation):
        # pytest-xdist monkeypatches sys.stderr with an object that is not an actual file.
        # https://docs.python.org/3/library/faulthandler.html#issue-with-file-descriptors
        # This is potentially dangerous, but the best we can do.
        if not hasattr(sys, "__stderr__") or not sys.__stderr__:
            return None
        return sys.__stderr__.fileno()


class Transport(ABC):
    def __init__(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop
        self.on_message: Callable[[ParsedMessagePayload], None] = lambda _: None
        self.on_error_future: asyncio.Future = loop.create_future()

    @abstractmethod
    def request_stop(self) -> None:
        pass

    def dispose(self) -> None:
        pass

    @abstractmethod
    async def wait_until_stopped(self) -> None:
        pass

    @abstractmethod
    async def connect(self) -> None:
        pass

    @abstractmethod
    async def run(self) -> None:
        pass

    @abstractmethod
    def send(self, message: Dict) -> None:
        pass

    def serialize_message(self, message: Dict) -> bytes:
        msg = json.dumps(message)
        if "DEBUGP" in os.environ:  # pragma: no cover
            print("\x1b[32mSEND>\x1b[0m", json.dumps(message, indent=2))
        return msg.encode()

    def deserialize_message(self, data: Union[str, bytes]) -> ParsedMessagePayload:
        obj = json.loads(data)

        if "DEBUGP" in os.environ:  # pragma: no cover
            print("\x1b[33mRECV>\x1b[0m", json.dumps(obj, indent=2))
        return obj



# ⛔ `PipeTransport` LIVED HERE AND IS GONE, with the Node driver, on
# 2026-08-28. It spawned `node` on the forked `cli.js` and spoke the same
# protocol the in-process server now answers; what removed it was not that
# it was unused but that it was measured redundant - 188 e2e passed on both
# transports, protocol parity on methods, parameter names, object types,
# initializer fields, events and parentage, and the realness gates green on
# the Python path.
#
# ⛔ The `Transport` ABC above STAYS, and it is not vestigial: it is the
# seam the in-process transport implements, and the reason the swap was one
# class rather than a rewrite of the client.
#
# ⛔ To get the old arm back for a comparison - which is the only thing it
# was still for - check out the last commit that carried it into a git
# worktree and point `INVPW_DRIVER_TREE` at it. `scripts/capture_protocol.py`
# reads that variable.
