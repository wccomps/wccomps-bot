"""Admin commands for ticket management."""

import logging
from datetime import timedelta

import discord
from asgiref.sync import sync_to_async
from discord import app_commands
from discord.ext import commands
from django.utils import timezone

from bot.permissions import check_ticketing_admin, check_ticketing_support
from bot.ticket_dashboard import (
    post_ticket_to_dashboard,
    update_ticket_dashboard,
)
from bot.utils import (
    ConfirmView,
    get_team_or_respond,
    log_to_ops_channel,
)
from ticketing.models import Ticket, TicketCategory, TicketHistory

logger = logging.getLogger(__name__)


class UserOrIdTransformer(app_commands.Transformer):
    """Transform either a User mention or a Discord ID string into a User object."""

    async def transform(self, interaction: discord.Interaction, value: discord.User | str) -> discord.User | None:
        """
        Transform input into a discord.User.

        Accepts:
        - @mention (discord automatically converts to discord.User)
        - Raw Discord ID as string (we fetch the user)
        """
        # If the value is already a User, return it (from @mention)
        if isinstance(value, discord.User):
            return value

        # Try to parse as Discord ID
        try:
            user_id = int(value)
            return await interaction.client.fetch_user(user_id)
        except (ValueError, discord.NotFound, discord.HTTPException) as e:
            raise app_commands.AppCommandError("Invalid user. Please provide a @mention or valid Discord ID.") from e


