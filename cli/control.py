"""Inspect and validate the control plane (loops + playbooks).

Usage:
    uv run python -m cli.control              # list the inventory
    uv run python -m cli.control validate     # validate; exit 1 on any error

`validate` is the CI-friendly gate: it fails if any loop/playbook manifest is
malformed, so a broken control-plane file can't merge silently.
"""

from __future__ import annotations

import argparse
import sys

from agents._lib.control_plane import discover


def cmd_list() -> int:
    cp = discover()
    print(f"=== loops ({len(cp.loops)}) ===")
    for lp in cp.loops:
        flag = "on " if lp.enabled else "off"
        print(f"  [{flag}] {lp.name:<20} {lp.schedule:<14} {lp.target}")
    print(f"\n=== playbooks ({len(cp.playbooks)}) ===")
    for pb in cp.playbooks:
        pub = "→memory" if pb.publish_to_memory else "local  "
        applies = ",".join(pb.applies_to) or "-"
        print(f"  [{pub}] {pb.name:<28} applies_to: {applies}")
    if cp.errors:
        print(f"\n{len(cp.errors)} error(s):")
        for e in cp.errors:
            print(f"  ✗ {e}")
        return 1
    return 0


def cmd_validate() -> int:
    cp = discover()
    if cp.errors:
        print(f"control plane INVALID — {len(cp.errors)} error(s):")
        for e in cp.errors:
            print(f"  ✗ {e}")
        return 1
    print(f"control plane ok — {len(cp.loops)} loop(s), {len(cp.playbooks)} playbook(s)")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Inspect/validate the control plane.")
    parser.add_argument(
        "action", nargs="?", choices=("list", "validate"), default="list",
        help="list the inventory (default) or validate and exit nonzero on error",
    )
    args = parser.parse_args()
    return cmd_validate() if args.action == "validate" else cmd_list()


if __name__ == "__main__":
    sys.exit(main())
