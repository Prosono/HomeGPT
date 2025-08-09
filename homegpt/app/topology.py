# homegpt/app/topology.py
from __future__ import annotations
from collections import defaultdict
from typing import List, Dict, Any
import asyncio
from websockets.exceptions import ConnectionClosedError


def pack_topology_for_prompt(
    areas: List[Dict[str, Any]],
    devices: List[Dict[str, Any]],
    entities: List[Dict[str, Any]],
    states: List[Dict[str, Any]],
    max_lines: int = 80,
) -> str:
    """
    Build a compact, token-friendly snapshot of the home for prompting.
    Emits counts per area and a short people snapshot.
    """
    # Index areas and a quick lookup for device->area
    area_by_id = {a.get("area_id"): a for a in areas}
    dev_area_by_id = {d.get("id"): d.get("area_id") for d in devices}

    def area_name(area_id: str | None) -> str:
        if not area_id:
            return "Unassigned"
        return (area_by_id.get(area_id) or {}).get("name") or "Unassigned"

    # Count domains per area
    counts = defaultdict(lambda: defaultdict(int))
    for e in entities:
        eid = e.get("entity_id") or ""
        # Prefer registry-provided domain if present; else split from entity_id
        dom = e.get("domain") or (eid.split(".", 1)[0] if "." in eid else "sensor")

        # Prefer entity area; fall back to device->area; else unassigned
        a_id = e.get("area_id") or dev_area_by_id.get(e.get("device_id"))
        a = area_name(a_id)
        counts[a][dom] += 1

    # Minimal people snapshot from current states
    people = []
    for s in states:
        eid = s.get("entity_id", "")
        if eid.startswith("person.") or eid.startswith("device_tracker."):
            attr = s.get("attributes", {})
            people.append({
                "name": attr.get("friendly_name", eid),
                "state": s.get("state"),
                "zone": attr.get("source") or attr.get("zone") or "",
                "last": s.get("last_changed"),
            })

    # Emit compact lines
    lines = []
    lines.append("HOME TOPOLOGY (auto)")
    for a in sorted(counts.keys()):
        c = counts[a]
        line = (
            f"AREA {a} | lights={c.get('light',0)} motion={c.get('binary_sensor',0)} "
            f"climate={c.get('climate',0)} cams={c.get('camera',0)} "
            f"sensors={c.get('sensor',0)} switches={c.get('switch',0)}"
        )
        lines.append(line)

    if people:
        lines.append("PEOPLE:")
        for p in people[:8]:
            zone = f" zone={p['zone']}" if p["zone"] else ""
            lines.append(f" - {p['name']}: {p['state']}{zone} last={p['last']}")

    return "\n".join(lines[:max_lines])


async def fetch_topology_snapshot(ha, max_lines: int = 80) -> str:
    lines = ["TOPOLOGY SNAPSHOT", ""]

    try:
        # Full snapshot
        areas, devices, entities, states = await asyncio.gather(
            ha.list_areas(),
            ha.list_devices(),
            ha.list_entities(),   # <-- can be large
            ha.states(),
        )
    except (ConnectionClosedError, Exception):
        # Fall back: skip entities (they're usually the heavy one)
        areas, devices, states = await asyncio.gather(
            ha.list_areas(),
            ha.list_devices(),
            ha.states(),
        )
        entities = []

    # …build your summary from areas/devices/entities/states as before…
    # Keep it compact and guard against huge outputs:
    # if len(lines) > max_lines: lines = lines[:max_lines-1] + ["… (truncated)"]

    return "\n".join(lines[: max_lines - 1] + ["… (truncated)"] if len(lines) > max_lines else lines)