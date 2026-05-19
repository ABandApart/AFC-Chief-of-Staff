"""Discord deployment-specific constants for the AFC Richmond server.

These are Snowflake IDs (non-secret) — they appear in URLs whenever a
channel or server is linked. The bot **token** is the only Discord
secret; it lives in keychain as `discord-bot-token`.

If a channel is recreated or renamed in Discord, its ID changes. Update
the matching constant here and re-deploy. The architecture's intent is
that channel names are stable for the operator's use but channel IDs
are the source of truth for the code.
"""

# Server (guild)
GUILD_ID = 1499781130306588802

# Channels (in declaration order matching architecture/50-channel-layer.md)
BRIEFING_CHANNEL_ID = 1506399554608824481
TASK_TINDER_CHANNEL_ID = 1506399587135651870
APPROVALS_CHANNEL_ID = 1506399626037694464
CAPTURE_CHANNEL_ID = 1506399687660273695
SYSTEM_CHANNEL_ID = 1506399724578537473
ARCHIVE_CHANNEL_ID = 1506399755914186835
