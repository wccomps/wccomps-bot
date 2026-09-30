"""Discord roles in the competition guild, kept in line with Authentik groups and team seats."""

import asyncio
import logging
import time
from dataclasses import dataclass

import discord
from asgiref.sync import sync_to_async
from django.conf import settings

from core.discord_tasks import SyncRolesResult

logger = logging.getLogger(__name__)

GUILD_CHUNK_TIMEOUT = 30.0


@dataclass(frozen=True)
class RoleGrants:
    """Managed role IDs, and the ones each linked Discord user should hold."""

    managed: frozenset[int]
    by_member: dict[int, frozenset[int]]


def role_grants(discord_ids: set[int] | None = None) -> RoleGrants:
    """What active DiscordLinks grant: the role mapped from each of the account's Authentik groups, and
    for a seat on an active team, that team's role and Blueteam. Limited to discord_ids when given."""
    from team.models import DiscordLink, Team

    mapped = settings.GROUP_ROLE_MAPPING
    team_roles = set(Team.objects.exclude(discord_role_id=None).values_list("discord_role_id", flat=True))
    managed = frozenset(role_id for role_id in {*mapped.values(), *team_roles, settings.BLUETEAM_ROLE_ID} if role_id)

    links = DiscordLink.objects.filter(is_active=True).select_related("user__usergroups", "team")
    if discord_ids is not None:
        links = links.filter(discord_id__in=discord_ids)
    by_member: dict[int, frozenset[int]] = {}
    for link in links:
        user_groups = getattr(link.user, "usergroups", None)
        groups = set(user_groups.groups) if user_groups else set()
        roles = [role_id for group, role_id in mapped.items() if group in groups]
        if link.team and link.team.is_active:
            roles += [link.team.discord_role_id or 0, settings.BLUETEAM_ROLE_ID]
        by_member[link.discord_id] = frozenset(role_id for role_id in roles if role_id)
    return RoleGrants(managed, by_member)


class AuthentikRoleSyncManager:
    """Keeps each competition-guild member's managed roles (mapped group roles, team roles, Blueteam) equal to
    what their active DiscordLink grants. Anyone else loses them, including members who haven't linked; bots
    are left alone."""

    def __init__(self, bot: discord.Client) -> None:
        self.bot = bot
        self.competition_guild_id = settings.COMPETITION_GUILD_ID

    def _get_competition_guild(self) -> discord.Guild | None:
        guild = self.bot.get_guild(self.competition_guild_id)
        if not guild:
            logger.error(f"Competition guild {self.competition_guild_id} not found")
        return guild

    async def sync_roles(self, dry_run: bool = False) -> SyncRolesResult:
        """Add missing granted roles and remove managed roles nobody granted, member by member."""
        stats: SyncRolesResult = {"roles_added": 0, "roles_removed": 0, "errors": 0, "changes": []}
        guild = self._get_competition_guild()
        if not guild:
            stats["errors"] = 1
            return stats

        if not guild.chunked:
            chunk_start = time.time()
            try:
                await asyncio.wait_for(guild.chunk(), timeout=GUILD_CHUNK_TIMEOUT)
            except TimeoutError:
                logger.warning(
                    f"Guild chunk timed out after {time.time() - chunk_start:.2f}s, "
                    f"using cached members ({len(guild.members)} available)"
                )

        grants = await sync_to_async(role_grants)()
        roles = self._manageable_roles(guild, grants.managed, stats)
        prefix = "[DRY RUN] " if dry_run else ""

        for member in guild.members:
            if member.bot:
                continue
            add, remove = _changes(member, roles, grants.by_member.get(member.id, frozenset()))
            if not add and not remove:
                continue
            # Re-read this member's grant: a link made or ended since the pass began must not be undone
            granted = (await sync_to_async(role_grants)({member.id})).by_member.get(member.id)
            add, remove = _changes(member, roles, granted or frozenset())
            who = f"{member.name} ({member.display_name})"
            reason = "not granted by their Authentik groups or team seat" if granted is not None else "not linked"
            try:
                if add and not dry_run:
                    await member.add_roles(*add, reason="Authentik sync: granted")
                if remove and not dry_run:
                    await member.remove_roles(*remove, reason=f"Authentik sync: {reason}")
            except discord.HTTPException as e:
                logger.warning(f"Could not update roles for {who}: {e}")
                stats["errors"] += 1
                stats["changes"].append(f"⚠ Could not update roles for {who}: {e}")
                continue
            stats["roles_added"] += len(add)
            stats["roles_removed"] += len(remove)
            stats["changes"] += [f"{prefix}✓ Added {role.name} to {who}" for role in add]
            stats["changes"] += [f"{prefix}✗ Removed {role.name} from {who} ({reason})" for role in remove]

        summary = (
            f"Role sync [{'DRY RUN' if dry_run else 'LIVE'}]: {stats['roles_added']} added, "
            f"{stats['roles_removed']} removed, {stats['errors']} errors"
        )
        if dry_run or stats["roles_added"] or stats["roles_removed"] or stats["errors"]:
            logger.info(summary)
        else:
            logger.debug(summary)
        return stats

    @staticmethod
    def _manageable_roles(
        guild: discord.Guild, role_ids: frozenset[int], stats: SyncRolesResult
    ) -> dict[int, discord.Role]:
        """The managed roles the bot can assign; each one it can't is reported once rather than failing per member."""
        roles: dict[int, discord.Role] = {}
        for role_id in sorted(role_ids):
            role = guild.get_role(role_id)
            if role is None:
                problem = f"Role {role_id} is configured but not in the guild"
            elif not role.is_assignable():
                problem = f"Bot can't manage {role.name}: it sits above the bot's top role or needs Manage Roles"
            else:
                roles[role_id] = role
                continue
            logger.warning(problem)
            stats["errors"] += 1
            stats["changes"].append(f"⚠ {problem}")
        return roles


def _changes(
    member: discord.Member, roles: dict[int, discord.Role], granted: frozenset[int]
) -> tuple[list[discord.Role], list[discord.Role]]:
    held = {role.id for role in member.roles}
    add = [role for role_id, role in roles.items() if role_id in granted and role_id not in held]
    remove = [role for role_id, role in roles.items() if role_id not in granted and role_id in held]
    return add, remove
