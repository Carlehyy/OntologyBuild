"""Outbound companion tunnel.

The user's machine connects outward over WSS.  A loopback-only TCP listener is
created on the server and multiplexed through that WebSocket, so raw CDP is
never exposed on either machine's public network interface.
"""
from __future__ import annotations

import asyncio
import json
import struct
from dataclasses import dataclass, field

from fastapi import WebSocket, WebSocketDisconnect


@dataclass
class CompanionConnection:
    source_id: str
    websocket: WebSocket
    server: asyncio.AbstractServer | None = None
    port: int = 0
    next_stream_id: int = 1
    streams: dict[int, asyncio.StreamWriter] = field(default_factory=dict)
    tasks: set[asyncio.Task] = field(default_factory=set)
    send_lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    async def send_json(self, value: dict) -> None:
        async with self.send_lock:
            await self.websocket.send_text(json.dumps(value, ensure_ascii=False))

    async def send_bytes(self, stream_id: int, data: bytes) -> None:
        async with self.send_lock:
            await self.websocket.send_bytes(struct.pack("!I", stream_id) + data)

    async def start(self) -> None:
        self.server = await asyncio.start_server(self._local_client, "127.0.0.1", 0)
        self.port = int(self.server.sockets[0].getsockname()[1])

    async def _local_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        stream_id = self.next_stream_id
        self.next_stream_id += 1
        self.streams[stream_id] = writer
        try:
            await self.send_json({"type": "open", "streamId": stream_id})
            while data := await reader.read(64 * 1024):
                await self.send_bytes(stream_id, data)
        except (ConnectionError, RuntimeError, WebSocketDisconnect):
            pass
        finally:
            self.streams.pop(stream_id, None)
            try:
                await self.send_json({"type": "close", "streamId": stream_id})
            except Exception:
                pass
            writer.close()
            await writer.wait_closed()

    async def run(self) -> None:
        while True:
            message = await self.websocket.receive()
            if message.get("type") == "websocket.disconnect":
                raise WebSocketDisconnect(message.get("code", 1000))
            data = message.get("bytes")
            if data is not None:
                if len(data) < 4:
                    continue
                stream_id = struct.unpack("!I", data[:4])[0]
                writer = self.streams.get(stream_id)
                if writer and not writer.is_closing():
                    writer.write(data[4:])
                    await writer.drain()
                continue
            raw = message.get("text")
            if not raw:
                continue
            try:
                control = json.loads(raw)
            except ValueError:
                continue
            if control.get("type") == "close":
                writer = self.streams.pop(int(control.get("streamId") or 0), None)
                if writer:
                    writer.close()
            elif control.get("type") == "ping":
                await self.send_json({"type": "pong"})

    async def close(self) -> None:
        if self.server:
            self.server.close()
            await self.server.wait_closed()
        for writer in list(self.streams.values()):
            writer.close()
        self.streams.clear()
        for task in list(self.tasks):
            task.cancel()


class CompanionHub:
    def __init__(self) -> None:
        self._connections: dict[str, CompanionConnection] = {}
        self._lock = asyncio.Lock()

    async def register(self, source_id: str, websocket: WebSocket) -> CompanionConnection:
        connection = CompanionConnection(source_id, websocket)
        await connection.start()
        async with self._lock:
            old = self._connections.pop(source_id, None)
            self._connections[source_id] = connection
        if old:
            await old.close()
        return connection

    async def unregister(self, source_id: str, connection: CompanionConnection) -> None:
        async with self._lock:
            if self._connections.get(source_id) is connection:
                self._connections.pop(source_id, None)
        await connection.close()

    def endpoint(self, source_id: str) -> str | None:
        connection = self._connections.get(source_id)
        return f"http://127.0.0.1:{connection.port}" if connection and connection.port else None

    def is_online(self, source_id: str) -> bool:
        return bool(self.endpoint(source_id))


companion_hub = CompanionHub()
