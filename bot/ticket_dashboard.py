"""Ticket dashboard management for #ticket-queue channel."""

import logging

import discord
from asgiref.sync import sync_to_async

from bot.utils import DISCORD_EMBED_FIELD_CHAR_LIMIT
from core.tickets_config import TicketCategoryConfig, get_category_config
from ticketing.models import Ticket

logger = logging.getLogger(__name__)


def get_ticket_color(status: str) -> discord.Color:
    """Get embed color based on ticket status."""
    colors: dict[str, discord.Color] = {
        "open": discord.Color.red(),
        "claimed": discord.Color.orange(),
        "resolved": discord.Color.green(),
        "cancelled": discord.Color.dark_grey(),
    }
    return colors.get(status, discord.Color.default())


def format_ticket_embed(ticket: Ticket) -> discord.Embed:
    """Format ticket as Discord embed."""
    cat_info = get_category_config(ticket.category_id) or {}

    embed = discord.Embed(
        title=f"Ticket {ticket.ticket_number}: {ticket.title}",
        description=ticket.description,
        color=get_ticket_color(ticket.status),
        timestamp=ticket.created_at,
    )

    # Team info
    embed.add_field(
        name="Team",
        value=f"{ticket.team.team_name} (#{ticket.team.team_number})",
        inline=True,
    )

    # Status
    status_display = ticket.status.replace("_", " ").title()
    embed.add_field(name="Status", value=status_display, inline=True)

    # Category-specific fields (hostname, service, IP)
    if ticket.hostname:
        embed.add_field(name="Hostname", value=ticket.hostname, inline=True)
    if ticket.service_name:
        embed.add_field(name="Service", value=ticket.service_name, inline=True)
    if ticket.ip_address:
        embed.add_field(name="IP Address", value=ticket.ip_address, inline=True)

    # Assigned to (ticket.assigned_to is now a User)
    if ticket.assigned_to:
        embed.add_field(
            name="Assigned To",
            value=ticket.assigned_to.username,
            inline=False,
        )

    # Point impact
    points = cat_info.get("points", 0)
    if cat_info.get("variable_points", False):
        point_text = "Variable"
        embed.add_field(name="Point Impact", value=point_text, inline=True)
    elif points > 0:
        point_text = f"{points} points"
        embed.add_field(name="Point Impact", value=point_text, inline=True)

    # Resolution info
    if ticket.resolved_at:
        embed.add_field(
            name="Resolved At",
            value=discord.utils.format_dt(ticket.resolved_at, style="R"),
            inline=True,
        )
        if ticket.resolution_notes:
            embed.add_field(
                name="Resolution Notes",
                value=ticket.resolution_notes[:DISCORD_EMBED_FIELD_CHAR_LIMIT],
                inline=False,
            )

    embed.set_footer(text=f"Category: {cat_info.get('display_name', f'Category {ticket.category_id}')}")

    return embed


async def post_ticket_to_dashboard(bot: discord.Client, ticket: Ticket) -> None:
    """Trigger unified dashboard update for new ticket."""
    # Trigger unified dashboard update only (no individual messages)
    if hasattr(bot, "unified_dashboard") and bot.unified_dashboard:
        await bot.unified_dashboard.trigger_update()
        logger.info(f"Triggered dashboard update for new ticket {ticket.ticket_number}")


async def update_ticket_dashboard(bot: discord.Client, ticket: Ticket) -> None:
    """Trigger unified dashboard update for ticket changes."""
    # Trigger unified dashboard update only (no individual messages)
    if hasattr(bot, "unified_dashboard") and bot.unified_dashboard:
        await bot.unified_dashboard.trigger_update()
        logger.debug(f"Triggered dashboard update for ticket {ticket.ticket_number}")


