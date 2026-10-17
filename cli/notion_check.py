"""Notion connection check (`PRD-notion-integration.md`, increment N1).

    uv run python -m cli.notion_check

Authenticates with keychain `notion-token`, then lists every page and data
source shared with the integration. N1's verification: the
"AFC Richmond Chief-of-Staff" page must appear. Run as barry-agent (the keychain
item lives there). Read-only — it changes nothing in Notion.
"""

from __future__ import annotations

import argparse
import sys

from agents._lib import notion_client
from agents._lib.notion_client import NotionClient, NotionError

EXPECTED_ROOT = "AFC Richmond Chief-of-Staff"


def main() -> int:
    parser = argparse.ArgumentParser(description="Check the Notion integration.")
    parser.add_argument("--expect", default=EXPECTED_ROOT,
                        help=f"page title that must be visible (default: {EXPECTED_ROOT!r})")
    args = parser.parse_args()

    try:
        client = NotionClient.from_keychain()
    except RuntimeError as e:
        print(f"FAIL  {e}")
        print("      Store it as barry-agent: security add-generic-password "
              "-a \"$USER\" -s notion-token -T \"\" -U -w")
        return 1

    try:
        bot = client.me()
        items = list(client.iter_search())
    except NotionError as e:
        print(f"FAIL  {e}")
        if e.status == 401:
            print("      The key was rejected. Re-copy the integration's API key.")
        return 1

    print(f"OK    authenticated as {bot.get('name') or bot.get('id')} "
          f"(Notion-Version {notion_client.NOTION_VERSION})")
    pages = [i for i in items if i.get("object") == "page"]
    sources = [i for i in items if i.get("object") == "data_source"]
    print(f"      shared with the integration: {len(pages)} pages, {len(sources)} data sources")
    for item in pages + sources:
        print(f"      {item['object']:<11} {notion_client.title_of(item)}  ({item['id']})")

    if not any(notion_client.title_of(p) == args.expect for p in pages):
        print(f"FAIL  {args.expect!r} is not visible. In Notion, open the page → ••• → "
              "Connections → add the integration.")
        return 1
    print(f"OK    {args.expect!r} is visible")
    return 0


if __name__ == "__main__":
    sys.exit(main())
