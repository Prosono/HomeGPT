from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from statistics import mean
from typing import Any

from homegpt.api import db
from homegpt.app import run as runtime_loop
from homegpt.app.ha import HAClient
from homegpt.app.openai_client import OpenAIClient
from homegpt.app.policy import ACTIONS_JSON_SCHEMA, SYSTEM_ACTIVE, SYSTEM_PASSIVE
from homegpt.app.topology import fetch_topology_snapshot, pack_states_for_prompt

logger = logging.getLogger("HomeGPT.analysis")

TOPO_MAX_CHARS = 4000
STATE_MAX_CHARS = 6000
HISTORY_MAX_CHARS = 9000
EVENTS_MAX_CHARS = 3000
TOTAL_MAX_CHARS = 26000
CONTEXT_MAX_CHARS = 2000

DEFAULT_HISTORY_HOURS = 6
HISTORY_MAX_LINES = 200
STATE_MAX_LINES = 120
TOPO_MAX_LINES = 80

TRUE_STATES = {"on", "open", "unlocked", "detected", "motion", "home", "present"}
FALSE_STATES = {"off", "closed", "locked", "clear", "no_motion", "away", "not_home"}

_NOISE_LINES = re.compile(
    r"^(\s*"
    r"(SMARTi Dashboard.*|SMARTi Has .*|category_\d+_.*:|available_power_sensors_part\d+:|"
    r"Home Assistant .*: \d+ .*|Vetle's device Climate React:.*|"
    r"Average .*: unavailable.*|.*Missing Title:.*|.*Missing Subtitle:.*)"
    r")\s*$",
    re.IGNORECASE,
)


@dataclass
class AnalysisExecution:
    summary: str
    actions: list[Any]


def coerce_headings(md: str) -> str:
    labels = [
        "Security", "Comfort", "Energy", "Anomalies",
        "Presence", "Occupancy", "Actions to take", "Actions", "Next steps",
    ]
    group = "|".join(map(re.escape, labels))
    pattern = re.compile(rf"(?im)^\s*(?:\*\*|__)?\s*({group})\s*(?:\*\*|__)?\s*:?\s*$")
    return pattern.sub(lambda m: f"### {m.group(1)}", md or "")


def extract_entity_ids(text: str) -> list[str]:
    pattern = r"\b(?:sensor|switch|light|climate|lock|binary_sensor|device_tracker)\.[a-zA-Z0-9_]+\b"
    return list(dict.fromkeys(re.findall(pattern, text)))


def extract_events_from_summary(aid: int, ts: str, summary: str):
    summary = coerce_headings(summary)
    events = []
    blocks = re.split(r"(?im)^###\s+", summary)
    titles = re.findall(r"(?im)^###\s+(.+)$", summary)
    for i, block in enumerate(blocks[1:]):
        heading = (titles[i] or "").strip().lower()
        if "security" in heading:
            cat = "security"
        elif "comfort" in heading:
            cat = "comfort"
        elif "energy" in heading:
            cat = "energy"
        elif "anomal" in heading:
            cat = "anomalies"
        else:
            continue
        parts = re.split(r"(?m)^\s*[-•]\s+|^\s*$", block)
        parts = [p.strip() for p in parts if p.strip()]
        if not parts:
            parts = [block.strip()]
        for part in parts:
            title = part.split(". ")[0][:140]
            ent_ids = ",".join(extract_entity_ids(part))
            events.append((aid, ts, cat, title, part, ent_ids))
    return events


def extract_followups(aid: int, ts: str, summary: str):
    followup_re = re.compile(r"(?ms)^\s*\(?(\d+)[\)\.\:]\s*(.+?)(?=^\s*\(?\d+[\)\.\:]\s*|\Z)")
    rows = []
    for _, label in followup_re.findall(summary):
        normalized = " ".join(label.split()).strip().lower()
        if "list" in normalized and "automation" in normalized:
            code = "list_automations"
        elif "timeline" in normalized and ("energy" in normalized or "sensor" in normalized):
            code = "show_energy_timeline"
        elif "troubleshoot" in normalized or "faulty" in normalized:
            code = "troubleshoot_sensor"
        else:
            continue
        rows.append((aid, ts, label.strip()[:255], code))
    return rows