class AdminTicketsCog(commands.Cog):
    """Admin commands for ticket management."""

    # Create tickets command group as class attribute
    tickets_group = app_commands.Group(name="tickets", description="Ticket management commands")

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    async def admin_category_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        """Autocomplete for ticket category (admin - all categories)."""
        from core.tickets_config import get_all_categories

        categories = await sync_to_async(get_all_categories)()
        choices = []
        for cat_id, cat_info in categories.items():
            name = cat_info["display_name"]
            if not current or current.lower() in name.lower():
                choices.append(app_commands.Choice(name=name, value=str(cat_id)))
        return choices[:25]

    @tickets_group.command(name="create", description="[ADMIN] Create a ticket for a team")
    @app_commands.describe(
        team_number="Team number (1-50)",
        category="Ticket category",
        description="Description of the issue",
    )
    @app_commands.autocomplete(category=admin_category_autocomplete)
    @app_commands.check(check_ticketing_admin)
    async def admin_ticket_create(
        self,
        interaction: discord.Interaction,
        team_number: int,
        category: str,
        description: str,
    ) -> None:
        """Create a ticket as admin."""
        if not interaction.guild:
            await interaction.response.send_message("This command must be used in a guild", ephemeral=True)
            return

        team = await get_team_or_respond(interaction, team_number)
        if not team:
            return

        from core.tickets_config import get_category_config

        category_id = int(category)
        cat_info = await sync_to_async(get_category_config)(category_id)
        if not cat_info:
            await interaction.response.send_message("Invalid ticket category.", ephemeral=True)
            return

        # For box-reset, use description as hostname
        hostname = description if "hostname" in cat_info.get("required_fields", []) else ""

        # Create ticket atomically to prevent race conditions
        from ticketing.utils import acreate_ticket_atomic

        category_obj = await TicketCategory.objects.aget(pk=category_id)
        ticket = await acreate_ticket_atomic(
            team=team,
            category=category_obj,
            title=cat_info["display_name"],
            description=description,
            hostname=hostname,
            actor_username=f"admin:{interaction.user}",
            enforce_team_limit=False,
        )

        # Create thread in team's category
        try:
            from bot.thread_creator import create_ticket_thread

            await create_ticket_thread(
                bot=self.bot,
                guild=interaction.guild,
                ticket=ticket,
                team=team,
            )
        except Exception as e:
            logger.exception(f"Failed to create thread for ticket {ticket.ticket_number}: {e}")

        # Post to dashboard
        try:
            await post_ticket_to_dashboard(self.bot, ticket)
        except Exception as e:
            logger.exception(f"Failed to post ticket to dashboard: {e}")

        # Log to ops
        await log_to_ops_channel(
            self.bot,
            f"Admin Ticket Created: {ticket.ticket_number} - {cat_info['display_name']} "
            f"for **{team.team_name}** by {interaction.user.mention}",
        )

        await interaction.response.send_message(
            f"Created ticket **{ticket.ticket_number}** for **{team.team_name}**\n"
            f"Category: {cat_info['display_name']}\n"
            f"Point cost: {cat_info.get('points', 0)} points",
            ephemeral=True,
        )

    @tickets_group.command(name="list", description="[ADMIN] List open tickets")
    @app_commands.describe(status="Filter by status", team_number="Filter by team number")
    @app_commands.choices(
        status=[
            app_commands.Choice(name="Open", value="open"),
            app_commands.Choice(name="Claimed", value="claimed"),
            app_commands.Choice(name="Resolved", value="resolved"),
            app_commands.Choice(name="All", value="all"),
        ]
    )
    @app_commands.check(check_ticketing_support)
    async def admin_ticket_list(
        self,
        interaction: discord.Interaction,
        status: str = "open",
        team_number: int | None = None,
    ) -> None:
        """List tickets with optional filters."""

        # Build query
        query = Ticket.objects.select_related("team")
        if status != "all":
            query = query.filter(status=status)
        if team_number:
            query = query.filter(team__team_number=team_number)

        # Get total count first
        total_count = await query.acount()

        if total_count == 0:
            await interaction.response.send_message("No tickets found matching criteria", ephemeral=True)
            return

        # Fetch tickets (limit to 25 due to Discord embed field limit)
        display_limit = 25
        tickets = [t async for t in query.order_by("-created_at")[:display_limit]]

        # Build title showing count
        if total_count > display_limit:
            title = f"Tickets ({status}) - Showing {display_limit} of {total_count}"
        else:
            title = f"Tickets ({status}) - {total_count} total"

        embed = discord.Embed(title=title, color=discord.Color.blue())

        from core.tickets_config import get_category_config

        for ticket in tickets:
            cat_info = await sync_to_async(get_category_config)(ticket.category_id) or {}
            value = (
                f"Team: {ticket.team.team_name}\n"
                f"Category: {cat_info.get('display_name', f'Category {ticket.category_id}')}\n"
                f"Status: {ticket.status}\n"
                f"Created: {discord.utils.format_dt(ticket.created_at, style='R')}"
            )
            if ticket.assigned_to:
                value += f"\nAssigned: {ticket.assigned_to.username}"

            embed.add_field(
                name=f"{ticket.ticket_number}: {ticket.title}",
                value=value,
                inline=False,
            )

        if total_count > display_limit:
            embed.set_footer(text=f"Use web interface to see all {total_count} tickets")

        await interaction.response.send_message(embed=embed, ephemeral=True)

    @tickets_group.command(name="resolve", description="[ADMIN] Resolve a ticket and apply points")
    @app_commands.describe(
        ticket_number="Ticket number (e.g., T050-003)",
        notes="Resolution notes",
        points="Point adjustment (for variable point categories)",
    )
    @app_commands.check(check_ticketing_support)
    async def admin_ticket_resolve(
        self,
        interaction: discord.Interaction,
        ticket_number: str,
        notes: str = "",
        points: int | None = None,
    ) -> None:
        """Resolve a ticket and apply point adjustments."""

        ticket = await Ticket.objects.select_related("team").filter(ticket_number=ticket_number).afirst()
        if not ticket:
            await interaction.response.send_message(f"Ticket {ticket_number} not found", ephemeral=True)
            return

        if ticket.status == "resolved":
            await interaction.response.send_message(
                f"Ticket {ticket.ticket_number} is already resolved", ephemeral=True
            )
            return

        from core.tickets_config import get_category_config

        cat_info = await sync_to_async(get_category_config)(ticket.category_id) or {}

        # Determine point penalty
        if cat_info.get("variable_points", False):
            if points is None:
                await interaction.response.send_message(
                    "This category requires an explicit point value.",
                    ephemeral=True,
                )
                return

            min_pts = int(cat_info.get("min_points", 0))
            max_pts = int(cat_info.get("max_points", 0))
            if points < min_pts:
                await interaction.response.send_message(
                    f"Point value must be at least {min_pts}.",
                    ephemeral=True,
                )
                return
            if max_pts and points > max_pts:
                await interaction.response.send_message(
                    f"Point value must be at most {max_pts}.",
                    ephemeral=True,
                )
                return

            point_penalty = points
        else:
            point_penalty = cat_info.get("points", 0)

        from ticketing.utils import get_user_for_ticket

        resolver = await sync_to_async(get_user_for_ticket)(discord_id=interaction.user.id)

        # Update ticket (assigned_to and resolved_by are now User, not DiscordLink)
        ticket.status = "resolved"
        ticket.resolved_at = timezone.now()
        ticket.resolved_by = resolver
        ticket.resolution_notes = notes
        ticket.points_charged = point_penalty
        if not ticket.assigned_to:
            ticket.assigned_to = resolver
            ticket.assigned_at = timezone.now()

        # Schedule thread archiving after 60 seconds
        if ticket.discord_thread_id:
            ticket.thread_archive_scheduled_at = timezone.now() + timedelta(seconds=60)

        await ticket.asave()

        # Create history entry
        await TicketHistory.objects.acreate(
            ticket=ticket,
            action="resolved",
            details={"notes": notes, "point_penalty": point_penalty, "actor": str(interaction.user)},
        )

        # Update dashboard
        try:
            await update_ticket_dashboard(self.bot, ticket)
        except Exception as e:
            logger.exception(f"Failed to update dashboard: {e}")

        # Log to ops
        await log_to_ops_channel(
            self.bot,
            f"Ticket Resolved: {ticket.ticket_number} for **{ticket.team.team_name}** by {interaction.user.mention}\n"
            f"Point Penalty: {point_penalty} points",
        )

        await interaction.response.send_message(
            f"Resolved ticket {ticket.ticket_number}\n"
            f"Point Penalty: {point_penalty} points applied to {ticket.team.team_name}",
            ephemeral=True,
        )

    @tickets_group.command(name="cancel", description="[ADMIN] Cancel a ticket without applying points")
    @app_commands.describe(
        ticket_number="Ticket number (e.g., T050-003)",
        reason="Reason for cancellation",
    )
    @app_commands.check(check_ticketing_admin)
    async def admin_ticket_cancel(self, interaction: discord.Interaction, ticket_number: str, reason: str = "") -> None:
        """Cancel a ticket without point penalty."""

        ticket = await Ticket.objects.select_related("team").filter(ticket_number=ticket_number).afirst()
        if not ticket:
            await interaction.response.send_message(f"Ticket {ticket_number} not found", ephemeral=True)
            return

        if ticket.status in {"resolved", "cancelled"}:
            await interaction.response.send_message(
                f"Ticket {ticket.ticket_number} is already {ticket.status}",
                ephemeral=True,
            )
            return

        from ticketing.utils import get_user_for_ticket

        canceller = await sync_to_async(get_user_for_ticket)(discord_id=interaction.user.id)

        # Update ticket
        ticket.status = "cancelled"
        ticket.resolved_at = timezone.now()
        ticket.resolution_notes = reason or "Cancelled by admin"
        ticket.points_charged = 0
        if not ticket.assigned_to:
            ticket.assigned_to = canceller
            ticket.assigned_at = timezone.now()

        # Schedule thread archiving if Discord thread exists
        if ticket.discord_thread_id:
            from datetime import timedelta

            ticket.thread_archive_scheduled_at = timezone.now() + timedelta(seconds=60)

        await ticket.asave()

        # Create history entry
        await TicketHistory.objects.acreate(
            ticket=ticket,
            action="cancelled",
            details={"reason": reason, "actor": str(interaction.user)},
        )

        # Update dashboard
        try:
            await update_ticket_dashboard(self.bot, ticket)
        except Exception as e:
            logger.exception(f"Failed to update dashboard: {e}")

        # Log to ops
        await log_to_ops_channel(
            self.bot,
            f"Ticket Cancelled: {ticket.ticket_number} for **{ticket.team.team_name}** by {interaction.user.mention}\n"
            f"Reason: {reason or 'No reason provided'}",
        )

        await interaction.response.send_message(
            f"Cancelled ticket {ticket.ticket_number} (no point penalty applied)",
            ephemeral=True,
        )

    @tickets_group.command(
        name="change-category",
        description="[ADMIN] Change the category of a ticket",
    )
    @app_commands.describe(
        ticket_number="Ticket number (e.g., T050-003)",
        new_category="New category for the ticket",
    )
    @app_commands.autocomplete(new_category=admin_category_autocomplete)
    @app_commands.check(check_ticketing_admin)
    async def admin_change_category(
        self, interaction: discord.Interaction, ticket_number: str, new_category: str
    ) -> None:
        """Change the category of a ticket."""
        ticket = await Ticket.objects.select_related("team").filter(ticket_number=ticket_number).afirst()
        if not ticket:
            await interaction.response.send_message(f"Ticket {ticket_number} not found", ephemeral=True)
            return

        from core.tickets_config import get_category_config

        old_category_id = ticket.category_id
        new_category_id = int(new_category)
        if old_category_id == new_category_id:
            await interaction.response.send_message(
                f"Ticket {ticket.ticket_number} is already in that category",
                ephemeral=True,
            )
            return

        old_cat_info = await sync_to_async(get_category_config)(old_category_id) or {}
        new_cat_info = await sync_to_async(get_category_config)(new_category_id) or {}

        # Update category
        ticket.category_id = new_category_id
        await ticket.asave()

        # Create history entry
        await TicketHistory.objects.acreate(
            ticket=ticket,
            action="category_changed",
            details={
                "actor": str(interaction.user),
                "old_category": str(old_category_id),
                "old_category_name": old_cat_info.get("display_name", str(old_category_id)),
                "new_category": str(new_category_id),
                "new_category_name": new_cat_info.get("display_name", str(new_category_id)),
                "old_points": old_cat_info.get("points", 0),
                "new_points": new_cat_info.get("points", 0),
            },
        )

        # Update dashboard
        try:
            await update_ticket_dashboard(self.bot, ticket)
        except Exception as e:
            logger.exception(f"Failed to update dashboard: {e}")

        # Log to ops
        old_cat_name = old_cat_info.get("display_name", str(old_category_id))
        new_cat_name = new_cat_info.get("display_name", str(new_category_id))

        await log_to_ops_channel(
            self.bot,
            f"Ticket Category Changed: {ticket.ticket_number} for **{ticket.team.team_name}**\n"
            f"Changed by {interaction.user.mention}: {old_cat_name} → {new_cat_name}\n"
            f"Point impact: {old_cat_info.get('points', 0)}pt → {new_cat_info.get('points', 0)}pt",
        )

        await interaction.response.send_message(
            f"Changed {ticket.ticket_number} from {old_cat_name} to {new_cat_name}",
            ephemeral=True,
        )

    @tickets_group.command(
        name="reassign",
        description="[ADMIN] Reassign a ticket to a different volunteer",
    )
    @app_commands.describe(
        ticket_number="Ticket number (e.g., T050-003)",
        volunteer="Discord user (@mention or ID) to assign (leave empty to unassign)",
    )
    @app_commands.check(check_ticketing_admin)
    async def admin_ticket_reassign(
        self,
        interaction: discord.Interaction,
        ticket_number: str,
        volunteer: app_commands.Transform[discord.User, UserOrIdTransformer] | None = None,
    ) -> None:
        """Reassign a ticket to a different volunteer."""
        await interaction.response.defer(ephemeral=True)

        ticket = await Ticket.objects.select_related("team").filter(ticket_number=ticket_number).afirst()
        if not ticket:
            await interaction.followup.send(f"Ticket {ticket_number} not found", ephemeral=True)
            return

        # Resolved/cancelled tickets can be reassigned too (#36); only the assignee changes.
        old_assignee = ticket.assigned_to.username if ticket.assigned_to else "Unassigned"

        if volunteer:
            # If ticket is open, claim it first
            if ticket.status == "open":
                from ticketing.utils import aclaim_ticket_atomic

                claimed_ticket, error = await aclaim_ticket_atomic(
                    ticket_id=ticket.id,
                    actor_username=f"discord:{interaction.user}",
                    discord_id=volunteer.id,
                    discord_username=str(volunteer),
                )

                if error or claimed_ticket is None:
                    await interaction.followup.send(
                        f"Failed to claim ticket: {error or 'Unknown error'}", ephemeral=True
                    )
                    return

                ticket = claimed_ticket
                new_assignee = str(volunteer)
            else:
                # Reassign claimed ticket
                from ticketing.utils import areassign_ticket_atomic

                reassigned_ticket, error = await areassign_ticket_atomic(
                    ticket_id=ticket.id,
                    actor_username=f"discord:{interaction.user}",
                    discord_id=volunteer.id,
                    discord_username=str(volunteer),
                )

                if error or reassigned_ticket is None:
                    await interaction.followup.send(
                        f"Failed to reassign ticket: {error or 'Unknown error'}", ephemeral=True
                    )
                    return

                ticket = reassigned_ticket
                new_assignee = str(volunteer)

            # Add volunteer to Discord thread if it exists
            if ticket.discord_thread_id and interaction.guild:
                try:
                    thread = interaction.guild.get_thread(ticket.discord_thread_id)
                    if thread:
                        await thread.add_user(volunteer)
                        logger.info(f"Added {volunteer} to thread {ticket.discord_thread_id}")
                except Exception as e:
                    logger.warning(f"Failed to add user to thread: {e}")
        else:
            # Unassign - use unclaim
            from ticketing.utils import aunclaim_ticket_atomic

            unclaimed_ticket, error = await aunclaim_ticket_atomic(
                ticket_id=ticket.id,
                actor_username=f"discord:{interaction.user}",
            )

            if error or unclaimed_ticket is None:
                await interaction.followup.send(
                    f"Failed to unassign ticket: {error or 'Unknown error'}", ephemeral=True
                )
                return

            ticket = unclaimed_ticket
            new_assignee = "Unassigned"

        # Update dashboard
        try:
            await update_ticket_dashboard(self.bot, ticket)
        except Exception as e:
            logger.exception(f"Failed to update dashboard: {e}")

        await interaction.followup.send(
            f"Ticket {ticket.ticket_number} reassigned\n• From: {old_assignee}\n• To: {new_assignee}",
            ephemeral=True,
        )

        # Log to ops
        await log_to_ops_channel(
            self.bot,
            f"Ticket reassigned by {interaction.user.mention}\n"
            f"• Ticket: {ticket.ticket_number} ({ticket.team.team_name})\n"
            f"• From: {old_assignee}\n"
            f"• To: {new_assignee}",
        )

    @tickets_group.command(name="reopen", description="[ADMIN] Reopen a resolved ticket")
    @app_commands.describe(ticket_number="Ticket number (e.g., T050-003)", reason="Reason for reopening")
    @app_commands.check(check_ticketing_admin)
    async def admin_ticket_reopen(self, interaction: discord.Interaction, ticket_number: str, reason: str) -> None:
        """Reopen a resolved ticket."""
        await interaction.response.defer(ephemeral=True)

        ticket = await Ticket.objects.select_related("team").filter(ticket_number=ticket_number).afirst()
        if not ticket:
            await interaction.followup.send(f"Ticket {ticket_number} not found", ephemeral=True)
            return

        if ticket.status != "resolved":
            await interaction.followup.send(f"Cannot reopen - ticket is {ticket.status}", ephemeral=True)
            return

        # Reopen ticket (only change status and clear resolved timestamp)
        old_status = ticket.status
        ticket.status = "open"
        ticket.resolved_at = None
        await ticket.asave()

        # Create history
        await TicketHistory.objects.acreate(
            ticket=ticket,
            action="reopened",
            details={
                "actor": str(interaction.user),
                "reason": reason,
                "old_status": old_status,
            },
        )

        refund_msg = ""
        if ticket.points_charged > 0:
            refund_msg = f"\n• Refunded: {ticket.points_charged} points"

        await interaction.followup.send(
            f"Ticket {ticket.ticket_number} reopened\n• Reason: {reason}{refund_msg}",
            ephemeral=True,
        )

        # Log to ops
        await log_to_ops_channel(
            self.bot,
            f"Ticket reopened by {interaction.user.mention}\n"
            f"• Ticket: {ticket.ticket_number} ({ticket.team.team_name})\n"
            f"• Reason: {reason}{refund_msg}",
        )

    @tickets_group.command(name="clear", description="[ADMIN] Delete all tickets and reset counters")
    @app_commands.check(check_ticketing_admin)
    async def admin_ticket_clear(self, interaction: discord.Interaction) -> None:
        """Delete all tickets and reset team counters."""
        from core.models import AuditLog
        from team.models import Team
        from ticketing.models import TicketAttachment, TicketComment, TicketHistory

        # Get counts
        ticket_count = await Ticket.objects.acount()
        attachment_count = await TicketAttachment.objects.acount()
        comment_count = await TicketComment.objects.acount()
        history_count = await TicketHistory.objects.acount()
        teams_to_reset = await Team.objects.filter(ticket_counter__gt=0).acount()

        if ticket_count == 0:
            await interaction.response.send_message("No tickets to clear", ephemeral=True)
            return

        view = ConfirmView(confirm_label="Confirm Delete")
        await interaction.response.send_message(
            f"**WARNING: This will DELETE ALL TICKETS**\n\n"
            f"• Tickets: {ticket_count}\n"
            f"• Attachments: {attachment_count}\n"
            f"• Comments: {comment_count}\n"
            f"• History: {history_count}\n"
            f"• Teams to reset: {teams_to_reset}\n\n"
            f"This action cannot be undone. Are you sure?",
            view=view,
            ephemeral=True,
        )

        await view.wait()

        if view.confirmed is None:
            await interaction.edit_original_response(content="Timed out", view=None)
            return

        if not view.confirmed:
            await interaction.edit_original_response(content="Cancelled", view=None)
            return

        # Delete tickets
        @sync_to_async
        def clear_tickets() -> None:
            from django.db import transaction

            with transaction.atomic():
                Ticket.objects.all().delete()
                Team.objects.filter(ticket_counter__gt=0).update(ticket_counter=0)
                AuditLog.objects.create(
                    action="clear_tickets",
                    admin_user=str(interaction.user),
                    target_entity="tickets",
                    target_id=0,
                    details={
                        "tickets_deleted": ticket_count,
                        "attachments_deleted": attachment_count,
                        "comments_deleted": comment_count,
                        "history_deleted": history_count,
                        "teams_reset": teams_to_reset,
                    },
                )

        await clear_tickets()

        await interaction.edit_original_response(
            content=f"✅ Cleared all tickets\n• Deleted {ticket_count} tickets\n• Reset {teams_to_reset} team counters",
            view=None,
        )

        # Log to ops
        await log_to_ops_channel(
            self.bot,
            f"🗑️ All tickets cleared by {interaction.user.mention}\n"
            f"• Tickets deleted: {ticket_count}\n"
            f"• Teams reset: {teams_to_reset}",
        )


async def setup(bot: commands.Bot) -> None:
    """Setup function to add cog to bot."""
    await bot.add_cog(AdminTicketsCog(bot))
