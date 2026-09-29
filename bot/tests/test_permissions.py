"""Tests for permission checking functionality."""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, Mock, patch

import discord
import pytest
from django.contrib.auth.models import User

from bot.permissions import (
    _get_authentik_groups_sync,
    _permission_cache,
    can_manage_tickets_async,
    can_support_tickets_async,
    check_admin,
    check_gold_team,
    check_ticketing_admin,
    check_ticketing_support,
    is_admin_async,
    is_gold_team_async,
)
from core.models import UserGroups
from team.models import DiscordLink, Team


@pytest.mark.django_db(transaction=True)
class TestPermissionCache:
    """Test permission caching logic."""

    def teardown_method(self) -> None:
        """Clear permission cache after each test."""
        _permission_cache.clear()

    def test_cache_hit_returns_cached_groups(self) -> None:
        """Test that valid cache entries are returned."""
        discord_id = 123456789
        groups = ["WCComps_Discord_Admin"]

        _permission_cache[discord_id] = {
            "groups": groups,
            "expires_at": datetime.now(UTC) + timedelta(minutes=5),
        }

        result = _get_authentik_groups_sync(discord_id)

        assert result == groups

    def test_cache_miss_queries_database(self) -> None:
        """Test that cache miss queries database."""
        discord_id = 987654321

        result = _get_authentik_groups_sync(discord_id)

        assert result == []
        assert discord_id not in _permission_cache

    def test_expired_cache_is_removed(self) -> None:
        """Test that expired cache entries are removed."""
        discord_id = 123456789
        groups = ["WCComps_Discord_Admin"]

        _permission_cache[discord_id] = {
            "groups": groups,
            "expires_at": datetime.now(UTC) - timedelta(minutes=1),
        }

        _get_authentik_groups_sync(discord_id)

        assert discord_id not in _permission_cache


