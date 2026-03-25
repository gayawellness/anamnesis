"""Anamnesis CLI — command-line tools for memory management.

Usage:
    python3 -m anamnesis.cli boot --bank <name>
    python3 -m anamnesis.cli boot --bank <name> --json
    python3 -m anamnesis.cli generate-boot-prompt --bank <name> --format <format>
    python3 -m anamnesis.cli export --bank <name> --output backup.json
    python3 -m anamnesis.cli export --all --output full_backup.json
    python3 -m anamnesis.cli import --file backup.json
    python3 -m anamnesis.cli import --file backup.json --merge
    python3 -m anamnesis.cli prune --bank <name> --dry-run
    python3 -m anamnesis.cli prune --bank <name>
    python3 -m anamnesis.cli restore --memory-id <uuid>
"""

from __future__ import annotations

import argparse
import json
import sys

from anamnesis.cli.generate_boot import generate_boot_prompt, SUPPORTED_FORMATS


def cmd_boot(args: argparse.Namespace) -> None:
    """Fetch a boot briefing from the Anamnesis API."""
    from anamnesis.sdk.client import AnamnesisClient, AnamnesisError

    client = AnamnesisClient.from_env()
    try:
        result = client.boot(
            bank=args.bank,
            agent_name=getattr(args, "agent", None),
            include_recent_sessions=not getattr(args, "no_recent", False),
        )

        if args.json_output:
            print(json.dumps(result, indent=2, default=str))
            return

        # Human-readable output
        print(f"=== Boot Briefing: {args.bank} ===\n")

        print(f"Mission: {result.get('mission', '(none)')}\n")

        directives = result.get("directives", [])
        if directives:
            print("Directives:")
            for d in directives:
                print(f"  - {d}")
            print()

        cold = result.get("cold_start_warning", False)
        hours = result.get("hours_since_last_query")
        if hours is not None:
            status = "COLD START" if cold else "warm"
            print(f"Status: {status} ({hours}h since last query)\n")
        elif cold:
            print("Status: COLD START (no previous queries)\n")

        priorities = result.get("top_priorities", [])
        if priorities:
            print(f"Top Priorities ({len(priorities)}):")
            for p in priorities:
                deps = ""
                if p.get("dependencies"):
                    deps = f" [deps: {', '.join(p['dependencies'])}]"
                print(f"  [{p['weight']:.1f}] {p['content']}{deps}")
            print()

        outcomes = result.get("recent_outcomes", [])
        if outcomes:
            print(f"Recent Outcomes ({len(outcomes)}):")
            for o in outcomes:
                print(f"  [{o['when']}] ({o['source']}) {o['content']}")
            print()

        alerts = result.get("active_decay_alerts", [])
        if alerts:
            print(f"Decay Alerts ({len(alerts)}):")
            for a in alerts:
                print(f"  [{a['status'].upper()}] {a['content']} ({a['condition']})")
            print()

        rules = result.get("architecture_rules", [])
        if rules:
            print(f"Architecture Rules ({len(rules)}):")
            for r in rules:
                print(f"  - {r}")
            print()

        gaps = result.get("gaps_identified", [])
        if gaps:
            print(f"Gaps ({len(gaps)}):")
            for g in gaps:
                print(f"  - {g}")
            print()

    except AnamnesisError as e:
        print(f"Boot failed: {e}", file=sys.stderr)
        sys.exit(1)
    finally:
        client.close()


def cmd_generate_boot(args: argparse.Namespace) -> None:
    """Generate a boot protocol prompt for an agent platform."""
    output = generate_boot_prompt(bank=args.bank, fmt=args.format)
    if args.output:
        with open(args.output, "w") as f:
            f.write(output)
        print(f"Boot prompt written to {args.output}")
    else:
        print(output)


