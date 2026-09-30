"""Tests for end competition workflow."""

from typing import Any

import pytest

from bot.discord_manager import DiscordManager
from team.models import Team


@pytest.fixture
def teams_with_discord_roles(db):
    """Create test teams with Discord role and category IDs."""

    async def _create_teams(count: int = 2) -> list[Team]:
        teams = []
        for i in range(1, count + 1):
            team = await Team.objects.acreate(
                team_number=i,
                team_name=f"BlueTeam{i:02d}",
                discord_role_id=5000 + i,
                discord_category_id=6000 + i,
            )
            teams.append(team)
        return teams

    return _create_teams


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
class TestEndCompetition:
    """Tests for end competition workflow."""

    async def test_removes_team_roles_from_all_teams(self, mock_discord_guild: Any, teams_with_discord_roles) -> None:
        """Should remove roles from all teams with Discord role IDs."""
        await teams_with_discord_roles(2)

        manager = DiscordManager(mock_discord_guild)
        removed_count = await manager.remove_all_team_roles()

        assert removed_count == 2

    async def test_calls_remove_roles_for_each_member(self, mock_discord_guild: Any, teams_with_discord_roles) -> None:
        """Should call remove_roles on each team member."""
        await teams_with_discord_roles(2)

        manager = DiscordManager(mock_discord_guild)
        await manager.remove_all_team_roles()

        member1 = mock_discord_guild.get_role(5001).members[0]
        member2 = mock_discord_guild.get_role(5002).members[0]

        assert member1.remove_roles.called
        assert member2.remove_roles.called

    async def test_removes_both_team_and_blueteam_roles(
        self, mock_discord_guild: Any, teams_with_discord_roles
    ) -> None:
        """Should remove both specific team role and general Blueteam role."""
        await teams_with_discord_roles(1)

        manager = DiscordManager(mock_discord_guild)
        await manager.remove_all_team_roles()

        member = mock_discord_guild.get_role(5001).members[0]
        first_call_args = member.remove_roles.call_args_list[0][0]
        role_names = [role.name for role in first_call_args]

        assert "Team 01" in role_names
        assert "Blueteam" in role_names

    async def test_clears_discord_ids_after_removal(self, mock_discord_guild: Any, teams_with_discord_roles) -> None:
        """Should clear discord_role_id and discord_category_id after removal."""
        teams = await teams_with_discord_roles(2)

        manager = DiscordManager(mock_discord_guild)
        await manager.remove_all_team_roles()
        await Team.objects.all().aupdate(discord_category_id=None, discord_role_id=None)

        for team in teams:
            await team.arefresh_from_db()
            assert team.discord_role_id is None
            assert team.discord_category_id is None

    async def test_handles_no_teams_gracefully(self, mock_discord_guild: Any, db) -> None:
        """Should handle case with no teams gracefully."""
        manager = DiscordManager(mock_discord_guild)
        removed_count = await manager.remove_all_team_roles()

        assert removed_count == 0

    async def test_handles_teams_without_discord_roles(self, mock_discord_guild: Any, db) -> None:
        """Should skip teams without discord_role_id."""
        await Team.objects.acreate(
            team_number=1,
            team_name="TeamWithoutRole",
            discord_role_id=None,
            discord_category_id=None,
        )

        manager = DiscordManager(mock_discord_guild)
        removed_count = await manager.remove_all_team_roles()

        assert removed_count == 0

    async def test_handles_missing_role_in_discord(self, mock_discord_guild: Any, db) -> None:
        """Should handle case where role doesn't exist in Discord."""
        await Team.objects.acreate(
            team_number=1,
            team_name="TeamWithMissingRole",
            discord_role_id=99999,  # Non-existent role
            discord_category_id=6001,
        )

        # Make get_role return None for non-existent role
        original_get_role = mock_discord_guild.get_role

        def get_role_with_missing(role_id):
            if role_id == 99999:
                return None
            return original_get_role(role_id)

        mock_discord_guild.get_role = get_role_with_missing

        manager = DiscordManager(mock_discord_guild)
        # Should not raise, should handle gracefully
        removed_count = await manager.remove_all_team_roles()
        assert removed_count == 0
