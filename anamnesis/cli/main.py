"""Anamnesis CLI — command-line tools for memory management.

Usage:
    python3 -m anamnesis.cli generate-boot-prompt --bank <name> --format <format>
    python3 -m anamnesis.cli export --bank <name> --output backup.json
    python3 -m anamnesis.cli export --all --output full_backup.json
    python3 -m anamnesis.cli import --file backup.json
    python3 -m anamnesis.cli import --file backup.json --merge
"""

from __future__ import annotations

import argparse
import json
import sys

from anamnesis.cli.generate_boot import generate_boot_prompt, SUPPORTED_FORMATS


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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="anamnesis",
        description="Anamnesis CLI — strategic memory management tools",
    )
    sub = parser.add_subparsers(dest="command")

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

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    if args.command is None:
        parser.print_help()
        sys.exit(1)

    commands = {
        "generate-boot-prompt": cmd_generate_boot,
        "export": cmd_export,
        "import": cmd_import,
    }

    handler = commands.get(args.command)
    if handler:
        handler(args)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
