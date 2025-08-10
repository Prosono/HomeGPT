import json
import os
import logging
from typing import Any, Dict, Optional, Iterable

from openai import OpenAI
from openai._exceptions import OpenAIError, APIError, RateLimitError, APIConnectionError, AuthenticationError, BadRequestError

logger = logging.getLogger("HomeGPT.OpenAI")

# Models we know work with Chat Completions + JSON. We don't *enforce*
# this list, we just warn and fallback if totally unknown.
KNOWN_MODELS = {
    "gpt-5", "gpt-5-mini", "gpt-5-nano",
}

DEFAULT_MODEL = "gpt-5"  # keeps things working if config is empty/bad

def _pick_model(cfg_model: Optional[str]) -> str:
    model = (cfg_model or os.getenv("OPENAI_MODEL") or DEFAULT_MODEL).strip()
    if model not in KNOWN_MODELS:
        logger.warning("Unknown/untested model '%s'. Proceeding anyway; "
                       "known models: %s", model, ", ".join(sorted(KNOWN_MODELS)))
    return model

def _client() -> OpenAI:
    # expects OPENAI_API_KEY in env (add-on supports this)
    key = os.getenv("OPENAI_API_KEY")
    if not key:
        raise RuntimeError("OPENAI_API_KEY is not set")
    return OpenAI(api_key=key)

def _make_messages(system: str, user: str, extra: Optional[Iterable[Dict[str, Any]]] = None):
    msgs = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    if extra:
        msgs.extend(extra)
    return msgs

class OpenAIClient:
    """
    Small wrapper around OpenAI Chat Completions with:
      - model selection (supports gpt-5 family)
      - JSON response mode when schema is requested
      - mild retry logic & clean error messages
    """

    def __init__(self, model: Optional[str] = None, timeout: float = 30.0, max_retries: int = 2):
        self.model = _pick_model(model)
        self.timeout = timeout
        self.max_retries = max_retries
        self._client = _client()
        logger.info("OpenAI client ready. Model=%s", self.model)

    def complete_text(self, system: str, user: str) -> str:
        """
        Plain text completion (no enforced JSON).
        """
        resp = self._chat(messages=_make_messages(system, user), force_json=False)
        return resp.get("text", "")

    def complete_json(self, system: str, user: str, schema: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        Ask the model to return JSON. If `schema` is provided, it is included in the
        user prompt as guidance (we keep using Chat Completions for broad model support).
        Returns a dict; if parsing fails, returns {"text": <raw string>}.
        """
        prompt = user
        if schema:
            prompt = (
                f"{user}\n\n"
                "Return a single JSON object ONLY, matching this shape. Do not add prose outside JSON.\n"
                f"JSON schema (informal): {json.dumps(schema, ensure_ascii=False)}"
            )

        resp = self._chat(
            messages=_make_messages(system, prompt),
            force_json=True
        )

        raw = resp.get("text", "")
        if not raw:
            return {}

        # Try strict JSON first; if it fails, try to salvage (strip code fences etc.)
        try:
            return json.loads(raw)
        except Exception:
            # common cases: ```json ... ```
            cleaned = raw.strip()
            if cleaned.startswith("```"):
                cleaned = cleaned.strip("`")
                # after stripping backticks, content might start with 'json\n{...'
                if cleaned.lower().startswith("json"):
                    cleaned = cleaned.split("\n", 1)[1] if "\n" in cleaned else ""
            try:
                return json.loads(cleaned)
            except Exception:
                logger.warning("JSON parse failed; returning raw text. Raw: %s", raw[:400])
                return {"text": raw}

    # -------------- internals --------------

    def _chat(self, *, messages, force_json: bool) -> Dict[str, Any]:
        last_err: Optional[Exception] = None
        for attempt in range(self.max_retries + 1):
            try:
                logger.debug("Calling OpenAI model=%s json=%s attempt=%d",
                             self.model, force_json, attempt + 1)

                kwargs: Dict[str, Any] = {
                    "model": self.model,
                    "messages": messages,
                    "timeout": self.timeout,
                }
                if force_json:
                    # Standard JSON mode for Chat Completions
                    kwargs["response_format"] = {"type": "json_object"}

                r = self._client.chat.completions.create(**kwargs)
                msg = r.choices[0].message
                text = (msg.content or "").strip()
                logger.debug("OpenAI tokens: prompt=%s completion=%s",
                             getattr(r.usage, "prompt_tokens", None),
                             getattr(r.usage, "completion_tokens", None))
                return {"text": text, "raw": r}
            except (RateLimitError, APIConnectionError, APIError) as e:
                last_err = e
                logger.warning("OpenAI transient error (%s). attempt=%d/%d",
                               type(e).__name__, attempt + 1, self.max_retries)
                if attempt < self.max_retries:
                    continue
                break
            except (AuthenticationError, BadRequestError, OpenAIError) as e:
                # Non-retryable
                logger.error("OpenAI error: %s", e)
                raise
            except Exception as e:
                last_err = e
                logger.exception("Unexpected error calling OpenAI")
                if attempt < self.max_retries:
                    continue
                break
        if last_err:
            raise last_err
        return {}
