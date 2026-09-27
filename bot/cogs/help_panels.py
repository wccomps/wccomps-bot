"""Help panels cog - persistent button panels for blue teams."""

import logging
from collections.abc import Awaitable, Callable
from typing import cast

import discord
from discord.ext import commands
from django.conf import settings

from core.tickets_config import get_all_categories, get_category_config
from team.models import DiscordLink
from ticketing.models import TicketCategory
from ticketing.utils import TicketRateLimitError

logger = logging.getLogger(__name__)


async def create_ticket(
    interaction: discord.Interaction,
    category_id: str,
    service_name: str = "",
    description: str = "",
    hostname: str = "",
    ip_address: str = "",
) -> None:
    """Create a ticket from a Discord modal submission."""
    await interaction.response.defer(ephemeral=True)

    link = await (
        DiscordLink.objects.filter(discord_id=interaction.user.id, is_active=True).select_related("team").afirst()
    )

    if not link or not link.team:
        await interaction.followup.send(
            "You must be linked to a competition team to create tickets.\nClick the **🔗 Link Account** button first.",
            ephemeral=True,
        )
        return

    try:
        from asgiref.sync import sync_to_async

        cat_id_int = int(category_id)
        cat_info = await sync_to_async(get_category_config)(cat_id_int)
        if not cat_info:
            await interaction.followup.send("Invalid ticket category.", ephemeral=True)
            return

        field_values = {
            "service_name": service_name,
            "description": description,
            "hostname": hostname,
            "ip_address": ip_address,
        }
        required_fields = cat_info.get("required_fields", [])
        missing_fields = [f for f in required_fields if not field_values.get(f)]
        if missing_fields:
            await interaction.followup.send(
                f"Missing required fields: {', '.join(missing_fields)}",
                ephemeral=True,
            )
            return

        from ticketing.utils import acreate_ticket_atomic

        category_obj = await TicketCategory.objects.aget(pk=cat_id_int)
        ticket = await acreate_ticket_atomic(
            team=link.team,
            category=category_obj,
            title=cat_info["display_name"],
            description=description,
            hostname=hostname,
            ip_address=ip_address,
            service_name=service_name,
            actor_username=f"discord:{interaction.user.name}",
        )

        from core.models import DiscordTask

        await DiscordTask.objects.acreate(
            task_type="ticket_created_web",
            payload={"ticket_id": ticket.id},
        )

        await interaction.followup.send(
            f"✅ Ticket **{ticket.ticket_number}** created!\n"
            f"Category: **{cat_info['display_name']}**\n"
            f"Points: **{cat_info.get('points', 0)}**\n\n"
            f"A volunteer will respond shortly.",
            ephemeral=True,
        )

    except TicketRateLimitError as e:
        logger.warning(f"Ticket rate limit hit by {interaction.user.name} for {link.team.team_name}")
        await interaction.followup.send(str(e), ephemeral=True)
    except Exception as e:
        logger.error(f"Failed to create ticket: {e}", exc_info=True)
        await interaction.followup.send(f"Failed to create ticket: {e!s}", ephemeral=True)


class ServiceScoringModal(discord.ui.Modal, title="Service Scoring Validation"):
    """Modal for service scoring validation tickets."""

    category_id: str = ""

    service_name: discord.ui.TextInput[discord.ui.Modal] = discord.ui.TextInput(
        label="Service Name",
        placeholder="e.g., HTTP, DNS, SSH",
        required=True,
        max_length=100,
    )

    description: discord.ui.TextInput[discord.ui.Modal] = discord.ui.TextInput(
        label="Description (optional)",
        placeholder="Additional details about the issue",
        style=discord.TextStyle.paragraph,
        required=False,
        max_length=1000,
    )

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await create_ticket(
            interaction,
            category_id=self.category_id,
            service_name=self.service_name.value,
            description=self.description.value,
        )


