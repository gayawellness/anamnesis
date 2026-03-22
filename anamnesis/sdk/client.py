"""Anamnesis Python SDK — thin REST client for the 4D memory engine."""

from __future__ import annotations

import os
from typing import Optional

import httpx


class AnamnesisClient:
    """Synchronous client for the Anamnesis REST API."""

    def __init__(self, base_url: str = "http://localhost:8400",
                 api_key: str = ""):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self._client = httpx.Client(
            base_url=f"{self.base_url}/api/v1",
            headers=self._headers(),
            timeout=60.0,
        )

    @classmethod
    def from_env(cls) -> AnamnesisClient:
        """Create client from ANAMNESIS_URL and ANAMNESIS_API_KEY env vars."""
        return cls(
            base_url=os.getenv("ANAMNESIS_URL", "http://localhost:8400"),
            api_key=os.getenv("ANAMNESIS_API_KEY", ""),
        )

    def _headers(self) -> dict:
        h = {"Content-Type": "application/json"}
        if self.api_key:
            h["Authorization"] = f"Bearer {self.api_key}"
        return h

    def _check(self, resp: httpx.Response) -> dict:
        if resp.status_code >= 400:
            raise AnamnesisError(
                f"HTTP {resp.status_code}: {resp.text}",
                status_code=resp.status_code,
            )
        return resp.json()

    # ── Health ──

    def health(self) -> dict:
        return self._check(self._client.get("/health"))

    # ── Banks ──

    def create_bank(self, name: str, mission: str,
                    directives: list[str] = None,
                    disposition: str = "balanced",
                    weight_factors: dict[str, float] = None,
                    default_decay_days: int = 90) -> dict:
        body = {
            "name": name,
            "mission": mission,
            "directives": directives or [],
            "disposition": disposition,
            "weight_factors": weight_factors or {
                "semantic": 0.30, "temporal": 0.20,
                "relational": 0.20, "strategic": 0.30,
            },
            "default_decay_days": default_decay_days,
        }
        return self._check(self._client.post("/banks", json=body))

    def list_banks(self) -> list[dict]:
        return self._check(self._client.get("/banks"))

    def get_bank(self, bank_id: str) -> dict:
        return self._check(self._client.get(f"/banks/{bank_id}"))

    def update_bank(self, bank_id: str, **kwargs) -> dict:
        return self._check(self._client.put(f"/banks/{bank_id}", json=kwargs))

    # ── Core Operations ──

    def retain(self, bank: str, content: str,
               content_type: str = "fact",
               source: str = "sdk",
               reasoning: Optional[str] = None,
               authority: str = "inferred",
               confidence: float = 0.8,
               decay_condition: Optional[str] = None,
               tags: list[str] = None,
               supersedes: list[str] = None,
               depends_on: list[str] = None) -> dict:
        body = {
            "bank": bank,
            "content": content,
            "content_type": content_type,
            "source": source,
            "reasoning": reasoning,
            "authority": authority,
            "confidence": confidence,
            "decay_condition": decay_condition,
            "tags": tags or [],
            "supersedes": supersedes or [],
            "depends_on": depends_on or [],
        }
        return self._check(self._client.post("/retain", json=body))

    def recall(self, bank: str, query: str,
               limit: int = 10,
               content_types: list[str] = None,
               min_weight: float = None,
               tags: list[str] = None) -> dict:
        body: dict = {
            "bank": bank,
            "query": query,
            "limit": limit,
        }
        filters = {}
        if content_types:
            filters["content_types"] = content_types
        if min_weight is not None:
            filters["min_weight"] = min_weight
        if tags:
            filters["tags"] = tags
        if filters:
            body["filters"] = filters
        return self._check(self._client.post("/recall", json=body))

    def reflect(self, bank: str, question: str,
                context: Optional[str] = None,
                max_memories: int = 20,
                synthesis_style: str = "strategic") -> dict:
        body = {
            "bank": bank,
            "question": question,
            "context": context,
            "max_memories": max_memories,
            "synthesis_style": synthesis_style,
        }
        return self._check(self._client.post("/reflect", json=body))

    def decay_check(self, bank: str) -> dict:
        return self._check(self._client.post("/decay-check", json={"bank": bank}))

    def reweight(self, bank: str, trigger_event: Optional[str] = None) -> dict:
        body = {"bank": bank}
        if trigger_event:
            body["trigger_event"] = trigger_event
        return self._check(self._client.post("/reweight", json=body))

    # ── Convenience ──

    def remember(self, bank: str, content: str,
                 tags: list[str] = None) -> dict:
        """Quick-store a memory with sensible defaults."""
        return self.retain(
            bank=bank,
            content=content,
            content_type="observation",
            source="sdk",
            tags=tags or [],
        )

    def bulk_retain(self, memories: list[dict]) -> dict:
        return self._check(self._client.post("/bulk-retain", json={"memories": memories}))

    def get_memory(self, memory_id: str) -> dict:
        return self._check(self._client.get(f"/memories/{memory_id}"))

    # ── Cleanup ──

    def close(self):
        self._client.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


class AnamnesisError(Exception):
    """Error from Anamnesis API."""

    def __init__(self, message: str, status_code: int = 0):
        super().__init__(message)
        self.status_code = status_code
