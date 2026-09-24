"""WebSocket connection hub broadcasting live state to UI clients."""
from __future__ import annotations

import asyncio
import json
import time

from fastapi import WebSocket


class Hub:
    def __init__(self) -> None:
        self.clients: dict[WebSocket, set[str]] = {}

    async def connect(self, ws: WebSocket) -> None:
        """Accept only — call `register` AFTER the hello message is sent."""
        await ws.accept()

    def register(self, ws: WebSocket) -> None:
        self.clients[ws] = set()

    def disconnect(self, ws: WebSocket) -> None:
        self.clients.pop(ws, None)

    def subscribe(self, ws: WebSocket, symbol: str) -> None:
        if ws in self.clients:
            self.clients[ws].add(symbol)

    def subscribed(self, symbol: str) -> bool:
        return any(symbol in subs for subs in self.clients.values())

    async def broadcast(self, payload: dict, symbol: str | None = None) -> None:
        """Send to all clients (optionally only those subscribed to `symbol`)."""
        if not self.clients:
            return
        msg = json.dumps(payload, default=str)
        dead = []
        for ws, subs in list(self.clients.items()):
            if symbol is not None and symbol not in subs:
                continue
            try:
                await ws.send_text(msg)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(ws)