def strip_noise(text: str) -> str:
    out = []
    for line in (text or "").splitlines():
        if not line.strip():
            continue
        if _NOISE_LINES.match(line):
            continue
        out.append(line)
    return "\n".join(out)


def clamp_chars(text: str, max_chars: int) -> str:
    if len(text or "") <= max_chars:
        return text or ""
    used = 0
    out: list[str] = []
    for line in (text or "").splitlines():
        ln = len(line) + 1
        if used + ln > max_chars:
            break
        out.append(line)
        used += ln
    out.append("… [truncated]")
    return "\n".join(out)


def load_context_memos(entity_ids: list[str], category: str) -> list[str]:
    out: list[str] = []
    with db._conn() as c:
        if entity_ids:
            likes = [f"%{entity_id}%" for entity_id in entity_ids]
            q = (
                "SELECT ef.note FROM event_feedback ef "
                "JOIN analysis_events ev ON ev.id=ef.event_id WHERE "
                + " OR ".join(["ev.entity_ids LIKE ?"] * len(likes))
                + " ORDER BY ef.ts DESC LIMIT 10"
            )
            out += [r[0] for r in c.execute(q, likes).fetchall()]
        q2 = (
            "SELECT ef.note FROM event_feedback ef "
            "JOIN analysis_events ev ON ev.id=ef.event_id "
            "WHERE (ev.entity_ids IS NULL OR ev.entity_ids = '') AND ev.category=? "
            "ORDER BY ef.ts DESC LIMIT 10"
        )
        out += [r[0] for r in c.execute(q2, (category,)).fetchall()]
    return out


def build_context_memos_block(entity_ids: list[str]) -> str:
    cats = ["security", "comfort", "energy", "anomalies", "presence"]
    sections: list[str] = []

    for cat in cats:
        try:
            memos = load_context_memos(entity_ids, cat)
            memos = list(dict.fromkeys([memo.strip() for memo in memos if memo and memo.strip()]))[:6]
            if memos:
                body = "\n".join(f"- {memo}" for memo in memos)
                sections.append(f"### {cat.title()}\n{body}")
        except Exception:
            continue

    return clamp_chars("\n\n".join(sections), CONTEXT_MAX_CHARS)


def compose_user_prompt(
    *,
    lang: str,
    hours: int | None,
    topo: str,
    state_block: str,
    history_block: str,
    events_block: str = "",
    context_block: str = "",
) -> str:
    def _norm(value: str) -> str:
        return strip_noise(value or "").strip()

    topo = clamp_chars(_norm(topo), TOPO_MAX_CHARS)
    state_block = clamp_chars(_norm(state_block), STATE_MAX_CHARS)
    history_block = clamp_chars(_norm(history_block), HISTORY_MAX_CHARS)
    events_block = clamp_chars(_norm(events_block), EVENTS_MAX_CHARS) if events_block else ""
    context_block = clamp_chars(_norm(context_block), CONTEXT_MAX_CHARS) if context_block else ""

    hrs_txt = f" Last window: {hours}h." if hours is not None else ""
    header = (
        f"Language: {lang}.\n"
        "Use CURRENT STATE as ground truth. Be concise and actionable.\n"
        "If USER CONTEXT MEMOS conflict with weak/ambiguous signals, prefer the memos."
        f"{hrs_txt}\n\n"
    )

    sections: list[str] = []
    if context_block:
        sections.append("### USER CONTEXT MEMOS (from prior feedback)\n" + context_block)
    if topo:
        if not topo.lstrip().startswith(("### ", "TOPOLOGY", "TOPOLOGY (", "USER CONTEXT")):
            sections.append("### TOPOLOGY\n" + topo)
        else:
            sections.append(topo)
    if state_block:
        if not state_block.lstrip().startswith(("### ", "CURRENT STATE")):
            sections.append("### CURRENT STATE\n" + state_block)
        else:
            sections.append(state_block)
    if history_block:
        title = f"### HISTORY (compressed{f', last {hours}h' if hours is not None else ''})"
        if not history_block.lstrip().startswith("### "):
            sections.append(title + "\n" + history_block)
        else:
            sections.append(history_block)
    if events_block:
        if not events_block.lstrip().startswith(("### ", "EVENTS")):
            sections.append("### EVENTS\n" + events_block)
        else:
            sections.append(events_block)

    body = "\n\n".join(section for section in sections if section.strip())
    return clamp_chars(header + body, TOTAL_MAX_CHARS)


