"""Ticket-authenticated live browser WebSocket (kept outside JWT dependencies)."""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.config import settings
from app.data_channel.steward.browser_runtime import browser_manager

router = APIRouter()


@router.websocket("/conversations/{conversation_id}/browser/live")
async def browser_live(websocket: WebSocket, conversation_id: str, ticket: str = ""):
    valid, _user_id = browser_manager.redeem_ticket(ticket, conversation_id)
    if not valid:
        await websocket.close(code=4401, reason="invalid or expired browser ticket")
        return
    await websocket.accept()
    stopped = asyncio.Event()

    async def send_frames() -> None:
        interval = max(100, int(settings.steward_browser_frame_interval_ms)) / 1000
        while not stopped.is_set():
            try:
                frame = await browser_manager.screenshot(conversation_id)
                await websocket.send_json({"type": "frame", **frame})
            except Exception as exc:  # noqa: BLE001
                await websocket.send_json({"type": "error", "message": str(exc)})
                await asyncio.sleep(1)
            await asyncio.sleep(interval)

    async def receive_input() -> None:
        while not stopped.is_set():
            message = await websocket.receive_json()
            if message.get("type") == "ping":
                await websocket.send_json({"type": "pong"})
                continue
            try:
                await browser_manager.input(conversation_id, message)
            except Exception as exc:  # noqa: BLE001 — 单个坏按键不应断开实时会话
                await websocket.send_json({"type": "error", "message": str(exc)})

    sender = asyncio.create_task(send_frames())
    receiver = asyncio.create_task(receive_input())
    try:
        done, _ = await asyncio.wait({sender, receiver}, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            task.result()
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        stopped.set()
        sender.cancel()
        receiver.cancel()
        await asyncio.gather(sender, receiver, return_exceptions=True)
