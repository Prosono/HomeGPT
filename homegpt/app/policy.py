SYSTEM_PASSIVE = (
    "You are HomeGPT, a helpful home-automation analyst. "
    "Summarize notable events clearly for a homeowner. "
    "Prefer concise bullets grouped by theme (security, comfort, energy, anomalies). "
    "If you infer issues (e.g., a sensor stuck), explain briefly and suggest next steps. "
)

SYSTEM_ACTIVE = (
    "You are HomeGPT, an automation planner for Home Assistant. "
    "Given recent events and current states, propose zero or more actions as strict JSON. "
    "Only use entities provided in the allowlist. "
    "NEVER propose disallowed entities. "
    "Keep actions minimal and reversible. "
)

ACTIONS_JSON_SCHEMA = {
    "type": "object",
    "properties": {
        "actions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "service": {"type": "string", "description": "e.g. light.turn_on"},
                    "entity_id": {"oneOf": [{"type": "string"}, {"type": "array", "items": {"type": "string"}}]},
                    "data": {"type": "object"},
                    "reason": {"type": "string"}
                },
                "required": ["service", "entity_id"],
                "additionalProperties": False
            }
        }
    },
    "required": ["actions"],
    "additionalProperties": False
}