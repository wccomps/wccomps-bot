"""Scoring commands for CCDC competitions."""

import logging

import discord
from discord import app_commands
from discord.ext import commands
from django.utils import timezone

from bot.permissions import check_blue_team
from bot.utils import DISCORD_EMBED_FIELD_CHAR_LIMIT
from team.models import DiscordLink

logger = logging.getLogger(__name__)


class ScoringCog(commands.Cog):
    """Commands for scoring and incident reports."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(
        name="incident-report", description="[BLUE TEAM] Submit an incident report for detected red team activity"
    )
    @app_commands.describe(
        affected_box="Which box was affected (e.g., web-server, mail-server)",
        affected_service="Which service was attacked (e.g., HTTP, SSH, SMTP)",
        source_ip="Source IP address of the attack",
        attack_vector="Type of attack (e.g., SQL Injection, Brute Force)",
        description="Detailed description of what you detected and how you mitigated it",
    )
    @app_commands.check(check_blue_team)
    async def incident_report(
        self,
        interaction: discord.Interaction,
        affected_box: str,
        affected_service: str,
        source_ip: str,
        attack_vector: str,
        description: str,
    ) -> None:
        """Submit an incident report for red team activity."""
        link = await (
            DiscordLink.objects.filter(discord_id=interaction.user.id, is_active=True)
            .select_related("team", "user")
            .afirst()
        )
        if not link or not link.team:
            return
        team = link.team
        submitted_by_user = link.user

        # Import models here to avoid circular imports
        from scoring.models import IncidentReport

        if not source_ip.strip():
            await interaction.response.send_message(
                "Source IP address is required. Please include the IP address from your evidence.",
                ephemeral=True,
            )
            return

        if len(description) < 50:
            await interaction.response.send_message(
                "Description too short. Please provide a detailed explanation (at least 50 characters) including:\n"
                "• How you detected the attack\n"
                "• Evidence you collected (timestamp, IP, logs)\n"
                "• Mitigation steps you took",
                ephemeral=True,
            )
            return

        try:
            incident = await IncidentReport.objects.acreate(
                team=team,
                affected_boxes=[affected_box] if affected_box else [],
                affected_service=affected_service,
                source_ip=source_ip,
                destination_ip="",  # Will be filled in from web interface
                attack_description=f"{attack_vector}\n\n{description}",
                attack_detected_at=timezone.now(),
                submitted_by=submitted_by_user,
            )

            embed = discord.Embed(
                title="Incident Report Submitted",
                description="Your incident report has been submitted for gold team review.",
                color=discord.Color.blue(),
            )
            embed.add_field(name="Report ID", value=f"#{incident.id}", inline=True)
            embed.add_field(name="Team", value=team.team_name, inline=True)
            embed.add_field(name="Status", value="Pending Review", inline=True)
            embed.add_field(name="Affected Box", value=affected_box, inline=True)
            embed.add_field(name="Affected Service", value=affected_service, inline=True)
            embed.add_field(name="Source IP", value=f"`{source_ip}`", inline=True)
            embed.add_field(name="Attack Vector", value=attack_vector, inline=False)
            embed.add_field(name="Description", value=description[:DISCORD_EMBED_FIELD_CHAR_LIMIT], inline=False)

            embed.add_field(
                name="Upload Evidence",
                value=f"Visit the web interface to upload screenshots showing IP addresses and timestamps:\n"
                f"`/scoring/incident/{incident.id}/`",
                inline=False,
            )

            embed.set_footer(
                text="Gold team will review and match this to red team findings. Points may be returned if validated."
            )

            await interaction.response.send_message(embed=embed, ephemeral=True)
            logger.info(f"Incident report #{incident.id} created by {interaction.user} for team {team.team_name}")

        except Exception as e:
            logger.exception(f"Failed to create incident report: {e}")
            await interaction.response.send_message(
                "Failed to submit incident report. Please try again or contact staff if the problem persists.",
                ephemeral=True,
            )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(ScoringCog(bot))
