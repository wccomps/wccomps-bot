"""Permission checking for Discord bot commands."""

import logging
from datetime import UTC, datetime, timedelta
from typing import TypedDict

import discord

from core.models import UserGroups
from core.permission_constants import check_groups_for_permission
from team.models import DiscordLink

logger = logging.getLogger(__name__)


class PermissionCacheEntry(TypedDict):
    groups: list[str]
    expires_at: datetime


_permission_cache: dict[int, PermissionCacheEntry] = {}


def clear_permission_cache() -> None:
    """Drop cached groups so the next check reads the refreshed UserGroups."""
    _permission_cache.clear()


def _get_authentik_groups_sync(discord_user_id: int) -> list[str]:
    """Authentik groups of a linked Discord user, cached for 5 minutes; [] if not linked."""
    try:
        now = datetime.now(UTC)
        if discord_user_id in _permission_cache:
            cached = _permission_cache[discord_user_id]
            if cached["expires_at"] > now:
                logger.debug(f"Permission cache hit for {discord_user_id}")
                return cached["groups"]
            del _permission_cache[discord_user_id]

        try:
            logger.debug(f"Checking DiscordLink table for {discord_user_id}")
            discord_link = (
                DiscordLink.objects.select_related("user").filter(discord_id=discord_user_id, is_active=True).first()
            )

            if not discord_link:
                logger.debug(f"No DiscordLink found for {discord_user_id}")
                return []

            try:
                groups = discord_link.user.usergroups.groups
                logger.debug(f"Found groups for {discord_user_id}: {groups}")
            except UserGroups.DoesNotExist:
                logger.warning(f"No UserGroups found for user {discord_link.user.username}")
                groups = []

        except Exception as db_error:
            logger.exception(f"Database query failed: {db_error}")
            return []

        _permission_cache[discord_user_id] = {
            "groups": groups,
            "expires_at": now + timedelta(minutes=5),
        }

        return list(groups)
    except Exception as e:
        logger.warning(f"Error getting Authentik groups for Discord user {discord_user_id}: {e}")
        return []


async def get_authentik_groups_async(discord_user_id: int) -> list[str]:
    from asgiref.sync import sync_to_async

    return await sync_to_async(_get_authentik_groups_sync)(discord_user_id)


async def is_admin_async(interaction: discord.Interaction) -> bool:
    """Check if user has admin permissions via Authentik groups."""
    try:
        groups = await get_authentik_groups_async(interaction.user.id)
        return check_groups_for_permission(groups, "admin")
    except Exception as e:
        logger.exception(f"Failed to check admin permission: {e}")
        return False


async def can_manage_tickets_async(interaction: discord.Interaction) -> bool:
    """Check if user can manage tickets via Authentik groups."""
    try:
        groups = await get_authentik_groups_async(interaction.user.id)
        return check_groups_for_permission(groups, "ticketing_admin")
    except Exception as e:
        logger.exception(f"Failed to check ticketing admin permission: {e}")
        return False


async def can_support_tickets_async(interaction: discord.Interaction) -> bool:
    """Check if user can work on tickets via Authentik groups."""
    try:
        groups = await get_authentik_groups_async(interaction.user.id)
        return check_groups_for_permission(groups, "ticketing_support")
    except Exception as e:
        logger.exception(f"Failed to check ticketing support permission: {e}")
        return False


async def is_gold_team_async(interaction: discord.Interaction) -> bool:
    """Check if user is member of GoldTeam via Authentik groups."""
    try:
        groups = await get_authentik_groups_async(interaction.user.id)
        return check_groups_for_permission(groups, "gold_team")
    except Exception as e:
        logger.exception(f"Failed to check GoldTeam permission: {e}")
        return False


# Permission check functions for use with @app_commands.check()
async def check_admin(interaction: discord.Interaction) -> bool:
    has_permission = await is_admin_async(interaction)
    if not has_permission:
        await interaction.response.send_message(
            "❌ Admin permissions required.\n\n"
            "You need the `WCComps_Discord_Admin` Authentik group.\n"
            "If you have this group, link your account with `/link`.",
            ephemeral=True,
        )
    return has_permission


async def check_ticketing_admin(interaction: discord.Interaction) -> bool:
    has_permission = await can_manage_tickets_async(interaction)
    if not has_permission:
        await interaction.response.send_message(
            "❌ Ticketing admin permissions required.\n\n"
            "You need the `WCComps_Ticketing_Admin` Authentik group.\n"
            "If you have this group, link your account with `/link`.",
            ephemeral=True,
        )
    return has_permission


async def check_ticketing_support(interaction: discord.Interaction) -> bool:
    has_permission = await can_support_tickets_async(interaction)
    if not has_permission:
        await interaction.response.send_message(
            "❌ Ticketing support permissions required.\n\n"
            "You need the `WCComps_Ticketing_Support` Authentik group.\n"
            "If you have this group, link your account with `/link`.",
            ephemeral=True,
        )
    return has_permission


async def check_gold_team(interaction: discord.Interaction) -> bool:
    has_permission = await is_gold_team_async(interaction)
    if not has_permission:
        await interaction.response.send_message(
            "❌ GoldTeam permissions required.\n\n"
            "You need the `WCComps_GoldTeam` Authentik group.\n"
            "If you have this group, link your account with `/link`.",
            ephemeral=True,
        )
    return has_permission


async def is_blue_team_async(interaction: discord.Interaction) -> bool:
    try:
        from asgiref.sync import sync_to_async

        discord_link = await sync_to_async(
            lambda: (
                DiscordLink.objects.filter(discord_id=interaction.user.id, is_active=True, team__isnull=False)
                .select_related("team")
                .first()
            )
        )()
        return discord_link is not None and discord_link.team is not None
    except Exception as e:
        logger.exception(f"Failed to check Blue Team membership: {e}")
        return False


async def check_blue_team(interaction: discord.Interaction) -> bool:
    has_permission = await is_blue_team_async(interaction)
    if not has_permission:
        await interaction.response.send_message(
            "❌ Blue Team membership required.\n\n"
            "You must be linked to a competition team to use this command.\n"
            "Use `/link` to connect your Discord account to your team.",
            ephemeral=True,
        )
    return has_permission
