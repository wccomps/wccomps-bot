"""A user who links before joining the competition guild gets their roles on the next sync after they join."""

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from django.contrib.auth.models import User

from bot.cogs import authentik_groups
from bot.discord_manager import DiscordManager
from bot.role_sync import AuthentikRoleSyncManager
from core.models import UserGroups
from team.models import DiscordLink, Team

pytestmark = [pytest.mark.asyncio, pytest.mark.django_db(transaction=True)]

GUILD_ID = 7001
BLUETEAM = 7002
TEAM_ROLE = 7003
OPS_ROLE = 7004
STAFF_ID = 8001
TEAM_MEMBER_ID = 8002


def _role(role_id: int) -> SimpleNamespace:
    return SimpleNamespace(id=role_id, name=f"role{role_id}")


def _member(member_id: int, *role_ids: int) -> SimpleNamespace:
    return SimpleNamespace(
        id=member_id,
        bot=False,
        name=f"user{member_id}",
        display_name=f"User {member_id}",
        roles=[_role(r) for r in role_ids],
        add_roles=AsyncMock(),
        remove_roles=AsyncMock(),
    )


def _guild(members: list[SimpleNamespace], role_ids: set[int]) -> MagicMock:
    roles = {rid: _role(rid) for rid in role_ids}
    guild = MagicMock()
    guild.id = GUILD_ID
    guild.chunked = True
    guild.members = members
    guild.get_member.side_effect = lambda mid: next((m for m in members if m.id == mid), None)
    guild.get_role.side_effect = roles.get
    return guild


@pytest.fixture(autouse=True)
def discord_settings(settings: Any) -> None:
    settings.COMPETITION_GUILD_ID = GUILD_ID
    settings.BLUETEAM_ROLE_ID = BLUETEAM
    settings.GROUP_ROLE_MAPPING = {"WCComps_Ops": OPS_ROLE}


async def _link(discord_id: int, groups: list[str], team: Team | None = None) -> None:
    user = await User.objects.acreate(username=f"u{discord_id}")
    await UserGroups.objects.acreate(user=user, authentik_id=f"uid{discord_id}", groups=groups)
    await DiscordLink.objects.acreate(
        discord_id=discord_id, discord_username=user.username, user=user, team=team, is_active=True
    )


async def _sync(guild: MagicMock, *, dry_run: bool = False) -> dict[str, Any]:
    bot = MagicMock()
    bot.get_guild.return_value = guild
    with patch.object(DiscordManager, "assign_team_role", new_callable=AsyncMock, return_value=True) as assign:
        stats = await AuthentikRoleSyncManager(bot).sync_roles(dry_run=dry_run)
    return {"stats": stats, "assign": assign}


async def test_roles_arrive_on_the_first_sync_after_joining() -> None:
    team = await Team.objects.acreate(team_number=5, team_name="Team 05", discord_role_id=TEAM_ROLE)
    await _link(STAFF_ID, ["WCComps_Ops"])
    await _link(TEAM_MEMBER_ID, [team.authentik_group], team=team)

    before = await _sync(_guild([], {OPS_ROLE, BLUETEAM, TEAM_ROLE}))
    assert before["stats"]["roles_added"] == 0
    before["assign"].assert_not_awaited()

    staff, member = _member(STAFF_ID), _member(TEAM_MEMBER_ID)
    after = await _sync(_guild([staff, member], {OPS_ROLE, BLUETEAM, TEAM_ROLE}))

    staff.add_roles.assert_awaited_once()
    assert staff.add_roles.await_args.args[0].id == OPS_ROLE
    after["assign"].assert_awaited_once_with(member, 5)
    assert after["stats"]["roles_added"] == 2


async def test_team_member_holding_both_roles_is_left_alone() -> None:
    team = await Team.objects.acreate(team_number=6, team_name="Team 06", discord_role_id=TEAM_ROLE)
    await _link(TEAM_MEMBER_ID, [team.authentik_group], team=team)

    result = await _sync(_guild([_member(TEAM_MEMBER_ID, TEAM_ROLE, BLUETEAM)], {BLUETEAM, TEAM_ROLE}))

    result["assign"].assert_not_awaited()
    assert result["stats"]["roles_added"] == 0


async def test_missing_blueteam_role_does_not_count_as_missing_every_pass() -> None:
    team = await Team.objects.acreate(team_number=7, team_name="Team 07", discord_role_id=TEAM_ROLE)
    await _link(TEAM_MEMBER_ID, [team.authentik_group], team=team)

    result = await _sync(_guild([_member(TEAM_MEMBER_ID, TEAM_ROLE)], {TEAM_ROLE}))

    result["assign"].assert_not_awaited()


async def test_dry_run_reports_team_roles_without_assigning() -> None:
    team = await Team.objects.acreate(team_number=8, team_name="Team 08", discord_role_id=TEAM_ROLE)
    await _link(TEAM_MEMBER_ID, [team.authentik_group], team=team)

    result = await _sync(_guild([_member(TEAM_MEMBER_ID)], {BLUETEAM, TEAM_ROLE}), dry_run=True)

    result["assign"].assert_not_awaited()
    assert any("[DRY RUN]" in c and "team 08" in c for c in result["stats"]["changes"])


async def test_assign_team_role_sets_up_a_team_that_has_no_role_yet() -> None:
    await Team.objects.acreate(team_number=9, team_name="Team 09")
    member = _member(TEAM_MEMBER_ID)
    guild = _guild([member], {BLUETEAM})
    team_role = _role(TEAM_ROLE)
    manager = DiscordManager(guild)

    with patch.object(manager, "setup_team_infrastructure", AsyncMock(return_value=(team_role, None))) as setup:
        assert await manager.assign_team_role(member, 9)

    setup.assert_awaited_once_with(9)
    assert [r.id for r in member.add_roles.await_args.args] == [TEAM_ROLE, BLUETEAM]


async def test_refresh_loop_syncs_roles_after_refreshing_groups() -> None:
    calls: list[str] = []
    cog = authentik_groups.AuthentikGroupsCog.__new__(authentik_groups.AuthentikGroupsCog)
    cog.bot = MagicMock()

    async def refresh() -> None:
        calls.append("refresh")

    async def sync(bot: Any) -> None:
        calls.append("sync")

    with (
        patch.object(authentik_groups, "refresh_groups_now", refresh),
        patch.object(authentik_groups, "sync_roles_now", sync),
    ):
        await authentik_groups.AuthentikGroupsCog.refresh_task.coro(cog)

    assert calls == ["refresh", "sync"]


async def test_role_sync_failure_is_logged_not_raised() -> None:
    with patch.object(AuthentikRoleSyncManager, "sync_roles", AsyncMock(side_effect=RuntimeError("discord down"))):
        await authentik_groups.sync_roles_now(MagicMock())
