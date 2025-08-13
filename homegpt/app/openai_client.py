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
    # Newer models expect max_completion_tokens; older keep max_tokens
    return "max_completion_tokens" if model.startswith("gpt-5") else "max_tokens"


class OpenAIClient:
    """
    Wrapper around OpenAI Chat Completions with:
      - model selection
      - JSON mode support
      - robust retry/backoff
      - compatibility for token param name differences
    """

    def __init__(self, model: Optional[str] = None, timeout: float = None, max_retries: int = None):
        self.model = _pick_model(model)

        # Env-tunable defaults
        self.timeout = float(os.getenv("OPENAI_TIMEOUT", str(timeout if timeout is not None else 90.0)))
        self.max_retries = int(os.getenv("OPENAI_RETRIES", str(max_retries if max_retries is not None else 3)))
        self.temperature = float(os.getenv("OPENAI_TEMPERATURE", "0.2"))
        # single value; we’ll put it under the correct param name
        self.max_output_tokens = int(os.getenv("OPENAI_MAX_TOKENS", "500"))

        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise RuntimeError("OPENAI_API_KEY is not set")
        # disable SDK auto-retries (we manage them)
        self._client = OpenAI(api_key=api_key, timeout=self.timeout, max_retries=0)

        # pick initial token param based on model
        self._token_param_name = _token_param_for_model(self.model)

        logger.info(
            "OpenAI client ready. Model=%s timeout=%ss retries=%d",
            self.model, int(self.timeout), self.max_retries
        )

    # ---------- Public API ----------

    def complete_text(self, system: str, user: str) -> str:
        resp = self._chat(messages=_make_messages(system, user), force_json=False)
        return resp.get("text", "")

    def complete_json(self, system: str, user: str, schema: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
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

    # ---------- Internals ----------

    def _chat(self, *, messages, force_json: bool) -> Dict[str, Any]:
        """
        Robust wrapper with backoff and param-compat shim.
        """
        last_err: Optional[Exception] = None

        # Build base kwargs once; we may mutate token-param inside the loop
        base_kwargs: Dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "timeout": self.timeout,
            "temperature": self.temperature,
        }
        # add token cap under the currently selected param name
        base_kwargs[self._token_param_name] = self.max_output_tokens

        if force_json:
            base_kwargs["response_format"] = {"type": "json_object"}

        for attempt in range(self.max_retries + 1):
            try:
                logger.debug(
                    "Calling OpenAI model=%s json=%s attempt=%d token_param=%s",
                    self.model, force_json, attempt + 1, self._token_param_name
                )
                r = self._client.chat.completions.create(**base_kwargs)
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
                # tiny jitter
                delay += (os.urandom(1)[0] / 255.0 - 0.5) * 0.25
                logger.warning(
                    "OpenAI transient error (%s). attempt=%d/%d; sleeping %.2fs",
                    type(e).__name__, attempt + 1, self.max_retries, delay
                )
                if attempt < self.max_retries:
                    time.sleep(delay)
                    continue
                break

            except BadRequestError as e:
                # Non-retryable *except* for the token param name mismatch case
                msg = str(e).lower()
                body = getattr(e, "body", None)
                body_str = json.dumps(body) if body is not None else ""
                mismatch = "unsupported parameter" in msg or "unsupported_parameter" in body_str

                if mismatch:
                    # flip the param and retry once
                    old = self._token_param_name
                    new = "max_completion_tokens" if old == "max_tokens" else "max_tokens"
                    logger.warning(
                        "Server rejected %s; switching to %s and retrying once.",
                        old, new
                    )
                    base_kwargs.pop(old, None)
                    base_kwargs[new] = self.max_output_tokens
                    self._token_param_name = new
                    # retry this attempt (doesn't count against retries beyond this try)
                    if attempt < self.max_retries:
                        time.sleep(0.25)
                        continue

                # otherwise: genuine 400, don't spin
                logger.error("OpenAI client error: %s", e)
                raise

            except NotFoundError as e:
                logger.error("Model not found or not accessible: %s", e)
                # Optional graceful fallback:
                # if self.model.startswith("gpt-5"):
                #     logger.warning("Falling back to gpt-4o-mini")
                #     self.model = "gpt-4o-mini"
                #     # rebuild token param against new model
                #     new_param = _token_param_for_model(self.model)
                #     for k in ("max_tokens", "max_completion_tokens"):
                #         base_kwargs.pop(k, None)
                #     self._token_param_name = new_param
                #     base_kwargs[new_param] = self.max_output_tokens
                #     continue
                raise

            except (AuthenticationError) as e:
                logger.error("OpenAI auth error: %s", e)
                raise

            except OpenAIError as e:
                last_err = e
                logger.warning(
                    "OpenAIError: %s (attempt %d/%d)", e, attempt + 1, self.max_retries
                )
                if attempt < self.max_retries:
                    time.sleep(1.0 + 0.25 * attempt)
                    continue
                break

            except Exception as e:
                last_err = e
                logger.exception("Unexpected error calling OpenAI")
                if attempt < self.max_retries:
                    time.sleep(1.0 + 0.25 * attempt)
                    continue
                break

        if last_err:
            raise last_err
        return {}
