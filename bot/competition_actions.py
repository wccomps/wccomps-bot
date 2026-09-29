"""Shared competition actions for commands and timer."""

import logging

import discord
from asgiref.sync import sync_to_async

from bot.permissions import clear_permission_cache
from core.models import CompetitionConfig
from core.services.competition import CompetitionRunResult, run_competition_to_completion
from team.models import MAX_TEAMS

logger = logging.getLogger(__name__)


async def run_competition(enable: bool, actor: str) -> CompetitionRunResult:
    """Start (enable=True) or stop the competition via the shared service, off the event loop.

    Not thread_sensitive: the run makes dozens of Authentik calls and would otherwise hold the
    single thread every other sync_to_async call in the bot waits on.
    """
    result = await sync_to_async(run_competition_to_completion, thread_sensitive=False)(enable, actor)
    # The service refreshed stored groups; drop the bot's cached copies of them.
    clear_permission_cache()
    return result


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
        value=f"{account_status} ({MAX_TEAMS} teams)",
        inline=True,
    )

    # Last updated
    embed.set_footer(text="Last updated")
    embed.timestamp = discord.utils.utcnow()

    return embed
