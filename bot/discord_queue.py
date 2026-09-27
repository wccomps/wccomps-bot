"""Discord task queue processor for rate limit resilience."""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import timedelta

import discord
from asgiref.sync import sync_to_async
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from bot.discord_manager import DiscordManager
from bot.utils import DISCORD_EMBED_FIELD_CHAR_LIMIT, TEAM_CHAT_CHANNEL_KEYWORD, recycle_db_connection
from core.models import DiscordTask
from core.utils import role_sync_summary
from team.models import Team
from ticketing.models import Ticket, TicketComment

logger = logging.getLogger(__name__)


class DiscordQueueProcessor:
    """Process Discord tasks from database queue using async tasks."""

    QUEUE_POLL_INTERVAL_SECONDS = 2
    QUEUE_BATCH_SIZE = 10
    MAX_BACKOFF_SECONDS = 300

    # Handler registry: maps task_type -> handler method.
    # Adding a new task type? Also update DiscordTask in core/models.py
    # (TASK_TYPE_CHOICES, required_keys, docstring, factory classmethod).
    _task_handlers: dict[str, Callable[[DiscordQueueProcessor, DiscordTask], Awaitable[None]]] = {}

    def __init__(self, bot: discord.Client) -> None:
        self.bot = bot
        self.discord_manager: DiscordManager | None = None
        self.running = False
        self.task: asyncio.Task[None] | None = None

    def start(self) -> None:
        """Start the queue processor as an async task."""
        from bot.config import DISCORD_GUILD_ID

        self.running = True

        # Initialize discord manager with the configured guild
        guild_id = DISCORD_GUILD_ID
        if guild_id:
            guild = self.bot.get_guild(guild_id)
            if guild:
                self.discord_manager = DiscordManager(guild, self.bot)
            else:
                logger.error(f"Could not find configured guild {guild_id}")
        elif self.bot.guilds:
            # Fallback to first guild if DISCORD_GUILD_ID not set
            guild = self.bot.guilds[0]
            self.discord_manager = DiscordManager(guild, self.bot)

        # Start processing task
        self.task = asyncio.create_task(self._process_loop())
        logger.info("Discord queue processor started")

    def stop(self) -> None:
        """Stop the queue processor."""
        self.running = False
        if self.task:
            self.task.cancel()
        logger.info("Discord queue processor stopped")

    async def _process_loop(self) -> None:
        """Main processing loop (runs as async task)."""
        while self.running:
            try:
                await recycle_db_connection()
                await self._process_pending_tasks()
            except Exception as e:
                logger.exception(f"Error in queue processor: {e}")

            await asyncio.sleep(self.QUEUE_POLL_INTERVAL_SECONDS)

    async def _process_pending_tasks(self) -> None:
        """Process pending tasks from the queue."""

        # Get pending tasks that are ready to process (using sync_to_async)
        @sync_to_async
        def get_pending_tasks() -> list[DiscordTask]:
            now = timezone.now()
            return list(
                DiscordTask.objects.filter(status="pending")
                .filter(Q(next_retry_at__isnull=True) | Q(next_retry_at__lte=now))
                .order_by("created_at")[: DiscordQueueProcessor.QUEUE_BATCH_SIZE]
            )

        tasks = await get_pending_tasks()

        for task in tasks:
            await self._process_task(task)

    async def _process_task(self, task: DiscordTask) -> None:
        """Process a single task."""

        # Mark as processing
        @sync_to_async
        def mark_processing() -> None:
            with transaction.atomic():
                task.status = "processing"
                task.save()

        await mark_processing()

        try:
            # Dispatch to handler by task type
            handler = self._task_handlers.get(task.task_type)
            if handler is None:
                logger.warning(f"Unknown task type: {task.task_type}")
                await sync_to_async(lambda: setattr(task, "status", "failed"))()
                await sync_to_async(lambda: setattr(task, "error_message", f"Unknown task type: {task.task_type}"))()
                await sync_to_async(task.save)()
                return
            await handler(self, task)

            # Mark as completed
            @sync_to_async
            def mark_completed() -> None:
                task.status = "completed"
                task.completed_at = timezone.now()
                task.save()

            await mark_completed()
            logger.info(f"Completed task {task.id}: {task.task_type}")

        except discord.errors.RateLimited as rate_limit_error:
            # Handle rate limiting
            @sync_to_async
            def handle_rate_limit(error: discord.errors.RateLimited) -> float:
                retry_after = error.retry_after
                task.retry_count += 1
                task.next_retry_at = timezone.now() + timedelta(seconds=retry_after)
                task.status = "pending"
                task.error_message = f"Rate limited, retry after {retry_after}s"
                task.save()
                return retry_after

            retry_after = await handle_rate_limit(rate_limit_error)
            logger.warning(f"Task {task.id} rate limited, retrying in {retry_after}s")

        except Exception as error:
            # Handle other errors
            @sync_to_async
            def handle_error(exc: Exception) -> tuple[str, int]:
                task.retry_count += 1
                task.error_message = str(exc)

                if task.retry_count >= task.max_retries:
                    task.status = "failed"
                    task.save()
                    return "failed", task.max_retries
                # Exponential backoff
                backoff_seconds = min(2**task.retry_count, DiscordQueueProcessor.MAX_BACKOFF_SECONDS)
                task.next_retry_at = timezone.now() + timedelta(seconds=backoff_seconds)
                task.status = "pending"
                task.save()
                return "retry", backoff_seconds

            result, value = await handle_error(error)

            if result == "failed":
                logger.exception(f"Task {task.id} failed after {value} retries: {error}")
                # Alert to ops channel
                try:
                    from bot.utils import log_to_ops_channel

                    await log_to_ops_channel(
                        self.bot,
                        f"Task {task.id} ({task.task_type}) failed after {value} retries: {error}",
                    )
                except Exception as log_error:
                    logger.exception(f"Failed to log error to ops channel: {log_error}")
            else:
                logger.warning(f"Task {task.id} failed (attempt {task.retry_count}), retrying in {value}s")

    async def _handle_assign_role(self, task: DiscordTask) -> None:
        """Handle assign_role task."""
        if not self.discord_manager:
            raise RuntimeError("Discord manager not initialized")

        discord_id = task.payload.get("discord_id")
        team_number = task.payload.get("team_number")

        if not discord_id or not team_number:
            raise ValueError("Missing discord_id or team_number in payload")

        guild = self.discord_manager.guild
        member = guild.get_member(discord_id)

        # If not in cache, try to fetch from API
        if not member:
            try:
                member = await guild.fetch_member(discord_id)
            except discord.NotFound:
                # Member is not in the guild
                try:
                    user = await self.bot.fetch_user(discord_id)
                    username = f"{user.name} ({discord_id})"
                except Exception:
                    username = str(discord_id)

                logger.warning(
                    f"Member {username} not found in guild, skipping role assignment. "
                    f"Role will be assigned when they join the server."
                )
                return
            except Exception as e:
                logger.exception(f"Failed to fetch member {discord_id}: {e}")
                raise

        # Set up team infrastructure if needed
        @sync_to_async
        def get_team() -> Team:
            return Team.objects.get(team_number=team_number)

        team = await get_team()
        if not team.discord_role_id or not team.discord_category_id:
            logger.info(f"Setting up infrastructure for team {team_number}")
            await self.discord_manager.setup_team_infrastructure(team_number)

        # Assign role
        success = await self.discord_manager.assign_team_role(member, team_number)
        if not success:
            raise RuntimeError(f"Failed to assign role to {member}")

        logger.info(f"Assigned team {team_number} role to {member}")

    async def _handle_assign_group_roles(self, task: DiscordTask) -> None:
        """Handle assign_group_roles task."""
        if not self.discord_manager:
            raise RuntimeError("Discord manager not initialized")

        discord_id = task.payload.get("discord_id")
        authentik_groups = task.payload.get("authentik_groups", [])

        if not discord_id:
            raise ValueError("Missing discord_id in payload")

        guild = self.discord_manager.guild
        member = guild.get_member(discord_id)

        # If not in cache, try to fetch from API
        if not member:
            try:
                member = await guild.fetch_member(discord_id)
            except discord.NotFound:
                logger.warning(f"Member {discord_id} not found in guild, skipping group role assignment")
                return
            except Exception as e:
                logger.exception(f"Failed to fetch member {discord_id}: {e}")
                raise

        # Assign group-based roles
        success = await self.discord_manager.assign_group_roles(member, authentik_groups)
        if not success:
            raise RuntimeError(f"Failed to assign group roles to {member}")

        logger.info(f"Assigned group roles to {member}")

    async def _handle_remove_role(self, task: DiscordTask) -> None:
        """Handle remove_role task."""
        if not self.discord_manager:
            raise RuntimeError("Discord manager not initialized")

        discord_id = task.payload.get("discord_id")
        team_number = task.payload.get("team_number")

        if not discord_id or not team_number:
            raise ValueError("Missing discord_id or team_number in payload")

        guild = self.discord_manager.guild
        member = guild.get_member(discord_id)

        # If not in cache, try to fetch from API
        if not member:
            try:
                member = await guild.fetch_member(discord_id)
            except discord.NotFound:
                logger.warning(f"Member {discord_id} not in guild, skipping role removal")
                return
            except Exception as e:
                logger.exception(f"Failed to fetch member {discord_id}: {e}")
                raise

        success = await self.discord_manager.remove_team_role(member, team_number)
        if not success:
            raise RuntimeError(f"Failed to remove role from {member}")

        logger.info(f"Removed team {team_number} role from {member}")

    async def _handle_setup_team_infrastructure(self, task: DiscordTask) -> None:
        """Handle setup_team_infrastructure task."""
        if not self.discord_manager:
            raise RuntimeError("Discord manager not initialized")

        team_number = task.payload.get("team_number")
        if not team_number:
            raise ValueError("Missing team_number in payload")

        role, category = await self.discord_manager.setup_team_infrastructure(team_number)
        if not role or not category:
            raise RuntimeError(f"Failed to setup infrastructure for team {team_number}")

        logger.info(f"Set up infrastructure for team {team_number}")

    async def _handle_log_to_channel(self, task: DiscordTask) -> None:
        """Handle log_to_channel task."""
        message = task.payload.get("message")
        if not message:
            raise ValueError("Missing message in payload")

        from bot.utils import log_to_ops_channel

        await log_to_ops_channel(self.bot, message)

    async def _handle_ticket_created_web(self, task: DiscordTask) -> None:
        """Handle ticket creation from web UI - create thread and post to dashboard."""
        import time

        from bot.ticket_dashboard import post_ticket_to_dashboard

        start_time = time.time()
        ticket_id = task.payload.get("ticket_id")
        if not ticket_id:
            raise ValueError("Missing ticket_id in payload")

        @sync_to_async
        def get_ticket() -> Ticket:
            return Ticket.objects.select_related("team").get(id=ticket_id)

        ticket = await get_ticket()
        logger.info(f"Ticket {ticket.ticket_number}: DB fetch took {time.time() - start_time:.3f}s")

        # Check if thread already exists (from previous retry)
        if ticket.discord_thread_id:
            logger.info(f"Thread already exists for ticket {ticket.ticket_number}, updating dashboard")
            # Still trigger dashboard update
            await post_ticket_to_dashboard(self.bot, ticket)
            return

        # Try to create thread in team's category
        if ticket.team.discord_category_id:
            if not self.discord_manager:
                logger.warning("Discord manager not initialized; cannot create ticket thread")
            else:
                from bot.thread_creator import create_ticket_thread

                thread_start = time.time()
                thread = await create_ticket_thread(
                    bot=self.bot,
                    guild=self.discord_manager.guild,
                    ticket=ticket,
                    team=ticket.team,
                    pin_message=True,
                )
                if thread:
                    logger.info(
                        f"Ticket {ticket.ticket_number}: Thread creation took {time.time() - thread_start:.3f}s "
                        f"(total: {time.time() - start_time:.3f}s)"
                    )
        else:
            logger.warning(
                f"Team {ticket.team.team_name} has no category, ticket will appear in dashboard without thread"
            )

        # Always update dashboard, even if thread creation failed
        dashboard_start = time.time()
        await post_ticket_to_dashboard(self.bot, ticket)
        logger.info(f"Ticket {ticket.ticket_number}: Dashboard update took {time.time() - dashboard_start:.3f}s")

    async def _handle_post_comment(self, task: DiscordTask) -> None:
        """Handle posting a comment from web to Discord thread."""
        ticket_id = task.payload.get("ticket_id")
        comment_id = task.payload.get("comment_id")

        if not ticket_id or not comment_id:
            raise ValueError("Missing ticket_id or comment_id in payload")

        @sync_to_async
        def get_data() -> tuple[Ticket, TicketComment]:
            ticket = Ticket.objects.get(id=ticket_id)
            comment = TicketComment.objects.select_related("author").get(id=comment_id)
            return ticket, comment

        ticket, comment = await get_data()

        if not ticket.discord_thread_id:
            raise ValueError(f"Ticket #{ticket.id} has no Discord thread")

        # Get thread
        thread = self.bot.get_channel(ticket.discord_thread_id)
        if not thread:
            try:
                thread = await self.bot.fetch_channel(ticket.discord_thread_id)
            except Exception as e:
                raise ValueError(f"Could not find thread {ticket.discord_thread_id}: {e}") from e

        # Type guard for sendable channels
        if not isinstance(thread, (discord.TextChannel, discord.Thread)):
            raise TypeError(f"Channel {ticket.discord_thread_id} is not a text channel or thread")

        # Format message (comment.author is now a User, not DiscordLink)
        author_display = "Unknown"
        if comment.author:
            author_display = comment.author.username or "Unknown"
        message_content = f"**{author_display}**\n{comment.comment_text}"

        # Post to thread
        message = await thread.send(message_content)

        # Store Discord message ID
        @sync_to_async
        def save_message_id() -> None:
            comment.discord_message_id = message.id
            comment.save()

        await save_message_id()

        logger.info(f"Posted comment {comment_id} to thread {thread.id} (message {message.id})")

    async def _handle_post_ticket_update(self, task: DiscordTask) -> None:
        """Post a ticket status update (resolve/claim/unclaim/reopen) to the Discord thread."""
        action = task.payload.get("action", "")
        actor = task.payload.get("actor", "Unknown")

        @sync_to_async
        def get_ticket() -> Ticket:
            if task.ticket_id:
                return Ticket.objects.get(id=task.ticket_id)
            raise ValueError("No ticket linked to task")

        ticket = await get_ticket()

        if not ticket.discord_thread_id:
            raise ValueError(f"Ticket {ticket.ticket_number} has no Discord thread")

        thread = self.bot.get_channel(ticket.discord_thread_id)
        if not thread:
            try:
                thread = await self.bot.fetch_channel(ticket.discord_thread_id)
            except Exception as e:
                raise ValueError(f"Could not find thread {ticket.discord_thread_id}: {e}") from e

        if not isinstance(thread, (discord.TextChannel, discord.Thread)):
            raise TypeError(f"Channel {ticket.discord_thread_id} is not a text channel or thread")

        if action == "resolved":
            notes = task.payload.get("resolution_notes", "")
            points = task.payload.get("points_charged", 0)
            embed = discord.Embed(
                title="Ticket Resolved",
                color=discord.Color.green(),
            )
            embed.add_field(name="Resolved By", value=actor, inline=True)
            embed.add_field(name="Points Charged", value=str(points), inline=True)
            if notes:
                embed.add_field(name="Resolution Notes", value=notes[:DISCORD_EMBED_FIELD_CHAR_LIMIT], inline=False)
            await thread.send(embed=embed)
        elif action == "claimed":
            await thread.send(f"Ticket claimed by **{actor}**")
        elif action == "unclaimed":
            await thread.send(f"Ticket unclaimed by **{actor}**")
        elif action == "reopened":
            reason = task.payload.get("reason", "")
            msg = f"Ticket reopened by **{actor}**"
            if reason:
                msg += f"\nReason: {reason}"
            await thread.send(msg)
        else:
            await thread.send(f"Ticket updated: {action} by **{actor}**")

        logger.info(f"Posted ticket update ({action}) to thread {ticket.discord_thread_id}")

    async def _handle_add_user_to_thread(self, task: DiscordTask) -> None:
        """Add a user to a Discord thread."""
        discord_id = task.payload.get("discord_id")
        thread_id = task.payload.get("thread_id")

        if not discord_id or not thread_id:
            raise ValueError("Missing discord_id or thread_id in payload")

        # Get thread
        thread = self.bot.get_channel(thread_id)
        if not thread:
            try:
                thread = await self.bot.fetch_channel(thread_id)
            except Exception as e:
                raise ValueError(f"Could not find thread {thread_id}: {e}") from e

        # Type guard for threads
        if not isinstance(thread, discord.Thread):
            raise TypeError(f"Channel {thread_id} is not a thread")

        # Get user
        user = self.bot.get_user(discord_id)
        if not user:
            try:
                user = await self.bot.fetch_user(discord_id)
            except Exception as e:
                raise ValueError(f"Could not find user {discord_id}: {e}") from e

        # Add user to thread
        await thread.add_user(user)

        logger.info(f"Added user {discord_id} to thread {thread_id}")

    async def _handle_sync_roles(self, task: DiscordTask) -> None:
        """Handle role synchronization from Authentik to Discord.

        Uses AuthentikRoleSyncManager by default (syncs based on Authentik groups).
        Supports dry_run mode to preview changes without applying them.
        """
        import asyncio

        from bot.role_sync import AuthentikRoleSyncManager
        from bot.utils import log_to_ops_channel

        dry_run = task.payload.get("dry_run", False)
        sync_manager = AuthentikRoleSyncManager(self.bot)

        # Callback to save progress to database
        async def save_progress(current: int, total: int, role_name: str) -> None:
            @sync_to_async
            def update_payload() -> None:
                task.payload["progress"] = {
                    "current": current,
                    "total": total,
                    "current_role": role_name,
                }
                task.save()

            await update_payload()

        try:
            stats = await asyncio.wait_for(
                sync_manager.sync_roles(dry_run=dry_run, progress_callback=save_progress),
                timeout=300.0,
            )
        except TimeoutError:
            logger.error("Role sync timed out after 5 minutes")
            raise RuntimeError("Role sync timed out after 5 minutes") from None

        # Store results in task payload for retrieval
        @sync_to_async
        def store_results() -> None:
            task.payload["result"] = {
                "roles_added": stats["roles_added"],
                "roles_removed": stats["roles_removed"],
                "extra_linked": stats.get("extra_linked", 0),
                "unlinked_holders": stats.get("unlinked_holders", 0),
                "errors": stats["errors"],
                "changes_count": len(stats["changes"]),
                "changes": stats["changes"],
                "dry_run": dry_run,
            }
            # Clear progress now that we're done
            task.payload.pop("progress", None)
            task.save()

        await store_results()

        # Log results to ops channel
        summary = role_sync_summary(stats, dry_run=dry_run)
        await log_to_ops_channel(self.bot, summary)

        logger.info(f"Role sync completed: {summary}")

    async def _handle_assign_role_by_username(self, task: DiscordTask) -> None:
        """Assign a role to users by their Discord username."""
        from bot.config import DISCORD_GUILD_ID

        guild_id = task.payload.get("guild_id") or DISCORD_GUILD_ID
        role_id = task.payload.get("role_id")
        usernames = task.payload.get("usernames", [])

        if not role_id or not usernames:
            raise ValueError("Missing role_id or usernames in payload")

        guild = self.bot.get_guild(guild_id)
        if not guild:
            raise RuntimeError(f"Guild {guild_id} not found")

        # Ensure members are cached with timeout
        if not guild.chunked:
            try:
                await asyncio.wait_for(guild.chunk(), timeout=30.0)
            except TimeoutError:
                logger.warning(f"Guild chunk timed out, using {len(guild.members)} cached members")

        role = guild.get_role(role_id)
        if not role:
            raise RuntimeError(f"Role {role_id} not found in guild {guild.name}")

        results = []
        for username in usernames:
            member = discord.utils.get(guild.members, name=username)
            if not member:
                member = discord.utils.get(guild.members, display_name=username)

            if member:
                if role in member.roles:
                    results.append(f"{username}: already has role")
                else:
                    await member.add_roles(role, reason="Assigned via admin task")
                    results.append(f"{username}: role added")
                    logger.info(f"Added role {role.name} to {member} in {guild.name}")
            else:
                results.append(f"{username}: not found in guild")
                logger.warning(f"User {username} not found in guild {guild.name}")

        @sync_to_async
        def store_results() -> None:
            task.payload["result"] = {"results": results}
            task.save()

        await store_results()
        logger.info(f"Role assignment complete: {results}")

    async def _handle_broadcast_message(self, task: DiscordTask) -> None:
        """Broadcast a message to announcement channel or team channels."""
        from bot.config import BLUETEAM_ROLE_ID, DISCORD_ANNOUNCEMENT_CHANNEL_ID, DISCORD_GUILD_ID
        from bot.utils import log_to_ops_channel
        from core.authentik_utils import parse_team_range

        target = task.payload.get("target", "")
        message = task.payload.get("message", "")
        sender = task.payload.get("sender", "Web Admin")

        if not target or not message:
            raise ValueError("Missing target or message in payload")

        guild = self.bot.get_guild(DISCORD_GUILD_ID)
        if not guild:
            raise RuntimeError("Guild not found")

        target_lower = target.lower().strip()
        sent_count = 0
        queued_count = 0
        failed_channels: list[str] = []

        if target_lower == "announcements":
            # Broadcast to announcements channel with @Blueteam mention
            channel = guild.get_channel(DISCORD_ANNOUNCEMENT_CHANNEL_ID)
            if not channel or not isinstance(channel, discord.TextChannel):
                raise RuntimeError("Announcements channel not found")

            blueteam_role = guild.get_role(BLUETEAM_ROLE_ID)
            role_mention = blueteam_role.mention if blueteam_role else "@Blueteam"

            await channel.send(f"{role_mention}\n\n{message}")
            sent_count = 1
            logger.info(f"Broadcast to announcements by {sender}")

        elif target_lower == "all-teams":
            # Broadcast to all team chat channels
            teams = [t async for t in Team.objects.filter(is_active=True).order_by("team_number")]
            for team in teams:
                result = await self._send_to_team_channel(guild, team, message, sender)
                if result == "sent":
                    sent_count += 1
                elif result == "queued":
                    queued_count += 1
                else:
                    failed_channels.append(f"Team {team.team_number:02d}")

        else:
            # Parse specific team range
            try:
                team_numbers = parse_team_range(target)
            except ValueError as e:
                raise ValueError(f"Invalid team range: {e}") from e

            for team_number in team_numbers:
                found_team = await Team.objects.filter(team_number=team_number).afirst()
                if not found_team:
                    failed_channels.append(f"Team {team_number:02d} (not found)")
                    continue

                result = await self._send_to_team_channel(guild, found_team, message, sender)
                if result == "sent":
                    sent_count += 1
                elif result == "queued":
                    queued_count += 1
                else:
                    failed_channels.append(f"Team {team_number:02d}")

        # Store results
        @sync_to_async
        def store_results() -> None:
            task.payload["result"] = {
                "sent_count": sent_count,
                "queued_count": queued_count,
                "failed_count": len(failed_channels),
            }
            task.save()

        await store_results()

        # Log to ops
        await log_to_ops_channel(
            self.bot,
            f"Broadcast by {sender}\n• Target: {target}\n• Sent: {sent_count}\n• Queued: {queued_count}",
        )

        logger.info(f"Broadcast complete: sent={sent_count}, queued={queued_count}, failed={len(failed_channels)}")

    async def _send_to_team_channel(self, guild: discord.Guild, team: Team, message: str, sender: str) -> str:
        """Send message to a team's chat channel. Returns 'sent', 'queued', or 'failed'."""
        from core.models import QueuedAnnouncement

        try:
            chat_channel = None
            if team.discord_category_id:
                category = guild.get_channel(team.discord_category_id)
                if category and isinstance(category, discord.CategoryChannel):
                    for channel in category.channels:
                        if (
                            isinstance(channel, discord.TextChannel)
                            and TEAM_CHAT_CHANNEL_KEYWORD in channel.name.lower()
                        ):
                            chat_channel = channel
                            break

            if chat_channel:
                await chat_channel.send(f"**Announcement from {sender}:**\n\n{message}")
                return "sent"
            else:
                # Queue for later delivery
                await QueuedAnnouncement.objects.acreate(
                    team=team,
                    message=message,
                    sender_name=sender,
                )
                return "queued"
        except Exception as e:
            logger.exception(f"Failed to send to team {team.team_number}: {e}")
            return "failed"


# Populate handler registry — kept at module level so it's set once after class definition.
DiscordQueueProcessor._task_handlers = {
    "assign_role": DiscordQueueProcessor._handle_assign_role,
    "assign_group_roles": DiscordQueueProcessor._handle_assign_group_roles,
    "remove_role": DiscordQueueProcessor._handle_remove_role,
    "setup_team_infrastructure": DiscordQueueProcessor._handle_setup_team_infrastructure,
    "log_to_channel": DiscordQueueProcessor._handle_log_to_channel,
    "ticket_created_web": DiscordQueueProcessor._handle_ticket_created_web,
    "post_comment": DiscordQueueProcessor._handle_post_comment,
    "post_ticket_update": DiscordQueueProcessor._handle_post_ticket_update,
    "add_user_to_thread": DiscordQueueProcessor._handle_add_user_to_thread,
    "sync_roles": DiscordQueueProcessor._handle_sync_roles,
    "assign_role_by_username": DiscordQueueProcessor._handle_assign_role_by_username,
    "broadcast_message": DiscordQueueProcessor._handle_broadcast_message,
}
