import os
import json
import httpx

OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
MODEL = os.environ.get("MODEL", "gpt-4o-mini")

class OpenAIClient:
    def __init__(self):
        self.client = httpx.Client(timeout=30)

    def complete_json(self, system: str, user: str, schema: dict | None = None) -> dict:
        # Uses JSON mode via response_format
        payload = {
            "model": MODEL,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": 0.2,
        }
        if schema:
            payload["response_format"] = {"type": "json_schema", "json_schema": {"name": "actions", "schema": schema}}
        else:
            payload["response_format"] = {"type": "text"}

        r = self.client.post(
            "https://api.openai.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {OPENAI_API_KEY}", "Content-Type": "application/json"},
            json=payload,
        )
        r.raise_for_status()
        content = r.json()["choices"][0]["message"]["content"]
        try:
            return json.loads(content)
        except Exception:
            return {"text": content}