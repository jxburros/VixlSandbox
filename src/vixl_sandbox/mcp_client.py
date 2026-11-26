"""Talk to one Vixl version's MCP server, running as its own process.

``VixlServer(python, workspace)`` starts ``python -m vixl mcp --workspace <workspace>`` with that version's
interpreter and keeps the stdio connection open on a background event loop, so a session can call tools from its
own thread, round after round. Releases whose ``mcp`` command has no ``--workspace`` option are started with the
workspace as their working folder instead.
"""

import asyncio
from pathlib import Path
import threading

STARTUP_TIMEOUT = 120  # the first start of a fresh environment compiles bytecode and loads fonts
CALL_TIMEOUT = 600


class ServerError(RuntimeError):
    pass


class VixlServer:
    def __init__(self, python, workspace, env=None, log=None, startup_timeout=STARTUP_TIMEOUT):
        self.python, self.workspace, self.env = str(python), Path(workspace), env
        # The server's own messages (stderr) go to a log file beside the session, not to the app's console.
        if log:
            Path(log).parent.mkdir(parents=True, exist_ok=True)
        self.log = open(log, "a", encoding="utf-8") if log else None  # noqa: SIM115 - closed in close()
        self.instructions, self.tools = "", []
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self.loop.run_forever, daemon=True)
        self.thread.start()
        self.queue = None
        self.ready = threading.Event()
        self.failure = None
        errors = []
        for args in (["-m", "vixl", "mcp", "--workspace", str(self.workspace)], ["-m", "vixl", "mcp"]):
            self.ready.clear()
            self.failure = None
            self.main = asyncio.run_coroutine_threadsafe(self._serve(args), self.loop)
            if not self.ready.wait(startup_timeout):
                self.main.cancel()
                errors.append(f"no answer after {startup_timeout} s")
                break
            if self.failure is None:
                return
            errors.append(str(self.failure))
        self.close()
        raise ServerError("the Vixl server did not start: " + "; ".join(errors))

    async def _serve(self, args):
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        params = StdioServerParameters(command=self.python, args=args, cwd=str(self.workspace), env=self.env)
        try:
            stream = stdio_client(params, errlog=self.log) if self.log else stdio_client(params)
            async with stream as (read, write), ClientSession(read, write) as session:
                init = await session.initialize()
                self.instructions = getattr(init, "instructions", None) or ""
                self.tools = [{"name": t.name, "description": t.description or "", "input_schema": t.inputSchema}
                              for t in (await session.list_tools()).tools]
                self.queue = asyncio.Queue()
                self.ready.set()
                while True:
                    item = await self.queue.get()
                    if item is None:
                        return
                    name, arguments, future = item
                    try:
                        result = await session.call_tool(name, arguments)
                    except Exception as exc:  # noqa: BLE001 - reported to the model as a failed call
                        future.set_exception(exc)
                    else:
                        future.set_result(result)
        except BaseException as exc:  # noqa: BLE001 - a server that fails to start is reported, not raised here
            if not self.ready.is_set():
                self.failure = exc
                self.ready.set()
            elif not isinstance(exc, asyncio.CancelledError):
                self.failure = exc

    def call(self, name, arguments, timeout=CALL_TIMEOUT):
        """Return (is_error, blocks): text and image blocks in the lab's neutral form."""
        if self.queue is None or self.main.done():
            raise ServerError(f"the Vixl server has stopped ({self.failure or 'no reason given'})")
        result = asyncio.run_coroutine_threadsafe(self._call(name, arguments), self.loop).result(timeout)
        blocks = []
        for item in result.content:
            if getattr(item, "type", None) == "image":
                blocks.append({"type": "image", "media_type": item.mimeType, "data": item.data})
            else:
                blocks.append({"type": "text", "text": getattr(item, "text", "")})
        return bool(getattr(result, "isError", False)), blocks or [{"type": "text", "text": "(no output)"}]

    async def _call(self, name, arguments):
        future = self.loop.create_future()
        await self.queue.put((name, arguments, future))
        return await future

    def close(self):
        if self.loop.is_closed():
            return
        if self.queue is not None:
            self.loop.call_soon_threadsafe(self.queue.put_nowait, None)
            try:
                self.main.result(10)
            except Exception:  # noqa: BLE001 - closing must not fail
                self.main.cancel()
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join(10)
        if not self.loop.is_running():
            self.loop.close()
        if self.log is not None:
            self.log.close()
