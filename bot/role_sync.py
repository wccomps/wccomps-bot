"""Role synchronization between volunteer and competition Discord guilds."""

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
    """Statistics for role synchronization."""

    roles_added: int
    roles_removed: int
    errors: int
    extra_linked: int  # linked users holding a role their Authentik groups don't grant
    unlinked_holders: int  # unlinked users holding a synced role (can't verify)
    changes: list[str]


class RoleSyncManager:
    """Manages role synchronization from volunteer guild to competition guild."""

    def __init__(self, bot: discord.Client) -> None:
        """Initialize role sync manager.

        Args:
            bot: Discord bot instance with access to both guilds
        """
        self.bot = bot
        self.volunteer_guild_id = settings.VOLUNTEER_GUILD_ID
        self.competition_guild_id = settings.COMPETITION_GUILD_ID
        self.role_mappings = settings.ROLE_SYNC_MAPPING

    def _get_guilds(self) -> tuple[discord.Guild | None, discord.Guild | None]:
        """Get volunteer and competition guild objects.

        Returns:
            tuple[Optional[Guild], Optional[Guild]]: (volunteer_guild, competition_guild)
        """
        volunteer_guild = self.bot.get_guild(self.volunteer_guild_id)
        competition_guild = self.bot.get_guild(self.competition_guild_id)

        if not volunteer_guild:
            logger.error(f"Volunteer guild {self.volunteer_guild_id} not found")
        if not competition_guild:
            logger.error(f"Competition guild {self.competition_guild_id} not found")

        return volunteer_guild, competition_guild

    async def sync_roles(
        self,
        progress_callback: Callable[[int, int, str], Awaitable[None]] | None = None,
    ) -> RoleSyncStats:
        """Synchronize roles from volunteer guild to competition guild.

        This performs one-way sync: volunteer guild -> competition guild.
        - Users with roles in volunteer guild get them added in competition guild
        - Users without roles in volunteer guild get them removed from competition guild

        Args:
            progress_callback: Optional async callback(current, total, role_name) for progress updates

        Returns:
            dict with sync statistics: roles_added, roles_removed, errors, changes
        """
        volunteer_guild, competition_guild = self._get_guilds()
        if not volunteer_guild or not competition_guild:
            return {"roles_added": 0, "roles_removed": 0, "errors": 1, "changes": []}

        stats: RoleSyncStats = {
            "roles_added": 0,
            "roles_removed": 0,
            "errors": 0,
            "changes": [],
        }

        logger.info("=" * 80)
        logger.info("ROLE SYNC STARTED")
        logger.info(f"Volunteer Guild: {volunteer_guild.name} (ID: {volunteer_guild.id})")
        logger.info(f"Competition Guild: {competition_guild.name} (ID: {competition_guild.id})")
        logger.info(f"Role Mappings: {len(self.role_mappings)} configured")
        for v_role_id, c_role_id in self.role_mappings.items():
            logger.info(f"  - {v_role_id} -> {c_role_id}")
        logger.info("=" * 80)

        # Chunk competition guild once at the start to get all members
        cached_member_count = len(competition_guild.members)
        logger.info(
            f"Fetching all members from competition guild "
            f"(currently have {cached_member_count} cached, chunked={competition_guild.chunked})..."
        )
        if not competition_guild.chunked:
            chunk_start = time.time()
            try:
                await asyncio.wait_for(competition_guild.chunk(), timeout=GUILD_CHUNK_TIMEOUT)
                chunk_duration = time.time() - chunk_start
                logger.info(f"Guild chunk completed in {chunk_duration:.2f}s")
            except TimeoutError:
                chunk_duration = time.time() - chunk_start
                logger.warning(
                    f"Guild chunk timed out after {chunk_duration:.2f}s, "
                    f"using cached members ({len(competition_guild.members)} available)"
                )
        else:
            logger.info("Guild already chunked, skipping chunk request")
        logger.info(f"Competition guild has {len(competition_guild.members)} total members")

        # Process each role mapping
        total_mappings = len(self.role_mappings)
        for idx, (volunteer_role_id, competition_role_id) in enumerate(self.role_mappings.items(), start=1):
            # Get role name for progress reporting
            competition_role = competition_guild.get_role(competition_role_id)
            role_name = competition_role.name if competition_role else f"Role {competition_role_id}"

            # Report progress via callback if provided
            if progress_callback:
                await progress_callback(idx, total_mappings, role_name)

            try:
                await self._sync_role_pair(
                    volunteer_guild,
                    competition_guild,
                    volunteer_role_id,
                    competition_role_id,
                    stats,
                )
            except Exception as e:
                logger.error(
                    f"Error syncing role {volunteer_role_id} -> {competition_role_id}: {e}",
                    exc_info=True,
                )
                stats["errors"] = stats["errors"] + 1

        logger.info("=" * 80)
        logger.info("ROLE SYNC COMPLETE")
        logger.info(f"Roles Added: {stats['roles_added']}")
        logger.info(f"Roles Removed: {stats['roles_removed']}")
        logger.info(f"Errors: {stats['errors']}")
        changes = stats.get("changes", [])
        if isinstance(changes, list):
            logger.info(f"Total Changes: {len(changes)}")
        logger.info("=" * 80)
        return stats

    async def _sync_role_pair(
        self,
        volunteer_guild: discord.Guild,
        competition_guild: discord.Guild,
        volunteer_role_id: int,
        competition_role_id: int,
        stats: RoleSyncStats,
    ) -> None:
        """Sync a single role pair between guilds.

        Args:
            volunteer_guild: Source guild with volunteer roles
            competition_guild: Target guild where roles should be synced
            volunteer_role_id: Role ID in volunteer guild to sync from
            competition_role_id: Role ID in competition guild to sync to
            stats: Statistics dict to update
        """
        volunteer_role = volunteer_guild.get_role(volunteer_role_id)
        competition_role = competition_guild.get_role(competition_role_id)

        if not volunteer_role:
            logger.warning(f"Volunteer role {volunteer_role_id} not found in {volunteer_guild.name}")
            return

        if not competition_role:
            logger.warning(f"Competition role {competition_role_id} not found in {competition_guild.name}")
            return

        logger.info("-" * 80)
        logger.info(
            f"Syncing: {volunteer_role.name} (ID: {volunteer_role_id}, {volunteer_guild.name}) -> "
            f"{competition_role.name} (ID: {competition_role_id}, {competition_guild.name})"
        )

        # Get members with role in volunteer guild
        volunteer_members_with_role = {m.id for m in volunteer_role.members}
        logger.info(f"Members with {volunteer_role.name} in volunteer guild: {len(volunteer_members_with_role)}")
        if volunteer_members_with_role:
            logger.debug(f"  Member IDs: {volunteer_members_with_role}")

        # Competition guild was already chunked at the start of sync_roles()
        logger.info(f"Processing {len(competition_guild.members)} competition guild members")

        members_checked = 0
        members_skipped_bot = 0
        members_no_action = 0

        for member in competition_guild.members:
            if member.bot:
                members_skipped_bot += 1
                logger.debug(f"Skipping bot user: {member.name} (ID: {member.id})")
                continue

            members_checked += 1

            try:
                should_have_role = member.id in volunteer_members_with_role
                has_role = competition_role in member.roles

                logger.debug(
                    f"Checking {member.name} (ID: {member.id}): should_have={should_have_role}, has={has_role}"
                )

                if should_have_role and not has_role:
                    # Add role
                    logger.info(
                        f"Adding {competition_role.name} to {member.name} (ID: {member.id}, "
                        f"display: {member.display_name})"
                    )
                    await member.add_roles(
                        competition_role,
                        reason=f"Role sync: has {volunteer_role.name} in volunteer guild",
                    )
                    change_msg = f"Added {competition_role.name} to {member.name} ({member.display_name})"
                    logger.info(f"✓ {change_msg}")
                    stats["roles_added"] = stats["roles_added"] + 1
                    stats["changes"].append(f"✓ {change_msg}")

                elif not should_have_role and has_role:
                    # Remove role
                    logger.info(
                        f"Removing {competition_role.name} from {member.name} (ID: {member.id}, "
                        f"display: {member.display_name})"
                    )
                    await member.remove_roles(
                        competition_role,
                        reason=f"Role sync: no longer has {volunteer_role.name} in volunteer guild",
                    )
                    change_msg = f"Removed {competition_role.name} from {member.name} ({member.display_name})"
                    logger.info(f"✗ {change_msg}")
                    stats["roles_removed"] = stats["roles_removed"] + 1
                    stats["changes"].append(f"✗ {change_msg}")

                else:
                    # No action needed - member already in correct state
                    members_no_action += 1
                    logger.debug(f"No action needed for {member.name} (ID: {member.id}): already in correct state")

            except discord.errors.Forbidden as e:
                error_msg = f"Missing permissions to modify roles for {member.name} (ID: {member.id}): {e}"
                logger.exception(error_msg)
                stats["errors"] = stats["errors"] + 1
                stats["changes"].append(f"⚠ {error_msg}")
            except Exception as e:
                error_msg = f"Error syncing role for {member.name} (ID: {member.id}): {e}"
                logger.error(error_msg, exc_info=True)
                stats["errors"] = stats["errors"] + 1
                stats["changes"].append(f"⚠ {error_msg}")

        logger.info(f"Role pair sync complete: {volunteer_role.name} -> {competition_role.name}")
        logger.info(f"  Total members in competition guild: {len(competition_guild.members)}")
        logger.info(f"  Bot members skipped: {members_skipped_bot}")
        logger.info(f"  Human members checked: {members_checked}")
        logger.info(f"  Members already in correct state: {members_no_action}")
        logger.info(f"  Members with changes: {members_checked - members_no_action}")
        logger.info("-" * 80)


