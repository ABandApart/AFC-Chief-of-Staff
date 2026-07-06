"""Cached credential access (macOS Keychain).

Every keychain read used to spawn a `security` subprocess — several per
LLM call once you count API key + db-url lookups. Secrets don't rotate
mid-process, so lookups are cached for the process lifetime. Call
`invalidate()` after rotating a credential in a long-running process
(the Discord bot); one-shot CLIs never need it.
"""

from __future__ import annotations

import subprocess
from functools import cache


@cache
def keychain_get(item_name: str) -> str:
    """Fetch a credential from the current user's macOS Keychain (cached).

    Returns the secret value. Raises RuntimeError if the item is missing.
    """
    result = subprocess.run(
        ["security", "find-generic-password", "-w", "-s", item_name],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"Keychain item '{item_name}' not found. "
            f"Run scripts/keychain_setup.sh or check item name."
        )
    return result.stdout.strip()


def invalidate() -> None:
    """Drop all cached credentials (call after rotating a keychain item)."""
    keychain_get.cache_clear()
