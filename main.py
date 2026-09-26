"""WCComps Discord Bot - Main entry point."""

import hashlib
import logging
import os
import sys

import discord

# Initialize Django before any imports that use Django models
import django
from discord.ext import commands

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "portal.settings")
django.setup()

from bot.competition_timer import CompetitionTimer
from bot.discord_queue import DiscordQueueProcessor
from bot.unified_dashboard import UnifiedDashboard

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger(__name__)


class PortalBot(commands.Bot):
    """WCComps Discord Bot."""

    def __init__(self) -> None:
        intents = discord.Intents.default()
        intents.message_content = True
        intents.members = True
        intents.guilds = True

        super().__init__(command_prefix="!", intents=intents)

        self.queue_processor: DiscordQueueProcessor | None = None
        self.competition_timer: CompetitionTimer | None = None
        self.unified_dashboard: UnifiedDashboard | None = None

    def _get_command_hash(self) -> str:
        """Hash cog source files to detect command changes."""
        from pathlib import Path

        cogs_dir = Path(__file__).parent / "bot" / "cogs"
        hasher = hashlib.sha256()
        for cog_file in sorted(cogs_dir.glob("*.py")):
            hasher.update(cog_file.read_bytes())
        return hasher.hexdigest()[:16]

    async def _should_sync_commands(self) -> bool:
        """Check if commands have changed and need syncing."""
        from asgiref.sync import sync_to_async

        from core.models import BotState

        current_hash = self._get_command_hash()

        # Force sync if explicitly requested
        if os.environ.get("SYNC_COMMANDS", "").lower() in ("true", "1", "yes"):
            logger.info(f"SYNC_COMMANDS=true, forcing sync (hash: {current_hash})")
            await sync_to_async(BotState.objects.update_or_create)(key="command_hash", defaults={"value": current_hash})
            return True

        # Check stored hash
        try:
            stored = await sync_to_async(BotState.objects.get)(key="command_hash")
            if stored.value == current_hash:
                logger.info(f"Commands unchanged (hash: {current_hash}), skipping sync")
                return False
            logger.info(f"Commands changed ({stored.value} -> {current_hash}), will sync")
        except BotState.DoesNotExist:
            logger.info(f"No stored command hash, will sync (hash: {current_hash})")

        await sync_to_async(BotState.objects.update_or_create)(key="command_hash", defaults={"value": current_hash})
        return True

    async def setup_hook(self) -> None:
        """Setup hook called when bot is ready."""
        logger.info("Loading cogs...")

        # Load cogs
        await self.load_extension("bot.cogs.linking")
        await self.load_extension("bot.cogs.ticketing")
        await self.load_extension("bot.cogs.scoring")
        await self.load_extension("bot.cogs.help_panels")
        await self.load_extension("bot.cogs.admin")
        await self.load_extension("bot.cogs.admin_teams")
        await self.load_extension("bot.cogs.admin_tickets")
        await self.load_extension("bot.cogs.admin_competition")
        await self.load_extension("bot.cogs.quotient_sync")

        logger.info("Cogs loaded")

        # Register persistent views for ticket buttons
        from bot.ticket_dashboard import TicketActionView

        self.add_view(TicketActionView(ticket_id=0))
        logger.info("Registered persistent ticket action view")

        # Log registered commands for debugging
        commands_list = self.tree.get_commands()
        logger.info(f"Registered {len(commands_list)} top-level commands:")
        for cmd in commands_list:
            if isinstance(cmd, discord.app_commands.Group):
                logger.info(f"  - {cmd.name} (Group with {len(cmd.commands)} subcommands)")
            else:
                logger.info(f"  - {cmd.name} (Command)")

        # Sync commands to guilds
        # Competition guild gets all commands
        from bot.config import DISCORD_GUILD_ID, VOLUNTEER_GUILD_ID

        competition_guild_id = DISCORD_GUILD_ID
        # Volunteer guild gets only the /link command so staff can link their accounts there
        volunteer_guild_id = VOLUNTEER_GUILD_ID

        # Only sync if commands have changed (checked against database)
        if not await self._should_sync_commands():
            return

        # Sync to competition guild (instant availability)
        if competition_guild_id:
            guild = discord.Object(id=competition_guild_id)
            # Copy commands from global tree to guild tree before syncing
            self.tree.copy_global_to(guild=guild)
            try:
                await self.tree.sync(guild=guild)
                logger.info(f"Command tree synced to competition guild ({competition_guild_id})")
            except discord.HTTPException as e:
                logger.warning(f"Guild sync failed: {e}")

        # Clear global commands to avoid duplicates with guild commands
        self.tree.clear_commands(guild=None)
        try:
            await self.tree.sync()
            logger.info("Cleared global commands")
        except discord.HTTPException as e:
            logger.warning(f"Failed to clear global commands: {e}")

        if volunteer_guild_id and volunteer_guild_id != competition_guild_id:
            volunteer_guild = discord.Object(id=volunteer_guild_id)
            self.tree.clear_commands(guild=volunteer_guild)
            link_command = self.tree.get_command("link")
            if link_command:
                self.tree.add_command(link_command, guild=volunteer_guild)
                try:
                    await self.tree.sync(guild=volunteer_guild)
                    logger.info(f"Synced /link command to volunteer guild ({volunteer_guild_id})")
                except discord.HTTPException as e:
                    logger.warning(f"Volunteer guild sync failed: {e}")

    async def on_ready(self) -> None:
        """Called when bot is ready."""
        if not self.user:
            logger.error("Bot user is None in on_ready")
            return
        logger.info(f"Logged in as {self.user} (ID: {self.user.id})")
        logger.info(f"Connected to {len(self.guilds)} guild(s)")

        for guild in self.guilds:
            logger.info(f"  - {guild.name} (ID: {guild.id})")

        # Start queue processor
        if not self.queue_processor:
            self.queue_processor = DiscordQueueProcessor(self)
            self.queue_processor.start()

        # Start competition timer
        if not self.competition_timer:
            self.competition_timer = CompetitionTimer(self)
            self.competition_timer.start()

        # Start unified dashboard
        if not self.unified_dashboard:
            self.unified_dashboard = UnifiedDashboard(self)
            self.unified_dashboard.start()

        # Refresh ticket action buttons on all active tickets
        await self._refresh_ticket_buttons()

    async def _refresh_ticket_buttons(self) -> None:
        """Refresh action buttons on all active ticket thread messages."""
        from bot.ticket_dashboard import TicketActionView, format_ticket_embed
        from ticketing.models import Ticket

        try:
            # Get all active tickets with threads
            tickets = Ticket.objects.filter(status__in=["open", "claimed"], discord_thread_id__isnull=False)

            tickets_list = [t async for t in tickets]
            if not tickets_list:
                logger.info("No active tickets with threads to refresh")
                return

            logger.info(f"Refreshing buttons on {len(tickets_list)} active ticket threads")

            refreshed = 0
            failed = 0

            for ticket in tickets_list:
                try:
                    logger.info(f"Processing ticket {ticket.ticket_number} (thread {ticket.discord_thread_id})")

                    # Skip if no thread ID
                    if not ticket.discord_thread_id:
                        logger.warning(f"Ticket {ticket.ticket_number} has no thread ID")
                        failed += 1
                        continue

                    # Fetch the thread
                    thread = self.get_channel(ticket.discord_thread_id)
                    if not thread:
                        thread = await self.fetch_channel(ticket.discord_thread_id)

                    if not isinstance(thread, discord.Thread):
                        logger.warning(
                            f"Channel {ticket.discord_thread_id} is not a thread for ticket {ticket.ticket_number}"
                        )
                        continue

                    # Find the ticket message (the one with embed and buttons)
                    # Search first few messages for one with an embed from the bot
                    ticket_message = None
                    async for message in thread.history(limit=10, oldest_first=True):
                        if message.author == self.user and message.embeds:
                            ticket_message = message
                            break

                    if ticket_message:
                        # Re-edit with fresh embed and view
                        from asgiref.sync import sync_to_async

                        embed = await sync_to_async(format_ticket_embed)(ticket)
                        view = TicketActionView(ticket.id)
                        await ticket_message.edit(embed=embed, view=view)
                        refreshed += 1
                        logger.info(f"Refreshed ticket {ticket.ticket_number}")
                    else:
                        logger.warning(f"No ticket message with embed found for ticket {ticket.ticket_number}")

                except discord.NotFound:
                    logger.warning(f"Thread {ticket.discord_thread_id} not found for ticket {ticket.ticket_number}")
                    failed += 1
                except Exception as e:
                    logger.warning(f"Failed to refresh buttons for ticket {ticket.ticket_number}: {e}")
                    failed += 1

            logger.info(f"Button refresh complete: {refreshed} refreshed, {failed} failed")

        except Exception as e:
            logger.exception(f"Error refreshing ticket buttons: {e}")

    async def close(self) -> None:
        """Cleanup on bot shutdown."""
        logger.info("Shutting down bot...")
        if self.queue_processor:
            self.queue_processor.stop()
        if self.competition_timer:
            self.competition_timer.stop()
        if self.unified_dashboard:
            self.unified_dashboard.stop()
        await super().close()


def main() -> None:
    """Main entry point."""
    # Get bot token from environment
    token = os.environ.get("DISCORD_BOT_TOKEN")
    if not token:
        logger.error("DISCORD_BOT_TOKEN environment variable not set")
        sys.exit(1)

    # Create and run bot
    bot = PortalBot()

    try:
        bot.run(token)
    except KeyboardInterrupt:
        logger.info("Received keyboard interrupt")
    except Exception as e:
        logger.exception(f"Fatal error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
