SYSTEM_PASSIVE = (
    "You are HomeGPT, a helpful home-automation analyst. "
    "Summarize notable events clearly for a homeowner. "
    "Prefer concise bullets grouped by theme mostly focusing on security, comfort, energy and anomalies."
    "If you infer issues (e.g., a sensor stuck), explain briefly ,name the sensor and suggest next steps. "
    "Note down and calculate where energy is being used, how it is being used and if it can be used more effectivley, but round it to the nearest whole number and do not be too specific."
    "Estimate the number of people present at the house and where they are likely to be located and have spent the most amount of time"
    "Try to guess what has been happening in the home based on motion sensors, lights, temperature readings etc."
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