def cmd_export(args: argparse.Namespace) -> None:
    """Export memory banks to a JSON backup file."""
    from anamnesis.sdk.client import AnamnesisClient, AnamnesisError

    client = AnamnesisClient.from_env()
    try:
        if args.all:
            from anamnesis.cli.export_import import export_all
            result = export_all(client)
        elif args.bank:
            from anamnesis.cli.export_import import export_bank
            result = export_bank(client, args.bank)
        else:
            print("Error: specify --bank <name> or --all", file=sys.stderr)
            sys.exit(1)

        output_json = json.dumps(result, indent=2, default=str)

        if args.output:
            with open(args.output, "w", encoding="utf-8") as f:
                f.write(output_json)

            # Count stats for summary
            banks = result.get("banks", [])
            total_memories = sum(len(b.get("memories", [])) for b in banks)
            total_entities = sum(len(b.get("entities", [])) for b in banks)
            total_relationships = sum(
                len(b.get("relationships", [])) for b in banks
            )
            print(
                f"Exported {len(banks)} bank(s) to {args.output}\n"
                f"  Memories:      {total_memories}\n"
                f"  Entities:      {total_entities}\n"
                f"  Relationships: {total_relationships}"
            )
        else:
            print(output_json)
    except AnamnesisError as e:
        print(f"Export failed: {e}", file=sys.stderr)
        sys.exit(1)
    finally:
        client.close()


def cmd_import(args: argparse.Namespace) -> None:
    """Import memory banks from a JSON backup file."""
    from anamnesis.sdk.client import AnamnesisClient, AnamnesisError
    from anamnesis.cli.export_import import import_bank

    client = AnamnesisClient.from_env()
    try:
        result = import_bank(client, args.file, merge=args.merge)

        mode = "merge" if args.merge else "overwrite"
        print(f"Import complete (mode: {mode})")
        print(f"  Banks:         {result.get('imported_banks', 0)}")
        print(f"  Memories:      {result.get('imported_memories', 0)}")
        print(f"  Entities:      {result.get('imported_entities', 0)}")
        print(f"  Relationships: {result.get('imported_relationships', 0)}")

        if result.get("skipped_memories", 0) > 0:
            print(f"  Skipped (dup): {result['skipped_memories']}")
        if result.get("skipped_entities", 0) > 0:
            print(f"  Skipped ents:  {result['skipped_entities']}")

        errors = result.get("errors", [])
        if errors:
            print(f"\n  Errors ({len(errors)}):")
            for err in errors[:10]:
                print(f"    - {err}")
            if len(errors) > 10:
                print(f"    ... and {len(errors) - 10} more")

    except FileNotFoundError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    except AnamnesisError as e:
        print(f"Import failed: {e}", file=sys.stderr)
        sys.exit(1)
    finally:
        client.close()


def cmd_prune(args: argparse.Namespace) -> None:
    """Preview or execute memory pruning for a bank."""
    from anamnesis.sdk.client import AnamnesisClient, AnamnesisError

    client = AnamnesisClient.from_env()
    try:
        dry_run = args.dry_run
        result = client.prune(bank=args.bank, dry_run=dry_run)

        candidates = result.get("candidates", [])
        archived = result.get("archived_count", 0)
        mode = "DRY RUN" if dry_run else "EXECUTED"

        print(f"=== Prune {mode}: {args.bank} ===\n")
        print(f"Candidates found: {len(candidates)}")

        if not dry_run:
            print(f"Memories archived: {archived}")

        if candidates:
            print()
            for c in candidates:
                content_preview = c.get("content", "")[:80]
                weight = c.get("weight", 0)
                reason = c.get("reason", "unknown")
                status = c.get("status", "unknown")
                print(f"  [{status}] (w={weight:.2f}) {content_preview}")
                print(f"         Reason: {reason}")
        else:
            print("\nNo prune candidates found.")

        if dry_run and candidates:
            print(f"\nRe-run without --dry-run to archive {len(candidates)} memories.")

    except AnamnesisError as e:
        print(f"Prune failed: {e}", file=sys.stderr)
        sys.exit(1)
    finally:
        client.close()


