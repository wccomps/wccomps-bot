"""Shared utility for creating Discord ticket threads."""

import logging

import discord
from asgiref.sync import sync_to_async

from bot.utils import TEAM_CHAT_CHANNEL_KEYWORD, THREAD_AUTO_ARCHIVE_MINUTES
from team.models import Team
from ticketing.models import Ticket

logger = logging.getLogger(__name__)


async def create_ticket_thread(
    guild: discord.Guild,
    ticket: Ticket,
    team: Team,
    pin_message: bool = False,
) -> discord.Thread | None:
    """Create a Discord thread for a ticket in the team's chat channel.

    Saves the thread ID on the ticket, adds active team members, and posts the ticket embed with action buttons.

    Returns the thread, or None if the category or chat channel could not be
    found. Discord API errors propagate.
    """
    from bot.ticket_dashboard import TicketActionView, format_ticket_embed
    from bot.utils import get_team_member_discord_ids

    if not team.discord_category_id:
        logger.warning(f"Team {team.team_name} has no discord_category_id; cannot create ticket thread")
        return None

    category = guild.get_channel(team.discord_category_id)
    if not category:
        logger.warning(f"Category channel {team.discord_category_id} not found in guild for team {team.team_name}")
        return None

    if not isinstance(category, discord.CategoryChannel):
        logger.warning(f"Channel {team.discord_category_id} is not a CategoryChannel for team {team.team_name}")
        return None

    chat_channel: discord.TextChannel | None = None
    for channel in category.channels:
        if isinstance(channel, discord.TextChannel) and TEAM_CHAT_CHANNEL_KEYWORD in channel.name.lower():
            chat_channel = channel
            break

    if not chat_channel:
        logger.warning(f"No text channel with 'chat' in name found in category {category.name}")
        return None

    thread = await chat_channel.create_thread(
        name=f"{ticket.ticket_number} - Team {team.team_number:02d} - {ticket.title[:60]}",
        auto_archive_duration=THREAD_AUTO_ARCHIVE_MINUTES,
    )

    # Targeted update: a full save of this instance would revert concurrent web changes.
    ticket.discord_thread_id = thread.id
    ticket.discord_channel_id = category.id
    await Ticket.objects.filter(pk=ticket.pk).aupdate(discord_thread_id=thread.id, discord_channel_id=category.id)

    team_member_ids = await get_team_member_discord_ids(team)
    for member_id in team_member_ids:
        try:
            member = guild.get_member(member_id)
            if member:
                await thread.add_user(member)
        except Exception as e:
            logger.warning(f"Failed to add member {member_id} to thread {thread.id}: {e}")

    embed = await sync_to_async(format_ticket_embed)(ticket)
    view = TicketActionView(ticket.id)
    message = await thread.send(
        f"**Ticket #{ticket.ticket_number}** - Use buttons below to manage this ticket.",
        embed=embed,
        view=view,
    )

    if pin_message:
        try:
            await message.pin()
        except Exception as pin_error:
            logger.warning(f"Failed to pin ticket message in thread {thread.id}: {pin_error}")

    logger.info(f"Created thread {thread.id} for ticket #{ticket.ticket_number}")
    return thread


async def publish_new_ticket(bot: discord.Client, guild: discord.Guild | None, ticket: Ticket) -> None:
    """Give a newly created ticket its team thread and put it on the dashboard.

    ``ticket.team`` must be loaded. Thread failures are logged, not raised: the
    ticket already exists and still belongs on the dashboard.
    """
    from bot.ticket_dashboard import post_ticket_to_dashboard

    if guild is None:
        logger.warning(f"No guild available; ticket {ticket.ticket_number} will have no thread")
    else:
        try:
            await create_ticket_thread(guild=guild, ticket=ticket, team=ticket.team, pin_message=True)
        except Exception:
            logger.exception(f"Failed to create thread for ticket {ticket.ticket_number}")
    await post_ticket_to_dashboard(bot, ticket)
