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

WS_OPTS = {
    # Accept arbitrarily large frames (entity registry can be big)
    "max_size": None,            # or set to e.g. 16 * 1024 * 1024
    # If you still hit size issues after inflation, you can add:
    # "compression": None,
    "ping_interval": 30,
    "ping_timeout": 30,
}

class HAClient:
    def __init__(self):
        if not SUPERVISOR_TOKEN:
            raise RuntimeError(
                "SUPERVISOR_TOKEN not set. Ensure your add-on config.yaml enables "
                "homeassistant_api: true (and restart the add-on), or provide a "
                "long-lived token flow."
            )

        self.session = aiohttp.ClientSession(headers={
            "Authorization": f"Bearer {SUPERVISOR_TOKEN}",
            "Content-Type": "application/json",
        })
        self._listeners = []  # callback functions for events
        self._req_id = 100    # ids for one-shot WS calls

    async def close(self):
        """Close aiohttp session."""
        await self.session.close()

    async def states(self):
        """Get all entity states from Home Assistant."""
        async with self.session.get(f"{BASE_HTTP}/states") as r:
            r.raise_for_status()
            return await r.json()

    async def call_service(self, domain: str, service: str, data: dict):
        """Call a Home Assistant service."""
        url = f"{BASE_HTTP}/services/{domain}/{service}"
        async with self.session.post(url, json=data) as r:
            txt = await r.text()
            if r.status >= 400:
                _LOGGER.error("Service call failed %s: %s", url, txt)
            r.raise_for_status()
            return json.loads(txt) if txt else {}

    async def notify(self, title: str, message: str, notification_id: str | None = None):
        """Send a persistent notification to Home Assistant."""
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
        """
        Async generator that yields state_changed events from Home Assistant.
        Will automatically reconnect if the connection drops.
        """
        while True:
            try:
                _LOGGER.info("Connecting to Home Assistant WebSocket...")
                # >>> pass WS_OPTS here <<<
                async with websockets.connect(WS_URL, **WS_OPTS) as ws:
                    # Authenticate
                    auth_msg = await ws.recv()  # Expect auth_required
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

                    # Yield incoming events
                    async for raw in ws:
                        try:
                            evt = json.loads(raw)
                            if evt.get("type") == "event":
                                event_data = evt["event"]
                                _LOGGER.debug(f"WS Event: {event_data}")

                                # Send to registered listeners
                                await self._handle_event(event_data)

                                # Yield to async for consumers
                                yield event_data

                        except json.JSONDecodeError:
                            _LOGGER.warning(f"Invalid JSON from WS: {raw}")

            except Exception as e:
                _LOGGER.error(f"WebSocket error: {e}, retrying in 5s...")
                await asyncio.sleep(5)

    # ---------------------------------------------------------------------
    # Registry helpers: short-lived WS connections so we don't touch the
    # streaming connection above. Each call authenticates, requests data,
    # waits for the matching id, returns result, and closes.
    # ---------------------------------------------------------------------

    def _next_id(self) -> int:
        self._req_id += 1
        return self._req_id

    async def _ws_once(self, req_type: str, payload: dict | None = None):
        """
        Open a short-lived WebSocket connection, auth, request `req_type`,
        return the .result, then close.
        """
        _LOGGER.debug("WS once -> %s", req_type)
        # >>> and here too <<<
        async with websockets.connect(WS_URL, **WS_OPTS) as ws:
            # auth_required
            await ws.recv()
            # auth
            await ws.send(json.dumps({
                "type": "auth",
                "access_token": SUPERVISOR_TOKEN
            }))
            auth_reply = json.loads(await ws.recv())
            if auth_reply.get("type") != "auth_ok":
                raise RuntimeError(f"WS auth failed (one-shot): {auth_reply}")

            req_id = self._next_id()
            msg = {"id": req_id, "type": req_type}
            if payload:
                msg.update(payload)
            await ws.send(json.dumps(msg))

            while True:
                raw = await ws.recv()
                try:
                    resp = json.loads(raw)
                except json.JSONDecodeError:
                    _LOGGER.warning("Non-JSON WS reply on one-shot: %r", raw)
                    continue
                if resp.get("id") == req_id:
                    if resp.get("success") is False:
                        raise RuntimeError(f"HA WS error for {req_type}: {resp}")
                    return resp.get("result", [])

    async def list_areas(self):
        """Return area registry list."""
        return await self._ws_once("config/area_registry/list")

    async def list_devices(self):
        """Return device registry list."""
        return await self._ws_once("config/device_registry/list")

    async def list_entities(self):
        """Return entity registry list."""
        return await self._ws_once("config/entity_registry/list")

    async def list_registries(self):
        """Fetch areas, devices, entities in parallel."""
        areas, devices, entities = await asyncio.gather(
            self.list_areas(),
            self.list_devices(),
            self.list_entities(),
        )
        return {"areas": areas, "devices": devices, "entities": entities}
