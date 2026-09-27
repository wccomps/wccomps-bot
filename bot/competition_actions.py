"""Shared competition actions for commands and timer."""

import logging
from typing import TypedDict

import discord
from asgiref.sync import sync_to_async

from bot.cogs.authentik_groups import refresh_groups_now
from core.authentik_manager import AuthentikManager
from core.authentik_utils import toggle_all_blueteam_accounts
from core.models import CompetitionConfig

logger = logging.getLogger(__name__)


class StartResult(TypedDict, total=False):
    """Result of start_competition."""

    success: bool
    error: str
    apps_enabled: list[str]
    apps_failed: list[tuple[str, str | None]]
    accounts_enabled: int
    accounts_failed: int
    quotient_synced: bool
    controlled_apps: list[str]


class StopResult(TypedDict, total=False):
    """Result of stop_competition."""

    success: bool
    error: str
    apps_disabled: list[str]
    apps_failed: list[tuple[str, str | None]]
    accounts_disabled: int
    accounts_failed: int
    controlled_apps: list[str]


async def start_competition() -> StartResult:
    """
    Start the competition by enabling applications and accounts.

    Returns:
        Dict with keys: success, apps_enabled, apps_failed, accounts_enabled,
        accounts_failed, quotient_synced, errors
    """
    config = await sync_to_async(CompetitionConfig.get_config)()

    if not config.controlled_applications:
        return {
            "success": False,
            "error": "No controlled applications configured",
        }

    # Enable applications via Authentik API
    auth_manager = AuthentikManager()
    app_results = auth_manager.enable_applications(config.controlled_applications)

    # Enable all blueteam accounts
    accounts_enabled, accounts_failed = await toggle_all_blueteam_accounts(is_active=True)

    # Team accounts' groups follow is_active; don't make teams wait for the next periodic refresh
    await refresh_groups_now()

    # Sync Quotient metadata
    try:
        from scoring.quotient_sync import sync_quotient_metadata

        await sync_to_async(sync_quotient_metadata)()
        quotient_synced = True
    except Exception as e:
        logger.warning(f"Failed to sync Quotient metadata: {e}")
        quotient_synced = False

    # Update config - clear start_time only, preserve end_time
    @sync_to_async
    def update_config() -> None:
        config.applications_enabled = True
        config.competition_start_time = None
        config.save()

    await update_config()

    # Build results
    success_apps = [app for app, (success, _) in app_results.items() if success]
    failed_apps = [(app, error) for app, (success, error) in app_results.items() if not success]

    return {
        "success": True,
        "apps_enabled": success_apps,
        "apps_failed": failed_apps,
        "accounts_enabled": accounts_enabled,
        "accounts_failed": accounts_failed,
        "quotient_synced": quotient_synced,
        "controlled_apps": config.controlled_applications,
    }


async def stop_competition() -> StopResult:
    """
    Stop the competition by disabling applications and accounts.

    Returns:
        Dict with keys: success, apps_disabled, apps_failed, accounts_disabled,
        accounts_failed
    """
    config = await sync_to_async(CompetitionConfig.get_config)()

    if not config.controlled_applications:
        return {
            "success": False,
            "error": "No controlled applications configured",
        }

    # Disable applications via Authentik API
    auth_manager = AuthentikManager()
    app_results = auth_manager.disable_applications(config.controlled_applications)

    # Disable all blueteam accounts
    accounts_disabled, accounts_failed = await toggle_all_blueteam_accounts(is_active=False)

    # Team accounts' groups follow is_active; don't make teams wait for the next periodic refresh
    await refresh_groups_now()

    # Update config - clear end_time only, preserve start_time
    @sync_to_async
    def update_config() -> None:
        config.applications_enabled = False
        config.competition_end_time = None
        config.save()

    await update_config()

    # Build results
    success_apps = [app for app, (success, _) in app_results.items() if success]
    failed_apps = [(app, error) for app, (success, error) in app_results.items() if not success]

    return {
        "success": True,
        "apps_disabled": success_apps,
        "apps_failed": failed_apps,
        "accounts_disabled": accounts_disabled,
        "accounts_failed": accounts_failed,
        "controlled_apps": config.controlled_applications,
    }


async def update_status_channel(bot: discord.Client) -> bool:
    """
    Update the competition status channel with current state.

    Args:
        bot: Discord bot client

    Returns:
        True if updated successfully, False otherwise
    """
    config = await sync_to_async(CompetitionConfig.get_config)()

    if not config.status_channel_id:
        return False

    channel = bot.get_channel(config.status_channel_id)
    if not channel or not isinstance(channel, discord.TextChannel):
        logger.warning(f"Status channel {config.status_channel_id} not found or not a text channel")
        return False

    # Build status embed
    embed = _build_status_embed(config)

    try:
        if config.status_message_id:
            # Try to edit existing message
            try:
                message = await channel.fetch_message(config.status_message_id)
                await message.edit(embed=embed)
                return True
            except discord.NotFound:
                logger.info("Status message not found, creating new one")

        # Create new message
        message = await channel.send(embed=embed)

        @sync_to_async
        def save_message_id() -> None:
            config.status_message_id = message.id
            config.save(update_fields=["status_message_id"])

        await save_message_id()
        return True

    except Exception as e:
        logger.exception(f"Failed to update status channel: {e}")
        return False


def _build_status_embed(config: CompetitionConfig) -> discord.Embed:
    """Build the status embed for the competition."""
    if config.applications_enabled:
        status = "RUNNING"
        color = discord.Color.green()
    elif config.competition_start_time:
        status = "SCHEDULED"
        color = discord.Color.blue()
    else:
        status = "STOPPED"
        color = discord.Color.red()

    embed = discord.Embed(
        title="Competition Status",
        description=f"**{status}**",
        color=color,
    )

    # Timing info
    if config.competition_start_time:
        embed.add_field(
            name="Scheduled Start",
            value=f"<t:{int(config.competition_start_time.timestamp())}:F>",
            inline=True,
        )

    if config.competition_end_time:
        embed.add_field(
            name="Scheduled End",
            value=f"<t:{int(config.competition_end_time.timestamp())}:F>",
            inline=True,
        )

    # Applications
    if config.controlled_applications:
        apps_str = ", ".join(config.controlled_applications)
        embed.add_field(
            name="Controlled Applications",
            value=apps_str,
            inline=False,
        )

    # Account status
    account_status = "Enabled" if config.applications_enabled else "Disabled"
    embed.add_field(
        name="Team Accounts",
        value=f"{account_status} (50 teams)",
        inline=True,
    )

    # Last updated
    embed.set_footer(text="Last updated")
    embed.timestamp = discord.utils.utcnow()

    return embed
