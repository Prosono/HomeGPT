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
        logger.warning("Unknown/untested model '%s'. Proceeding anyway; known models: %s",
                       model, ", ".join(sorted(KNOWN_MODELS)))
    return model

def _make_messages(system: str, user: str, extra: Optional[Iterable[Dict[str, Any]]] = None):
    msgs = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    if extra:
        msgs.extend(extra)
    return msgs

def _token_param_for_model(model: str) -> str:
    return "max_completion_tokens" if model.startswith("gpt-5") else "max_tokens"

def _model_allows_temperature(model: str) -> bool:
    # GPT-5 chat-completions: only default temp is supported → omit the param.
    return not model.startswith("gpt-5")

class OpenAIClient:
    def __init__(self, model: Optional[str] = None, timeout: float = None, max_retries: int = None):
        self.model = _pick_model(model)
        self.timeout = float(os.getenv("OPENAI_TIMEOUT", str(timeout if timeout is not None else 90.0)))
        self.max_retries = int(os.getenv("OPENAI_RETRIES", str(max_retries if max_retries is not None else 3)))
        env_temp = os.getenv("OPENAI_TEMPERATURE")
        self.temperature: Optional[float] = float(env_temp) if (env_temp is not None and _model_allows_temperature(self.model)) else None
        self.max_output_tokens = int(os.getenv("OPENAI_MAX_TOKENS", "500"))
        self._token_param_name = _token_param_for_model(self.model)

        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise RuntimeError("OPENAI_API_KEY is not set")
        self._client = OpenAI(api_key=api_key, timeout=self.timeout, max_retries=0)

        logger.info("OpenAI client ready. Model=%s timeout=%ss retries=%d", self.model, int(self.timeout), self.max_retries)

    # --------- Public API ---------
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

    # --------- Internals ---------
    def _chat(self, *, messages, force_json: bool) -> Dict[str, Any]:
        last_err: Optional[Exception] = None

        # Base kwargs (we may tweak inside loop)
        kwargs: Dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "timeout": self.timeout,
        }
        # cap output tokens
        kwargs[self._token_param_name] = self.max_output_tokens
        # response format & tools: force plain text for non-JSON runs
        if force_json:
            kwargs["response_format"] = {"type": "json_object"}
        else:
            kwargs["response_format"] = {"type": "text"}
            kwargs["tool_choice"] = "none"  # disable tool calls (prevents empty content)

        if self.temperature is not None:
            kwargs["temperature"] = self.temperature

        for attempt in range(self.max_retries + 1):
            try:
                logger.debug("Calling OpenAI model=%s json=%s attempt=%d token_param=%s",
                             self.model, force_json, attempt + 1, self._token_param_name)

                r = self._client.chat.completions.create(**kwargs)
                msg = r.choices[0].message
                text = (msg.content or "").strip()

                # If model tried to use tools or returned empty, retry once forcing text-only.
                if not text:
                    has_tools = bool(getattr(msg, "tool_calls", None))
                    if has_tools or (kwargs.get("tool_choice") != "none" or kwargs.get("response_format", {}).get("type") != "text"):
                        logger.warning("Empty content%s; retrying once with text-only + tool_choice=none.",
                                       " (tool_calls present)" if has_tools else "")
                        kwargs["response_format"] = {"type": "text"}
                        kwargs["tool_choice"] = "none"
                        r = self._client.chat.completions.create(**kwargs)
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
                last_err = e
                delay = min(2.0 * (2 ** attempt), 10.0)
                delay += (os.urandom(1)[0] / 255.0 - 0.5) * 0.25
                logger.warning("OpenAI transient error (%s). attempt=%d/%d; sleeping %.2fs",
                               type(e).__name__, attempt + 1, self.max_retries, delay)
                if attempt < self.max_retries:
                    time.sleep(delay)
                    continue
                break

            except BadRequestError as e:
                # Handle param-compat gracefully
                body = getattr(e, "body", None) or {}
                body_str = json.dumps(body).lower()
                msg = str(e).lower()

                # wrong token param name → flip it
                if "unsupported_parameter" in body_str or "unsupported parameter" in msg:
                    if "max_tokens" in body_str and self._token_param_name == "max_tokens":
                        logger.warning("Server rejected max_tokens; switching to max_completion_tokens.")
                        kwargs.pop("max_tokens", None)
                        kwargs["max_completion_tokens"] = self.max_output_tokens
                        self._token_param_name = "max_completion_tokens"
                        if attempt < self.max_retries:
                            time.sleep(0.25); continue
                    if "max_completion_tokens" in body_str and self._token_param_name == "max_completion_tokens":
                        logger.warning("Server rejected max_completion_tokens; switching to max_tokens.")
                        kwargs.pop("max_completion_tokens", None)
                        kwargs["max_tokens"] = self.max_output_tokens
                        self._token_param_name = "max_tokens"
                        if attempt < self.max_retries:
                            time.sleep(0.25); continue

                # temperature not supported → drop it once
                if "temperature" in body_str or "temperature" in msg:
                    if "temperature" in kwargs:
                        logger.warning("Temperature not supported by model %s; omitting and retrying.", self.model)
                        kwargs.pop("temperature", None)
                        self.temperature = None
                        if attempt < self.max_retries:
                            time.sleep(0.25); continue

                # tool_choice not supported (older models) → remove and retry
                if "tool_choice" in body_str:
                    if "tool_choice" in kwargs:
                        logger.warning("tool_choice not supported by this model; removing and retrying.")
                        kwargs.pop("tool_choice", None)
                        if attempt < self.max_retries:
                            time.sleep(0.25); continue

                # response_format not supported → remove and retry
                if "response_format" in body_str:
                    if "response_format" in kwargs:
                        logger.warning("response_format not supported; removing and retrying.")
                        kwargs.pop("response_format", None)
                        if attempt < self.max_retries:
                            time.sleep(0.25); continue

                logger.error("OpenAI client error: %s", e)
                raise

            except NotFoundError as e:
                logger.error("Model not found or not accessible: %s", e)
                raise

            except AuthenticationError as e:
                logger.error("OpenAI auth error: %s", e)
                raise

            except OpenAIError as e:
                last_err = e
                logger.warning("OpenAIError: %s (attempt %d/%d)", e, attempt + 1, self.max_retries)
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