class TicketActionView(discord.ui.View):
    """Action buttons for ticket dashboard."""

    def __init__(self, ticket_id: int, thread_url: str | None = None) -> None:
        super().__init__(timeout=None)
        self.ticket_id = ticket_id

        # Add thread link button if URL provided
        if thread_url:
            self.add_item(
                discord.ui.Button(
                    label="Go to Thread",
                    style=discord.ButtonStyle.link,
                    url=thread_url,
                    row=0,
                )
            )

    async def _get_ticket_id_from_interaction(self, interaction: discord.Interaction) -> int | None:
        """Extract ticket ID from interaction message or instance variable."""
        import re

        # If instance has ticket_id, use it (for newly created views)
        if hasattr(self, "ticket_id") and self.ticket_id:
            return self.ticket_id

        # Extract from message embed (for persistent views after bot restart)
        if interaction.message and interaction.message.embeds:
            embed = interaction.message.embeds[0]
            if embed.title:
                # Title format: "Ticket T050-008: Title"
                match = re.match(r"Ticket ([^:]+):", embed.title)
                if match:
                    ticket_number = match.group(1).strip()
                    # Look up ticket by ticket_number
                    ticket = await Ticket.objects.filter(ticket_number=ticket_number).afirst()
                    if ticket:
                        return ticket.id

        return None

    @discord.ui.button(
        label="Claim",
        style=discord.ButtonStyle.primary,
        custom_id="ticket_claim_persistent",
        row=1,
    )
    async def claim_button(self, interaction: discord.Interaction, button: discord.ui.Button[TicketActionView]) -> None:
        """Claim a ticket."""
        from bot.permissions import can_support_tickets_async

        # Extract ticket_id from message embed or instance variable
        ticket_id = await self._get_ticket_id_from_interaction(interaction)
        if not ticket_id:
            await interaction.response.send_message(
                "Could not identify ticket from this message.",
                ephemeral=True,
            )
            return

        # Check permissions
        if not await can_support_tickets_async(interaction):
            await interaction.response.send_message(
                "You don't have permission to claim tickets. "
                "Contact an administrator if you need ticketing support access.",
                ephemeral=True,
            )
            return

        # Use shared atomic claim function
        from ticketing.utils import aclaim_ticket_atomic

        ticket, error = await aclaim_ticket_atomic(
            ticket_id=ticket_id,
            actor_username=str(interaction.user),
            discord_id=interaction.user.id,
            discord_username=str(interaction.user),
        )

        if error or ticket is None:
            await interaction.response.send_message(error or "Failed to claim ticket.", ephemeral=True)
            return

        # Update dashboard
        await update_ticket_dashboard(interaction.client, ticket)

        # Add user to thread if ticket has a thread
        if ticket.discord_thread_id:
            try:
                thread = interaction.client.get_channel(ticket.discord_thread_id)
                if not thread:
                    thread = await interaction.client.fetch_channel(ticket.discord_thread_id)
                if thread and isinstance(thread, discord.Thread):
                    await thread.add_user(interaction.user)
            except Exception as e:
                logger.warning(f"Failed to add user {interaction.user.id} to thread {ticket.discord_thread_id}: {e}")

        await interaction.response.send_message(
            f"You have claimed ticket {ticket.ticket_number}.",
            ephemeral=True,
        )

    @discord.ui.button(
        label="Resolve",
        style=discord.ButtonStyle.success,
        custom_id="ticket_resolve_persistent",
        row=1,
    )
    async def resolve_button(
        self, interaction: discord.Interaction, button: discord.ui.Button[TicketActionView]
    ) -> None:
        """Show resolve modal with category dropdown and notes."""
        from bot.permissions import can_support_tickets_async

        # Extract ticket_id from message embed or instance variable
        ticket_id = await self._get_ticket_id_from_interaction(interaction)
        if not ticket_id:
            await interaction.response.send_message(
                "Could not identify ticket from this message.",
                ephemeral=True,
            )
            return

        # Check permissions
        if not await can_support_tickets_async(interaction):
            await interaction.response.send_message(
                "You don't have permission to resolve tickets. "
                "Contact an administrator if you need ticketing support access.",
                ephemeral=True,
            )
            return

        ticket = await Ticket.objects.filter(id=ticket_id).afirst()
        if not ticket:
            await interaction.response.send_message("Ticket not found.", ephemeral=True)
            return

        if ticket.status == "resolved":
            await interaction.response.send_message("This ticket is already resolved.", ephemeral=True)
            return

        cat_info = await sync_to_async(get_category_config)(ticket.category_id) or {}
        modal = ResolveTicketModal(ticket, cat_info)
        await interaction.response.send_modal(modal)

    @discord.ui.button(
        label="Cancel",
        style=discord.ButtonStyle.danger,
        custom_id="ticket_cancel_persistent",
        row=2,
    )
    async def cancel_button(
        self, interaction: discord.Interaction, button: discord.ui.Button[TicketActionView]
    ) -> None:
        """Cancel an unclaimed ticket."""

        from bot.permissions import can_support_tickets_async
        from team.models import DiscordLink

        # Extract ticket_id from message embed or instance variable
        ticket_id = await self._get_ticket_id_from_interaction(interaction)
        if not ticket_id:
            await interaction.response.send_message(
                "Could not identify ticket from this message.",
                ephemeral=True,
            )
            return

        # Check if user is ops or team member
        is_ops = await can_support_tickets_async(interaction)

        # Check if user is linked to a team
        @sync_to_async
        def get_team_link() -> DiscordLink | None:
            return (
                DiscordLink.objects.filter(discord_id=interaction.user.id, is_active=True)
                .select_related("team")
                .first()
            )

        link = await get_team_link()
        is_team_member = link and link.team

        if not is_ops and not is_team_member:
            await interaction.response.send_message(
                "You must be a team member or ops to cancel tickets.",
                ephemeral=True,
            )
            return

        # Get ticket
        ticket = await Ticket.objects.select_related("team").filter(id=ticket_id).afirst()
        if not ticket:
            await interaction.response.send_message("Ticket not found.", ephemeral=True)
            return

        # If team member (not ops), verify ticket belongs to their team
        if is_team_member and not is_ops and link and link.team and ticket.team.id != link.team.id:
            await interaction.response.send_message("This ticket does not belong to your team.", ephemeral=True)
            return

        from ticketing.utils import acancel_ticket_atomic

        cancelled, error = await acancel_ticket_atomic(ticket_id=ticket.id, actor_username=str(interaction.user))
        if error or cancelled is None:
            await interaction.response.send_message(error or "Failed to cancel ticket.", ephemeral=True)
            return

        await update_ticket_dashboard(interaction.client, cancelled)

        await interaction.response.send_message(
            f"Ticket {cancelled.ticket_number} has been cancelled (no point penalty).",
            ephemeral=True,
        )


