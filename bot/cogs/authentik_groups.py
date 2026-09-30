"""Background refresh of stored Authentik groups, so removals and deactivations take effect, followed by a
role sync so linked users get the Discord roles their groups and team seats call for."""

import logging

from asgiref.sync import sync_to_async
from discord.ext import commands, tasks

from bot.role_sync import AuthentikRoleSyncManager
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


async def sync_roles_now(bot: commands.Bot) -> None:
    """Add the roles linked users are missing; never raises (the caller is a background loop)."""
    try:
        await AuthentikRoleSyncManager(bot).sync_roles()
    except Exception as e:
        logger.warning(f"Role sync failed: {e}")


class AuthentikGroupsCog(commands.Cog):
    """Keeps UserGroups in step with Authentik between logins, and adds the Discord roles they grant."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.refresh_task.start()

    async def cog_unload(self) -> None:
        self.refresh_task.cancel()

    @tasks.loop(minutes=REFRESH_MINUTES)
    async def refresh_task(self) -> None:
        """Refresh stored groups from Authentik every few minutes, then sync roles from them."""
        await refresh_groups_now()
        await sync_roles_now(self.bot)

    @refresh_task.before_loop
    async def before_refresh(self) -> None:
        await self.bot.wait_until_ready()


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(AuthentikGroupsCog(bot))
