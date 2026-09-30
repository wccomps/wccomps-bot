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
