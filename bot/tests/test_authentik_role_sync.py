"""A synced role belongs to exactly the linked members of its group; every other holder loses it."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from bot.role_sync import AuthentikRoleSyncManager, RoleSyncStats

ROLE_ID = 4242
IN_GROUP_LINKED = 1  # linked, in the Authentik group
EXTRA_LINKED = 2  # linked, NOT in the group, but holds the Discord role (loses it)
UNLINKED_HOLDER = 3  # never linked, holds the Discord role (loses it)
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


def _stats() -> RoleSyncStats:
    return {"roles_added": 0, "roles_removed": 0, "errors": 0, "changes": []}


async def _run(guild, *, dry_run: bool, role_id: int = ROLE_ID) -> RoleSyncStats:
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


async def test_removes_role_from_linked_user_outside_the_group(guild):
    stats = await _run(guild, dry_run=False)

    _member_by_id(guild, EXTRA_LINKED).remove_roles.assert_awaited_once()
    removed = [c for c in stats["changes"] if "user2" in c]
    assert len(removed) == 1 and "not in WCComps_GoldTeam" in removed[0]


async def test_removes_role_from_unlinked_holder(guild):
    stats = await _run(guild, dry_run=False)

    _member_by_id(guild, UNLINKED_HOLDER).remove_roles.assert_awaited_once()
    assert stats["roles_removed"] == 2
    unlinked = [c for c in stats["changes"] if "user3" in c]
    assert len(unlinked) == 1 and "not linked" in unlinked[0]


async def test_never_touches_group_members_or_bots(guild):
    await _run(guild, dry_run=False)

    for member_id in (IN_GROUP_LINKED, IN_GROUP_HAS_ROLE, 99):
        _member_by_id(guild, member_id).remove_roles.assert_not_awaited()


async def test_dry_run_changes_nothing_but_reports_same(guild):
    stats = await _run(guild, dry_run=True)

    for member in guild.members:
        member.add_roles.assert_not_awaited()
        member.remove_roles.assert_not_awaited()
    assert (stats["roles_added"], stats["roles_removed"]) == (1, 2)


async def test_missing_role_is_an_error_not_silent(guild):
    stats = await _run(guild, dry_run=True, role_id=0)

    assert stats["errors"] == 1
    assert any("not configured" in c or "not found" in c for c in stats["changes"])
