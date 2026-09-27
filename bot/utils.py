"""Utility functions for the bot."""

import logging
from typing import Final, Literal

import discord
from asgiref.sync import sync_to_async
from django.conf import settings
from django.db import close_old_connections

from core.utils import missing_discord_settings
from team.models import MAX_TEAMS, Team

logger = logging.getLogger(__name__)

# -- Constants --

TEAM_CHAT_CHANNEL_KEYWORD = "chat"
THREAD_AUTO_ARCHIVE_MINUTES: Final[Literal[10080]] = 10080  # 7 days
DISCORD_EMBED_FIELD_CHAR_LIMIT = 1024

# -- Logging --


async def log_to_ops_channel(bot: discord.Client, message: str, embed: discord.Embed | None = None) -> None:
    """Log a message to the operations channel."""
    try:
        channel_id = settings.DISCORD_LOG_CHANNEL_ID
        if not channel_id:
            logger.warning("DISCORD_LOG_CHANNEL_ID not configured")
            return

        channel = bot.get_channel(channel_id)
        if not channel:
            logger.error(f"Operations channel {channel_id} not found")
            return

        # Type guard: only TextChannel and Thread have send()
        if isinstance(channel, (discord.TextChannel, discord.Thread)):
            if embed:
                await channel.send(message, embed=embed)
            else:
                await channel.send(message)
            logger.info(f"Logged to ops channel: {message}")
        else:
            logger.error(f"Channel {channel_id} is not a text channel or thread")
    except Exception as e:
        logger.exception(f"Failed to log to ops channel: {e}")


# -- Team Utilities --


async def recycle_db_connection() -> None:
    """Drop the bot's DB connection if it is broken or past CONN_MAX_AGE; the next query reconnects.

    Django only does this on request_started/request_finished, and the bot serves no requests,
    so after a Postgres restart its single connection stayed dead and every loop failed forever.
    Must run via sync_to_async's default (thread_sensitive=True) executor: that one thread owns
    the connection all bot ORM calls use.
    """
    await sync_to_async(close_old_connections)()


async def report_missing_discord_settings(bot: discord.Client) -> None:
    """Log and post to the ops channel any required Discord IDs that are unset.

    Unset IDs used to fail silently (roles/channels "not found"), so this runs at startup.
    """
    missing = missing_discord_settings()
    if not missing:
        return
    message = (
        f"⚠️ Discord settings missing from .env (set to 0): {', '.join(missing)}. "
        "Role sync, /link roles and announcements that depend on them will not work."
    )
    logger.error(message)
    await log_to_ops_channel(bot, message)


async def get_team_or_respond(
    interaction: discord.Interaction, team_number: int, validate_range: bool = True
) -> Team | None:
    """
    Get team by number or respond with error message.

    Args:
        interaction: Discord interaction to respond to
        team_number: Team number to look up
        validate_range: If True, validate team_number is between 1-MAX_TEAMS

    Returns:
        Team object if found, None if not found (error sent to user)
    """
    from team.models import Team

    if validate_range and (team_number < 1 or team_number > MAX_TEAMS):
        await interaction.response.send_message(f"Team number must be between 1 and {MAX_TEAMS}", ephemeral=True)
        return None

    team = await Team.objects.filter(team_number=team_number).afirst()
    if not team:
        await interaction.response.send_message(f"Team {team_number} not found", ephemeral=True)
        return None
    return team


# -- Role Management --


async def safe_remove_role(member: discord.Member, role: discord.Role, reason: str | None = None) -> bool:
    """
    Safely remove a role from a member, catching permission errors.

    Returns:
        True if role was removed or member didn't have it, False on error
    """
    if role not in member.roles:
        return True

    try:
        await member.remove_roles(role, reason=reason)
        return True
    except discord.errors.Forbidden:
        logger.warning(f"Permission denied removing role {role.name} from {member}")
        return False
    except Exception as e:
        logger.exception(f"Error removing role {role.name} from {member}: {e}")
        return False


async def remove_blueteam_role(member: discord.Member, guild: discord.Guild, reason: str | None = None) -> bool:
    """
    Remove Blueteam role from a member if they have it.

    Returns:
        True if removed or not present, False on error
    """
    blueteam_role = discord.utils.get(guild.roles, name="Blueteam")
    if not blueteam_role:
        return True

    return await safe_remove_role(member, blueteam_role, reason)


@sync_to_async
def get_team_member_discord_ids(team: Team) -> list[int]:
    """
    Get list of Discord IDs for all active team members.

    Args:
        team: Team object to get members from

    Returns:
        List of Discord IDs as integers
    """
    return list(team.members.filter(is_active=True).values_list("discord_id", flat=True))


# -- UI Components --


class ConfirmView(discord.ui.View):
    """Reusable confirmation dialog with confirm/cancel buttons."""

    def __init__(
        self,
        confirm_label: str = "Confirm",
        cancel_label: str = "Cancel",
        timeout: float = 60,
    ) -> None:
        super().__init__(timeout=timeout)
        self.confirmed: bool | None = None
        self._confirm_label = confirm_label
        self._cancel_label = cancel_label
        self._setup_buttons()

    def _setup_buttons(self) -> None:
        confirm_btn = ConfirmButton(label=self._confirm_label, style=discord.ButtonStyle.danger)
        cancel_btn = CancelButton(label=self._cancel_label, style=discord.ButtonStyle.secondary)
        self.add_item(confirm_btn)
        self.add_item(cancel_btn)


class ConfirmButton(discord.ui.Button["ConfirmView"]):
    """Confirm button for ConfirmView."""

    async def callback(self, interaction: discord.Interaction) -> None:
        if self.view:
            self.view.confirmed = True
            self.view.stop()
        await interaction.response.defer()


class CancelButton(discord.ui.Button["ConfirmView"]):
    """Cancel button for ConfirmView."""

    async def callback(self, interaction: discord.Interaction) -> None:
        if self.view:
            self.view.confirmed = False
            self.view.stop()
        await interaction.response.defer()
