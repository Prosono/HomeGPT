# homegpt/app/openai_client.py

import json
import os
import time
import logging
from typing import Any, Dict, Optional, Iterable

from openai import OpenAI
from openai._exceptions import (
    OpenAIError,
    APIError,
    RateLimitError,
    APIConnectionError,
    AuthenticationError,
    BadRequestError,
    NotFoundError,
    APITimeoutError,
)

logger = logging.getLogger("HomeGPT.OpenAI")

# Models we commonly test with. We don't enforce this list—just warn if unknown.
KNOWN_MODELS = {
    "gpt-5", "gpt-5-mini", "gpt-5-nano",
    "gpt-4o", "gpt-4o-mini",
}
DEFAULT_MODEL = "gpt-5"


def _pick_model(cfg_model: Optional[str]) -> str:
    model = (cfg_model or os.getenv("OPENAI_MODEL") or DEFAULT_MODEL).strip()
    if model not in KNOWN_MODELS:
        logger.warning(
            "Unknown/untested model '%s'. Proceeding anyway; known models: %s",
            model, ", ".join(sorted(KNOWN_MODELS))
        )
    return model


def _make_messages(system: str, user: str, extra: Optional[Iterable[Dict[str, Any]]] = None):
    msgs = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    if extra:
        msgs.extend(extra)
    return msgs


def _token_param_for_model(model: str) -> str:
    # GPT-5* uses max_completion_tokens on chat.completions; older models keep max_tokens.
    return "max_completion_tokens" if model.startswith("gpt-5") else "max_tokens"


def _model_allows_temperature(model: str) -> bool:
    # GPT-5 chat completions accept only the default temp (1). Omit the param entirely.
    return not model.startswith("gpt-5")


