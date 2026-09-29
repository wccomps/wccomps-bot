"""Bot ticket commands go through the locked lifecycle helpers."""

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import discord
import pytest
from django.utils import timezone

from bot.cogs.admin_tickets import AdminTicketsCog
from bot.cogs.ticketing import TicketingCog
from team.models import Team
from ticketing.models import Ticket, TicketCategory

pytestmark = [pytest.mark.asyncio, pytest.mark.django_db(transaction=True)]


@pytest.fixture
async def team(db: Any) -> Team:
    return await Team.objects.acreate(team_number=44, team_name="Team 44", max_members=5)


async def _ticket(team: Team, category: TicketCategory, status: str, **extra: Any) -> Ticket:
    return await Ticket.objects.acreate(
        ticket_number=f"T044-{status}", team=team, category=category, title="t", status=status, **extra
    )


async def test_resolve_refuses_cancelled_ticket(
    team: Team, box_reset_category: TicketCategory, mock_interaction: Any, mock_bot: Any
) -> None:
    ticket = await _ticket(team, box_reset_category, "cancelled")
    cog = AdminTicketsCog(mock_bot)
    await cog.admin_ticket_resolve.callback(cog, mock_interaction, ticket_number=ticket.ticket_number)

    assert "Cannot resolve ticket with status: cancelled" in mock_interaction.response.send_message.await_args.args[0]
    await ticket.arefresh_from_db()
    assert (ticket.status, ticket.points_charged) == ("cancelled", 0)


async def test_admin_cancels_claimed_ticket(
    team: Team, box_reset_category: TicketCategory, mock_interaction: Any, mock_bot: Any
) -> None:
    ticket = await _ticket(team, box_reset_category, "claimed", discord_thread_id=123)
    cog = AdminTicketsCog(mock_bot)
    with patch("bot.cogs.admin_tickets.log_to_ops_channel", new_callable=AsyncMock):
        await cog.admin_ticket_cancel.callback(cog, mock_interaction, ticket_number=ticket.ticket_number, reason="dup")

    await ticket.arefresh_from_db()
    assert (ticket.status, ticket.resolution_notes) == ("cancelled", "dup")
    assert ticket.thread_archive_scheduled_at is not None


async def test_archive_loop_does_not_undo_a_reopen(team: Team, box_reset_category: TicketCategory) -> None:
    ticket = await _ticket(
        team, box_reset_category, "resolved", discord_thread_id=321, thread_archive_scheduled_at=timezone.now()
    )

    async def reopen_while_archiving(**_: Any) -> None:
        await Ticket.objects.filter(pk=ticket.pk).aupdate(status="open")

    thread = MagicMock(spec=discord.Thread)
    thread.edit = AsyncMock(side_effect=reopen_while_archiving)
    bot = MagicMock()
    bot.get_channel.return_value = thread
    with patch.object(TicketingCog.archive_threads_task, "start"):
        cog = TicketingCog(bot)
    await cog.archive_threads_task.coro(cog)

    await ticket.arefresh_from_db()
    assert ticket.status == "open"
    assert ticket.thread_archive_scheduled_at is None