@pytest.mark.django_db(transaction=True)
class TestGetAuthentikGroups:
    """Test Authentik group retrieval."""

    def teardown_method(self) -> None:
        """Clear permission cache after each test."""
        _permission_cache.clear()

    def test_no_discord_link_returns_empty_list(self) -> None:
        """Test that users without DiscordLink return empty list."""
        result = _get_authentik_groups_sync(999999999)

        assert result == []

    def test_discord_link_without_usergroups(self) -> None:
        """Test DiscordLink without UserGroups returns empty list."""
        team = Team.objects.create(team_number=1, team_name="Test Team")
        user = User.objects.create_user(username="nonexistent_user")

        discord_link = DiscordLink.objects.create(
            team=team,
            discord_id=111111111,
            discord_username="testuser",
            user=user,
            is_active=True,
        )

        result = _get_authentik_groups_sync(discord_link.discord_id)

        assert result == []

    def test_groups_from_usergroups(self) -> None:
        """Test extracting groups from UserGroups model."""
        team = Team.objects.create(team_number=2, team_name="Test Team 2")

        user = User.objects.create_user(username="testuser2")

        UserGroups.objects.create(
            user=user,
            authentik_id="test-uid-2",
            groups=["WCComps_Discord_Admin", "WCComps_BlueTeam02"],
        )

        discord_link = DiscordLink.objects.create(
            team=team,
            discord_id=222222222,
            discord_username="testuser2",
            user=user,
            is_active=True,
        )

        result = _get_authentik_groups_sync(discord_link.discord_id)

        assert "WCComps_Discord_Admin" in result
        assert "WCComps_BlueTeam02" in result

    def test_groups_cached_after_first_query(self) -> None:
        """Test that groups are cached after first query."""
        team = Team.objects.create(team_number=5, team_name="Test Team 5")

        user = User.objects.create_user(username="testuser5")

        UserGroups.objects.create(
            user=user,
            authentik_id="test-uid-5",
            groups=["WCComps_GoldTeam"],
        )

        discord_link = DiscordLink.objects.create(
            team=team,
            discord_id=555555555,
            discord_username="testuser5",
            user=user,
            is_active=True,
        )

        # First call queries database
        result1 = _get_authentik_groups_sync(discord_link.discord_id)

        # Second call uses cache
        result2 = _get_authentik_groups_sync(discord_link.discord_id)

        assert result1 == result2
        assert discord_link.discord_id in _permission_cache

    def test_database_error_returns_empty_list(self) -> None:
        """Test that database errors return empty list."""
        with patch("bot.permissions.DiscordLink.objects.filter") as mock_filter:
            mock_filter.side_effect = Exception("Database connection failed")

            result = _get_authentik_groups_sync(666666666)

            assert result == []


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
class TestPermissionChecks:
    """Test async permission checking functions."""

    def teardown_method(self) -> None:
        """Clear permission cache after each test."""
        _permission_cache.clear()

    async def test_is_admin_async_with_admin_group(self) -> None:
        """Test is_admin_async returns True for admin users."""
        team = await Team.objects.acreate(team_number=10, team_name="Admin Team")

        user = await User.objects.acreate(username="admin_user")

        await UserGroups.objects.acreate(
            user=user,
            authentik_id="admin-uid",
            groups=["WCComps_Discord_Admin"],
        )

        await DiscordLink.objects.acreate(
            team=team,
            discord_id=1010101010,
            discord_username="admin_user",
            user=user,
            is_active=True,
        )

        interaction = Mock(spec=discord.Interaction)
        interaction.user = Mock()
        interaction.user.id = 1010101010

        result = await is_admin_async(interaction)

        assert result is True

    async def test_is_admin_async_without_admin_group(self) -> None:
        """Test is_admin_async returns False for non-admin users."""
        interaction = Mock(spec=discord.Interaction)
        interaction.user = Mock()
        interaction.user.id = 9999999999

        result = await is_admin_async(interaction)

        assert result is False

    async def test_is_admin_async_handles_exceptions(self) -> None:
        """Test is_admin_async handles exceptions gracefully."""
        interaction = Mock(spec=discord.Interaction)
        interaction.user = Mock()
        interaction.user.id = 8888888888

        with patch(
            "bot.permissions.get_authentik_groups_async",
            side_effect=Exception("Test error"),
        ):
            result = await is_admin_async(interaction)

            assert result is False

    async def test_can_manage_tickets_async_with_admin(self) -> None:
        """Test can_manage_tickets_async returns True for admins."""
        team = await Team.objects.acreate(team_number=11, team_name="Admin Team")

        user = await User.objects.acreate(username="admin_user2")

        await UserGroups.objects.acreate(
            user=user,
            authentik_id="admin-uid-2",
            groups=["WCComps_Discord_Admin"],
        )

        await DiscordLink.objects.acreate(
            team=team,
            discord_id=1111111111,
            discord_username="admin_user2",
            user=user,
            is_active=True,
        )

        interaction = Mock(spec=discord.Interaction)
        interaction.user = Mock()
        interaction.user.id = 1111111111

        result = await can_manage_tickets_async(interaction)

        assert result is True

    async def test_can_manage_tickets_async_with_ticketing_admin(self) -> None:
        """Test can_manage_tickets_async returns True for ticketing admins."""
        team = await Team.objects.acreate(team_number=12, team_name="Ticketing Team")

        user = await User.objects.acreate(username="ticketing_admin")

        await UserGroups.objects.acreate(
            user=user,
            authentik_id="ticketing-admin-uid",
            groups=["WCComps_Ticketing_Admin"],
        )

        await DiscordLink.objects.acreate(
            team=team,
            discord_id=1212121212,
            discord_username="ticketing_admin",
            user=user,
            is_active=True,
        )

        interaction = Mock(spec=discord.Interaction)
        interaction.user = Mock()
        interaction.user.id = 1212121212

        result = await can_manage_tickets_async(interaction)

        assert result is True

    async def test_can_manage_tickets_async_handles_exceptions(self) -> None:
        """Test can_manage_tickets_async handles exceptions gracefully."""
        interaction = Mock(spec=discord.Interaction)
        interaction.user = Mock()
        interaction.user.id = 1313131313

        with (
            patch("bot.permissions.is_admin_async", return_value=False),
            patch(
                "bot.permissions.get_authentik_groups_async",
                side_effect=Exception("Test error"),
            ),
        ):
            result = await can_manage_tickets_async(interaction)

            assert result is False

    async def test_can_support_tickets_async_with_support_group(self) -> None:
        """Test can_support_tickets_async returns True for support users."""
        team = await Team.objects.acreate(team_number=13, team_name="Support Team")

        user = await User.objects.acreate(username="support_user")

        await UserGroups.objects.acreate(
            user=user,
            authentik_id="support-uid",
            groups=["WCComps_Ticketing_Support"],
        )

        await DiscordLink.objects.acreate(
            team=team,
            discord_id=1414141414,
            discord_username="support_user",
            user=user,
            is_active=True,
        )

        interaction = Mock(spec=discord.Interaction)
        interaction.user = Mock()
        interaction.user.id = 1414141414

        result = await can_support_tickets_async(interaction)

        assert result is True

    async def test_can_support_tickets_async_handles_exceptions(self) -> None:
        """Test can_support_tickets_async handles exceptions gracefully."""
        interaction = Mock(spec=discord.Interaction)
        interaction.user = Mock()
        interaction.user.id = 1515151515

        with (
            patch("bot.permissions.is_admin_async", return_value=False),
            patch("bot.permissions.can_manage_tickets_async", return_value=False),
            patch(
                "bot.permissions.get_authentik_groups_async",
                side_effect=Exception("Test error"),
            ),
        ):
            result = await can_support_tickets_async(interaction)

            assert result is False

    async def test_is_gold_team_async_with_gold_team_group(self) -> None:
        """Test is_gold_team_async returns True for gold team users."""
        team = await Team.objects.acreate(team_number=14, team_name="Gold Team")

        user = await User.objects.acreate(username="gold_user")

        await UserGroups.objects.acreate(
            user=user,
            authentik_id="gold-uid",
            groups=["WCComps_GoldTeam"],
        )

        await DiscordLink.objects.acreate(
            team=team,
            discord_id=1616161616,
            discord_username="gold_user",
            user=user,
            is_active=True,
        )

        interaction = Mock(spec=discord.Interaction)
        interaction.user = Mock()
        interaction.user.id = 1616161616

        result = await is_gold_team_async(interaction)

        assert result is True

    async def test_is_gold_team_async_handles_exceptions(self) -> None:
        """Test is_gold_team_async handles exceptions gracefully."""
        interaction = Mock(spec=discord.Interaction)
        interaction.user = Mock()
        interaction.user.id = 1717171717

        with (
            patch("bot.permissions.is_admin_async", return_value=False),
            patch(
                "bot.permissions.get_authentik_groups_async",
                side_effect=Exception("Test error"),
            ),
        ):
            result = await is_gold_team_async(interaction)

            assert result is False


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
class TestPermissionCheckFunctions:
    """Test permission check functions for use with @app_commands.check()."""

    def teardown_method(self) -> None:
        """Clear permission cache after each test."""
        _permission_cache.clear()

    async def test_check_admin_allows_admin(self) -> None:
        """Test check_admin allows admin users."""
        team = await Team.objects.acreate(team_number=20, team_name="Admin Team")

        user = await User.objects.acreate(username="check_admin_user")

        await UserGroups.objects.acreate(
            user=user,
            authentik_id="check-admin-uid",
            groups=["WCComps_Discord_Admin"],
        )

        await DiscordLink.objects.acreate(
            team=team,
            discord_id=2222222222,
            discord_username="check_admin_user",
            user=user,
            is_active=True,
        )

        interaction = Mock(spec=discord.Interaction)
        interaction.user = Mock()
        interaction.user.id = 2222222222
        interaction.response = Mock()
        interaction.response.send_message = AsyncMock()

        result = await check_admin(interaction)

        assert result is True
        interaction.response.send_message.assert_not_called()

    async def test_check_admin_blocks_non_admin(self) -> None:
        """Test check_admin blocks non-admin users."""
        interaction = Mock(spec=discord.Interaction)
        interaction.user = Mock()
        interaction.user.id = 2323232323
        interaction.response = Mock()
        interaction.response.send_message = AsyncMock()

        result = await check_admin(interaction)

        assert result is False
        interaction.response.send_message.assert_called_once()
        call_args = interaction.response.send_message.call_args
        assert "Admin permissions required" in call_args.args[0]

    async def test_check_ticketing_admin_blocks_non_ticketing_admin(self) -> None:
        """Test check_ticketing_admin blocks unauthorized users."""
        interaction = Mock(spec=discord.Interaction)
        interaction.user = Mock()
        interaction.user.id = 2424242424
        interaction.response = Mock()
        interaction.response.send_message = AsyncMock()

        result = await check_ticketing_admin(interaction)

        assert result is False
        interaction.response.send_message.assert_called_once()
        call_args = interaction.response.send_message.call_args
        assert "Ticketing admin permissions required" in call_args.args[0]

    async def test_check_ticketing_support_blocks_non_support(self) -> None:
        """Test check_ticketing_support blocks unauthorized users."""
        interaction = Mock(spec=discord.Interaction)
        interaction.user = Mock()
        interaction.user.id = 2525252525
        interaction.response = Mock()
        interaction.response.send_message = AsyncMock()

        result = await check_ticketing_support(interaction)

        assert result is False
        interaction.response.send_message.assert_called_once()
        call_args = interaction.response.send_message.call_args
        assert "Ticketing support permissions required" in call_args.args[0]

    async def test_check_gold_team_blocks_non_gold_team(self) -> None:
        """Test check_gold_team blocks unauthorized users."""
        interaction = Mock(spec=discord.Interaction)
        interaction.user = Mock()
        interaction.user.id = 2626262626
        interaction.response = Mock()
        interaction.response.send_message = AsyncMock()

        result = await check_gold_team(interaction)

        assert result is False
        interaction.response.send_message.assert_called_once()
        call_args = interaction.response.send_message.call_args
        assert "GoldTeam permissions required" in call_args.args[0]
