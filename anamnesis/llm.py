"""LLM client for Anamnesis — used for fact extraction and reflect synthesis.

Supports Anthropic (Claude) and OpenAI. Falls back gracefully if neither is configured.
"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Optional

logger = logging.getLogger("anamnesis.llm")


class LLMClient:
    """Unified LLM client for Anamnesis internal operations."""

    def __init__(self, provider: str = "anthropic", model: Optional[str] = None):
        self.provider = provider
        if provider in ("anthropic", "claude"):
            import anthropic
            self._client = anthropic.Anthropic(
                api_key=os.getenv("ANTHROPIC_API_KEY")
            )
            self.model = model or os.getenv("ANAMNESIS_REFLECT_MODEL", "claude-haiku-4-5-20251001")
            logger.info("LLM client: Anthropic (%s)", self.model)
        elif provider in ("openai", "gpt"):
            import openai
            self._client = openai.OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
            self.model = model or os.getenv("ANAMNESIS_REFLECT_MODEL", "gpt-4o-mini")
            logger.info("LLM client: OpenAI (%s)", self.model)
        else:
            raise ValueError(f"Unknown LLM provider: {provider}. Use: anthropic, openai")

    def generate(self, system_prompt: str, user_prompt: str) -> str:
        """Generate a text response."""
        if self.provider in ("anthropic", "claude"):
            response = self._client.messages.create(
                model=self.model,
                max_tokens=4096,
                system=system_prompt,
                messages=[{"role": "user", "content": user_prompt}],
            )
            return response.content[0].text
        else:
            response = self._client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                max_tokens=4096,
            )
            return response.choices[0].message.content

    def generate_json(self, system_prompt: str, user_prompt: str) -> dict | list:
        """Generate a JSON response, with fallback parsing."""
        text = self.generate(system_prompt, user_prompt)
        return _parse_json(text)


def create_llm_client() -> Optional[LLMClient]:
    """Create LLM client from environment, or return None if not configured."""
    provider = os.getenv("ANAMNESIS_LLM_PROVIDER", os.getenv("AI_PROVIDER", ""))

    if not provider:
        # Auto-detect from available API keys
        if os.getenv("ANTHROPIC_API_KEY"):
            provider = "anthropic"
        elif os.getenv("OPENAI_API_KEY"):
            provider = "openai"
        else:
            logger.info("No LLM configured (no ANTHROPIC_API_KEY or OPENAI_API_KEY)")
            return None

    try:
        return LLMClient(provider=provider)
    except Exception as e:
        logger.warning("Failed to create LLM client: %s", e)
        return None


def _parse_json(text: str) -> dict | list:
    """Parse JSON from LLM response, handling markdown fences."""
    text = text.strip()
    # Strip markdown code fences
    if text.startswith("```"):
        lines = text.split("\n")
        lines = lines[1:]  # Remove opening fence
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # Try to find JSON in the text
        match = re.search(r'[\[{].*[\]}]', text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group())
            except json.JSONDecodeError:
                pass
        raise
