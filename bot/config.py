"""Bot-side environment variables; add new ones here rather than reading os.environ in other modules."""

import os

DISCORD_GUILD_ID = int(os.environ.get("DISCORD_GUILD_ID", "0"))
VOLUNTEER_GUILD_ID = int(os.environ.get("VOLUNTEER_GUILD_ID", "0"))
DISCORD_ANNOUNCEMENT_CHANNEL_ID = int(os.environ.get("DISCORD_ANNOUNCEMENT_CHANNEL_ID", "0"))
BLUETEAM_ROLE_ID = int(os.environ.get("BLUETEAM_ROLE_ID", "0"))
