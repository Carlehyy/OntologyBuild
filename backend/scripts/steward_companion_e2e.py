#!/usr/bin/env python3
"""End-to-end protocol smoke test for the distributable browser companion.

It uses a loopback fake CDP HTTP server and a real Node.js companion process,
then proves bidirectional TCP bytes traverse the authenticated WebSocket mux.
No external site or credential is required.
"""
from __future__ import annotations

import asyncio
import json
import struct
import sys
from pathlib import Path

import websockets


async def main() -> None:
    fake_ready = asyncio.Event()

    async def fake_cdp(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        request = await reader.readuntil(b"\r\n\r\n")
        assert request.startswith(b"GET /json/version ")
        body = json.dumps({
            "Browser": "Chrome/CompanionE2E", "Protocol-Version": "1.3",
            "webSocketDebuggerUrl": "ws://127.0.0.1/devtools/browser/e2e",
        }).encode()
        writer.write(
            b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
            + f"Content-Length: {len(body)}\r\nConnection: close\r\n\r\n".encode() + body
        )
        await writer.drain()
        fake_ready.set()
        writer.close()
        await writer.wait_closed()

    cdp_server = await asyncio.start_server(fake_cdp, "127.0.0.1", 0)
    cdp_port = int(cdp_server.sockets[0].getsockname()[1])
    completed: asyncio.Future[None] = asyncio.get_running_loop().create_future()

    async def platform(websocket) -> None:
        auth = json.loads(await asyncio.wait_for(websocket.recv(), 5))
        assert auth == {"type": "auth", "sourceId": "e2e-source", "token": "e2e-token"}
        await websocket.send(json.dumps({"type": "ready", "sourceId": "e2e-source"}))
        await websocket.send(json.dumps({"type": "open", "streamId": 7}))
        request = b"GET /json/version HTTP/1.1\r\nHost: 127.0.0.1\r\nConnection: close\r\n\r\n"
        await websocket.send(struct.pack("!I", 7) + request)
        chunks = bytearray()
        while b"Chrome/CompanionE2E" not in chunks:
            packet = await asyncio.wait_for(websocket.recv(), 5)
            if isinstance(packet, bytes):
                assert struct.unpack("!I", packet[:4])[0] == 7
                chunks.extend(packet[4:])
        assert b"HTTP/1.1 200 OK" in chunks
        await websocket.send(json.dumps({"type": "close", "streamId": 7}))
        if not completed.done():
            completed.set_result(None)
        await asyncio.sleep(0.1)

    websocket_server = await websockets.serve(platform, "127.0.0.1", 0)
    websocket_port = int(websocket_server.sockets[0].getsockname()[1])
    script = Path(__file__).resolve().parents[1] / "app/data_channel/steward/companion_client.mjs"
    process = await asyncio.create_subprocess_exec(
        "node", str(script), "--server", f"http://127.0.0.1:{websocket_port}",
        "--source", "e2e-source", "--token", "e2e-token", "--cdp-port", str(cdp_port),
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    try:
        await asyncio.wait_for(completed, 15)
        await asyncio.wait_for(fake_ready.wait(), 2)
        print("companion e2e passed: authenticated WSS mux transported CDP HTTP bytes")
    finally:
        process.terminate()
        await process.communicate()
        websocket_server.close()
        await websocket_server.wait_closed()
        cdp_server.close()
        await cdp_server.wait_closed()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as exc:
        print(f"companion e2e failed: {exc}", file=sys.stderr)
        raise
