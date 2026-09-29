"""Admin cog for general administrative commands."""

import logging

import discord
from discord import app_commands
from discord.ext import commands

from bot.permissions import check_admin
from bot.utils import log_to_ops_channel, send_lines
from core.models import AuditLog

logger = logging.getLogger(__name__)


class AdminCog(commands.Cog):
    """General admin commands."""

    admin_group = app_commands.Group(name="admin", description="General administrative commands")

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @admin_group.command(
        name="sync-roles",
        description="[ADMIN] Preview role synchronization (dry run only)",
    )
    @app_commands.check(check_admin)
    async def admin_sync_roles(self, interaction: discord.Interaction) -> None:
        """Preview the Authentik-group role sync for the competition guild (dry run only).

        Preview only; the live add-only sync runs from the portal Sync Roles page.
        """
        from bot.role_sync import AuthentikRoleSyncManager

        await interaction.response.defer(ephemeral=True)

        try:
            role_sync = AuthentikRoleSyncManager(self.bot)

            await interaction.followup.send(
                "Starting role synchronization preview (dry run)...\n"
                "No changes will be made. Run the add-only sync from the portal's Sync Roles page.",
                ephemeral=True,
            )

            stats = await role_sync.sync_roles(dry_run=True)

            result_parts = ["**Role sync preview complete (dry run)**"]
            if stats["roles_added"]:
                result_parts.append(f"• Would add roles: {stats['roles_added']}")
            if stats.get("extra_linked"):
                result_parts.append(f"• Linked users with extra roles (not removed): {stats['extra_linked']}")
            if stats.get("unlinked_holders"):
                result_parts.append(f"• Unlinked role holders (not removed): {stats['unlinked_holders']}")
            if stats["errors"]:
                result_parts.append(f"• Errors: {stats['errors']}")

            changes = stats.get("changes", [])
            changes_list = [str(c) for c in changes] if isinstance(changes, list) else []
            await send_lines(
                interaction,
                "\n".join(result_parts),
                changes_list,
                title="Changes",
                filename="role_sync_preview.txt",
            )

            await AuditLog.objects.acreate(
                action="role_sync",
                admin_user=str(interaction.user),
                target_entity="guilds",
                target_id=0,
                details={
                    "roles_added": stats["roles_added"],
                    "roles_removed": stats["roles_removed"],
                    "errors": stats["errors"],
                    "changes": changes_list[:50],
                },
            )

            summary = "\n".join(result_parts)
            ops_msg = (
                f"Role sync preview by {interaction.user.mention}\n{summary}\n• Changes listed: {len(changes_list)}"
            )
            await log_to_ops_channel(self.bot, ops_msg)

        except Exception as e:
            logger.error(f"Role sync failed: {e}", exc_info=True)
            await interaction.followup.send(f"Role sync failed: {e!s}", ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(AdminCog(bot))
