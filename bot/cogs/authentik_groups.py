"""Background refresh of stored Authentik groups, so removals and deactivations take effect."""

import logging

from asgiref.sync import sync_to_async
from discord.ext import commands, tasks

from core.services.user_groups import GroupRefreshAbortedError, refresh_user_groups

logger = logging.getLogger(__name__)

REFRESH_MINUTES = 5


async def refresh_groups_now() -> None:
    """Refresh once; never raises (the caller is a background loop)."""
    try:
        result = await sync_to_async(refresh_user_groups)()
    except GroupRefreshAbortedError as e:
        logger.error(f"Authentik group refresh skipped, nothing changed: {e}")
        return
    except Exception as e:
        logger.warning(f"Authentik group refresh failed: {e}")
        return
    if result.changed:
        logger.info(f"Authentik group refresh: {result.changed} of {result.checked} users changed")


class AuthentikGroupsCog(commands.Cog):
    """Keeps UserGroups in step with Authentik between logins."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.refresh_task.start()

    async def cog_unload(self) -> None:
        self.refresh_task.cancel()

    @tasks.loop(minutes=REFRESH_MINUTES)
    async def refresh_task(self) -> None:
        """Refresh stored groups from Authentik every few minutes."""
        await refresh_groups_now()

    @refresh_task.before_loop
    async def before_refresh(self) -> None:
        if self.bot.is_closed():
            return
        try:
            await self.bot.wait_until_ready()
        except RuntimeError:
            # Bot was never logged in (e.g., during tests)
            self.refresh_task.cancel()


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(AuthentikGroupsCog(bot))
