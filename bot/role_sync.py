"""Discord roles in the competition guild, synced from Authentik group membership."""

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from typing import TypedDict

import discord
from asgiref.sync import sync_to_async
from django.conf import settings

logger = logging.getLogger(__name__)

GUILD_CHUNK_TIMEOUT = 30.0


class RoleSyncStats(TypedDict, total=False):
    roles_added: int
    roles_removed: int
    errors: int
    extra_linked: int  # linked users holding a role their Authentik groups don't grant
    unlinked_holders: int  # unlinked users holding a synced role (can't verify)
    changes: list[str]


class AuthentikRoleSyncManager:
    """Adds competition-guild roles to Discord-linked users (DiscordLink): the roles their Authentik groups
    (UserGroups) map to, and a team seat's team and Blueteam roles. Runs after every group refresh, so a
    user who linked before joining the guild gets their roles on the next pass after they join."""

    def __init__(self, bot: discord.Client) -> None:
        self.bot = bot
        self.competition_guild_id = settings.COMPETITION_GUILD_ID
        self.group_role_mapping = settings.GROUP_ROLE_MAPPING

    def _get_competition_guild(self) -> discord.Guild | None:
        guild = self.bot.get_guild(self.competition_guild_id)
        if not guild:
            logger.error(f"Competition guild {self.competition_guild_id} not found")
        return guild

    async def sync_roles(
        self,
        dry_run: bool = False,
        progress_callback: Callable[[int, int, str], Awaitable[None]] | None = None,
    ) -> RoleSyncStats:
        """Add roles from Authentik groups (UserGroups), and team seats' team and Blueteam roles, to linked users
        in the competition guild.

        progress_callback, if given, is awaited with (current, total, role_name) per group mapping.
        Add-only: roles a user shouldn't have are counted (extra_linked, unlinked_holders), never removed.
        """
        competition_guild = self._get_competition_guild()
        if not competition_guild:
            return {"roles_added": 0, "roles_removed": 0, "errors": 1, "changes": []}

        stats: RoleSyncStats = {
            "roles_added": 0,
            "roles_removed": 0,
            "errors": 0,
            "extra_linked": 0,
            "unlinked_holders": 0,
            "changes": [],
        }

        mode = "DRY RUN" if dry_run else "LIVE"

        if not competition_guild.chunked:
            chunk_start = time.time()
            try:
                await asyncio.wait_for(competition_guild.chunk(), timeout=GUILD_CHUNK_TIMEOUT)
            except TimeoutError:
                logger.warning(
                    f"Guild chunk timed out after {time.time() - chunk_start:.2f}s, "
                    f"using cached members ({len(competition_guild.members)} available)"
                )

        @sync_to_async
        def get_authentik_data() -> tuple[dict[str, set[int]], set[int], dict[int, int]]:
            """Authentik group name -> linked Discord IDs, every linked Discord ID, and Discord ID -> team seat."""
            from core.models import UserGroups
            from team.models import DiscordLink

            group_to_discord_ids: dict[str, set[int]] = {group_name: set() for group_name in self.group_role_mapping}

            discord_links = list(DiscordLink.objects.filter(is_active=True).select_related("user", "team"))
            linked_discord_ids = {link.discord_id for link in discord_links}
            team_seats = {link.discord_id: link.team.team_number for link in discord_links if link.team}

            for link in discord_links:
                try:
                    user_groups = UserGroups.objects.get(user=link.user)
                    for group_name in self.group_role_mapping:
                        if group_name in user_groups.groups:
                            group_to_discord_ids[group_name].add(link.discord_id)
                except UserGroups.DoesNotExist:
                    continue

            return group_to_discord_ids, linked_discord_ids, team_seats

        group_to_discord_ids, linked_discord_ids, team_seats = await get_authentik_data()

        total_mappings = len(self.group_role_mapping)
        for idx, (group_name, role_id) in enumerate(self.group_role_mapping.items(), start=1):
            competition_role = competition_guild.get_role(role_id)
            role_name = competition_role.name if competition_role else f"Role {role_id}"

            if progress_callback:
                await progress_callback(idx, total_mappings, role_name)

            try:
                await self._sync_authentik_group(
                    competition_guild,
                    group_name,
                    role_id,
                    group_to_discord_ids[group_name],
                    linked_discord_ids,
                    stats,
                    dry_run,
                )
            except Exception as e:
                logger.error(
                    f"Error syncing group {group_name} -> role {role_id}: {e}",
                    exc_info=True,
                )
                stats["errors"] = stats["errors"] + 1

        await self._sync_team_roles(competition_guild, team_seats, stats, dry_run)

        summary = (
            f"Role sync [{mode}]: {stats['roles_added']} added, {stats['errors']} errors, "
            f"{stats.get('extra_linked', 0)} extra, {stats.get('unlinked_holders', 0)} unverified"
        )
        if dry_run or stats["roles_added"] or stats["errors"]:
            logger.info(summary)
        else:
            logger.debug(summary)
        return stats

    async def _sync_team_roles(
        self, competition_guild: discord.Guild, team_seats: dict[int, int], stats: RoleSyncStats, dry_run: bool
    ) -> None:
        """Give each linked team member in the guild their team role and the Blueteam role, if either is missing."""
        from bot.discord_manager import DiscordManager
        from team.models import Team

        role_ids = {
            team.team_number: team.discord_role_id
            async for team in Team.objects.filter(team_number__in=set(team_seats.values()))
        }
        # An unset or deleted Blueteam role can't be added, so it mustn't count as missing on every pass
        blueteam_id = settings.BLUETEAM_ROLE_ID if competition_guild.get_role(settings.BLUETEAM_ROLE_ID) else None
        manager = DiscordManager(competition_guild, self.bot)
        prefix = "[DRY RUN] " if dry_run else ""
        for discord_id, team_number in team_seats.items():
            member = competition_guild.get_member(discord_id)
            if not member:
                continue
            held = {role.id for role in member.roles}
            if role_ids.get(team_number) in held and (blueteam_id is None or blueteam_id in held):
                continue
            who = f"{member.name} ({member.display_name})"
            if not dry_run and not await manager.assign_team_role(member, team_number):
                stats["errors"] = stats["errors"] + 1
                stats["changes"].append(f"⚠ Could not assign team {team_number:02d} roles to {who}")
                continue
            stats["roles_added"] = stats["roles_added"] + 1
            stats["changes"].append(f"{prefix}✓ Added team {team_number:02d} roles to {who}")

    async def _sync_authentik_group(
        self,
        competition_guild: discord.Guild,
        group_name: str,
        role_id: int,
        should_have_role_discord_ids: set[int],
        linked_discord_ids: set[int],
        stats: RoleSyncStats,
        dry_run: bool,
    ) -> None:
        """Sync one Authentik group to its Discord role. Only ever ADDS the role.

        Most volunteers haven't linked yet, so removing roles from everyone not proven to be in
        the group would strip real staff. Holders who shouldn't have the role are reported instead:
        linked users outside the group (extra permissions) and unlinked users (can't verify yet).
        """
        competition_role = competition_guild.get_role(role_id)
        if not competition_role:
            msg = f"Discord role for {group_name} is not configured or not found (role ID {role_id})"
            logger.warning(msg)
            stats["errors"] = stats["errors"] + 1
            stats["changes"].append(f"⚠ {msg}")
            return

        prefix = "[DRY RUN] " if dry_run else ""
        for member in competition_guild.members:
            if member.bot:
                continue
            try:
                has_role = competition_role in member.roles
                who = f"{member.name} ({member.display_name})"
                if member.id in should_have_role_discord_ids:
                    if not has_role:
                        if not dry_run:
                            await member.add_roles(competition_role, reason=f"Authentik sync: member of {group_name}")
                        stats["roles_added"] = stats["roles_added"] + 1
                        stats["changes"].append(f"{prefix}✓ Added {competition_role.name} to {who}")
                elif has_role and member.id in linked_discord_ids:
                    stats["extra_linked"] = stats.get("extra_linked", 0) + 1
                    stats["changes"].append(
                        f"{prefix}✗ Extra: {who} has {competition_role.name} but is not in {group_name} (not removed)"
                    )
                elif has_role:
                    stats["unlinked_holders"] = stats.get("unlinked_holders", 0) + 1
                    stats["changes"].append(
                        f"{prefix}? Unverified: {who} has {competition_role.name} but has not linked (not removed)"
                    )
            except discord.errors.Forbidden as e:
                error_msg = f"Missing permissions to modify roles for {member.name} (ID: {member.id}): {e}"
                logger.warning(error_msg)
                stats["errors"] = stats["errors"] + 1
                stats["changes"].append(f"⚠ {error_msg}")
            except Exception as e:
                error_msg = f"Error syncing role for {member.name} (ID: {member.id}): {e}"
                logger.error(error_msg, exc_info=True)
                stats["errors"] = stats["errors"] + 1
                stats["changes"].append(f"⚠ {error_msg}")
