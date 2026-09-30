"""Tests for Discord manager functionality."""

from unittest.mock import AsyncMock, MagicMock

import discord
import pytest
import pytest_asyncio
from django.conf import settings

from bot.discord_manager import DiscordManager
from team.models import Team


@pytest_asyncio.fixture
async def team(db):
    """Create a test team with unique team number."""
    import random

    team_num = random.randint(1, 50)  # Valid range: 1-50
    return await Team.objects.acreate(
        team_number=team_num,
        team_name=f"Test Team {team_num}",
        authentik_group=f"WCComps_Team{team_num:02d}",
    )


@pytest.fixture
def mock_guild_with_base_roles():
    """Create a mock Discord guild with simplified setup."""
    guild = MagicMock(spec=discord.Guild)
    guild.id = 525435725123158026
    guild.name = "Test Guild"
    guild.roles = []
    guild.categories = []

    # Counters to ensure unique IDs even after removal
    role_counter = {"value": 0}
    category_counter = {"value": 0}

    # Setup get_role method with dynamic lookup
    def get_role_by_id(role_id):
        for role in guild.roles:
            if role.id == role_id:
                return role
        return None

    guild.get_role = MagicMock(side_effect=get_role_by_id)

    # Simplified create_role
    async def create_role(**kwargs):
        role = MagicMock(spec=discord.Role)
        role.name = kwargs.get("name", "New Role")
        role.color = kwargs.get("color", discord.Color.default())
        role.id = 6000 + role_counter["value"]
        role.position = len(guild.roles)
        role.edit = AsyncMock()
        guild.roles.append(role)
        role_counter["value"] += 1
        return role

    guild.create_role = AsyncMock(side_effect=create_role)

    # Simplified create_category
    async def create_category(**kwargs):
        category = MagicMock(spec=discord.CategoryChannel)
        category.name = kwargs.get("name", "New Category")
        category.id = 7000 + category_counter["value"]
        category.position = len(guild.categories)
        category.edit = AsyncMock()
        category.create_text_channel = AsyncMock(
            return_value=MagicMock(spec=discord.TextChannel, id=8000 + category_counter["value"])
        )
        category.create_voice_channel = AsyncMock(
            return_value=MagicMock(spec=discord.VoiceChannel, id=9000 + category_counter["value"])
        )
        guild.categories.append(category)
        category_counter["value"] += 1
        return category

    guild.create_category = AsyncMock(side_effect=create_category)

    return guild


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
class TestSetupTeamInfrastructure:
    """Test setup_team_infrastructure method."""

    async def test_creates_role_with_correct_name(self, team, mock_guild_with_base_roles):
        """Test that a role is created with format 'Team XX'."""
        manager = DiscordManager(mock_guild_with_base_roles)

        role, _category = await manager.setup_team_infrastructure(team.team_number)

        assert role is not None
        assert role.name == f"Team {team.team_number:02d}"

    async def test_creates_category_with_correct_name(self, team, mock_guild_with_base_roles):
        """Test that a category is created with format 'team XX'."""
        manager = DiscordManager(mock_guild_with_base_roles)

        _role, category = await manager.setup_team_infrastructure(team.team_number)

        assert category is not None
        assert category.name == f"team {team.team_number:02d}"

    async def test_white_and_orange_team_see_the_category_via_their_configured_roles(
        self, team, mock_guild_with_base_roles, monkeypatch
    ):
        guild = mock_guild_with_base_roles
        white, orange = MagicMock(spec=discord.Role, id=4101), MagicMock(spec=discord.Role, id=4102)
        white.name, orange.name = "Judges", "Volunteers"
        guild.roles.extend([white, orange])
        monkeypatch.setattr(settings, "WHITETEAM_ROLE_ID", 4101)
        monkeypatch.setattr(settings, "ORANGETEAM_ROLE_ID", 4102)

        await DiscordManager(guild).setup_team_infrastructure(team.team_number)

        overwrites = guild.create_category.await_args.kwargs["overwrites"]
        assert overwrites[white].read_messages and overwrites[orange].send_messages

    async def test_unset_role_ids_grant_nothing_even_if_a_role_has_the_old_name(
        self, team, mock_guild_with_base_roles, monkeypatch
    ):
        guild = mock_guild_with_base_roles
        white = MagicMock(spec=discord.Role, id=4101)
        white.name = "White Team"
        guild.roles.append(white)
        monkeypatch.setattr(settings, "WHITETEAM_ROLE_ID", 0)

        await DiscordManager(guild).setup_team_infrastructure(team.team_number)

        assert white not in guild.create_category.await_args.kwargs["overwrites"]

    async def test_creates_channels_within_category(self, team, mock_guild_with_base_roles):
        """Test that text and voice channels are created."""
        manager = DiscordManager(mock_guild_with_base_roles)

        _role, category = await manager.setup_team_infrastructure(team.team_number)

        # Verify create_text_channel was called with correct name
        category.create_text_channel.assert_called()
        call_args = category.create_text_channel.call_args
        expected_text = f"team{team.team_number:02d}-chat"
        assert expected_text in call_args[0] or call_args[0][0] == expected_text

        # Verify create_voice_channel was called with correct name
        category.create_voice_channel.assert_called()
        call_args = category.create_voice_channel.call_args
        expected_voice = f"team{team.team_number:02d}-voice"
        assert expected_voice in call_args[0] or call_args[0][0] == expected_voice

    async def test_returns_none_when_team_not_found(self, mock_guild_with_base_roles):
        """Test that method returns None when team doesn't exist."""
        guild = mock_guild_with_base_roles
        manager = DiscordManager(guild)

        # Track that guild methods are NOT called for non-existent team
        initial_role_count = len(guild.roles)
        initial_category_count = len(guild.categories)

        role, category = await manager.setup_team_infrastructure(999)

        assert role is None
        assert category is None
        # Verify no resources were created
        assert len(guild.roles) == initial_role_count
        assert len(guild.categories) == initial_category_count
        # Verify create_role was never called
        guild.create_role.assert_not_called()

    async def test_idempotent_no_duplicates_created(self, team, mock_guild_with_base_roles):
        """Test that running setup twice does not create duplicate roles or categories."""
        manager = DiscordManager(mock_guild_with_base_roles)
        guild = mock_guild_with_base_roles

        # Track initial counts
        initial_role_count = len(guild.roles)
        initial_category_count = len(guild.categories)

        # First setup
        role1, category1 = await manager.setup_team_infrastructure(team.team_number)
        assert role1 is not None
        assert category1 is not None

        # Verify one role and one category were created
        assert len(guild.roles) == initial_role_count + 1
        assert len(guild.categories) == initial_category_count + 1

        # Second setup (should be idempotent)
        role2, category2 = await manager.setup_team_infrastructure(team.team_number)
        assert role2 is not None
        assert category2 is not None

        # Verify NO additional roles or categories were created
        assert len(guild.roles) == initial_role_count + 1
        assert len(guild.categories) == initial_category_count + 1

        # Verify same instances returned
        assert role2.id == role1.id
        assert category2.id == category1.id

    async def test_self_healing_infrastructure(self, team, mock_guild_with_base_roles):
        """Test self-healing recreates missing Discord infrastructure (roles/categories)."""
        manager = DiscordManager(mock_guild_with_base_roles)
        guild = mock_guild_with_base_roles

        # Initial setup - create both role and category
        role1, category1 = await manager.setup_team_infrastructure(team.team_number)
        assert role1 is not None
        assert category1 is not None
        original_role_id = role1.id
        original_category_id = category1.id

        # Test 1: Missing category only - should recreate category, keep role
        guild.categories.remove(category1)
        role_count_before = len(guild.roles)

        role2, category2 = await manager.setup_team_infrastructure(team.team_number)

        assert role2.id == original_role_id  # Same role
        assert len(guild.roles) == role_count_before  # No new role created
        assert category2.id != original_category_id  # New category

        await team.arefresh_from_db()
        assert team.discord_role_id == original_role_id
        assert team.discord_category_id == category2.id

        # Test 2: Missing role only - should recreate role, keep category
        guild.roles.remove(role2)
        category_count_before = len(guild.categories)

        role3, category3 = await manager.setup_team_infrastructure(team.team_number)

        assert category3.id == category2.id  # Same category
        assert len(guild.categories) == category_count_before  # No new category
        assert role3.id != original_role_id  # New role

        await team.arefresh_from_db()
        assert team.discord_role_id == role3.id
        assert team.discord_category_id == category2.id

        # Test 3: Both missing - should recreate both
        guild.roles.remove(role3)
        guild.categories.remove(category3)
        role_count = len(guild.roles)
        category_count = len(guild.categories)

        role4, category4 = await manager.setup_team_infrastructure(team.team_number)

        assert role4.id != role3.id  # New role
        assert category4.id != category3.id  # New category
        assert len(guild.roles) == role_count + 1
        assert len(guild.categories) == category_count + 1

        await team.arefresh_from_db()
        assert team.discord_role_id == role4.id
        assert team.discord_category_id == category4.id


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
class TestAssignTeamRole:
    """Test assign_team_role method."""

    async def test_assigns_team_role_successfully(self, team, mock_guild_with_base_roles):
        """Test successfully assigning team role to a member."""
        guild = mock_guild_with_base_roles
        manager = DiscordManager(guild)

        # Setup team infrastructure first
        role, _ = await manager.setup_team_infrastructure(team.team_number)
        assert role is not None

        # Refresh team from database to get updated role ID
        await team.arefresh_from_db()

        # Create mock member
        member = MagicMock(spec=discord.Member)
        member.add_roles = AsyncMock()

        # Test assignment
        result = await manager.assign_team_role(member, team.team_number)

        assert result is True
        # Verify the exact role and reason were passed
        member.add_roles.assert_called_once_with(role, reason="WCComps team assignment")

    async def test_assigns_team_role_and_blueteam(self, team, mock_guild_with_base_roles):
        """Test that both team role and Blueteam role are assigned."""
        guild = mock_guild_with_base_roles
        manager = DiscordManager(guild)

        # Setup team infrastructure
        role, _ = await manager.setup_team_infrastructure(team.team_number)

        # Add Blueteam role to guild
        blueteam_role = MagicMock(spec=discord.Role)
        blueteam_role.name = "Blue Team (renamed)"
        blueteam_role.id = settings.BLUETEAM_ROLE_ID
        guild.roles.append(blueteam_role)

        member = MagicMock(spec=discord.Member)
        member.add_roles = AsyncMock()

        result = await manager.assign_team_role(member, team.team_number)

        assert result is True
        # Verify both roles were assigned
        call_args = member.add_roles.call_args[0]
        assert len(call_args) == 2
        assert role in call_args
        assert blueteam_role in call_args

    async def test_assign_team_role_team_not_found(self, mock_guild_with_base_roles):
        """Test assigning role when team doesn't exist."""
        manager = DiscordManager(mock_guild_with_base_roles)
        member = MagicMock(spec=discord.Member)
        member.add_roles = AsyncMock()

        result = await manager.assign_team_role(member, 999)

        assert result is False
        member.add_roles.assert_not_called()

    async def test_assign_team_role_sets_up_a_team_without_a_role(self, team, mock_guild_with_base_roles):
        """A member who joins before their team's infrastructure exists still gets the role."""
        manager = DiscordManager(mock_guild_with_base_roles)
        member = MagicMock(spec=discord.Member)
        member.add_roles = AsyncMock()

        result = await manager.assign_team_role(member, team.team_number)

        await team.arefresh_from_db()
        assert result is True
        assert team.discord_role_id is not None
        assert member.add_roles.await_args.args[0].id == team.discord_role_id

    async def test_assign_team_role_replaces_a_deleted_role(self, team, mock_guild_with_base_roles):
        """A stored role ID the guild no longer has is set up again rather than failing forever."""
        manager = DiscordManager(mock_guild_with_base_roles)
        member = MagicMock(spec=discord.Member)
        member.add_roles = AsyncMock()
        team.discord_role_id = 99999
        await team.asave()

        result = await manager.assign_team_role(member, team.team_number)

        await team.arefresh_from_db()
        assert result is True
        assert team.discord_role_id != 99999
        assert member.add_roles.await_args.args[0].id == team.discord_role_id

    async def test_assign_team_role_permission_denied(self, team, mock_guild_with_base_roles):
        """Test handling Forbidden error when assigning role."""
        guild = mock_guild_with_base_roles
        manager = DiscordManager(guild)

        # Setup team infrastructure
        role, _ = await manager.setup_team_infrastructure(team.team_number)

        member = MagicMock(spec=discord.Member)
        member.add_roles = AsyncMock(side_effect=discord.errors.Forbidden(MagicMock(), ""))

        result = await manager.assign_team_role(member, team.team_number)

        assert result is False
        # Verify add_roles was called but raised Forbidden
        member.add_roles.assert_called_once_with(role, reason="WCComps team assignment")


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
class TestRemoveTeamRole:
    """Test remove_team_role method."""

    async def test_removes_team_role_successfully(self, team, mock_guild_with_base_roles):
        """Test successfully removing team role from a member."""
        guild = mock_guild_with_base_roles
        manager = DiscordManager(guild)

        # Setup team infrastructure
        role, _ = await manager.setup_team_infrastructure(team.team_number)

        member = MagicMock(spec=discord.Member)
        member.roles = [role]
        member.remove_roles = AsyncMock()

        result = await manager.remove_team_role(member, team.team_number)

        assert result is True
        # Verify the exact role and reason were passed
        member.remove_roles.assert_called_once_with(role, reason="WCComps team removal")

    async def test_removes_team_role_and_blueteam(self, team, mock_guild_with_base_roles):
        """Test that both team role and Blueteam role are removed."""
        guild = mock_guild_with_base_roles
        manager = DiscordManager(guild)

        # Setup team infrastructure
        role, _ = await manager.setup_team_infrastructure(team.team_number)

        # Add Blueteam role
        blueteam_role = MagicMock(spec=discord.Role)
        blueteam_role.name = "Blue Team (renamed)"
        blueteam_role.id = settings.BLUETEAM_ROLE_ID
        guild.roles.append(blueteam_role)

        member = MagicMock(spec=discord.Member)
        member.roles = [role, blueteam_role]
        member.remove_roles = AsyncMock()

        result = await manager.remove_team_role(member, team.team_number)

        assert result is True
        # Verify both roles were removed
        call_args = member.remove_roles.call_args[0]
        assert len(call_args) == 2
        assert role in call_args
        assert blueteam_role in call_args

    async def test_remove_team_role_team_not_found(self, mock_guild_with_base_roles):
        """Test removing role when team doesn't exist."""
        manager = DiscordManager(mock_guild_with_base_roles)
        member = MagicMock(spec=discord.Member)
        member.remove_roles = AsyncMock()

        result = await manager.remove_team_role(member, 999)

        assert result is False
        # Verify no roles were removed
        member.remove_roles.assert_not_called()

    async def test_remove_team_role_no_discord_role_id(self, team, mock_guild_with_base_roles):
        """Test removing role when team has no discord_role_id."""
        manager = DiscordManager(mock_guild_with_base_roles)
        member = MagicMock(spec=discord.Member)
        member.remove_roles = AsyncMock()

        result = await manager.remove_team_role(member, team.team_number)

        assert result is False
        # Verify no roles were removed
        member.remove_roles.assert_not_called()

    async def test_remove_team_role_permission_denied(self, team, mock_guild_with_base_roles):
        """Test handling Forbidden error when removing role."""
        guild = mock_guild_with_base_roles
        manager = DiscordManager(guild)

        # Setup team infrastructure
        role, _ = await manager.setup_team_infrastructure(team.team_number)

        member = MagicMock(spec=discord.Member)
        member.roles = [role]
        member.remove_roles = AsyncMock(side_effect=discord.errors.Forbidden(MagicMock(), ""))

        result = await manager.remove_team_role(member, team.team_number)

        assert result is False
        # Verify remove_roles was called but raised Forbidden
        member.remove_roles.assert_called_once_with(role, reason="WCComps team removal")


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
class TestRemoveAllTeamRoles:
    """Test remove_all_team_roles method."""

    async def test_removes_all_team_roles(self, mock_guild_with_base_roles):
        """Test removing all team roles from all members."""
        guild = mock_guild_with_base_roles
        manager = DiscordManager(guild)

        # Create multiple teams
        team1 = await Team.objects.acreate(
            team_number=1,
            team_name="Team A",
            authentik_group="WCComps_BlueTeam01",
        )
        team2 = await Team.objects.acreate(
            team_number=2,
            team_name="Team B",
            authentik_group="WCComps_BlueTeam02",
        )

        # Setup infrastructure for both teams
        role1, _ = await manager.setup_team_infrastructure(team1.team_number)
        role2, _ = await manager.setup_team_infrastructure(team2.team_number)

        # Create mock members
        member1 = MagicMock(spec=discord.Member)
        member1.remove_roles = AsyncMock()
        member2 = MagicMock(spec=discord.Member)
        member2.remove_roles = AsyncMock()

        # Assign members to roles
        role1.members = [member1]
        role2.members = [member2]

        result = await manager.remove_all_team_roles()

        # Verify roles were removed with correct arguments
        assert result == 2
        member1.remove_roles.assert_called_once_with(role1, reason="Competition ended")
        member2.remove_roles.assert_called_once_with(role2, reason="Competition ended")

    async def test_removes_blueteam_role(self, mock_guild_with_base_roles):
        """Test that Blueteam role is removed from all members."""
        guild = mock_guild_with_base_roles
        manager = DiscordManager(guild)

        # Create team
        team = await Team.objects.acreate(
            team_number=3,
            team_name="Team C",
            authentik_group="WCComps_BlueTeam03",
        )
        role, _ = await manager.setup_team_infrastructure(team.team_number)

        # Add Blueteam role
        blueteam_role = MagicMock(spec=discord.Role)
        blueteam_role.name = "Blue Team (renamed)"
        blueteam_role.id = settings.BLUETEAM_ROLE_ID

        member = MagicMock(spec=discord.Member)
        member.remove_roles = AsyncMock()
        member.roles = [role, blueteam_role]

        role.members = [member]
        blueteam_role.members = [member]
        guild.roles.append(blueteam_role)

        result = await manager.remove_all_team_roles()

        # Verify both team role and Blueteam were removed
        assert result == 1
        assert member.remove_roles.call_count == 2
        # First call removes both team role and Blueteam
        assert member.remove_roles.call_args_list[0] == (
            (role, blueteam_role),
            {"reason": "Competition ended"},
        )
        # Second call removes Blueteam from remaining members
        assert member.remove_roles.call_args_list[1] == (
            (blueteam_role,),
            {"reason": "Competition ended"},
        )

    async def test_handles_permission_errors(self, mock_guild_with_base_roles):
        """Test handling permission errors when removing roles."""
        guild = mock_guild_with_base_roles
        manager = DiscordManager(guild)

        # Create team
        team = await Team.objects.acreate(
            team_number=4,
            team_name="Team D",
            authentik_group="WCComps_BlueTeam04",
        )
        role, _ = await manager.setup_team_infrastructure(team.team_number)

        # Create member that raises Forbidden error
        member = MagicMock(spec=discord.Member)
        member.remove_roles = AsyncMock(side_effect=discord.errors.Forbidden(MagicMock(), ""))

        role.members = [member]

        # Should not raise exception, just log error
        result = await manager.remove_all_team_roles()

        # Count is 0 because the operation failed
        assert result == 0