class OpenAIClient:
    """
    Chat Completions wrapper with:
      • model selection
      • JSON/text modes
      • retry/backoff for transient errors
      • param-compat shims (token cap & temperature)
      • empty-output recovery (retry / optional fallback)
    """

    def __init__(self, model: Optional[str] = None, timeout: float = None, max_retries: int = None):
        self.model = _pick_model(model)

        # Env-tunable defaults
        self.timeout = float(os.getenv("OPENAI_TIMEOUT", str(timeout if timeout is not None else 90.0)))
        self.max_retries = int(os.getenv("OPENAI_RETRIES", str(max_retries if max_retries is not None else 3)))
        self.max_output_tokens = int(os.getenv("OPENAI_MAX_TOKENS", "500"))
        self.enable_fallback = os.getenv("OPENAI_FALLBACK", "1") == "1"

        # Only include temperature for models that support it
        env_temp = os.getenv("OPENAI_TEMPERATURE")
        self.temperature: Optional[float] = float(env_temp) if (env_temp is not None and _model_allows_temperature(self.model)) else None

        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise RuntimeError("OPENAI_API_KEY is not set")
        # Disable SDK auto-retries; we handle retries ourselves
        self._client = OpenAI(api_key=api_key, timeout=self.timeout, max_retries=0)

        self._token_param_name = _token_param_for_model(self.model)

        logger.info(
            "OpenAI client ready. Model=%s timeout=%ss retries=%d",
            self.model, int(self.timeout), self.max_retries
        )

    # ---------------- Public API ----------------

    def complete_text(self, system: str, user: str) -> str:
        resp = self._chat(messages=_make_messages(system, user), force_json=False)
        return (resp.get("text") or "").strip()

    def complete_json(self, system: str, user: str, schema: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        prompt = user
        if schema:
            prompt = (
                f"{user}\n\n"
                "Return a single JSON object ONLY, matching this shape. Do not add prose outside JSON.\n"
                f"JSON schema (informal): {json.dumps(schema, ensure_ascii=False)}"
            )
        resp = self._chat(messages=_make_messages(system, prompt), force_json=True)
        raw = (resp.get("text") or "").strip()
        if not raw:
            return {}
        try:
            return json.loads(raw)
        except Exception:
            cleaned = raw.strip()
            if cleaned.startswith("```"):
                cleaned = cleaned.strip("`")
                if cleaned.lower().startswith("json"):
                    cleaned = cleaned.split("\n", 1)[1] if "\n" in cleaned else ""
            try:
                return json.loads(cleaned)
            except Exception:
                logger.warning("JSON parse failed; returning raw text. Raw: %s", raw[:400])
                return {"text": raw}

    # ---------------- Internals ----------------

    def _chat(self, *, messages, force_json: bool) -> Dict[str, Any]:
        """
        Robust wrapper around chat.completions.create with backoff and
        compatibility fallbacks.
        """
        last_err: Optional[Exception] = None

        # Base kwargs (mutable across retries)
        kwargs: Dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "timeout": self.timeout,
        }
        # Token cap under the correct name
        kwargs[self._token_param_name] = self.max_output_tokens

        # Response format:
        #  • JSON mode uses json_object
        #  • Text mode asks explicitly for "text" (some models ignore, we’ll drop it on 400)
        if force_json:
            kwargs["response_format"] = {"type": "json_object"}
        else:
            kwargs["response_format"] = {"type": "text"}

        # Temperature (omit for GPT-5)
        if self.temperature is not None:
            kwargs["temperature"] = self.temperature

        for attempt in range(self.max_retries + 1):
            try:
                logger.debug(
                    "Calling OpenAI model=%s json=%s attempt=%d token_param=%s",
                    self.model, force_json, attempt + 1, self._token_param_name
                )

                r = self._client.chat.completions.create(**kwargs)
                msg = r.choices[0].message
                text = (msg.content or "").strip()

                # If content is empty, try once more:
                # 1) remove response_format (older models), 2) optional model fallback.
                if not text:
                    logger.warning("Empty content from model. Retrying once without response_format.")
                    kwargs.pop("response_format", None)
                    r = self._client.chat.completions.create(**kwargs)
                    msg = r.choices[0].message
                    text = (msg.content or "").strip()

                    if not text and self.enable_fallback and self.model.startswith("gpt-5"):
                        logger.warning("Still empty; falling back to gpt-4o-mini for this request.")
                        # Swap model & token param for fallback
                        self.model = "gpt-4o-mini"
                        for k in ("max_tokens", "max_completion_tokens"):
                            kwargs.pop(k, None)
                        self._token_param_name = _token_param_for_model(self.model)
                        kwargs["model"] = self.model
                        kwargs[self._token_param_name] = self.max_output_tokens
                        # Keep temperature omitted unless user configured it; gpt-4o-mini supports it
                        # If you want 0.2 by default on fallback, uncomment:
                        # if "temperature" not in kwargs:
                        #     kwargs["temperature"] = 0.2
                        r = self._client.chat.completions.create(**kwargs)
                        msg = r.choices[0].message
                        text = (msg.content or "").strip()

                try:
                    logger.debug(
                        "OpenAI tokens: prompt=%s completion=%s",
                        getattr(r.usage, "prompt_tokens", None),
                        getattr(r.usage, "completion_tokens", None),
                    )
                except Exception:
                    pass

                return {"text": text, "raw": r}

            except (RateLimitError, APIConnectionError, APIError, APITimeoutError) as e:
                last_err = e
                delay = min(2.0 * (2 ** attempt), 10.0)
                delay += (os.urandom(1)[0] / 255.0 - 0.5) * 0.25  # jitter
                logger.warning(
                    "OpenAI transient error (%s). attempt=%d/%d; sleeping %.2fs",
                    type(e).__name__, attempt + 1, self.max_retries, delay
                )
                if attempt < self.max_retries:
                    time.sleep(delay)
                    continue
                break

            except BadRequestError as e:
                # Handle param-compat gracefully (flip token param, drop temp/response_format)
                body = getattr(e, "body", None) or {}
                body_str = json.dumps(body).lower()
                msg = str(e).lower()

                # Wrong token param name → flip it
                if "unsupported_parameter" in body_str or "unsupported parameter" in msg:
                    if "max_tokens" in body_str and self._token_param_name == "max_tokens":
                        logger.warning("Server rejected max_tokens; switching to max_completion_tokens.")
                        kwargs.pop("max_tokens", None)
                        kwargs["max_completion_tokens"] = self.max_output_tokens
                        self._token_param_name = "_
