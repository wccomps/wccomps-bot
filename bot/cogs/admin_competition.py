"""Admin commands for competition and account management."""

import csv
import io
import logging
import re

import discord
from asgiref.sync import sync_to_async
from discord import app_commands
from discord.ext import commands
from django.conf import settings
from django.utils import timezone

from bot.permissions import check_admin, check_gold_team
from bot.utils import TEAM_CHAT_CHANNEL_KEYWORD, ConfirmView, log_to_ops_channel
from core.authentik_manager import AuthentikManager
from core.authentik_utils import (
    generate_blueteam_password,
    parse_team_range,
)
from core.models import AuditLog, CompetitionConfig, QueuedAnnouncement
from core.utils import parse_datetime_to_utc
from team.models import DiscordLink, Team

logger = logging.getLogger(__name__)


class AdminCompetitionCog(commands.Cog):
    """Admin commands for competition and account management."""

    # Create competition command group as class attribute
    competition_group = app_commands.Group(
        name="competition",
        description="Competition management commands",
    )

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @competition_group.command(
        name="reset-blueteam-passwords",
        description="Reset passwords for blueteam accounts (optionally specify teams)",
    )
    @app_commands.check(check_gold_team)
    async def admin_reset_blueteam_passwords(
        self, interaction: discord.Interaction, team_numbers: str | None = None
    ) -> None:
        """Reset passwords for blueteam accounts and export CSV.

        Args:
            team_numbers: Optional comma-separated team numbers or ranges (e.g., "1,3,5-10")
                         If not provided, resets all 50 teams.
        """

        if not settings.AUTHENTIK_TOKEN:
            await interaction.response.send_message("Error: AUTHENTIK_TOKEN not configured in settings", ephemeral=True)
            return

        # If resetting all 50 teams, require confirmation
        if not team_numbers:
            view = ConfirmView(confirm_label="Confirm Reset All 50 Teams")
            await interaction.response.send_message(
                "⚠️ **WARNING: You are about to reset passwords for ALL 50 blue team accounts.**\n\n"
                "This will:\n"
                "• Generate new random passwords for team01-team50\n"
                "• Invalidate all current passwords\n"
                "• Revoke active sessions\n\n"
                "Are you sure you want to continue?",
                view=view,
                ephemeral=True,
            )

            await view.wait()

            if not view.confirmed:
                return

            await interaction.followup.send("Resetting all 50 team passwords...", ephemeral=True)
        else:
            await interaction.response.defer(ephemeral=True)

        # Parse team numbers if provided
        if team_numbers:
            try:
                teams = parse_team_range(team_numbers)
            except ValueError as e:
                await interaction.followup.send(f"Error: {e}", ephemeral=True)
                return
        else:
            teams = list(range(1, 51))  # All teams

        # Generate passwords for specified teams
        password_list = []
        for i in teams:
            username = f"team{i:02d}"
            password = generate_blueteam_password()
            password_list.append((i, username, password))

        # Reset passwords in Authentik
        auth_manager = AuthentikManager()
        failed_resets = []
        for team_num, username, password in password_list:
            success, error = await sync_to_async(auth_manager.reset_blueteam_password)(team_num, password)
            if not success:
                failed_resets.append((username, error))

        # Generate CSV in memory
        csv_buffer = io.StringIO()
        writer = csv.writer(csv_buffer)
        writer.writerow(["Username", "Password"])

        for _team_num, username, password in password_list:
            writer.writerow([username, password])

        csv_buffer.seek(0)

        # Create Discord file
        file = discord.File(
            fp=io.BytesIO(csv_buffer.getvalue().encode("utf-8")),
            filename="blueteam_passwords.csv",
        )

        team_count = len(password_list)

        # Create audit log
        await AuditLog.objects.acreate(
            action="blueteam_passwords_reset",
            admin_user=str(interaction.user),
            target_entity="authentik_users",
            target_id=0,
            details={
                "total_users": team_count,
                "failed_resets": len(failed_resets),
                "team_numbers": team_numbers or "all",
            },
        )

        # Log to ops
        teams_msg = f"teams {team_numbers}" if team_numbers else "all 50 accounts"
        await log_to_ops_channel(
            self.bot,
            f"BlueTeam Password Reset by {interaction.user.mention}\n"
            f"• Teams: {teams_msg}\n"
            f"• Total: {team_count} accounts\n"
            f"• Failed: {len(failed_resets)}",
        )

        success_count = team_count - len(failed_resets)
        result_msg = f"Password reset complete\n• Success: {success_count}/{team_count}\n"
        if failed_resets:
            result_msg += f"• Failed: {len(failed_resets)}/{team_count}\n"
        result_msg += "\nCSV file attached with all credentials."

        await interaction.followup.send(
            result_msg,
            file=file,
            ephemeral=True,
        )

        logger.info(f"Password reset performed by {interaction.user}. Failed: {len(failed_resets)}")

    @competition_group.command(name="set-max-members", description="[ADMIN] Set maximum team members globally")
    @app_commands.describe(max_members="Maximum members per team (1-20)")
    @app_commands.check(check_admin)
    async def admin_set_max_members(self, interaction: discord.Interaction, max_members: int) -> None:
        """Set global maximum team members."""
        if max_members < 1 or max_members > 20:
            await interaction.response.send_message("Maximum members must be between 1 and 20.", ephemeral=True)
            return

        config = await sync_to_async(CompetitionConfig.get_config)()
        old_max = config.max_team_members
        config.max_team_members = max_members
        await config.asave(update_fields=["max_team_members"])

        # Update all existing team records to match the new global max
        await Team.objects.aupdate(max_members=max_members)

        # Create audit log
        await AuditLog.objects.acreate(
            action="max_team_members_updated",
            admin_user=str(interaction.user),
            target_entity="competition_config",
            target_id=config.pk,
            details={
                "old_max": old_max,
                "new_max": max_members,
            },
        )

        # Log to ops
        await log_to_ops_channel(
            self.bot,
            f"Max Team Members Updated by {interaction.user.mention}\n• Old: {old_max}\n• New: {max_members}",
        )

        await interaction.response.send_message(
            f"Maximum team members set to {max_members} (was {old_max}).",
            ephemeral=True,
        )

    @competition_group.command(
        name="set-start-time",
        description="[ADMIN] Set competition start time (applications will be enabled automatically)",
    )
    @app_commands.describe(
        datetime_str="Start time in format: YYYY-MM-DDTHH:MM (e.g., 2025-01-15T09:00)",
        timezone_name="Timezone (defaults to Pacific Time)",
    )
    @app_commands.choices(
        timezone_name=[
            app_commands.Choice(name="Pacific Time (PT)", value="America/Los_Angeles"),
            app_commands.Choice(name="Mountain Time (MT)", value="America/Denver"),
            app_commands.Choice(name="Central Time (CT)", value="America/Chicago"),
            app_commands.Choice(name="Eastern Time (ET)", value="America/New_York"),
            app_commands.Choice(name="UTC", value="UTC"),
        ]
    )
    @app_commands.check(check_admin)
    async def admin_set_start_time(
        self,
        interaction: discord.Interaction,
        datetime_str: str,
        timezone_name: str = "America/Los_Angeles",
    ) -> None:
        """Set competition start time for automatic application enabling."""
        try:
            start_time = parse_datetime_to_utc(datetime_str, timezone_name)
        except ValueError:
            await interaction.response.send_message(
                "Invalid datetime format. Use: YYYY-MM-DDTHH:MM (e.g., 2025-01-15T09:00)",
                ephemeral=True,
            )
            return
        except Exception as e:
            await interaction.response.send_message(
                f"Error parsing timezone: {e}",
                ephemeral=True,
            )
            return

        # Get or create config
        config = await sync_to_async(CompetitionConfig.get_config)()

        # Populate controlled applications from Authentik if not already set
        if not config.controlled_applications:
            await sync_to_async(config.ensure_controlled_applications)()

        config.competition_start_time = start_time
        await config.asave(update_fields=["competition_start_time"])

        # Create audit log
        await AuditLog.objects.acreate(
            action="competition_start_time_set",
            admin_user=str(interaction.user),
            target_entity="competition_config",
            target_id=config.pk,
            details={
                "start_time": start_time.isoformat(),
                "controlled_apps": config.controlled_applications,
            },
        )

        # Log to ops
        await log_to_ops_channel(
            self.bot,
            f"Competition Start Time Set by {interaction.user.mention}\n"
            f"• Start Time: {discord.utils.format_dt(start_time, style='F')}\n"
            f"• Controlled Apps: {', '.join(config.controlled_applications)}\n"
            f"• Applications will be enabled automatically at start time",
        )

        await interaction.response.send_message(
            f"Competition start time set to: {discord.utils.format_dt(start_time, style='F')}\n"
            f"Controlled applications: {', '.join(config.controlled_applications)}\n\n"
            f"Applications will be automatically enabled at start time.",
            ephemeral=True,
        )

    @competition_group.command(
        name="set-end-time",
        description="[ADMIN] Set competition end time (applications will be disabled automatically)",
    )
    @app_commands.describe(
        datetime_str="End time in format: YYYY-MM-DDTHH:MM (e.g., 2025-01-15T17:00)",
        timezone_name="Timezone (defaults to Pacific Time)",
    )
    @app_commands.choices(
        timezone_name=[
            app_commands.Choice(name="Pacific Time (PT)", value="America/Los_Angeles"),
            app_commands.Choice(name="Mountain Time (MT)", value="America/Denver"),
            app_commands.Choice(name="Central Time (CT)", value="America/Chicago"),
            app_commands.Choice(name="Eastern Time (ET)", value="America/New_York"),
            app_commands.Choice(name="UTC", value="UTC"),
        ]
    )
    @app_commands.check(check_admin)
    async def admin_set_end_time(
        self,
        interaction: discord.Interaction,
        datetime_str: str,
        timezone_name: str = "America/Los_Angeles",
    ) -> None:
        """Set competition end time for automatic application disabling."""
        try:
            end_time = parse_datetime_to_utc(datetime_str, timezone_name)
        except ValueError:
            await interaction.response.send_message(
                "Invalid datetime format. Use: YYYY-MM-DDTHH:MM (e.g., 2025-01-15T17:00)",
                ephemeral=True,
            )
            return
        except Exception as e:
            await interaction.response.send_message(
                f"Error parsing timezone: {e}",
                ephemeral=True,
            )
            return

        # Get or create config
        config = await sync_to_async(CompetitionConfig.get_config)()

        # Populate controlled applications from Authentik if not already set
        if not config.controlled_applications:
            await sync_to_async(config.ensure_controlled_applications)()

        config.competition_end_time = end_time
        await config.asave(update_fields=["competition_end_time"])

        # Create audit log
        await AuditLog.objects.acreate(
            action="competition_end_time_set",
            admin_user=str(interaction.user),
            target_entity="competition_config",
            target_id=config.pk,
            details={
                "end_time": end_time.isoformat(),
                "controlled_apps": config.controlled_applications,
            },
        )

        # Log to ops
        await log_to_ops_channel(
            self.bot,
            f"Competition End Time Set by {interaction.user.mention}\n"
            f"• End Time: {discord.utils.format_dt(end_time, style='F')}\n"
            f"• Controlled Apps: {', '.join(config.controlled_applications)}\n"
            f"• Applications will be disabled automatically at end time",
        )

        await interaction.response.send_message(
            f"Competition end time set to: {discord.utils.format_dt(end_time, style='F')}\n"
            f"Controlled applications: {', '.join(config.controlled_applications)}\n\n"
            f"Applications will be automatically disabled at end time.",
            ephemeral=True,
        )

    @competition_group.command(
        name="start-competition",
        description="[ADMIN] Start the competition (enable applications and accounts)",
    )
    @app_commands.check(check_admin)
    async def admin_start_competition(self, interaction: discord.Interaction) -> None:
        """Start the competition by enabling applications and Authentik accounts."""
        await self._run_competition(interaction, enable=True)

    @competition_group.command(
        name="stop-competition",
        description="[ADMIN] Stop the competition (disable applications and accounts)",
    )
    @app_commands.check(check_admin)
    async def admin_stop_competition(self, interaction: discord.Interaction) -> None:
        """Stop the competition by disabling applications and Authentik accounts."""
        await self._run_competition(interaction, enable=False)

    async def _run_competition(self, interaction: discord.Interaction, enable: bool) -> None:
        from bot.competition_actions import run_competition, update_status_channel

        await interaction.response.defer(ephemeral=True)
        result = await run_competition(enable, actor=f"discord:{interaction.user}")
        if not result.success:
            await interaction.followup.send(f"Error: {result.error}", ephemeral=True)
            return

        verb = "Started" if enable else "Stopped"
        await log_to_ops_channel(self.bot, f"**Competition {verb}** by {interaction.user.mention}\n{result.summary()}")
        await update_status_channel(self.bot)
        await interaction.followup.send(f"**Competition {verb}!**\n\n{result.summary()}", ephemeral=True)

    @competition_group.command(
        name="cleanup-competition",
        description="[ADMIN] Clean up Discord infrastructure (requires competition stopped)",
    )
    @app_commands.check(check_admin)
    async def admin_cleanup_competition(self, interaction: discord.Interaction) -> None:
        """Clean up Discord channels, roles, and links after competition."""
        from bot.competition_actions import update_status_channel

        # Check if competition is stopped
        config = await sync_to_async(CompetitionConfig.get_config)()
        if config.applications_enabled:
            await interaction.response.send_message(
                "Competition must be stopped before cleanup. Use `/competition stop-competition` first.",
                ephemeral=True,
            )
            return

        # Show confirmation
        view = ConfirmView(confirm_label="Confirm Cleanup")
        await interaction.response.send_message(
            "**This will permanently:**\n"
            "- Delete all team Discord channels and categories\n"
            "- Remove team roles from all members\n"
            "- Deactivate all team member Discord links\n"
            "- Remove student helper and Room Judge roles\n"
            "- Clear queued announcements\n\n"
            "Are you sure you want to continue?",
            view=view,
            ephemeral=True,
        )

        await view.wait()
        if not view.confirmed:
            await interaction.followup.send("Cleanup cancelled.", ephemeral=True)
            return

        await interaction.followup.send(
            "Starting cleanup... This runs in background. Check ops channel for progress.",
            ephemeral=True,
        )

        async def cleanup() -> None:
            try:
                await log_to_ops_channel(
                    self.bot,
                    f"Competition Cleanup Started by {interaction.user.mention}",
                )

                guild = interaction.guild
                if not guild:
                    await log_to_ops_channel(self.bot, "Cleanup Error: Guild not found")
                    return

                # Deactivate team member links only (preserve admin/support links)
                links_to_deactivate = [
                    link
                    async for link in DiscordLink.objects.filter(is_active=True, team__isnull=False).select_related(
                        "team", "user"
                    )
                ]

                deactivated = 0
                for link in links_to_deactivate:
                    link.is_active = False
                    link.unlinked_at = timezone.now()
                    await link.asave()
                    deactivated += 1

                    await AuditLog.objects.acreate(
                        action="user_unlinked",
                        admin_user=str(interaction.user),
                        target_entity="discord_link",
                        target_id=link.discord_id,
                        details={
                            "discord_id": link.discord_id,
                            "team_name": link.team.team_name if link.team else "Unknown",
                            "authentik_username": link.user.username,
                            "reason": "competition_cleanup",
                        },
                    )

                await log_to_ops_channel(self.bot, f"Deactivated {deactivated} team member links")

                # Delete ALL team categories/channels
                deleted_count = 0
                for category in guild.categories:
                    match = re.match(r"^team\s*(\d+)$", category.name, re.IGNORECASE)
                    if match:
                        try:
                            for channel in category.channels:
                                await channel.delete(reason="Competition cleanup")
                            await category.delete(reason="Competition cleanup")
                            deleted_count += 1
                            logger.info(f"Deleted {category.name}")
                        except Exception as e:
                            logger.exception(f"Failed to delete {category.name}: {e}")

                await log_to_ops_channel(self.bot, f"Deleted {deleted_count} team categories")

                # Remove team roles from members
                from bot.discord_manager import DiscordManager

                discord_manager = DiscordManager(guild, self.bot)
                removed_count = await discord_manager.remove_all_team_roles()
                await log_to_ops_channel(self.bot, f"Removed roles from {removed_count} members")

                # Clear Discord IDs from teams
                await Team.objects.all().aupdate(discord_category_id=None, discord_role_id=None)

                # Remove student helper roles
                active_helpers = [
                    dl
                    async for dl in DiscordLink.objects.filter(is_student_helper=True, is_active=True).select_related(
                        "user"
                    )
                ]

                helpers_removed = 0
                for discord_link in active_helpers:
                    try:
                        if discord_link.helper_role_id:
                            role = guild.get_role(discord_link.helper_role_id)
                            if role:
                                for member in guild.members:
                                    if role in member.roles:
                                        try:
                                            await member.remove_roles(role, reason="Competition cleanup")
                                        except Exception as e:
                                            logger.warning(f"Could not remove helper role from {member}: {e}")

                        discord_link.is_student_helper = False
                        discord_link.helper_removal_reason = "Competition cleanup"
                        discord_link.helper_deactivated_at = timezone.now()
                        await discord_link.asave()
                        helpers_removed += 1

                        await AuditLog.objects.acreate(
                            action="helper_removed",
                            admin_user=str(interaction.user),
                            target_entity="discordlink",
                            target_id=discord_link.id,
                            details={
                                "discord_id": discord_link.discord_id,
                                "discord_username": discord_link.discord_username,
                                "role_name": discord_link.helper_role_name,
                                "reason": "competition_cleanup",
                            },
                        )
                    except Exception as e:
                        logger.exception(f"Error removing helper {discord_link.user.username}: {e}")

                if helpers_removed > 0:
                    await log_to_ops_channel(self.bot, f"Removed {helpers_removed} student helper role(s)")

                # Remove helper roles from all members
                helper_role_ids: set[int] = set()
                async for role_id in DiscordLink.objects.filter(helper_role_id__isnull=False).values_list(
                    "helper_role_id", flat=True
                ):
                    if role_id is not None:
                        helper_role_ids.add(role_id)

                helper_role_removals = 0
                for role_id in helper_role_ids:
                    role = guild.get_role(role_id)
                    if role:
                        for member in role.members:
                            try:
                                await member.remove_roles(role, reason="Competition cleanup")
                                helper_role_removals += 1
                            except Exception as e:
                                logger.warning(f"Could not remove {role.name} from {member}: {e}")

                # Remove WCComps Room Judge role
                room_judge_role = discord.utils.get(guild.roles, name="WCComps Room Judge")
                if room_judge_role:
                    for member in room_judge_role.members:
                        try:
                            await member.remove_roles(room_judge_role, reason="Competition cleanup")
                            helper_role_removals += 1
                        except Exception as e:
                            logger.warning(f"Could not remove Room Judge from {member}: {e}")

                if helper_role_removals > 0:
                    await log_to_ops_channel(
                        self.bot, f"Removed helper/judge roles from {helper_role_removals} members"
                    )

                # Clear competition config times
                if config:
                    config.competition_start_time = None
                    config.competition_end_time = None
                    await config.asave()

                # Clear queued announcements
                deleted_announcements = await QueuedAnnouncement.objects.all().adelete()
                if deleted_announcements[0] > 0:
                    await log_to_ops_channel(self.bot, f"Cleared {deleted_announcements[0]} queued announcements")

                # Create audit log
                await AuditLog.objects.acreate(
                    action="competition_cleanup",
                    admin_user=str(interaction.user),
                    target_entity="competition",
                    target_id=0,
                    details={
                        "deactivated_links": deactivated,
                        "deleted_categories": deleted_count,
                        "removed_roles": removed_count,
                        "helpers_removed": helpers_removed,
                    },
                )

                await log_to_ops_channel(
                    self.bot,
                    f"Competition Cleanup Complete\n"
                    f"- Deactivated {deactivated} team links\n"
                    f"- Deleted {deleted_count} team categories\n"
                    f"- Removed {removed_count} role assignments",
                )

                # Update status channel
                await update_status_channel(self.bot)

            except Exception as e:
                logger.exception(f"Cleanup error: {e}")
                await log_to_ops_channel(self.bot, f"Cleanup Error: {e}")

        self.bot.loop.create_task(cleanup())

    @competition_group.command(
        name="set-apps",
        description="[ADMIN] Set which Authentik applications to control",
    )
    @app_commands.describe(app_slugs="Comma-separated list of application slugs (e.g., netbird,scoring)")
    @app_commands.check(check_admin)
    async def admin_competition_set_apps(self, interaction: discord.Interaction, app_slugs: str) -> None:
        """Set which applications to control."""

        # Parse slugs
        slugs = [s.strip() for s in app_slugs.split(",") if s.strip()]

        if not slugs:
            await interaction.response.send_message("Please provide at least one application slug.", ephemeral=True)
            return

        config = await sync_to_async(CompetitionConfig.get_config)()
        config.controlled_applications = slugs
        await config.asave()

        # Create audit log
        await AuditLog.objects.acreate(
            action="competition_apps_configured",
            admin_user=str(interaction.user),
            target_entity="competition_config",
            target_id=config.pk,
            details={
                "controlled_apps": slugs,
            },
        )

        # Log to ops
        await log_to_ops_channel(
            self.bot,
            f"Competition Applications Configured by {interaction.user.mention}\n• Applications: {', '.join(slugs)}",
        )

        await interaction.response.send_message(f"Controlled applications set to: {', '.join(slugs)}", ephemeral=True)

    @competition_group.command(
        name="broadcast",
        description="[ADMIN] Broadcast a message to announcement channel or team channels",
    )
    @app_commands.describe(
        target="Where to broadcast: 'announcements', 'all-teams', or specific teams (e.g., '1,3,5-10')",
        message="Message to broadcast",
    )
    @app_commands.check(check_admin)
    async def admin_broadcast(self, interaction: discord.Interaction, target: str, message: str) -> None:
        """Broadcast a message to announcement channel or team channels."""

        await interaction.response.defer(ephemeral=True)

        guild = interaction.guild
        if not guild:
            await interaction.followup.send("This command must be used in a guild", ephemeral=True)
            return

        target_lower = target.lower().strip()
        sent_count = 0
        failed_channels = []

        # Determine broadcast target
        if target_lower == "announcements":
            # Broadcast to announcements channel with @Blueteam mention
            announcement_channel_id = settings.DISCORD_ANNOUNCEMENT_CHANNEL_ID
            channel = guild.get_channel(announcement_channel_id)

            if not channel or not isinstance(channel, discord.TextChannel):
                await interaction.followup.send(
                    f"Announcements channel not found (ID: {announcement_channel_id})",
                    ephemeral=True,
                )
                return

            # Get Blueteam role
            blueteam_role = guild.get_role(settings.BLUETEAM_ROLE_ID)
            role_mention = blueteam_role.mention if blueteam_role else "@Blueteam"

            try:
                await channel.send(f"{role_mention}\n\n{message}")
                sent_count = 1

                # Log to ops
                await log_to_ops_channel(
                    self.bot,
                    f"Broadcast to Announcements by {interaction.user.mention}\nMessage: {message[:100]}...",
                )

                await interaction.followup.send("Broadcast sent to announcements channel", ephemeral=True)
            except Exception as e:
                logger.exception(f"Failed to broadcast to announcements: {e}")
                await interaction.followup.send(f"Failed to send broadcast: {e}", ephemeral=True)
            return

        if target_lower == "all-teams":
            # Broadcast to all team chat channels
            teams = [t async for t in Team.objects.filter(is_active=True).order_by("team_number")]
            team_numbers = [t.team_number for t in teams]

        else:
            # Parse specific team range
            try:
                team_numbers = parse_team_range(target)
            except ValueError as e:
                await interaction.followup.send(
                    f"Invalid team range format: {e}\n\n"
                    f"Examples:\n"
                    f"• Single teams: `1,3,5`\n"
                    f"• Range: `1-10`\n"
                    f"• Mixed: `1,3,5-10,15`\n"
                    f"• All teams: `all-teams`\n"
                    f"• Announcements: `announcements`",
                    ephemeral=True,
                )
                return

        from core.models import QueuedAnnouncement

        queued_count = 0

        # Send to team channels (or queue if channel doesn't exist yet)
        for team_number in team_numbers:
            try:
                team = await Team.objects.filter(team_number=team_number).afirst()
                if not team:
                    failed_channels.append(f"Team {team_number:02d} (not found)")
                    continue

                # Try to find team chat channel
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
                    # Send message directly
                    await chat_channel.send(f"**Announcement from {interaction.user.name}:**\n\n{message}")
                    sent_count += 1
                else:
                    # Queue announcement for later delivery when channel is created
                    await QueuedAnnouncement.objects.acreate(
                        team=team,
                        message=message,
                        sender_name=interaction.user.name,
                    )
                    queued_count += 1

            except Exception as e:
                logger.exception(f"Failed to broadcast to team {team_number}: {e}")
                failed_channels.append(f"Team {team_number:02d} ({str(e)[:50]})")
                continue

        # Create audit log
        await AuditLog.objects.acreate(
            action="broadcast_message",
            admin_user=str(interaction.user),
            target_entity="broadcast",
            target_id=0,
            details={
                "target": target,
                "message_preview": message[:200],
                "sent_count": sent_count,
                "queued_count": queued_count,
                "failed_count": len(failed_channels),
            },
        )

        # Log to ops
        ops_msg_parts = [
            f"Broadcast by {interaction.user.mention}",
            f"• Target: {target}",
            f"• Sent: {sent_count} channels",
        ]
        if queued_count > 0:
            ops_msg_parts.append(f"• Queued: {queued_count} (pending channel creation)")
        if failed_channels:
            ops_msg_parts.append(f"• Failed: {len(failed_channels)}")
        ops_msg_parts.append(f"Message: {message[:100]}...")

        await log_to_ops_channel(self.bot, "\n".join(ops_msg_parts))

        # Build response
        result_msg = f"Broadcast complete\n• Sent: {sent_count} channels"
        if queued_count > 0:
            result_msg += f"\n• Queued: {queued_count} (will deliver when team channels are created)"
        if failed_channels:
            result_msg += f"\n• Failed: {len(failed_channels)}"
            if len(failed_channels) <= 10:
                result_msg += "\n\nFailed:\n" + "\n".join([f"• {fc}" for fc in failed_channels])
            else:
                result_msg += "\n\nFailed (first 10):\n" + "\n".join([f"• {fc}" for fc in failed_channels[:10]])

        await interaction.followup.send(result_msg, ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    """Setup function to add cog to bot."""
    await bot.add_cog(AdminCompetitionCog(bot))
