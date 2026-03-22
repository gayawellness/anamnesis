#!/usr/bin/env python3
"""Anamnesis CLI — command-line interface for the 4D Strategic Memory Engine.

Usage:
    python -m anamnesis.cli reflect --bank my_project --question "What should I focus on?"
    python -m anamnesis.cli retain --bank my_project --content "Decision made" --reasoning "Why"
    python -m anamnesis.cli recall --bank my_project --query "lead scoring"
    python -m anamnesis.cli remember --bank my_project --content "Quick note"
    python -m anamnesis.cli health
"""

import argparse
import json
import os
import sys

try:
    import httpx
except ImportError:
    print("ERROR: httpx not installed. Run: pip install httpx")
    sys.exit(1)

BASE_URL = os.getenv("ANAMNESIS_URL", "http://localhost:8400")
API_KEY = os.getenv("ANAMNESIS_API_KEY", "")


def _headers():
    h = {"Content-Type": "application/json"}
    if API_KEY:
        h["Authorization"] = f"Bearer {API_KEY}"
    return h


def _post(path, body):
    try:
        r = httpx.post(f"{BASE_URL}/api/v1{path}", json=body,
                       headers=_headers(), timeout=60.0)
        if r.status_code >= 400:
            print(f"ERROR: {r.status_code} — {r.text}", file=sys.stderr)
            sys.exit(1)
        return r.json()
    except httpx.ConnectError:
        print("ERROR: Cannot connect to Anamnesis at " + BASE_URL, file=sys.stderr)
        print("Start the server: cd ~/AI-Team && ./anamnesis/scripts/start_server.sh",
              file=sys.stderr)
        sys.exit(1)


def _get(path):
    try:
        r = httpx.get(f"{BASE_URL}/api/v1{path}", headers=_headers(), timeout=10.0)
        return r.json()
    except httpx.ConnectError:
        print("ERROR: Cannot connect to Anamnesis at " + BASE_URL, file=sys.stderr)
        sys.exit(1)


def cmd_reflect(args):
    body = {
        "bank": args.bank,
        "question": args.question,
        "synthesis_style": args.style or "strategic",
    }
    if args.context:
        body["context"] = args.context

    result = _post("/reflect", body)

    print("=" * 60)
    print("STRATEGIC BRIEFING")
    print("=" * 60)
    print()
    print(result["synthesis"])
    print()
    if result.get("gaps_identified"):
        print("GAPS IDENTIFIED:")
        for gap in result["gaps_identified"]:
            print(f"  • {gap}")
    print()
    print(f"Confidence: {result.get('confidence', 'N/A')}")
    print(f"Memories cited: {len(result.get('cited_memories', []))}")


def cmd_retain(args):
    body = {
        "bank": args.bank,
        "content": args.content,
        "content_type": args.type or "fact",
        "source": args.source or "neville",
        "authority": args.authority or "inferred",
        "confidence": args.confidence or 0.8,
        "tags": args.tags.split(",") if args.tags else [],
    }
    if args.reasoning:
        body["reasoning"] = args.reasoning
    if args.decay:
        body["decay_condition"] = args.decay

    result = _post("/retain", body)
    print(f"Retained: {result['memory_id']}")
    print(f"Weight: {result['weight']}")
    if result.get("entities_linked"):
        print(f"Entities: {', '.join(result['entities_linked'])}")
    if result.get("extracted_facts"):
        print(f"Facts extracted: {len(result['extracted_facts'])}")


def cmd_recall(args):
    body = {
        "bank": args.bank,
        "query": args.query,
        "limit": args.limit or 10,
    }
    filters = {}
    if args.min_weight:
        filters["min_weight"] = args.min_weight
    if args.tags:
        filters["tags"] = args.tags.split(",")
    if filters:
        body["filters"] = filters

    result = _post("/recall", body)

    print(f"Found {result['total_candidates']} candidates "
          f"({result['retrieval_time_ms']:.0f}ms)")
    print()
    for i, mem in enumerate(result["memories"], 1):
        w = mem["weight"]
        score = mem["score"]
        src = mem["source"]
        print(f"[{i}] (score={score:.3f}, weight={w:.1f}, source={src})")
        print(f"    {mem['content'][:120]}")
        if mem.get("reasoning"):
            print(f"    WHY: {mem['reasoning'][:100]}")
        print()


def cmd_remember(args):
    body = {
        "bank": args.bank,
        "content": args.content,
        "content_type": "observation",
        "source": "neville",
        "tags": args.tags.split(",") if args.tags else [],
    }
    result = _post("/retain", body)
    print(f"Remembered: {result['memory_id']} (weight={result['weight']})")


def cmd_health(args):
    result = _get("/health")
    status = result.get("status", "unknown")
    print(f"Status: {status}")
    print(f"DB: {'✅' if result.get('db_connected') else '❌'}")
    print(f"Embeddings: {'✅' if result.get('embedding_configured') else '❌'}")
    print(f"LLM: {'✅' if result.get('llm_configured') else '❌'}")
    print(f"Memories: {result.get('memory_count', 0)}")
    print(f"Banks: {result.get('bank_count', 0)}")


def main():
    parser = argparse.ArgumentParser(description="Anamnesis — 4D Strategic Memory Engine")
    sub = parser.add_subparsers(dest="command", required=True)

    # reflect
    p = sub.add_parser("reflect", help="Get strategic briefing")
    p.add_argument("--bank", required=True)
    p.add_argument("--question", required=True)
    p.add_argument("--context", default=None)
    p.add_argument("--style", default="strategic",
                   choices=["strategic", "factual", "narrative"])

    # retain
    p = sub.add_parser("retain", help="Store memory with reasoning")
    p.add_argument("--bank", required=True)
    p.add_argument("--content", required=True)
    p.add_argument("--reasoning", default=None)
    p.add_argument("--authority", default="inferred",
                   choices=["explicit", "inferred", "system"])
    p.add_argument("--source", default="neville")
    p.add_argument("--type", default="fact",
                   choices=["fact", "decision", "observation", "instruction", "event"])
    p.add_argument("--tags", default="")
    p.add_argument("--confidence", type=float, default=0.8)
    p.add_argument("--decay", default=None)

    # recall
    p = sub.add_parser("recall", help="Search memory")
    p.add_argument("--bank", required=True)
    p.add_argument("--query", required=True)
    p.add_argument("--limit", type=int, default=10)
    p.add_argument("--min-weight", type=float, default=None)
    p.add_argument("--tags", default=None)

    # remember
    p = sub.add_parser("remember", help="Quick store")
    p.add_argument("--bank", required=True)
    p.add_argument("--content", required=True)
    p.add_argument("--tags", default="")

    # health
    sub.add_parser("health", help="Check engine status")

    args = parser.parse_args()

    commands = {
        "reflect": cmd_reflect,
        "retain": cmd_retain,
        "recall": cmd_recall,
        "remember": cmd_remember,
        "health": cmd_health,
    }
    commands[args.command](args)


if __name__ == "__main__":
    main()
