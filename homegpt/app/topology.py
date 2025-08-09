from collections import defaultdict

def pack_topology_for_prompt(areas, devices, entities, states, max_lines: int = 80) -> str:
    """Return a compact, plain-text snapshot for the prompt."""
    # Index areas
    area_by_id = {a["area_id"]: a for a in areas}
    area_name = lambda aid: (area_by_id.get(aid) or {}).get("name") or "Unassigned"

    # Count domains per area
    counts = defaultdict(lambda: defaultdict(int))
    for e in entities:
        eid = e.get("entity_id") or ""
        dom = e.get("domain") or (eid.split(".", 1)[0] if "." in eid else "sensor")
        a = area_name(e.get("area_id") or (e.get("device_id") and next((d.get("area_id") for d in devices if d["id"] == e["device_id"]), None)))
        counts[a][dom] += 1

    # People snapshot from states
    people = []
    for s in states:
        eid = s.get("entity_id","")
        if eid.startswith("person.") or eid.startswith("device_tracker."):
            people.append({
                "name": s.get("attributes",{}).get("friendly_name", eid),
                "state": s.get("state"),
                "zone": s.get("attributes",{}).get("source"),
                "last": s.get("last_changed"),
            })

    # Emit compact lines
    lines = []
    lines.append("HOME TOPOLOGY (auto)")
    for a in sorted(counts.keys()):
        c = counts[a]
        line = f"AREA {a} | lights={c.get('light',0)} motion={c.get('binary_sensor',0)} climate={c.get('climate',0)} cams={c.get('camera',0)} sensors={c.get('sensor',0)} switches={c.get('switch',0)}"
        lines.append(line)

    if people:
        lines.append("PEOPLE:")
        for p in people[:8]:
            lines.append(f" - {p['name']}: {p['state']} zone={p['zone']} last={p['last']}")

    return "\n".join(lines[:max_lines])
