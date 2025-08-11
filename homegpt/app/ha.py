import os
import json
import asyncio
import logging
from urllib.parse import quote

import aiohttp
import websockets

_LOGGER = logging.getLogger("homegpt.ha")

# Supervisor-provided auth and endpoints
SUPERVISOR_TOKEN = os.environ.get("SUPERVISOR_TOKEN")
BASE_HTTP = os.environ.get("SUPERVISOR_API", "http://supervisor/core/api")
WS_URL = "ws://supervisor/core/websocket"


class HAClient:
    """
    Thin async client for Home Assistant when running inside a Supervisor add-on.
    Uses REST for simple reads/writes and WebSocket for event stream + registries.
    """

    def __init__(self) -> None:
        if not SUPERVISOR_TOKEN:
            raise RuntimeError(
                "SUPERVISOR_TOKEN not set. Ensure your add-on config.yaml enables "
                "homeassistant_api: true (and restart the add-on), or provide a "
                "long-lived token via environment."
            )
        self.session = aiohttp.ClientSession(
            headers={
                "Authorization": f"Bearer {SUPERVISOR_TOKEN}",
                "Content-Type": "application/json",
            },
            timeout=aiohttp.ClientTimeout(total=60),
        )
        self._req_id = 1

    async def close(self) -> None:
        """Close the underlying HTTP session."""
        try:
            await self.session.close()
        except Exception:
            pass

    # ---------------- REST helpers ----------------

    async def states(self) -> list[dict]:
        """Return all entity states."""
        url = f"{BASE_HTTP}/states"
        async with self.session.get(url) as r:
            r.raise_for_status()
            return await r.json()

    async def call_service(self, domain: str, service: str, data: dict) -> dict:
        """Call a Home Assistant service."""
        url = f"{BASE_HTTP}/services/{domain}/{service}"
        async with self.session.post(url, data=json.dumps(data)) as r:
            txt = await r.text()
            if r.status >= 400:
                _LOGGER.error("Service call failed %s: %s", url, txt)
            r.raise_for_status()
            return json.loads(txt) if txt else {}

    async def notify(self, title: str, message: str, notification_id: str | None = None) -> dict:
        """Send a persistent notification."""
        data: dict = {"title": title, "message": message}
        if notification_id:
            data["notification_id"] = notification_id
        return await self.call_service("persistent_notification", "create", data)

    # ---------------- WebSocket helpers ----------------

    def _next_id(self) -> int:
        self._req_id += 1
        return self._req_id

    async def _ws_auth(self, ws) -> None:
        """
        Proper HA WS handshake:
        1) Server sends {"type": "auth_required"}
        2) Client sends {"type": "auth", "access_token": SUPERVISOR_TOKEN}
        3) Server sends {"type": "auth_ok"} (or "auth_invalid")
        """
        # 1) Read server greeting
        first = json.loads(await ws.recv())
        if first.get("type") != "auth_required":
            raise RuntimeError(f"WebSocket unexpected greeting: {first}")

        # 2) Send token
        await ws.send(json.dumps({"type": "auth", "access_token": SUPERVISOR_TOKEN}))

        # 3) Expect auth_ok (or auth_invalid)
        second = json.loads(await ws.recv())
        t = second.get("type")
        if t == "auth_ok":
            return
        if t == "auth_invalid":
            raise RuntimeError(f"WebSocket auth_invalid: {second.get('message') or second}")
        raise RuntimeError(f"WebSocket auth failed: {second}")

    async def _ws_once(self, req_type: str, payload: dict | None = None):
        """Open a short-lived WS, send a single request, return its .result."""
        req_id = self._next_id()
        async with websockets.connect(WS_URL, open_timeout=10, close_timeout=5) as ws:
            await self._ws_auth(ws)
            body = {"id": req_id, "type": req_type}
            if payload:
                body.update(payload)
            await ws.send(json.dumps(body))
            # Wait for matching id
            while True:
                msg = json.loads(await ws.recv())
                if msg.get("id") != req_id:
                    continue
                if msg.get("type") != "result":
                    raise RuntimeError(f"Unexpected WS message: {msg}")
                if not msg.get("success", False):
                    raise RuntimeError(f"WS call {req_type} failed: {msg}")
                return msg.get("result")

    async def websocket_events(self):
        """
        Async generator yielding Home Assistant state_changed events.
        Automatically reconnects on errors.
        """
        while True:
            try:
                async with websockets.connect(WS_URL, open_timeout=10, close_timeout=5) as ws:
                    await self._ws_auth(ws)
                    # subscribe
                    await ws.send(
                        json.dumps(
                            {"id": self._next_id(), "type": "subscribe_events", "event_type": "state_changed"}
                        )
                    )
                    # ack
                    ack = json.loads(await ws.recv())
                    if not ack.get("success", False):
                        raise RuntimeError(f"subscribe_events failed: {ack}")
                    _LOGGER.info("Subscribed to state_changed events")

                    async for raw in ws:
                        try:
                            msg = json.loads(raw)
                            if msg.get("type") == "event":
                                yield msg.get("event")
                        except Exception as e:
                            _LOGGER.warning("Error decoding WS event: %s", e)
            except Exception as e:
                _LOGGER.error("WS disconnected (%s); reconnecting in 5s...", e)
                await asyncio.sleep(5)

    # ---------------- Registries ----------------

    async def list_areas(self) -> list[dict]:
        return await self._ws_once("config/area_registry/list")

    async def list_devices(self) -> list[dict]:
        return await self._ws_once("config/device_registry/list")

    async def list_entities(self) -> list[dict]:
        return await self._ws_once("config/entity_registry/list")

    async def list_registries(self) -> dict:
        """Fetch areas, devices, entities concurrently."""
        areas, devices, entities = await asyncio.gather(
            self.list_areas(), self.list_devices(), self.list_entities()
        )
        return {"areas": areas, "devices": devices, "entities": entities}

    # ---------------- History & Statistics ----------------

    async def history_period(
        self,
        start_iso: str | None,
        end_iso: str | None,
        entity_ids: list[str] | None = None,
        minimal_response: bool = True,
        include_start_time_state: bool = True,
        significant_changes_only: bool | None = None,
    ) -> list:
        """
        Wrapper for:
          GET /api/history/period/<start>?end_time=...&filter_entity_id=a,b&minimal_response=1&include_start_time_state=1

        NOTE: Newer HA builds will 400 if filter_entity_id is omitted. We therefore
        require a non-empty entity_ids and skip the call if empty.
        """
        # Build URL path safely (allow colon, T, +, -, Z)
        start_path = ""
        if start_iso:
            start_path = "/" + quote(start_iso, safe=":T+-Z")

        url = f"{BASE_HTTP}/history/period{start_path}"

        # Require entity list to avoid 400
        if not entity_ids:
            _LOGGER.info("history_period skipped: empty entity_ids")
            return []

        params: dict[str, str] = {
            "end_time": end_iso or "",
            "filter_entity_id": ",".join(entity_ids),
        }
        if minimal_response:
            params["minimal_response"] = "1"
        if include_start_time_state:
            params["include_start_time_state"] = "1"
        if significant_changes_only is not None:
            params["significant_changes_only"] = "1" if significant_changes_only else "0"

        # First attempt
        async with self.session.get(url, params=params) as r:
            if r.status == 400 and minimal_response:
                # Retry without minimal_response
                _LOGGER.warning("history_period 400; retrying without minimal_response")
                params.pop("minimal_response", None)
                async with self.session.get(url, params=params) as r2:
                    r2.raise_for_status()
                    data = await r2.json()
                    # Diagnostics
                    try:
                        groups = len(data) if isinstance(data, list) else 0
                        rows = sum(len(g) for g in data if isinstance(g, list))
                        _LOGGER.info("HA history OK (retry): groups=%d total_rows=%d", groups, rows)
                    except Exception:
                        pass
                    return data

            r.raise_for_status()
            data = await r.json()
            # Diagnostics
            try:
                groups = len(data) if isinstance(data, list) else 0
                rows = sum(len(g) for g in data if isinstance(g, list))
                _LOGGER.info("HA history OK: groups=%d total_rows=%d", groups, rows)
            except Exception:
                pass
            return data

    async def statistics_during(
        self,
        start_iso: str,
        end_iso: str,
        statistic_ids: list[str],
        period: str = "hour",
    ) -> list[dict]:
        """
        GET /api/statistics/during?start_time=...&end_time=...&statistic_ids=a,b&period=hour
        Good for energy/power series without huge payloads.
        """
        params = {
            "start_time": start_iso,
            "end_time": end_iso,
            "period": period,
            "statistic_ids": ",".join(statistic_ids),
        }
        url = f"{BASE_HTTP}/statistics/during"
        async with self.session.get(url, params=params) as r:
            r.raise_for_status()
            return await r.json()
