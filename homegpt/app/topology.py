import requests

def build_home_layout(ha_url, ha_token):
    """Automatically build home layout from Home Assistant areas/entities."""
    headers = {
        "Authorization": f"Bearer {ha_token}",
        "Content-Type": "application/json",
    }

    # Get areas
    areas_resp = requests.get(f"{ha_url}/api/config/area_registry", headers=headers)
    areas_resp.raise_for_status()
    areas = areas_resp.json()

    # Get entities
    entities_resp = requests.get(f"{ha_url}/api/config/entity_registry", headers=headers)
    entities_resp.raise_for_status()
    entities = entities_resp.json()

    # Map areas to entity aliases
    aliases = {}
    areas_dict = {}
    for area in areas:
        area_name = area["name"]
        area_id = area["area_id"]
        area_entities = [e for e in entities if e.get("area_id") == area_id]
        areas_dict[area_name] = [e["entity_id"] for e in area_entities]

        for e in area_entities:
            friendly_name = e.get("original_name") or e.get("name") or e["entity_id"]
            aliases[e["entity_id"]] = f"{area_name} {friendly_name}"

    # Construct layout object
    layout = {
        "floors": [  # If you want floors, you’d need to infer from names
            {"name": "Main", "areas": list(areas_dict.keys())}
        ],
        "adjacency": {},  # Can be filled if you want smart guesses
        "aliases": aliases,
        "include_domains": ["light", "switch", "sensor", "climate", "camera", "binary_sensor", "device_tracker", "person"],
        "exclude_entities": [],
    }

    return layout
