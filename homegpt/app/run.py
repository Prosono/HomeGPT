"""
Core runtime for HomeGPT.

This module orchestrates the daily summarisation task and the reactive
control loop.  It has been updated to guard against unexpected
exceptions inside those loops so that one failure does not crash the
entire add‑on.  It also uses ``asyncio.gather`` to run both tasks
concurrently instead of waiting for the first exception.
"""

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

EVENT_BUFFER: list[dict] = []  # recent events


async def save_analysis(mode: str, focus: str, summary: str, actions: list):
    """
    Insert analysis into the database and log it.
    """
    row = db.add_analysis(mode, focus, summary, json.dumps(actions))
    _LOGGER.info(f"Saved analysis #{row[0]} mode={mode} focus={focus}")
    return row


async def summarize_daily(ha: HAClient, gpt: OpenAIClient) -> None:
    """
    Periodically summarise the buffered events at a configured time of day.

    If an exception occurs while sleeping or summarising, it is logged and
    the loop continues rather than exiting.  This prevents a single
    failure from terminating the entire process.
    """
    while True:
        try:
            wait_seconds = await next_time_of_day(SUMMARIZE_TIME)
            await asyncio.sleep(wait_seconds)
            if not EVENT_BUFFER:
                await ha.notify("HomeGPT Daily", "No notable events recorded today.")
                continue
            bullets = [
                f"{e['ts']} · {e['entity_id']} : {e['from']} → {e['to']}"
                for e in EVENT_BUFFER[-2000:]
            ]
            prompt = (
                f"Language: {LANG}.\nSummarize today's home activity from these lines:\n"
                + "\n".join(bullets)
            )
            res = gpt.complete_json(SYSTEM_PASSIVE, prompt)
            text = res.get("text") or json.dumps(res, indent=2)
            await save_analysis("passive", "daily_summary", text, [])
            await ha.notify("HomeGPT – Daily Summary", text)
            EVENT_BUFFER.clear()
        except Exception as exc:
            _LOGGER.exception("Error in summarize_daily: %s", exc)
            # continue looping on error
            continue


async def reactive_control(ha: HAClient, gpt: OpenAIClient) -> None:
    """
    Listen for real‑time Home Assistant events and, in active mode, call
    the language model to propose actions.  Rate limits and allowlists
    are honoured.  Errors within the loop are logged and ignored so that
    subsequent events continue to be processed.
    """
    limiter = RateLimiter(MAX_ACTIONS_PER_HOUR)
    async for evt in ha.websocket_events():
        try:
            if evt.get("event_type") != "state_changed":
                continue
            d = evt.get("data", {})
            entity_id = d.get("entity_id")
            ts = datetime.now(timezone.utc).isoformat()
            EVENT_BUFFER.append({
                "ts": ts,
                "entity_id": entity_id,
                "from": (d.get("old_state") or {}).get("state"),
                "to": (d.get("new_state") or {}).get("state"),
            })
            # In passive mode or without an entity_id we just buffer events
            if MODE != "active" or not entity_id:
                continue
            # Only act on explicitly allow‑listed entities or sensors/persons/etc.
            if not (
                entity_id in ALLOWLIST
                or entity_id.split(".")[0] in {"binary_sensor", "sensor", "person", "device_tracker"}
            ):
                continue
            states = await ha.states()
            prompt = (
                f"Recent event:\n- entity: {entity_id}\n- from: {EVENT_BUFFER[-1]['from']}\n- to: {EVENT_BUFFER[-1]['to']}\n\n"
                "Current allowlist (ONLY act on these):\n" + "\n".join(sorted(ALLOWLIST))
                + "\n\nCurrent states (subset):\n"
                + "\n".join(f"{s['entity_id']}={s['state']}" for s in states[:400])
            )
            plan = gpt.complete_json(SYSTEM_ACTIVE, prompt, schema=ACTIONS_JSON_SCHEMA)
            actions: list[dict] = []
            for a in plan.get("actions", []):
                svc = a.get("service", "")
                if "." not in svc:
                    continue
                targets = [t for t in (a.get("entity_id") or []) if t in ALLOWLIST]
                if not targets:
                    continue
                if not limiter.allow():
                    await ha.notify("HomeGPT – rate limited", f"Skipped {svc} on {targets}")
                    continue
                if DRY_RUN:
                    actions.append({**a, "dry_run": True})
                    continue
                domain, service = svc.split(".", 1)
                await ha.call_service(domain, service, {"entity_id": targets, **(a.get("data") or {})})
                actions.append(a)
            if actions:
                await save_analysis("active", entity_id, f"Executed {len(actions)} actions", actions)
                await ha.notify("HomeGPT – Actions", json.dumps(actions, indent=2))
        except Exception as exc:
            _LOGGER.exception("Error processing event: %s", exc)
            continue


async def main() -> None:
    """
    Entry point for the HomeGPT core process.

    Runs the daily summariser and reactive controller concurrently. The
    tasks will keep running until the process is interrupted. When
    shutting down, the Home Assistant client is closed gracefully.
    """
    # Load the same config the API uses so we honor `model: gpt-5`
    import yaml
    import logging
    from pathlib import Path

    CONFIG_PATH = Path("/config/homegpt_config.yaml")
    cfg = {}
    if CONFIG_PATH.exists():
        try:
            cfg = yaml.safe_load(CONFIG_PATH.read_text()) or {}
        except Exception as e:
            logging.getLogger("HomeGPT").warning(
                "Failed to load config: %s", e
            )

    model = cfg.get("model", "gpt-5")
    ha, gpt = HAClient(), OpenAIClient(model=model)

    try:
        await asyncio.gather(
            summarize_daily(ha, gpt),
            reactive_control(ha, gpt),
        )
    finally:
        await ha.close()



if __name__ == "__main__":
    asyncio.run(main())