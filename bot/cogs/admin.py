"""Admin cog for general administrative commands."""

import logging

import discord
from discord import app_commands
from discord.ext import commands

from bot.permissions import check_admin
from bot.utils import log_to_ops_channel
from core.models import AuditLog

logger = logging.getLogger(__name__)


class AdminCog(commands.Cog):
    """General admin commands."""

    # Create admin command group as class attribute
    admin_group = app_commands.Group(name="admin", description="General administrative commands")

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @admin_group.command(
        name="sync-roles",
        description="[ADMIN] Preview role synchronization (dry run only)",
    )
    @app_commands.check(check_admin)
    async def admin_sync_roles(self, interaction: discord.Interaction) -> None:
        """Preview role sync from volunteer guild to competition guild (dry run only).

        Preview only; the live add-only sync runs from the portal Sync Roles page.
        """
        from bot.role_sync import AuthentikRoleSyncManager

        await interaction.response.defer(ephemeral=True)

        try:
            # Create Authentik role sync manager for dry run preview
            role_sync = AuthentikRoleSyncManager(self.bot)

            # Perform dry run only
            await interaction.followup.send(
                "Starting role synchronization preview (dry run)...\n"
                "No changes will be made. Run the add-only sync from the portal's Sync Roles page.",
                ephemeral=True,
            )

            stats = await role_sync.sync_roles(dry_run=True)

            # Build result message with detailed changes (only show non-zero metrics)
            result_parts = ["**Role sync preview complete (dry run)**"]
            if stats["roles_added"]:
                result_parts.append(f"• Would add roles: {stats['roles_added']}")
            if stats.get("extra_linked"):
                result_parts.append(f"• Linked users with extra roles (not removed): {stats['extra_linked']}")
            if stats.get("unlinked_holders"):
                result_parts.append(f"• Unlinked role holders (not removed): {stats['unlinked_holders']}")
            if stats["errors"]:
                result_parts.append(f"• Errors: {stats['errors']}")

            result_msg = "\n".join(result_parts)

            changes = stats.get("changes", [])
            if isinstance(changes, list) and changes:
                result_msg += "\n\n**Changes:**\n"
                # Limit to 20 changes to avoid message length limits
                for change in changes[:20]:
                    result_msg += f"{change}\n"
                if len(changes) > 20:
                    result_msg += f"\n... and {len(changes) - 20} more (check logs for full list)"

            await interaction.followup.send(result_msg, ephemeral=True)

            # Create audit log
            changes_list = changes if isinstance(changes, list) else []
            await AuditLog.objects.acreate(
                action="role_sync",
                admin_user=str(interaction.user),
                target_entity="guilds",
                target_id=0,
                details={
                    "roles_added": stats["roles_added"],
                    "roles_removed": stats["roles_removed"],
                    "errors": stats["errors"],
                    "changes": changes_list[:50],  # Store first 50 changes
                },
            )

            # Log to ops channel with changes
            ops_msg = f"Role sync executed by {interaction.user.mention}\n{result_msg}"
            await log_to_ops_channel(self.bot, ops_msg)

        except Exception as e:
            logger.error(f"Role sync failed: {e}", exc_info=True)
            await interaction.followup.send(f"Role sync failed: {e!s}", ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    """Setup function to add cog to bot."""
    await bot.add_cog(AdminCog(bot))
