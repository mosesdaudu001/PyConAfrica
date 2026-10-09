"""
A TCP server that replies after a fixed delay. Runs in its OWN process, on its
own cores, so that the thing being measured in gil_bench.py is the client's
ability to overlap blocking reads and nothing else.

Protocol: client sends 8 bytes, server waits DELAY_S, server sends 8 bytes back.
"""
import asyncio
import os
import sys

DELAY_S = float(os.environ.get("GIL_DELAY_S", "0.005"))
PORT = int(os.environ.get("GIL_PORT", "9911"))


async def handle(reader, writer):
    try:
        while True:
            data = await reader.readexactly(8)
            await asyncio.sleep(DELAY_S)
            writer.write(data)
            await writer.drain()
    except (asyncio.IncompleteReadError, ConnectionResetError, BrokenPipeError):
        pass
    finally:
        try:
            writer.close()
        except Exception:  # noqa: BLE001
            pass


async def main():
    server = await asyncio.start_server(handle, "127.0.0.1", PORT, backlog=512)
    print(f"delay_server ready port={PORT} delay={DELAY_S}s", flush=True)
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        sys.exit(0)
