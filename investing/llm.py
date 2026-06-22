"""
investing/llm.py — structured, schema-validated LLM access (P0.2, P0.3).

The greedy ``re.search(r'\\{.*\\}')`` parsing is gone. We use strict tool use so
the model returns an object matching :class:`LLMQualitative`'s JSON schema,
validate it with Pydantic, do exactly one repair retry on failure, and otherwise
raise :class:`LLMSchemaError` (the ``LLM_SCHEMA_ERROR`` contract).

The model is taken only from :mod:`investing.config` — no hardcoded model strings.
Claude returns *qualitative* judgement only; the schema has no price/score/
quantity/status field, so it structurally cannot make the final decision.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Optional, Type, TypeVar

from pydantic import BaseModel, ValidationError

from . import config
from .schemas import LLMQualitative, LLMSchemaError

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)


def _get_client(client: Optional[Any]) -> Any:
    if client is not None:
        return client
    # Prefer the bot's shared (tracked) client if present.
    try:
        import _ctx
        if getattr(_ctx, "claude", None) is not None:
            return _ctx.claude
    except Exception:
        pass
    import anthropic  # lazy — keeps the package importable without the SDK
    import os
    return anthropic.Anthropic(api_key=os.environ.get("CLAUDE_API_KEY"))


def _tool_for(schema_model: Type[BaseModel], name: str) -> dict:
    return {
        "name": name,
        "description": f"Return a single structured {name} object. "
                       "Use ONLY qualitative judgement — never invent prices, "
                       "scores, stops, share counts or a final decision.",
        "input_schema": schema_model.model_json_schema(),
    }


def _extract_tool_input(resp: Any, tool_name: str) -> Optional[dict]:
    for block in getattr(resp, "content", []) or []:
        if getattr(block, "type", None) == "tool_use" and getattr(block, "name", None) == tool_name:
            return block.input
    return None


def structured_call(
    schema_model: Type[T],
    *,
    system: str,
    user: str,
    tool_name: str = "submit",
    client: Optional[Any] = None,
    model: Optional[str] = None,
    max_tokens: Optional[int] = None,
) -> T:
    """Run a tool-use call and validate the result against ``schema_model``.

    One corrective retry on validation/parse failure; then ``LLMSchemaError``.
    Falls back to ``CLAUDE_MODEL_FALLBACK`` if the primary model errors at the
    transport level.
    """
    cl = _get_client(client)
    model = model or config.CLAUDE_MODEL_PRIMARY
    max_tokens = max_tokens or config.LLM_MAX_TOKENS
    tool = _tool_for(schema_model, tool_name)

    messages = [{"role": "user", "content": user}]

    def _call(mdl: str):
        return cl.messages.create(
            model=mdl,
            max_tokens=max_tokens,
            system=system,
            tools=[tool],
            tool_choice={"type": "tool", "name": tool_name},
            messages=messages,
        )

    # transport-level resilience: primary then fallback model
    try:
        resp = _call(model)
    except Exception as e:
        logger.warning("Primary model %s failed (%s); trying fallback %s",
                       model, e, config.CLAUDE_MODEL_FALLBACK)
        resp = _call(config.CLAUDE_MODEL_FALLBACK)

    raw = _extract_tool_input(resp, tool_name)
    last_err: Optional[str] = None
    for attempt in range(config.LLM_SCHEMA_REPAIR_RETRIES + 1):
        if raw is not None:
            try:
                return schema_model.model_validate(raw)
            except ValidationError as ve:
                last_err = str(ve)
        else:
            last_err = "model returned no tool_use block"

        if attempt >= config.LLM_SCHEMA_REPAIR_RETRIES:
            break

        # one repair attempt — show the model exactly what failed
        repair = (
            "Twoja poprzednia odpowiedź nie przeszła walidacji schematu:\n"
            f"{last_err}\n\nPopraw i ponownie wywołaj narzędzie z poprawną strukturą. "
            "Zwróć wyłącznie ocenę jakościową."
        )
        messages.append({"role": "user", "content": repair})
        try:
            resp = _call(model)
        except Exception:
            resp = _call(config.CLAUDE_MODEL_FALLBACK)
        raw = _extract_tool_input(resp, tool_name)

    raise LLMSchemaError(f"LLM_SCHEMA_ERROR: {last_err}; raw={json.dumps(raw)[:500] if raw else None}")


def extract_qualitative(*, system: str, user: str, client: Optional[Any] = None) -> LLMQualitative:
    """Convenience wrapper: get validated :class:`LLMQualitative` from news/context."""
    return structured_call(LLMQualitative, system=system, user=user,
                           tool_name="submit_qualitative", client=client)
