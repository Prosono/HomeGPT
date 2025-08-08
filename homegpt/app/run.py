import os
import json
import asyncio
import logging
from datetime import datetime, timezone

from homegpt.util import setup_logging, RateLimiter, next_time_of_day
from homegpt.ha import HAClient
from homegpt.openai_client import OpenAIClient
from homegpt.policy import SYSTEM_PASSIVE, SYSTEM_ACTIVE, ACTIONS_JSON_SCHEMA

LANG = os.environ.get("LANGUAGE", "en")
MODE = os.environ.get("MODE", "passive")
SUMMARIZE_TIME = os.environ.get("SUMMARIZE_TIME", "21:30")
ALLOWLIST = set(json.loads(os.environ.get("CONTROL_ALLOWLIST", "[]")))
MAX_ACTIONS_PER_HOUR = int(os.environ.get("MAX_ACTIONS_PER_HOUR", 10))
DRY_RUN = os.environ.get("DRY_RUN", "true").lower() == "true"
LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO")

setup_logging(LOG_LEVEL)
_LOGGER = logging.getLogger("homegpt")

EVENT_BUFFER = []  # store tuples (ts_iso, entity_id, from, to)

async def summarize_daily(ha: HAClient, gpt: OpenAIClient):
    while True:
        secs = await next_time_of_day(SUMMARIZE_TIME)
        await asyncio.sleep(secs)
        if not EVENT_BUFFER:
            await ha.notify("HomeGPT Daily", "No notable events recorded today.")
            continue
        bullets = [
            f"{e['ts']} · {e['entity_id']} : {e['from']} → {e['to']}"
            for e in EVENT_BUFFER[-2000:]
        ]
        user = (
            f"Language: {LANG}.\n"
            f"Summarize today's home activity from these lines (newest last).\n"
            + "\n".join(bullets)
        )
        res = gpt.complete_json(SYSTEM_PASSIVE, user)
        text = res.get("text") or json.dumps(res, indent=2)
        await ha.notify("HomeGPT – Daily Summary", text)
        EVENT_BUFFER.clear()

async def reactive_control(ha: HAClient, gpt: OpenAIClient):
    limiter = RateLimiter(MAX_ACTIONS_PER_HOUR)
    async for evt in ha.websocket_events():
        if evt.get("event_type") != "state_changed":
            continue
        data = evt.get("data", {})
        entity_id = data.get("entity_id")
        old = (data.get("old_state") or {}).get("state")
        new = (data.get("new_state") or {}).get("state")
        ts = datetime.now(timezone.utc).isoformat()
        EVENT_BUFFER.append({"ts": ts, "entity_id": entity_id, "from": old, "to": new})

        if MODE != "active" or not entity_id:
            continue

        trigger_ok = (
            entity_id in ALLOWLIST
            or entity_id.split(".")[0] in {"binary_sensor", "sensor", "person", "device_tracker"}
        )
        if not trigger_ok:
            continue

        states = await ha.states()
        allow = sorted(list(ALLOWLIST))
        user = (
            "Recent event:\n"
            f"- entity: {entity_id}\n- from: {old}\n- to: {new}\n\n"
            "Current allowlist (ONLY act on these):\n"
            + "\n".join(allow)
            + "\n\n"
            "Current states (subset):\n"
            + "\n".join(f"{s['entity_id']}={s['state']}" for s in states[:400])
        )
        plan = gpt.complete_json(SYSTEM_ACTIVE, user, schema=ACTIONS_JSON_SCHEMA)
        actions = plan.get("actions", [])
        if not actions:
            continue

        executed = []
        for a in actions:
            service = a.get("service", "")
            if "." not in service:
                continue
            domain, svc = service.split(".", 1)
            targets = a.get("entity_id")
            if isinstance(targets, str):
                targets = [targets]
            targets = [t for t in (targets or []) if t in ALLOWLIST]
            if not targets:
                continue
            data = a.get("data") or {}

            if not limiter.allow():
                await ha.notify("HomeGPT – rate limited", f"Skipped action {service} on {targets} (rate limit)")
                continue

            if DRY_RUN:
                executed.append({"service": service, "targets": targets, "data": data, "dry_run": True})
                continue

            await ha.call_service(domain, svc, {"entity_id": targets, **data})
            executed.append({"service": service, "targets": targets, "data": data})

        if executed:
            await ha.notify("HomeGPT – Actions", json.dumps(executed, indent=2))

async def main():
    ha = HAClient()
    gpt = OpenAIClient()
    try:
        tasks = [
            asyncio.create_task(summarize_daily(ha, gpt)),
            asyncio.create_task(reactive_control(ha, gpt)),
        ]
        await asyncio.wait(tasks, return_when=asyncio.FIRST_EXCEPTION)
    finally:
        await ha.close()

if __name__ == "__main__":
    asyncio.run(main())