class BoxResetModal(discord.ui.Modal, title="Box Reset / Scrub"):
    """Modal for box reset tickets."""

    category_id: str = ""

    hostname: discord.ui.TextInput[discord.ui.Modal] = discord.ui.TextInput(
        label="Hostname",
        placeholder="e.g., web01, dc01",
        required=True,
        max_length=255,
    )

    ip_address: discord.ui.TextInput[discord.ui.Modal] = discord.ui.TextInput(
        label="IP Address",
        placeholder="e.g., 10.0.1.50",
        required=True,
        max_length=50,
    )

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await create_ticket(
            interaction,
            category_id=self.category_id,
            hostname=self.hostname.value,
            ip_address=self.ip_address.value,
        )


class ScoringServiceCheckModal(discord.ui.Modal, title="Scoring Service Check"):
    """Modal for scoring service check tickets."""

    category_id: str = ""

    service_name: discord.ui.TextInput[discord.ui.Modal] = discord.ui.TextInput(
        label="Service Name",
        placeholder="e.g., HTTP, DNS, SSH",
        required=True,
        max_length=100,
    )

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await create_ticket(
            interaction,
            category_id=self.category_id,
            service_name=self.service_name.value,
        )


class ConsultationModal(discord.ui.Modal, title="Consultation Request"):
    """Modal for consultation tickets."""

    description: discord.ui.TextInput[discord.ui.Modal] = discord.ui.TextInput(
        label="Description",
        placeholder="Describe what you need help with",
        style=discord.TextStyle.paragraph,
        required=True,
        max_length=1000,
    )

    hostname: discord.ui.TextInput[discord.ui.Modal] = discord.ui.TextInput(
        label="Hostname (hands-on only)",
        placeholder="Leave blank for phone consultation",
        required=False,
        max_length=255,
    )

    def __init__(self, category_id: str):
        super().__init__()
        self.category_id = category_id
        cat_info = get_category_config(int(category_id))
        required = cat_info.get("required_fields", []) if cat_info else []
        # Remove hostname field if not required (e.g., phone consultation)
        if "hostname" not in required:
            if cat_info:
                self.title = cat_info["display_name"]
            self.remove_item(self.hostname)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        # Only include hostname if it's still in the modal
        has_hostname = any(
            isinstance(item, discord.ui.TextInput) and item.label == "Hostname (hands-on only)"
            for item in self.children
        )
        await create_ticket(
            interaction,
            category_id=self.category_id,
            description=self.description.value,
            hostname=self.hostname.value if has_hostname else "",
        )


class OtherModal(discord.ui.Modal, title="Other / General Issue"):
    """Modal for other/general tickets."""

    category_id: str = ""

    description: discord.ui.TextInput[discord.ui.Modal] = discord.ui.TextInput(
        label="Description",
        placeholder="Describe your issue or request",
        style=discord.TextStyle.paragraph,
        required=True,
        max_length=1000,
    )

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await create_ticket(
            interaction,
            category_id=self.category_id,
            description=self.description.value,
        )


