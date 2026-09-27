"""The bot announces unset Discord IDs at startup in the log and the ops channel."""

import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bot.utils import report_missing_discord_settings


@pytest.mark.asyncio
async def test_missing_ids_logged_and_posted_to_ops(caplog):
    with (
        patch("bot.utils.missing_discord_settings", return_value=["GOLDTEAM_ROLE_ID", "BLUETEAM_ROLE_ID"]),
        patch("bot.utils.log_to_ops_channel", new=AsyncMock()) as ops,
        caplog.at_level(logging.ERROR, logger="bot.utils"),
    ):
        await report_missing_discord_settings(MagicMock())

    assert any("GOLDTEAM_ROLE_ID" in r.getMessage() for r in caplog.records if r.levelno == logging.ERROR)
    ops.assert_awaited_once()
    assert "BLUETEAM_ROLE_ID" in ops.await_args.args[1]


@pytest.mark.asyncio
async def test_silent_when_everything_is_set(caplog):
    with (
        patch("bot.utils.missing_discord_settings", return_value=[]),
        patch("bot.utils.log_to_ops_channel", new=AsyncMock()) as ops,
        caplog.at_level(logging.ERROR, logger="bot.utils"),
    ):
        await report_missing_discord_settings(MagicMock())

    ops.assert_not_awaited()
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR]
