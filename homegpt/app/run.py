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
import inspect
import logging
from datetime import datetime, timezone

from homegpt.api import db
from homegpt.app.config import load_runtime_settings
from homegpt.app.util import setup_logging, RateLimiter, next_time_of_day
from homegpt.app.ha import HAClient
from homegpt.app.openai_client import OpenAIClient
from homegpt.app.policy import SYSTEM_PASSIVE, SYSTEM_ACTIVE, ACTIONS_JSON_SCHEMA

# Runtime config defaults
_INITIAL_SETTINGS = load_runtime_settings()
LOG_LEVEL = _INITIAL_SETTINGS.get("log_level", os.environ.get("LOG_LEVEL", "INFO"))

setup_logging(LOG_LEVEL)
_LOGGER = logging.getLogger("homegpt")

EVENT_BUFFER: list[dict] = []  # recent events
EVENT_BUFFER_MAX = 20000
EVENT_LOCK = asyncio.Lock()


def _settings() -> dict:
    return load_runtime_settings()


def _allowlist(cfg: dict) -> set[str]:
    return {str(entity_id) for entity_id in (cfg.get("control_allowlist") or [])}


def _normalize_targets(raw_targets) -> list[str]:
    if isinstance(raw_targets, str):
        return [raw_targets]
    if isinstance(raw_targets, list):
        return [str(target) for target in raw_targets if isinstance(target, str)]
    return []


def _ensure_model_client(gpt: OpenAIClient, cfg: dict) -> OpenAIClient:
    target_model = str(cfg.get("model") or "gpt-5")
    target_api_key = str(cfg.get("openai_api_key") or "")
    if gpt.model == target_model and getattr(gpt, "api_key", "") == target_api_key:
        return gpt
    _LOGGER.info("Refreshing OpenAI client for model=%s", target_model)
    return OpenAIClient(
        model=target_model,
        timeout=gpt.timeout,
        max_retries=gpt.max_retries,
        api_key=target_api_key or None,
    )


def event_count() -> int:
    return len(EVENT_BUFFER)


async def append_event(event: dict) -> int:
    async with EVENT_LOCK:
        EVENT_BUFFER.append(event)
        if len(EVENT_BUFFER) > EVENT_BUFFER_MAX:
            del EVENT_BUFFER[: len(EVENT_BUFFER) - EVENT_BUFFER_MAX]
        return len(EVENT_BUFFER)


async def snapshot_events(limit: int | None = None) -> list[dict]:
    async with EVENT_LOCK:
        if limit is None:
            return list(EVENT_BUFFER)
        return list(EVENT_BUFFER[-limit:])


async def clear_events() -> None:
    async with EVENT_LOCK:
        EVENT_BUFFER.clear()


async def drain_events(limit: int | None = None) -> list[dict]:
    async with EVENT_LOCK:
        if limit is None:
            events = list(EVENT_BUFFER)
        else:
            events = list(EVENT_BUFFER[-limit:])
        EVENT_BUFFER.clear()
        return events


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
            cfg = _settings()
            wait_seconds = await next_time_of_day(str(cfg.get("summarize_time", "21:30")))
            await asyncio.sleep(wait_seconds)
            events = await snapshot_events(limit=2000)
            if not events:
                await ha.notify("HomeGPT Daily", "No notable events recorded today.")
                continue
            gpt = _ensure_model_client(gpt, cfg)
            bullets = [
                f"{e['ts']} · {e['entity_id']} : {e['from']} → {e['to']}"
                for e in events
            ]
            prompt = (
                f"Language: {cfg.get('language', 'en')}.\nSummarize today's home activity from these lines:\n"
                + "\n".join(bullets)
            )
            text = gpt.complete_text(SYSTEM_PASSIVE, prompt)
            await save_analysis("passive", "daily_summary", text, [])
            await ha.notify("HomeGPT – Daily Summary", text)
            await clear_events()
        except Exception as exc:
            _LOGGER.exception("Error in summarize_daily: %s", exc)
            # continue looping on error
            continue


async def reactive_control(ha: HAClient, gpt: OpenAIClient, on_event=None) -> None:
    """
    Listen for real‑time Home Assistant events and, in active mode, call
    the language model to propose actions.  Rate limits and allowlists
    are honoured.  Errors within the loop are logged and ignored so that
    subsequent events continue to be processed.
    """
    limiter = RateLimiter(int(_settings().get("max_actions_per_hour", 10)))
    async for evt in ha.websocket_events():
        try:
            if evt.get("event_type") != "state_changed":
                continue
            cfg = _settings()
            allowlist = _allowlist(cfg)
            limiter.max_per_hour = int(cfg.get("max_actions_per_hour", limiter.max_per_hour))
            gpt = _ensure_model_client(gpt, cfg)
            d = evt.get("data", {})
            entity_id = d.get("entity_id")
            ts = datetime.now(timezone.utc).isoformat()
            event = {
                "ts": ts,
                "entity_id": entity_id,
                "from": (d.get("old_state") or {}).get("state"),
                "to": (d.get("new_state") or {}).get("state"),
            }
            buffered_count = await append_event(event)
            if on_event is not None:
                try:
                    maybe_awaitable = on_event(event, buffered_count)
                    if inspect.isawaitable(maybe_awaitable):
                        await maybe_awaitable
                except Exception as hook_exc:
                    _LOGGER.exception("Error in reactive_control event hook: %s", hook_exc)
            # In passive mode or without an entity_id we just buffer events
            if str(cfg.get("mode", "passive")).lower() != "active" or not entity_id:
                continue
            # Only act on explicitly allow‑listed entities or sensors/persons/etc.
            if not (
                entity_id in allowlist
                or entity_id.split(".")[0] in {"binary_sensor", "sensor", "person", "device_tracker"}
            ):
                continue
            states = await ha.states()
            prompt = (
                f"Recent event:\n- entity: {entity_id}\n- from: {event['from']}\n- to: {event['to']}\n\n"
                "Current allowlist (ONLY act on these):\n" + "\n".join(sorted(allowlist))
                + "\n\nCurrent states (subset):\n"
                + "\n".join(f"{s['entity_id']}={s['state']}" for s in states[:400])
            )
            plan = gpt.complete_json(SYSTEM_ACTIVE, prompt, schema=ACTIONS_JSON_SCHEMA)
            actions: list[dict] = []
            for a in plan.get("actions", []):
                svc = a.get("service", "")
                if "." not in svc:
                    continue
                targets = [target for target in _normalize_targets(a.get("entity_id")) if target in allowlist]
                if not targets:
                    continue
                if not limiter.allow():
                    await ha.notify("HomeGPT – rate limited", f"Skipped {svc} on {targets}")
                    continue
                if bool(cfg.get("dry_run", True)):
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
    db.init_db()
    cfg = _settings()
    model = str(cfg.get("model", "gpt-5"))
    ha, gpt = HAClient(), OpenAIClient(model=model, api_key=cfg.get("openai_api_key") or None)

    try:
        await asyncio.gather(
            summarize_daily(ha, gpt),
            reactive_control(ha, gpt),
        )
    finally:
        await ha.close()



if __name__ == "__main__":
    asyncio.run(main())
