"""Slash commands are re-synced on the next start unless the last sync fully succeeded."""

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import discord
import pytest

from core.models import BotState

pytestmark = [pytest.mark.asyncio, pytest.mark.django_db(transaction=True)]


async def _run_setup_hook(sync: AsyncMock, settings: Any) -> None:
    from main import PortalBot

    settings.COMPETITION_GUILD_ID = 1
    settings.VOLUNTEER_GUILD_ID = 0

    bot = PortalBot()
    with (
        patch.object(bot, "load_extension", new_callable=AsyncMock),
        patch.object(bot, "add_cog", new_callable=AsyncMock),
        patch.object(bot, "add_view"),
        patch.object(bot.tree, "sync", sync),
    ):
        await bot.setup_hook()


async def test_failed_sync_is_not_recorded(db: Any, settings: Any) -> None:
    await _run_setup_hook(AsyncMock(side_effect=discord.HTTPException(MagicMock(status=500), "boom")), settings)
    assert not await BotState.objects.filter(key="command_hash").aexists()


async def test_successful_sync_is_recorded_and_skipped_next_time(db: Any, settings: Any) -> None:
    await _run_setup_hook(AsyncMock(), settings)
    assert await BotState.objects.filter(key="command_hash").aexists()

    second = AsyncMock()
    await _run_setup_hook(second, settings)
    second.assert_not_awaited()
