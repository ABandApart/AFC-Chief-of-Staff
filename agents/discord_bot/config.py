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

# The operator's Discord user ID — the ONLY account allowed to decide approvals
# (B2 / PRD-b2 Amendment 1). A non-secret Snowflake, like the channel IDs above.
# FAIL-CLOSED: while this is 0 (unset), *every* approval click is denied — the
# gate is dead until it's configured, which is the safe default. Set it to your
# own Discord user ID (Developer Mode → right-click your name → Copy User ID)
# and re-deploy before using #approvals.
OPERATOR_DISCORD_ID = 0
