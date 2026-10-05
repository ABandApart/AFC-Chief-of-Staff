"""Post one plain message to a Discord channel over the REST API (best effort).

For loops that run outside the bot process and need to say one line in a
channel, the way `agents/briefing/run.py` posts the briefing. Never raises: a
failed notice must not fail the work it reports on.
"""

from __future__ import annotations

import json
import logging
import urllib.request

from agents._lib.creds import keychain_get

logger = logging.getLogger(__name__)

DISCORD_API = "https://discord.com/api/v10"
MAX_CONTENT = 2000  # Discord's message length limit


def post(channel_id: int, text: str, *, user_agent: str = "aiadaptive-cos") -> bool:
    """Returns True if Discord accepted the message."""
    try:
        req = urllib.request.Request(
            f"{DISCORD_API}/channels/{channel_id}/messages",
            data=json.dumps({"content": text[:MAX_CONTENT]}).encode("utf-8"),
            headers={
                "Authorization": f"Bot {keychain_get('discord-bot-token')}",
                "Content-Type": "application/json",
                "User-Agent": user_agent,
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310
            resp.read()
        return True
    except Exception:
        logger.warning("discord post to %s failed", channel_id, exc_info=True)
        return False
