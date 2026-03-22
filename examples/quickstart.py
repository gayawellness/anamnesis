"""Anamnesis Quickstart — create a bank, store memories, get a strategic briefing.

Prerequisites:
    1. Anamnesis server running: docker compose up
    2. pip install httpx

Usage:
    python examples/quickstart.py
"""

from anamnesis.sdk import AnamnesisClient

client = AnamnesisClient(base_url="http://localhost:8400")

# 1. Create a memory bank with strategic context
bank = client.create_bank(
    name="my_startup",
    mission="Build and scale a B2B SaaS product to $50K MRR",
    directives=[
        "Focus on enterprise customers ($10K+ ACV)",
        "Product-led growth: free tier converts to paid",
        "Engineering velocity over perfection — ship weekly",
    ],
    disposition="balanced",
)
print(f"Bank created: {bank['name']}")

# 2. Store memories WITH strategic reasoning
client.retain(
    bank="my_startup",
    content="Enterprise demo conversion rate is 34% — highest among all segments",
    content_type="fact",
    reasoning="Enterprise demos convert 2x better than SMB. Focus sales energy here.",
    authority="explicit",
    source="analytics_dashboard",
    tags=["revenue", "enterprise", "conversion"],
    confidence=0.95,
)

client.retain(
    bank="my_startup",
    content="Free tier users who hit the 1000-event limit convert to paid at 12%",
    content_type="fact",
    reasoning="The usage limit is the natural conversion trigger. Don't lower it.",
    authority="explicit",
    source="product_analytics",
    tags=["plg", "conversion", "pricing"],
    confidence=0.9,
)

client.retain(
    bank="my_startup",
    content="Decided to pause the mobile app — engineering bandwidth needed for API v2",
    content_type="decision",
    reasoning="API v2 unlocks enterprise integrations worth $200K pipeline. Mobile app serves existing free users only.",
    authority="explicit",
    source="founder",
    tags=["decision", "prioritization", "api"],
    confidence=1.0,
    decay_condition="after:90d",  # Revisit this decision in 90 days
)

print("3 memories stored with strategic context")

# 3. Search memory — 4D retrieval
results = client.recall(
    bank="my_startup",
    query="What converts best for revenue?",
    limit=5,
)
print(f"\nRecall found {results['total_candidates']} candidates:")
for mem in results["memories"]:
    print(f"  [{mem['score']:.3f}] {mem['content'][:80]}")

# 4. Get a strategic briefing — this is the magic
print("\n" + "=" * 60)
print("STRATEGIC BRIEFING")
print("=" * 60)
directive = client.reflect(
    bank="my_startup",
    question="What should I focus on this week?",
    context="We have 3 engineers and $50K runway remaining",
)
print(directive["synthesis"])
print(f"\nConfidence: {directive['confidence']}")
print(f"Gaps: {directive['gaps_identified']}")

client.close()