class ResolveTicketModal(discord.ui.Modal, title="Resolve Ticket"):
    """Modal for resolving a ticket."""

    def __init__(self, ticket: Ticket, cat_info: TicketCategoryConfig) -> None:
        super().__init__()
        self.ticket = ticket

        # Create notes input
        self.notes: discord.ui.TextInput[ResolveTicketModal] = discord.ui.TextInput(
            label="Resolution Notes",
            placeholder="Describe how the issue was resolved...",
            style=discord.TextStyle.paragraph,
            required=False,
            max_length=1000,
        )

        self.points: discord.ui.TextInput[ResolveTicketModal]
        if cat_info.get("variable_points", False):
            min_pts = cat_info.get("min_points", 0)
            max_pts = cat_info.get("max_points", 0)
            placeholder = f"Enter points ({min_pts}-{max_pts})" if max_pts else f"Enter points (min {min_pts})"
            self.points = discord.ui.TextInput(
                label="Points",
                placeholder=placeholder,
                required=True,
                max_length=5,
            )
        else:
            # Show fixed points that will be charged if left blank
            fixed_pts = cat_info.get("points", 0)
            self.points = discord.ui.TextInput(
                label="Points Override",
                placeholder=f"Leave blank for default ({fixed_pts}pt) or enter to override",
                required=False,
                max_length=5,
            )

        # Add items to modal
        self.add_item(self.notes)
        self.add_item(self.points)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        """Handle modal submission."""
        points_override = None
        if self.points.value.strip():
            try:
                points_override = int(self.points.value.strip())
            except ValueError:
                await interaction.response.send_message("Invalid point value. Must be a number.", ephemeral=True)
                return

        # Use shared atomic resolve function
        from ticketing.utils import aresolve_ticket_atomic

        ticket, error = await aresolve_ticket_atomic(
            ticket_id=self.ticket.id,
            actor_username=str(interaction.user),
            resolution_notes=self.notes.value,
            points_override=points_override,
            discord_id=interaction.user.id,
            discord_username=str(interaction.user),
        )

        if error or ticket is None:
            await interaction.response.send_message(error or "Failed to resolve ticket.", ephemeral=True)
            return

        # Update dashboard
        await update_ticket_dashboard(interaction.client, ticket)

        await interaction.response.send_message(
            f"Ticket {ticket.ticket_number} resolved with {ticket.points_charged} point penalty.",
            ephemeral=True,
        )
