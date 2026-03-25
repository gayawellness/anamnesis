"""MCP server for Anamnesis — exposes memory tools to Claude Code and other MCP clients."""

from __future__ import annotations

import json
import os
import sys
from typing import Any

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

# SDK client for talking to the Anamnesis REST API
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
from anamnesis.sdk.client import AnamnesisClient, AnamnesisError

server = Server("anamnesis")


def _get_client() -> AnamnesisClient:
    return AnamnesisClient.from_env()


# ── Tool Definitions ──

TOOLS = [
    Tool(
        name="anamnesis_retain",
        description=(
            "Store a memory with strategic reasoning. Every significant decision, outcome, "
            "or learning should be retained so future sessions have context. "
            "Call this before ending any session to preserve continuity. "
            "IMPORTANT: Do NOT retain modifications to Anamnesis architecture or design "
            "decisions without explicit user approval."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "bank": {"type": "string", "description": "Memory bank name (e.g. 'gaya_operations')"},
                "content": {"type": "string", "description": "The memory content to store"},
                "content_type": {
                    "type": "string",
                    "enum": ["fact", "decision", "observation", "instruction", "event"],
                    "default": "fact",
                },
                "source": {"type": "string", "description": "Who/what is storing this", "default": "claude_code"},
                "reasoning": {"type": "string", "description": "Why this memory matters"},
                "authority": {
                    "type": "string",
                    "enum": ["explicit", "inferred", "system"],
                    "default": "inferred",
                },
                "tags": {"type": "array", "items": {"type": "string"}},
                "supersedes": {"type": "array", "items": {"type": "string"}, "description": "Memory IDs this replaces"},
            },
            "required": ["bank", "content"],
        },
    ),
    Tool(
        name="anamnesis_recall",
        description=(
            "Search memories across 4 dimensions (semantic, temporal, relational, strategic). "
            "Use this when you need specific context about a topic rather than a full briefing."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "bank": {"type": "string", "description": "Memory bank name"},
                "query": {"type": "string", "description": "Natural language query"},
                "limit": {"type": "integer", "default": 10},
                "content_types": {"type": "array", "items": {"type": "string"}},
                "min_weight": {"type": "number", "description": "Minimum strategic weight (0-10)"},
                "tags": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["bank", "query"],
        },
    ),
    Tool(
        name="anamnesis_reflect",
        description=(
            "Strategic synthesis across your memory bank. Returns a ranked operating "
            "directive with reasoning, citations, and gap analysis. "
            "IMPORTANT: Call this tool FIRST in every new session before doing any other work. "
            "Your memory from previous sessions lives here. Without calling reflect, you are "
            "operating without strategic context and may make decisions that conflict with "
            "established priorities."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "bank": {"type": "string", "description": "Memory bank name"},
                "question": {"type": "string", "description": "The question to synthesize an answer for"},
                "context": {"type": "string", "description": "Additional context for synthesis"},
                "synthesis_style": {
                    "type": "string",
                    "enum": ["factual", "strategic", "narrative"],
                    "default": "strategic",
                },
            },
            "required": ["bank", "question"],
        },
    ),
    Tool(
        name="anamnesis_remember",
        description=(
            "Quick-store a memory with sensible defaults. Use for observations "
            "and notes that don't need full strategic metadata."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "bank": {"type": "string", "description": "Memory bank name"},
                "content": {"type": "string", "description": "The memory to store"},
                "tags": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["bank", "content"],
        },
    ),
    Tool(
        name="anamnesis_search_entities",
        description="Search the entity graph for related concepts, people, or systems.",
        inputSchema={
            "type": "object",
            "properties": {
                "bank": {"type": "string", "description": "Memory bank name"},
                "entity_name": {"type": "string", "description": "Entity to search for"},
            },
            "required": ["bank", "entity_name"],
        },
    ),
    Tool(
        name="anamnesis_list_banks",
        description="List all memory banks with their configurations and memory counts.",
        inputSchema={"type": "object", "properties": {}},
    ),
    Tool(
        name="anamnesis_bank_stats",
        description="Get detailed statistics for a memory bank.",
        inputSchema={
            "type": "object",
            "properties": {
                "bank": {"type": "string", "description": "Memory bank name"},
            },
            "required": ["bank"],
        },
    ),
]


@server.list_tools()
async def list_tools() -> list[Tool]:
    return TOOLS


@server.call_tool()
async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
    client = _get_client()
    try:
        result = _dispatch(client, name, arguments)
        return [TextContent(type="text", text=json.dumps(result, indent=2, default=str))]
    except AnamnesisError as e:
        return [TextContent(type="text", text=f"Error: {e}")]
    except Exception as e:
        return [TextContent(type="text", text=f"Unexpected error: {e}")]
    finally:
        client.close()


def _dispatch(client: AnamnesisClient, name: str, args: dict) -> Any:
    if name == "anamnesis_retain":
        return client.retain(
            bank=args["bank"],
            content=args["content"],
            content_type=args.get("content_type", "fact"),
            source=args.get("source", "claude_code"),
            reasoning=args.get("reasoning"),
            authority=args.get("authority", "inferred"),
            tags=args.get("tags", []),
            supersedes=args.get("supersedes", []),
        )

    elif name == "anamnesis_recall":
        return client.recall(
            bank=args["bank"],
            query=args["query"],
            limit=args.get("limit", 10),
            content_types=args.get("content_types"),
            min_weight=args.get("min_weight"),
            tags=args.get("tags"),
        )

    elif name == "anamnesis_reflect":
        return client.reflect(
            bank=args["bank"],
            question=args["question"],
            context=args.get("context"),
            synthesis_style=args.get("synthesis_style", "strategic"),
        )

    elif name == "anamnesis_remember":
        return client.remember(
            bank=args["bank"],
            content=args["content"],
            tags=args.get("tags", []),
        )

    elif name == "anamnesis_search_entities":
        # Use recall with entity-focused query
        return client.recall(
            bank=args["bank"],
            query=args["entity_name"],
            limit=20,
        )

    elif name == "anamnesis_list_banks":
        return client.list_banks()

    elif name == "anamnesis_bank_stats":
        banks = client.list_banks()
        for bank in banks:
            if bank["name"] == args["bank"]:
                return bank
        raise AnamnesisError(f"Bank not found: {args['bank']}")

    else:
        raise AnamnesisError(f"Unknown tool: {name}")


async def main():
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