def _parse_iso_aware(value: str) -> datetime:
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except Exception:
        return datetime.now(timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _try_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(str(value).replace(",", "."))
    except Exception:
        return None


def _domain_of(entity_id: str) -> str:
    return entity_id.split(".", 1)[0] if "." in entity_id else ""


def _is_true_state(value: str) -> bool | None:
    lowered = str(value).strip().lower()
    if lowered in TRUE_STATES:
        return True
    if lowered in FALSE_STATES:
        return False
    return None


def _format_pct(value: float) -> str:
    return f"{value:.0f}%"


def _sec_hm(seconds: float) -> str:
    seconds = max(0, int(seconds))
    hours, remainder = divmod(seconds, 3600)
    minutes, _ = divmod(remainder, 60)
    if hours >= 1:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"


def _compress_entity_series(series: list[dict], now: datetime, jitter_sec: int = 90) -> tuple[str, float]:
    if not series:
        return ("", 0.0)

    entity_id = series[0].get("entity_id", "unknown.unknown")
    domain = _domain_of(entity_id)
    rows = []
    for row in series:
        state = row.get("state")
        ts = _parse_iso_aware(row.get("last_changed") or row.get("last_updated") or row.get("time_fired", ""))
        rows.append((ts, state, row.get("attributes", {})))
    rows.sort(key=lambda item: item[0])

    coalesced: list[tuple[datetime, Any, dict]] = []
    for ts, state, attrs in rows:
        if coalesced and (ts - coalesced[-1][0]).total_seconds() <= jitter_sec:
            coalesced[-1] = (ts, state, attrs)
        else:
            coalesced.append((ts, state, attrs))
    rows = coalesced

    last_ts, last_state, last_attr = rows[-1]
    last_state_str = str(last_state)

    numeric_values: list[float] = []
    for _, state, _ in rows:
        numeric = _try_float(state)
        if numeric is not None and str(state).lower() not in {"unknown", "unavailable"}:
            numeric_values.append(numeric)

    true_flags = [(_is_true_state(state), ts) for ts, state, _ in rows]
    if any(flag is not None for flag, _ in true_flags) and domain in {"binary_sensor", "lock", "cover", "switch"}:
        true_duration = 0.0
        longest_true = 0.0
        for (flag, ts), nxt in zip(true_flags, true_flags[1:] + [(None, now)]):
            next_ts = nxt[1] if nxt[1] is not None else now
            duration = (next_ts - ts).total_seconds()
            if flag is True:
                true_duration += max(0.0, duration)
                longest_true = max(longest_true, max(0.0, duration))
        total = (now - rows[0][0]).total_seconds() or 1.0
        pct = true_duration / total
        last_change_ago = _sec_hm((now - last_ts).total_seconds())

        pretty_state = last_state_str.upper()
        if domain == "lock":
            pretty_state = "UNLOCKED" if _is_true_state(last_state_str) else "LOCKED"
        elif domain == "cover":
            pretty_state = "OPEN" if _is_true_state(last_state_str) else "CLOSED"
        elif domain == "binary_sensor":
            pretty_state = "ON" if _is_true_state(last_state_str) else "OFF"

        line = (
            f"- {entity_id}: {pretty_state} "
            f"(true {_format_pct(pct)}, longest {_sec_hm(longest_true)}, last change {last_change_ago} ago)"
        )
        activity = max(1.0, len(rows) / max(1.0, (now - rows[0][0]).total_seconds() / 3600.0))
        return (line, activity)

    if len(numeric_values) >= 2:
        current = _try_float(last_state_str)
        v_min = min(numeric_values)
        v_max = max(numeric_values)
        v_mean = mean(numeric_values)
        value_range = max(1e-9, v_max - v_min)
        jumps = 0
        previous = numeric_values[0]
        for value in numeric_values[1:]:
            if abs(value - previous) >= max(1.0, 0.10 * value_range):
                jumps += 1
            previous = value

        unit = last_attr.get("unit_of_measurement") or ""
        delta_txt = ""
        if "kwh" in unit.lower() or "wh" in unit.lower() or "energy" in entity_id:
            delta = numeric_values[-1] - numeric_values[0]
            if abs(delta) > 1e-6:
                delta_txt = f", Δ {delta:.2f}{unit}"

        now_txt = f"{current:.2f}{unit}" if current is not None else last_state_str
        line = (
            f"- {entity_id}: now {now_txt}, min {v_min:.2f}{unit}, max {v_max:.2f}{unit}, "
            f"avg {v_mean:.2f}{unit}, changes {jumps}{delta_txt}"
        )
        return (line, max(1.0, jumps))

    line = f"- {entity_id}: state={last_state_str} (last change {_sec_hm((now - last_ts).total_seconds())} ago)"
    activity = max(1.0, len(rows) / max(1.0, (now - rows[0][0]).total_seconds() / 3600.0))
    return (line, activity)


def compress_history_for_prompt(
    hist: list,
    *,
    now: datetime | None = None,
    max_lines: int = 160,
    jitter_sec: int = 90,
) -> str:
    if not hist:
        return "(no history available)"

    now = now or datetime.now(timezone.utc)
    lines: list[tuple[str, float]] = []

    for series in hist:
        if not isinstance(series, list) or not series:
            continue
        if all((str(row.get("state")).lower() in {"unknown", "unavailable", "none"}) for row in series):
            continue
        try:
            line, score = _compress_entity_series(series, now, jitter_sec=jitter_sec)
            if line:
                lines.append((line, score))
        except Exception:
            continue

    if not lines:
        return "(history had no usable states)"

    lines.sort(key=lambda item: item[1], reverse=True)
    kept = lines[:max_lines]
    body = "\n".join(line for (line, _) in kept)
    return "HISTORY (compressed):\n" + body


async def execute_analysis(
    *,
    mode: str,
    focus: str,
    cfg: dict,
    event_limit: int,
    have_real: bool,
    reset_event_pressure,
) -> AnalysisExecution:
    summary = ""
    actions: list[Any] = []

    if have_real:
        ha = HAClient()
        gpt = OpenAIClient(model=cfg.get("model"), api_key=cfg.get("openai_api_key") or None)
        try:
            if mode == "passive":
                events = await runtime_loop.drain_events(limit=event_limit)
                reset_event_pressure()

                if not events:
                    return AnalysisExecution(summary="No notable events recorded.", actions=[])

                topo = await fetch_topology_snapshot(ha, max_lines=TOPO_MAX_LINES)
                all_states = await ha.states()
                state_block = pack_states_for_prompt(all_states, max_lines=STATE_MAX_LINES)

                cfg_hours = int(cfg.get("history_hours", DEFAULT_HISTORY_HOURS))
                now = datetime.now(timezone.utc).replace(microsecond=0)
                start = (now - timedelta(hours=cfg_hours)).replace(microsecond=0)

                entity_ids = sorted({e.get("entity_id") for e in events if e.get("entity_id")})[:100]
                if not entity_ids:
                    entity_ids = sorted({s.get("entity_id") for s in all_states if s.get("entity_id")})[:300]

                logger.info("History query: %d ids (first 15): %s", len(entity_ids), entity_ids[:15])

                try:
                    hist = await ha.history_period(
                        start.isoformat(timespec="seconds"),
                        now.isoformat(timespec="seconds"),
                        entity_ids=entity_ids,
                        minimal_response=True,
                        include_start_time_state=True,
                        significant_changes_only=None,
                    )
                except Exception as exc:
                    logger.warning("History fetch failed (first attempt): %s", exc)
                    try:
                        hist = await ha.history_period(
                            start.isoformat(timespec="seconds"),
                            now.isoformat(timespec="seconds"),
                            entity_ids=entity_ids,
                            minimal_response=False,
                            include_start_time_state=True,
                            significant_changes_only=False,
                        )
                    except Exception as retry_exc:
                        logger.warning("History fetch failed (second attempt): %s", retry_exc)
                        hist = []

                try:
                    groups = len(hist) if isinstance(hist, list) else 0
                    rows = sum(len(group) for group in hist if isinstance(group, list))
                    logger.info("History fetched: groups=%d total_rows=%d", groups, rows)
                except Exception:
                    pass

                try:
                    history_block = compress_history_for_prompt(
                        hist,
                        now=datetime.now(timezone.utc),
                        max_lines=int(cfg.get("history_max_lines", HISTORY_MAX_LINES)),
                        jitter_sec=int(cfg.get("history_jitter_sec", 90)),
                    )
                except Exception as exc:
                    logger.warning("History pack failed: %s", exc)
                    history_block = "(history unavailable)"

                bullets = [
                    f"{event['ts']} · {event['entity_id']} : {event['from']} → {event['to']}"
                    for event in events
                ]
                events_block = "\n".join(bullets) if bullets else "(none)"
                entity_ids_for_context = sorted({event.get("entity_id") for event in events if event.get("entity_id")})
                context_block = build_context_memos_block(entity_ids_for_context)

                user = compose_user_prompt(
                    lang=cfg.get("language", "en"),
                    hours=cfg_hours,
                    topo=topo,
                    state_block=state_block,
                    history_block=history_block,
                    events_block=events_block,
                    context_block=context_block,
                )
                summary = gpt.complete_text(SYSTEM_PASSIVE, user)
                actions = []
            else:
                states = await ha.states()
                lines = [f"{state['entity_id']}={state['state']}" for state in states[:400]]
                user = f"Mode: {mode}\nCurrent states (subset):\n" + "\n".join(lines)
                plan = gpt.complete_json(SYSTEM_ACTIVE, user, schema=ACTIONS_JSON_SCHEMA)
                summary = plan.get("text") or plan.get("summary") or "No summary."
                actions = plan.get("actions") or []
        finally:
            await ha.close()
    else:
        summary = f"Analysis in {mode} mode. Focus: {focus or 'General'}."
        actions = ["light.turn_off living_room", "climate.set_temperature bedroom 20°C"]

    return AnalysisExecution(summary=summary, actions=actions)


def store_analysis_output(mode: str, focus: str, summary: str, actions: list[Any]):
    row = db.add_analysis(mode, focus, summary, json.dumps(actions))

    if isinstance(row, (list, tuple)):
        row_id, row_ts = row[0], row[1]
        events = extract_events_from_summary(row_id, row_ts, summary)
        if events:
            with db._conn() as c:
                c.executemany(
                    "INSERT OR IGNORE INTO analysis_events (analysis_id, ts, category, title, body, entity_ids) VALUES (?,?,?,?,?,?)",
                    events,
                )
                c.commit()

        followups = extract_followups(row_id, row_ts, summary)
        if followups:
            with db._conn() as c:
                c.executemany(
                    "INSERT INTO followup_requests (analysis_id, ts, label, code) VALUES (?,?,?,?)",
                    followups,
                )
                c.commit()

    return row