def cmd_restore(args: argparse.Namespace) -> None:
    """Restore an archived memory back to active status."""
    from anamnesis.sdk.client import AnamnesisClient, AnamnesisError

    client = AnamnesisClient.from_env()
    try:
        result = client.restore(memory_id=args.memory_id)
        print(f"Restored memory {result['memory_id']} to status '{result['status']}'")
        content = result.get("content", "")
        if content:
            print(f"  Content: {content[:120]}")
    except AnamnesisError as e:
        print(f"Restore failed: {e}", file=sys.stderr)
        sys.exit(1)
    finally:
        client.close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="anamnesis",
        description="Anamnesis CLI — strategic memory management tools",
    )
    sub = parser.add_subparsers(dest="command")

    # boot
    boot_cmd = sub.add_parser(
        "boot",
        help="Fetch a cold-start boot briefing from a memory bank",
    )
    boot_cmd.add_argument(
        "--bank", required=True, help="Memory bank name",
    )
    boot_cmd.add_argument(
        "--agent", default=None,
        help="Agent name (optional, for personalized briefing)",
    )
    boot_cmd.add_argument(
        "--no-recent", action="store_true", default=False,
        help="Exclude recent session outcomes",
    )
    boot_cmd.add_argument(
        "--json", dest="json_output", action="store_true", default=False,
        help="Output raw JSON instead of human-readable format",
    )

    # generate-boot-prompt
    boot_gen = sub.add_parser(
        "generate-boot-prompt",
        help="Generate a boot protocol prompt for an agent platform",
    )
    boot_gen.add_argument(
        "--bank", required=True, help="Memory bank name"
    )
    boot_gen.add_argument(
        "--format", required=True, choices=SUPPORTED_FORMATS,
        help="Output format for the agent platform",
    )
    boot_gen.add_argument(
        "--output", default=None, help="Write output to file instead of stdout",
    )

    # export
    export_cmd = sub.add_parser(
        "export",
        help="Export memory banks to JSON backup",
    )
    export_group = export_cmd.add_mutually_exclusive_group(required=True)
    export_group.add_argument(
        "--bank", default=None, help="Name of the bank to export",
    )
    export_group.add_argument(
        "--all", action="store_true", default=False,
        help="Export all banks",
    )
    export_cmd.add_argument(
        "--output", "-o", default=None,
        help="Output file path (prints to stdout if not set)",
    )

    # import
    import_cmd = sub.add_parser(
        "import",
        help="Import memory banks from JSON backup",
    )
    import_cmd.add_argument(
        "--file", "-f", required=True,
        help="Path to the JSON backup file",
    )
    import_cmd.add_argument(
        "--merge", action="store_true", default=False,
        help="Merge mode: skip memories that already exist instead of failing",
    )

    # prune
    prune_cmd = sub.add_parser(
        "prune",
        help="Preview or archive stale, decayed, and superseded memories",
    )
    prune_cmd.add_argument(
        "--bank", required=True, help="Memory bank name",
    )
    prune_cmd.add_argument(
        "--dry-run", action="store_true", default=False,
        help="Preview candidates without archiving (default behavior)",
    )

    # restore
    restore_cmd = sub.add_parser(
        "restore",
        help="Restore an archived memory back to active status",
    )
    restore_cmd.add_argument(
        "--memory-id", required=True,
        help="UUID of the archived memory to restore",
    )

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    if args.command is None:
        parser.print_help()
        sys.exit(1)

    commands = {
        "boot": cmd_boot,
        "generate-boot-prompt": cmd_generate_boot,
        "export": cmd_export,
        "import": cmd_import,
        "prune": cmd_prune,
        "restore": cmd_restore,
    }

    handler = commands.get(args.command)
    if handler:
        handler(args)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
