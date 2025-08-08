import os
import json
import asyncio
import logging
from datetime import datetime, timezone
from homegpt.api import db
from homegpt.app.util import setup_logging, RateLimiter, next_time_of_day
from homegpt.app.ha import HAClient
from homegpt.app.openai_client import OpenAIClient
from homegpt.app.policy import SYSTEM_PASSIVE, SYSTEM_ACTIVE, ACTIONS_JSON_SCHEMA

# Environment config
LANG = os.environ.get("LANGUAGE", "en")
MODE = os.environ.get("MODE", "passive")
SUMMARIZE_TIME = os.environ.get("SUMMARIZE_TIME", "21:30")
ALLOWLIST = set(json.loads(os.environ.get("CONTROL_ALLOWLIST", "[]")))
MAX_ACTIONS_PER_HOUR = int(os.environ.get("MAX_ACTIONS_PER_HOUR", 10))
DRY_RUN = os.environ.get("DRY_RUN", "true").lower() == "true"
LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO")

setup_logging(LOG_LEVEL)
_LOGGER = logging.getLogger("homegpt")

EVENT_BUFFER = []  # recent events

async def save_analysis(mode, focus, summary, actions):
    """Insert analysis into DB and log it."""
    row = db.add_analysis(mode, focus, summary, json.dumps(actions))
    _LOGGER.info(f"Saved analysis #{row[0]} mode={mode} focus={focus}")
    return row

async def summarize_daily(ha, gpt):
    while True:
        await asyncio.sleep(await next_time_of_day(SUMMARIZE_TIME))
        if not EVENT_BUFFER:
            await ha.notify("HomeGPT Daily", "No notable events recorded today.")
            continue
        bullets = [f"{e['ts']} · {e['entity_id']} : {e['from']} → {e['to']}" for e in EVENT_BUFFER[-2000:]]
        prompt = f"Language: {LANG}.\nSummarize today's home activity from these lines:\n" + "\n".join(bullets)
        res = gpt.complete_json(SYSTEM_PASSIVE, prompt)
        text = res.get("text") or json.dumps(res, indent=2)
        await save_analysis("passive", "daily_summary", text, [])
        await ha.notify("HomeGPT – Daily Summary", text)
        EVENT_BUFFER.clear()

async def reactive_control(ha, gpt):
    limiter = RateLimiter(MAX_ACTIONS_PER_HOUR)
    async for evt in ha.websocket_events():
        if evt.get("event_type") != "state_changed": continue
        d = evt.get("data", {})
        entity_id = d.get("entity_id")
        ts = datetime.now(timezone.utc).isoformat()
        EVENT_BUFFER.append({"ts": ts, "entity_id": entity_id,
                             "from": (d.get("old_state") or {}).get("state"),
                             "to": (d.get("new_state") or {}).get("state")})

        if MODE != "active" or not entity_id: continue
        if not (entity_id in ALLOWLIST or entity_id.split(".")[0] in {"binary_sensor", "sensor", "person", "device_tracker"}):
            continue

        states = await ha.states()
        prompt = (
            f"Recent event:\n- entity: {entity_id}\n- from: {EVENT_BUFFER[-1]['from']}\n- to: {EVENT_BUFFER[-1]['to']}\n\n"
            "Current allowlist (ONLY act on these):\n" + "\n".join(sorted(ALLOWLIST)) +
            "\n\nCurrent states (subset):\n" + "\n".join(f"{s['entity_id']}={s['state']}" for s in states[:400])
        )
        plan = gpt.complete_json(SYSTEM_ACTIVE, prompt, schema=ACTIONS_JSON_SCHEMA)
        actions = []
        for a in plan.get("actions", []):
            svc = a.get("service", "")
            if "." not in svc: continue
            targets = [t for t in (a.get("entity_id") or []) if t in ALLOWLIST]
            if not targets: continue
            if not limiter.allow():
                await ha.notify("HomeGPT – rate limited", f"Skipped {svc} on {targets}")
                continue
            if DRY_RUN:
                actions.append({**a, "dry_run": True}); continue
            domain, service = svc.split(".", 1)
            await ha.call_service(domain, service, {"entity_id": targets, **(a.get("data") or {})})
            actions.append(a)

        if actions:
            await save_analysis("active", entity_id, f"Executed {len(actions)} actions", actions)
            await ha.notify("HomeGPT – Actions", json.dumps(actions, indent=2))

async def main():
    ha, gpt = HAClient(), OpenAIClient()
    try:
        await asyncio.wait([
            asyncio.create_task(summarize_daily(ha, gpt)),
            asyncio.create_task(reactive_control(ha, gpt)),
        ], return_when=asyncio.FIRST_EXCEPTION)
    finally:
        await ha.close()

if __name__ == "__main__":
    asyncio.run(main())
