"""Authentik role sync only adds roles; holders who shouldn't have them are reported, never removed."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from bot.role_sync import AuthentikRoleSyncManager
from core.discord_tasks import SyncRolesResult

ROLE_ID = 4242
IN_GROUP_LINKED = 1  # linked, in the Authentik group
EXTRA_LINKED = 2  # linked, NOT in the group, but holds the Discord role
UNLINKED_HOLDER = 3  # never linked, holds the Discord role
IN_GROUP_HAS_ROLE = 4  # linked, in group, already has role


def _member(member_id: int, *, has_role: bool, role: object, bot: bool = False) -> SimpleNamespace:
    return SimpleNamespace(
        id=member_id,
        bot=bot,
        name=f"user{member_id}",
        display_name=f"User {member_id}",
        roles=[role] if has_role else [],
        add_roles=AsyncMock(),
        remove_roles=AsyncMock(),
    )


@pytest.fixture
def guild():
    role = SimpleNamespace(id=ROLE_ID, name="Gold Team")
    members = [
        _member(IN_GROUP_LINKED, has_role=False, role=role),
        _member(EXTRA_LINKED, has_role=True, role=role),
        _member(UNLINKED_HOLDER, has_role=True, role=role),
        _member(IN_GROUP_HAS_ROLE, has_role=True, role=role),
        _member(99, has_role=True, role=role, bot=True),
    ]
    g = MagicMock()
    g.name = "Competition"
    g.members = members
    g.get_role.side_effect = lambda rid: role if rid == ROLE_ID else None
    return g


def _stats() -> SyncRolesResult:
    return {"roles_added": 0, "roles_removed": 0, "errors": 0, "extra_linked": 0, "unlinked_holders": 0, "changes": []}


async def _run(guild, *, dry_run: bool, role_id: int = ROLE_ID) -> SyncRolesResult:
    stats = _stats()
    manager = AuthentikRoleSyncManager.__new__(AuthentikRoleSyncManager)
    await manager._sync_authentik_group(
        guild,
        "WCComps_GoldTeam",
        role_id,
        should_have_role_discord_ids={IN_GROUP_LINKED, IN_GROUP_HAS_ROLE},
        linked_discord_ids={IN_GROUP_LINKED, EXTRA_LINKED, IN_GROUP_HAS_ROLE},
        stats=stats,
        dry_run=dry_run,
    )
    return stats


def _member_by_id(guild, member_id):
    return next(m for m in guild.members if m.id == member_id)


async def test_live_sync_adds_role_to_linked_group_member(guild):
    stats = await _run(guild, dry_run=False)

    _member_by_id(guild, IN_GROUP_LINKED).add_roles.assert_awaited_once()
    assert stats["roles_added"] == 1


async def test_never_removes_roles(guild):
    stats = await _run(guild, dry_run=False)

    for member in guild.members:
        member.remove_roles.assert_not_awaited()
    assert stats["roles_removed"] == 0


async def test_linked_user_outside_group_reported_as_extra_permission(guild):
    stats = await _run(guild, dry_run=False)

    assert stats["extra_linked"] == 1
    extra = [c for c in stats["changes"] if "user2" in c]
    assert len(extra) == 1 and "not in WCComps_GoldTeam" in extra[0]


async def test_unlinked_holder_reported_separately(guild):
    stats = await _run(guild, dry_run=False)

    assert stats["unlinked_holders"] == 1
    unlinked = [c for c in stats["changes"] if "user3" in c]
    assert len(unlinked) == 1 and "not linked" in unlinked[0]


async def test_dry_run_changes_nothing_but_reports_same(guild):
    stats = await _run(guild, dry_run=True)

    for member in guild.members:
        member.add_roles.assert_not_awaited()
        member.remove_roles.assert_not_awaited()
    assert (stats["roles_added"], stats["extra_linked"], stats["unlinked_holders"]) == (1, 1, 1)


async def test_missing_role_is_an_error_not_silent(guild):
    stats = await _run(guild, dry_run=True, role_id=0)

    assert stats["errors"] == 1
    assert any("not configured" in c or "not found" in c for c in stats["changes"])
