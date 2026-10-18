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
from typing import Any

from invisible_playwright._pw._impl._connection import Connection
from invisible_playwright._pw._impl._object_factory import create_remote_object
from invisible_playwright._juggler.transport import make_transport
from invisible_playwright._pw.async_api._generated import Playwright as AsyncPlaywright


class PlaywrightContextManager:
    def __init__(self) -> None:
        self._connection: Connection
        self._exit_was_called = False

    async def __aenter__(self) -> AsyncPlaywright:
        loop = asyncio.get_running_loop()
        # MODIFIED by invisible_playwright: the transport is the in-process
        # server, from `make_transport`. The Node driver was removed on
        # 2026-08-28, and `INVPW_TRANSPORT=driver` is refused with a message
        # that says so - see `_juggler/transport.py`.
        transport = make_transport(loop)
        self._connection = Connection(
            None,
            create_remote_object,
            transport,
            loop,
        )
        # MODIFIED by invisible_playwright: the one bridge between the two
        # object graphs. See the sync entry point's `_context_manager.py` for
        # why - this is the same line, needed for the same reason, on the
        # other facade.
        transport.bind_impl_objects(self._connection._objects,
                                   self._connection.deliver_event)
        loop.create_task(self._connection.run())
        playwright_future = self._connection.playwright_future

        done, _ = await asyncio.wait(
            {self._connection._transport.on_error_future, playwright_future},
            return_when=asyncio.FIRST_COMPLETED,
        )
        if not playwright_future.done():
            playwright_future.cancel()
        playwright = AsyncPlaywright(next(iter(done)).result())
        playwright.stop = self.__aexit__  # type: ignore
        return playwright

    async def start(self) -> AsyncPlaywright:
        return await self.__aenter__()

    async def __aexit__(self, *args: Any) -> None:
        if self._exit_was_called:
            return
        self._exit_was_called = True
        await self._connection.stop_async()
