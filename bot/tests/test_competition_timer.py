"""Tests for competition timer background task."""

from datetime import timedelta
from unittest.mock import AsyncMock, patch

import discord
import pytest
from django.utils import timezone

from bot.competition_timer import CompetitionTimer
from core.models import CompetitionConfig


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
class TestCompetitionTimer:
    """Test competition timer functionality."""

    async def test_check_loop_continues_while_running(self) -> None:
        """Test that _check_loop continues checking while running is True."""
        bot = AsyncMock(spec=discord.Client)
        timer = CompetitionTimer(bot)
        timer.running = True

        check_count = 0

        async def mock_check() -> None:
            nonlocal check_count
            check_count += 1
            if check_count >= 2:
                timer.running = False

        with (
            patch.object(timer, "_check_competition_times", side_effect=mock_check),
            patch("asyncio.sleep", new_callable=AsyncMock),
        ):
            await timer._check_loop()

        assert check_count == 2

    async def test_check_loop_handles_exceptions(self) -> None:
        """Test that _check_loop handles exceptions and continues."""
        bot = AsyncMock(spec=discord.Client)
        timer = CompetitionTimer(bot)
        timer.running = True

        call_count = 0

        async def mock_check() -> None:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("Test error")
            timer.running = False

        with (
            patch.object(timer, "_check_competition_times", side_effect=mock_check),
            patch("asyncio.sleep", new_callable=AsyncMock),
        ):
            await timer._check_loop()

        assert call_count == 2

    async def test_check_competition_times_no_enable_needed(self) -> None:
        """Test _check_competition_times when applications should not be enabled."""
        bot = AsyncMock(spec=discord.Client)
        timer = CompetitionTimer(bot)

        # Use update_or_create with pk=1 to match get_config() singleton pattern
        config, _ = await CompetitionConfig.objects.aupdate_or_create(
            pk=1,
            defaults={
                "competition_start_time": timezone.now() + timedelta(hours=1),
                "applications_enabled": False,
                "controlled_applications": ["app1", "app2"],
            },
        )

        await timer._check_competition_times()

        await config.arefresh_from_db()
        assert config.applications_enabled is False
        assert config.last_check is not None

    async def test_check_competition_times_calls_start_competition(self) -> None:
        """Test _check_competition_times calls start_competition when scheduled."""
        bot = AsyncMock(spec=discord.Client)
        timer = CompetitionTimer(bot)

        # Use update_or_create with pk=1 to match get_config() singleton pattern
        await CompetitionConfig.objects.aupdate_or_create(
            pk=1,
            defaults={
                "competition_start_time": timezone.now() - timedelta(minutes=5),
                "applications_enabled": False,
                "controlled_applications": ["app1"],
                "last_check": timezone.now() - timedelta(hours=1),
            },
        )

        with patch("bot.competition_timer.start_competition", new_callable=AsyncMock) as mock_start:
            mock_start.return_value = {
                "success": True,
                "apps_enabled": ["app1"],
                "apps_failed": [],
                "accounts_enabled": 50,
                "accounts_failed": 0,
                "controlled_apps": ["app1"],
            }
            with (
                patch("bot.competition_timer.log_to_ops_channel", new_callable=AsyncMock),
                patch("bot.competition_timer.update_status_channel", new_callable=AsyncMock),
            ):
                await timer._check_competition_times()

            mock_start.assert_called_once()

    async def test_check_competition_times_exception_handling(self) -> None:
        """Test _check_competition_times handles exceptions."""
        bot = AsyncMock(spec=discord.Client)
        timer = CompetitionTimer(bot)

        # Use update_or_create with pk=1 to match get_config() singleton pattern
        await CompetitionConfig.objects.aupdate_or_create(
            pk=1,
            defaults={
                "competition_start_time": timezone.now() - timedelta(minutes=5),
                "applications_enabled": False,
                "controlled_applications": ["app1"],
                "last_check": timezone.now() - timedelta(hours=1),
            },
        )

        with patch("bot.competition_timer.start_competition", new_callable=AsyncMock) as mock_start:
            mock_start.side_effect = Exception("Start error")

            with patch("bot.competition_timer.log_to_ops_channel", new_callable=AsyncMock):
                # Should not raise exception
                await timer._check_competition_times()

    async def test_check_competition_times_with_no_config(self) -> None:
        """Test _check_competition_times handles missing config."""
        bot = AsyncMock(spec=discord.Client)
        timer = CompetitionTimer(bot)

        # Should not raise exception - get_config creates if missing
        await timer._check_competition_times()


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_repeated_auto_start_failure_is_posted_once() -> None:
    """The timer retries a failed start every minute; ops hears about it once."""
    bot = AsyncMock(spec=discord.Client)
    timer = CompetitionTimer(bot)
    await CompetitionConfig.objects.aupdate_or_create(
        pk=1,
        defaults={"competition_start_time": timezone.now() - timedelta(minutes=1), "applications_enabled": False},
    )
    failure = {"success": False, "error": "No controlled applications configured"}

    with (
        patch("bot.competition_timer.start_competition", new=AsyncMock(return_value=failure)) as start,
        patch("bot.competition_timer.log_to_ops_channel", new_callable=AsyncMock) as ops,
        patch("bot.competition_timer.update_status_channel", new_callable=AsyncMock),
    ):
        await timer._check_competition_times()
        await timer._check_competition_times()

    assert start.await_count == 2
    ops.assert_awaited_once()
