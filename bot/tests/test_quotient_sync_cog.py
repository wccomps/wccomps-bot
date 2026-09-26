"""Quotient sync backs off and logs state changes only while Quotient is down."""

import logging
from unittest.mock import MagicMock, patch

import pytest

from bot.cogs import quotient_sync
from bot.cogs.quotient_sync import QuotientSyncCog


@pytest.fixture
def cog():
    cog = QuotientSyncCog.__new__(QuotientSyncCog)
    cog.bot = MagicMock()
    cog.quotient_unavailable = False
    return cog


def _fail():
    raise ValueError("Failed to retrieve infrastructure from Quotient")


@pytest.mark.asyncio
async def test_first_failure_warns_once_and_backs_off(cog, caplog):
    with (
        patch.object(quotient_sync, "sync_quotient_metadata", _fail),
        patch.object(type(cog.sync_quotient_task), "change_interval") as change_interval,
        caplog.at_level(logging.DEBUG, logger="bot.cogs.quotient_sync"),
    ):
        await cog.run_sync()
        await cog.run_sync()
        await cog.run_sync()

    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert not [r for r in caplog.records if r.exc_info]
    change_interval.assert_called_once_with(minutes=quotient_sync.BACKOFF_MINUTES)
    assert cog.quotient_unavailable


@pytest.mark.asyncio
async def test_recovery_logs_once_and_restores_interval(cog, caplog):
    cog.quotient_unavailable = True
    with (
        patch.object(quotient_sync, "sync_quotient_metadata", lambda: None),
        patch.object(type(cog.sync_quotient_task), "change_interval") as change_interval,
        caplog.at_level(logging.DEBUG, logger="bot.cogs.quotient_sync"),
    ):
        await cog.run_sync()

    assert [r for r in caplog.records if r.levelno == logging.INFO and "reachable" in r.getMessage()]
    change_interval.assert_called_once_with(minutes=quotient_sync.SYNC_MINUTES)
    assert not cog.quotient_unavailable


@pytest.mark.asyncio
async def test_success_while_up_does_not_touch_interval(cog):
    with (
        patch.object(quotient_sync, "sync_quotient_metadata", lambda: None),
        patch.object(type(cog.sync_quotient_task), "change_interval") as change_interval,
    ):
        await cog.run_sync()

    change_interval.assert_not_called()


@pytest.mark.asyncio
async def test_unexpected_error_keeps_traceback(cog, caplog):
    def boom():
        raise RuntimeError("bug")

    with (
        patch.object(quotient_sync, "sync_quotient_metadata", boom),
        caplog.at_level(logging.DEBUG, logger="bot.cogs.quotient_sync"),
    ):
        await cog.run_sync()

    assert [r for r in caplog.records if r.levelno >= logging.ERROR and r.exc_info]
