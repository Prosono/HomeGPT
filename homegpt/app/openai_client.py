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

# Models we know work with Chat Completions. We don't *enforce* this list,
# we just warn and proceed if unknown.
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

class OpenAIClient:
    """
    Small wrapper around OpenAI Chat Completions with:
      - model selection (supports gpt-5 family)
      - JSON response mode when schema is requested
      - robust retry & sensible defaults for bigger prompts
    """

    def __init__(self, model: Optional[str] = None, timeout: float = None, max_retries: int = None):
        # Model
        self.model = _pick_model(model)

        # Tunables (env overrides)
        self.timeout = float(os.getenv("OPENAI_TIMEOUT", str(timeout if timeout is not None else 90.0)))
        self.max_retries = int(os.getenv("OPENAI_RETRIES", str(max_retries if max_retries is not None else 3)))
        self.temperature = float(os.getenv("OPENAI_TEMPERATURE", "0.2"))
        self.max_tokens = int(os.getenv("OPENAI_MAX_TOKENS", "500"))

        # One client, we disable SDK internal retries (we handle them)
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise RuntimeError("OPENAI_API_KEY is not set")
        self._client = OpenAI(api_key=api_key, timeout=self.timeout, max_retries=0)

        logger.info("OpenAI client ready. Model=%s timeout=%.0fs retries=%d", self.model, self.timeout, self.max_retries)

    # ---------------- Public API ----------------

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
        sane defaults for larger prompts.
        """
        last_err: Optional[Exception] = None

        # Base kwargs for *every* request
        base_kwargs: Dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "timeout": self.timeout,         # request-level timeout
            "temperature": self.temperature, # keep outputs consistent
            "max_tokens": self.max_tokens,   # cap output size for speed
        }
        if force_json:
            base_kwargs["response_format"] = {"type": "json_object"}

        for attempt in range(self.max_retries + 1):
            try:
                logger.debug("Calling OpenAI model=%s json=%s attempt=%d",
                             self.model, force_json, attempt + 1)

                r = self._client.chat.completions.create(**base_kwargs)
                msg = r.choices[0].message
                text = (msg.content or "").strip()
                try:
                    logger.debug("OpenAI tokens: prompt=%s completion=%s",
                                 getattr(r.usage, "prompt_tokens", None),
                                 getattr(r.usage, "completion_tokens", None))
                except Exception:
                    pass
                return {"text": text, "raw": r}

            except (RateLimitError, APIConnectionError, APIError, APITimeoutError) as e:
                # Transient → backoff & retry
                last_err = e
                delay = min(2.0 * (2 ** attempt), 10.0)  # 2s, 4s, 8s, … capped
                jitter = 0.25 * (0.5 - os.urandom(1)[0] / 255.0)  # ±0.125s jitter
                logger.warning("OpenAI transient error (%s). attempt=%d/%d; sleeping %.2fs",
                               type(e).__name__, attempt + 1, self.max_retries, delay + jitter)
                if attempt < self.max_retries:
                    time.sleep(delay + jitter)
                    continue
                break

            except NotFoundError as e:
                # Model not accessible / not found
                logger.error("Model not found or not accessible: %s", e)
                # Optional graceful fallback. Uncomment if you want automatic fallback:
                # if self.model.startswith("gpt-5"):
                #     logger.warning("Falling back to gpt-4o-mini")
                #     self.model = "gpt-4o-mini"
                #     continue
                raise

            except (AuthenticationError, BadRequestError) as e:
                # Non-retryable client errors
                logger.error("OpenAI client error: %s", e)
                raise

            except OpenAIError as e:
                # Generic SDK error; try once more if attempts remain
                last_err = e
                logger.warning("OpenAIError: %s (attempt %d/%d)", e, attempt + 1, self.max_retries)
                if attempt < self.max_retries:
                    time.sleep(1.0 + 0.25 * attempt)
                    continue
                break

            except Exception as e:
                # Truly unexpected; still allow a retry or two
                last_err = e
                logger.exception("Unexpected error calling OpenAI")
                if attempt < self.max_retries:
                    time.sleep(1.0 + 0.25 * attempt)
                    continue
                break

        if last_err:
            raise last_err
        return {}