class CategorySelect(discord.ui.Select["TicketCategoryView"]):
    """Select menu for choosing ticket category."""

    def __init__(self) -> None:
        categories = get_all_categories(user_creatable_only=True)
        options = []
        for cat_id, cat_info in categories.items():
            points = cat_info.get("points", 0)
            options.append(
                discord.SelectOption(
                    label=cat_info["display_name"],
                    value=str(cat_id),
                    description=f"{points} points",
                )
            )

        super().__init__(
            placeholder="Select a ticket category...",
            min_values=1,
            max_values=1,
            options=options,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        """Handle category selection."""
        category_id = self.values[0]

        # Look up category config to determine which modal to show
        cat_info = get_category_config(int(category_id))
        required = cat_info.get("required_fields", []) if cat_info else []

        modal: ServiceScoringModal | BoxResetModal | ScoringServiceCheckModal | ConsultationModal | OtherModal
        if "service_name" in required and "description" in required:
            modal = ServiceScoringModal()
            modal.category_id = category_id
        elif "hostname" in required and "ip_address" in required:
            modal = BoxResetModal()
            modal.category_id = category_id
        elif "service_name" in required:
            modal = ScoringServiceCheckModal()
            modal.category_id = category_id
        elif "hostname" in required:
            modal = ConsultationModal(category_id)
        elif "description" in required:
            # Check if it's a consultation type (has optional hostname)
            optional = cat_info.get("optional_fields", []) if cat_info else []
            if "hostname" in optional:
                modal = ConsultationModal(category_id)
            else:
                modal = OtherModal()
                modal.category_id = category_id
        else:
            modal = OtherModal()
            modal.category_id = category_id

        await interaction.response.send_modal(modal)


class TicketCategoryView(discord.ui.View):
    """View for selecting ticket category."""

    def __init__(self) -> None:
        super().__init__(timeout=300)  # 5 minute timeout
        self.add_item(CategorySelect())


class LinkButton(discord.ui.Button["TeamHelpView"]):
    """Button for linking account."""

    def __init__(self) -> None:
        super().__init__(
            style=discord.ButtonStyle.primary,
            label="🔗 Link Account",
            custom_id="help_panel:link",
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        """Handle link account button click."""
        if not self.view:
            raise RuntimeError("Button callback invoked without view")
        await self.view.link_account(interaction)


class TicketButton(discord.ui.Button["TeamHelpView"]):
    """Button for creating ticket."""

    def __init__(self) -> None:
        super().__init__(
            style=discord.ButtonStyle.success,
            label="🎫 Create Ticket",
            custom_id="help_panel:ticket",
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        """Handle create ticket button click."""
        if not self.view:
            raise RuntimeError("Button callback invoked without view")
        await self.view.create_ticket(interaction)


class TeamHelpView(discord.ui.View):
    """Persistent view with help buttons for blue teams."""

    def __init__(self, bot: commands.Bot, show_link: bool = True, show_ticket: bool = True):
        super().__init__(timeout=None)
        self.bot = bot

        # Add link button if requested
        if show_link:
            self.add_item(LinkButton())

        # Add ticket button if requested
        if show_ticket:
            self.add_item(TicketButton())

    async def link_account(self, interaction: discord.Interaction) -> None:
        """Handle link account button click."""
        # Import here to avoid circular dependency
        from bot.cogs.linking import LinkingCog

        cog = self.bot.get_cog("LinkingCog")
        if not isinstance(cog, LinkingCog):
            await interaction.response.send_message("Linking system is not available.", ephemeral=True)
            return

        # Call the link command logic
        callback = cast(
            Callable[[LinkingCog, discord.Interaction], Awaitable[None]],
            cog.link_command.callback,
        )
        await callback(cog, interaction)

    async def create_ticket(self, interaction: discord.Interaction) -> None:
        """Handle create ticket button click - show category selection."""
        await interaction.response.send_message(
            "Select a ticket category:",
            view=TicketCategoryView(),
            ephemeral=True,
        )


class HelpPanelsCog(commands.Cog):
    """Manages persistent help panels for blue teams."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    async def cog_load(self) -> None:
        """Called when cog is loaded - set up persistent views."""
        # Register persistent views
        self.bot.add_view(TeamHelpView(self.bot, show_link=True, show_ticket=False))
        self.bot.add_view(TeamHelpView(self.bot, show_link=False, show_ticket=True))
        self.bot.add_view(TeamHelpView(self.bot, show_link=True, show_ticket=True))

        logger.info("Help panel views registered")

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        """Post help panels to configured channels on bot startup."""
        try:
            # Post link panel to link channel (hidden after linking)
            link_channel_id = getattr(settings, "DISCORD_LINK_CHANNEL_ID", None)
            if link_channel_id:
                await self._post_link_panel(link_channel_id)

            # Post ticket panel to welcome-rules (always visible)
            welcome_channel_id = getattr(settings, "DISCORD_WELCOME_CHANNEL_ID", None)
            if welcome_channel_id:
                await self._post_ticket_panel(welcome_channel_id)

        except Exception as e:
            logger.error(f"Failed to post help panels: {e}", exc_info=True)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        """Delete messages posted to #link channel and trigger link flow."""
        # Ignore bot's own messages
        if message.author.bot:
            return

        # Check if message is in link channel
        link_channel_id = getattr(settings, "DISCORD_LINK_CHANNEL_ID", None)
        if link_channel_id and message.channel.id == link_channel_id:
            try:
                await message.delete()
                logger.info(f"Deleted message from {message.author} in #link channel")
            except discord.HTTPException as e:
                logger.exception(f"Failed to delete message in #link: {e}")
                return

            # Trigger link flow via DM
            from bot.cogs.linking import LinkingCog

            cog = self.bot.get_cog("LinkingCog")
            if isinstance(cog, LinkingCog):
                await cog.send_link_dm(message.author)

    async def _post_link_panel(self, channel_id: int) -> None:
        """Post the link account panel to a channel."""
        channel = self.bot.get_channel(channel_id)
        if not channel or not isinstance(channel, discord.TextChannel):
            logger.warning(f"Link channel {channel_id} not found or not a text channel")
            return

        embed = discord.Embed(
            title="🔗 Link Your Discord Account",
            description=(
                "**Welcome to WCComps!**\n\n"
                "Before you can participate, you need to link your Discord account to your Authentik account.\n\n"
                "**Click the button below to get started:**"
            ),
            color=discord.Color.blue(),
        )
        embed.add_field(
            name="What happens when I link?",
            value=(
                "• You'll be redirected to Authentik to authenticate\n"
                "• Your Discord account will be linked to your team\n"
                "• You'll automatically get your team role\n"
                "• You'll gain access to your team channels"
            ),
            inline=False,
        )

        view = TeamHelpView(self.bot, show_link=True, show_ticket=False)

        # Check if we already posted a panel (look for bot's recent messages)
        async for message in channel.history(limit=10):
            if message.author == self.bot.user and message.embeds and message.embeds[0].title == embed.title:
                # Update existing message
                await message.edit(embed=embed, view=view)
                logger.info(f"Updated link panel in channel {channel_id}")
                return

        # Post new message
        await channel.send(embed=embed, view=view)
        logger.info(f"Posted link panel to channel {channel_id}")

    async def _post_ticket_panel(self, channel_id: int) -> None:
        """Post the ticket creation panel to a channel."""
        channel = self.bot.get_channel(channel_id)
        if not channel or not isinstance(channel, discord.TextChannel):
            logger.warning(f"Ticket channel {channel_id} not found or not a text channel")
            return

        # Build category list
        categories_text = []
        for cat_info in get_all_categories(user_creatable_only=True).values():
            points = cat_info.get("points", 0)
            categories_text.append(f"• **{cat_info['display_name']}** - {points}pt")

        embed = discord.Embed(
            title="🎫 Need Help?",
            description=(
                "**Create a support ticket and our volunteers will assist you!**\n\n"
                "Click the button below to create a ticket. A volunteer will respond shortly.\n\n"
                "**Available Categories:**\n" + "\n".join(categories_text)
            ),
            color=discord.Color.green(),
        )

        view = TeamHelpView(self.bot, show_link=False, show_ticket=True)

        # Check if we already posted a panel
        async for message in channel.history(limit=10):
            if message.author == self.bot.user and message.embeds and message.embeds[0].title == embed.title:
                # Update existing message
                await message.edit(embed=embed, view=view)
                logger.info(f"Updated ticket panel in channel {channel_id}")
                return

        # Post new message
        await channel.send(embed=embed, view=view)
        logger.info(f"Posted ticket panel to channel {channel_id}")

    async def post_team_ticket_panel(self, team_channel_id: int) -> None:
        """Post ticket panel to a team's channel (called when team is created)."""
        await self._post_ticket_panel(team_channel_id)


async def setup(bot: commands.Bot) -> None:
    """Setup function to add cog to bot."""
    await bot.add_cog(HelpPanelsCog(bot))
