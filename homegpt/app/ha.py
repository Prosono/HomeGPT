import os
import json
import asyncio
import logging
import aiohttp
import websockets

_LOGGER = logging.getLogger("homegpt.ha")

SUPERVISOR_TOKEN = os.environ.get("SUPERVISOR_TOKEN")
BASE_HTTP = os.environ.get("SUPERVISOR_API", "http://supervisor/core/api")
WS_URL = "ws://supervisor/core/websocket"

class HAClient:
    def __init__(self):
        self.session = aiohttp.ClientSession(headers={
            "Authorization": f"Bearer {SUPERVISOR_TOKEN}",
            "Content-Type": "application/json",
        })
        self._listeners = []  # callback functions for events

    async def close(self):
        await self.session.close()

    async def states(self):
        async with self.session.get(f"{BASE_HTTP}/states") as r:
            r.raise_for_status()
            return await r.json()

    async def call_service(self, domain: str, service: str, data: dict):
        url = f"{BASE_HTTP}/services/{domain}/{service}"
        async with self.session.post(url, json=data) as r:
            txt = await r.text()
            if r.status >= 400:
                _LOGGER.error("Service call failed %s: %s", url, txt)
            r.raise_for_status()
            return json.loads(txt) if txt else {}

    async def notify(self, title: str, message: str, notification_id: str | None = None):
        data = {"title": title, "message": message}
        if notification_id:
            data["notification_id"] = notification_id
        return await self.call_service("persistent_notification", "create", data)

    def add_listener(self, callback):
        """Register a callback that will be called with each event."""
        self._listeners.append(callback)

    async def _handle_event(self, event):
        """Call all registered listeners with the event."""
        for cb in self._listeners:
            try:
                await cb(event)
            except Exception as e:
                _LOGGER.error(f"Error in event listener {cb}: {e}")

    async def websocket_events(self):
        """Persistent websocket listener with auto-reconnect."""
        while True:
            try:
                _LOGGER.info("Connecting to Home Assistant WebSocket...")
                async with websockets.connect(WS_URL) as ws:
                    # Authenticate
                    auth_msg = await ws.recv()  # auth_required
                    _LOGGER.debug(f"Auth message: {auth_msg}")
                    await ws.send(json.dumps({
                        "type": "auth",
                        "access_token": SUPERVISOR_TOKEN
                    }))
                    msg = json.loads(await ws.recv())
                    if msg.get("type") != "auth_ok":
                        raise Exception(f"WS auth failed: {msg}")
                    _LOGGER.info("WebSocket authenticated")

                    # Subscribe to state_changed events
                    await ws.send(json.dumps({
                        "id": 1,
                        "type": "subscribe_events",
                        "event_type": "state_changed"
                    }))
                    result = json.loads(await ws.recv())
                    if not result.get("success", False):
                        raise Exception(f"Subscribe failed: {result}")
                    _LOGGER.info("Subscribed to state_changed events")

                    # Listen for events
                    async for raw in ws:
                        try:
                            evt = json.loads(raw)
                            if evt.get("type") == "event":
                                _LOGGER.debug(f"WS Event: {evt['event']}")
                                await self._handle_event(evt["event"])
                        except json.JSONDecodeError:
                            _LOGGER.warning(f"Invalid JSON from WS: {raw}")

            except Exception as e:
                _LOGGER.error(f"WebSocket error: {e}, retrying in 5s...")
                await asyncio.sleep(5)