class AuthentikRoleSyncManager:
    """Manages role synchronization from Authentik groups to Discord competition guild.

    This syncs based on Authentik group membership (via UserGroups model) rather than
    Discord-to-Discord syncing. Users must have linked their Discord account via DiscordLink.
    """

    def __init__(self, bot: discord.Client) -> None:
        """Initialize Authentik role sync manager.

        Args:
            bot: Discord bot instance with access to competition guild
        """
        self.bot = bot
        self.competition_guild_id = settings.COMPETITION_GUILD_ID
        self.group_role_mapping = settings.GROUP_ROLE_MAPPING

    def _get_competition_guild(self) -> discord.Guild | None:
        """Get the competition guild object."""
        guild = self.bot.get_guild(self.competition_guild_id)
        if not guild:
            logger.error(f"Competition guild {self.competition_guild_id} not found")
        return guild

    async def sync_roles(
        self,
        dry_run: bool = False,
        progress_callback: Callable[[int, int, str], Awaitable[None]] | None = None,
    ) -> RoleSyncStats:
        """Synchronize roles from Authentik groups to competition Discord guild.

        This uses the UserGroups model (populated from Authentik OIDC) and DiscordLink
        to determine which Discord users should have which roles.

        Args:
            dry_run: If True, only report what would be done without making changes
            progress_callback: Optional async callback(current, total, role_name) for progress updates

        Returns:
            dict with sync statistics: roles_added, roles_removed, errors, changes
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
        logger.info("=" * 80)
        logger.info(f"AUTHENTIK ROLE SYNC STARTED [{mode}]")
        logger.info(f"Competition Guild: {competition_guild.name} (ID: {competition_guild.id})")
        logger.info(f"Group->Role Mappings: {len(self.group_role_mapping)} configured")
        for group_name, role_id in self.group_role_mapping.items():
            logger.info(f"  - {group_name} -> {role_id}")
        logger.info("=" * 80)

        # Chunk competition guild once at the start to get all members
        cached_member_count = len(competition_guild.members)
        logger.info(
            f"Fetching all members from competition guild "
            f"(currently have {cached_member_count} cached, chunked={competition_guild.chunked})..."
        )
        if not competition_guild.chunked:
            chunk_start = time.time()
            try:
                await asyncio.wait_for(competition_guild.chunk(), timeout=GUILD_CHUNK_TIMEOUT)
                chunk_duration = time.time() - chunk_start
                logger.info(f"Guild chunk completed in {chunk_duration:.2f}s")
            except TimeoutError:
                chunk_duration = time.time() - chunk_start
                logger.warning(
                    f"Guild chunk timed out after {chunk_duration:.2f}s, "
                    f"using cached members ({len(competition_guild.members)} available)"
                )
        else:
            logger.info("Guild already chunked, skipping chunk request")
        logger.info(f"Competition guild has {len(competition_guild.members)} total members")

        # Get all Authentik group memberships and Discord links from database
        @sync_to_async
        def get_authentik_data() -> tuple[dict[str, set[int]], set[int]]:
            """Get Authentik group name -> linked Discord IDs, plus every linked Discord ID."""
            from core.models import UserGroups
            from team.models import DiscordLink

            group_to_discord_ids: dict[str, set[int]] = {group_name: set() for group_name in self.group_role_mapping}

            # Get all active Discord links with their users
            discord_links = DiscordLink.objects.filter(is_active=True).select_related("user")
            linked_discord_ids = {link.discord_id for link in discord_links}

            for link in discord_links:
                try:
                    user_groups = UserGroups.objects.get(user=link.user)
                    for group_name in self.group_role_mapping:
                        if group_name in user_groups.groups:
                            group_to_discord_ids[group_name].add(link.discord_id)
                except UserGroups.DoesNotExist:
                    continue

            return group_to_discord_ids, linked_discord_ids

        group_to_discord_ids, linked_discord_ids = await get_authentik_data()

        for group_name, discord_ids in group_to_discord_ids.items():
            logger.info(f"  {group_name}: {len(discord_ids)} linked Discord users")

        # Process each group->role mapping
        total_mappings = len(self.group_role_mapping)
        for idx, (group_name, role_id) in enumerate(self.group_role_mapping.items(), start=1):
            competition_role = competition_guild.get_role(role_id)
            role_name = competition_role.name if competition_role else f"Role {role_id}"

            # Report progress via callback if provided
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

        logger.info("=" * 80)
        logger.info(f"AUTHENTIK ROLE SYNC COMPLETE [{mode}]")
        logger.info(f"Roles Added: {stats['roles_added']}")
        logger.info(f"Roles Removed: {stats['roles_removed']}")
        logger.info(f"Errors: {stats['errors']}")
        changes = stats.get("changes", [])
        if isinstance(changes, list):
            logger.info(f"Total Changes: {len(changes)}")
        logger.info("=" * 80)
        return stats

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
                logger.exception(error_msg)
                stats["errors"] = stats["errors"] + 1
                stats["changes"].append(f"⚠ {error_msg}")
            except Exception as e:
                error_msg = f"Error syncing role for {member.name} (ID: {member.id}): {e}"
                logger.error(error_msg, exc_info=True)
                stats["errors"] = stats["errors"] + 1
                stats["changes"].append(f"⚠ {error_msg}")
