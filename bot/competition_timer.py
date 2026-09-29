"""Competition timer background task to enable/disable applications at scheduled times."""

import asyncio
import contextlib
import logging

import discord
from asgiref.sync import sync_to_async
from django.utils import timezone

from bot.competition_actions import run_competition, update_status_channel
from bot.heartbeat import record as record_heartbeat
from bot.utils import log_to_ops_channel, recycle_db_connection
from core.models import CompetitionConfig

logger = logging.getLogger(__name__)


class CompetitionTimer:
    """Background task to monitor competition start/end times."""

    def __init__(self, bot: discord.Client) -> None:
        self.bot = bot
        self.task: asyncio.Task[None] | None = None
        self.running = False
        # Last start/stop failure posted to ops, so a retry that fails the same way stays quiet.
        self._last_failure: str | None = None

    def start(self) -> None:
        if not self.running:
            self.running = True
            self.task = asyncio.create_task(self._check_loop())
            logger.info("Competition timer started")

    def stop(self) -> None:
        self.running = False
        if self.task:
            self.task.cancel()
            logger.info("Competition timer stopped")

    async def _check_loop(self) -> None:
        while self.running:
            try:
                await recycle_db_connection()
                await self._check_competition_times()
                record_heartbeat("timer", self.bot)
            except Exception as e:
                logger.exception(f"Error in competition timer check: {e}")

            await asyncio.sleep(60)

    async def _check_competition_times(self) -> None:

        # Errors here propagate so _check_loop skips its heartbeat: a timer that can't read the
        # config is not alive in any useful sense.
        @sync_to_async
        def check_and_update() -> tuple[bool, bool]:
            config = CompetitionConfig.get_config()
            config.last_check = timezone.now()
            config.save(update_fields=["last_check"])
            return config.should_enable_applications(), config.should_disable_applications()

        should_start, should_stop = await check_and_update()
        if not (should_start or should_stop):
            self._last_failure = None
            return

        try:
            enable = should_start
            action, done = ("Start", "Started") if enable else ("Stop", "Stopped")
            logger.info(f"Competition {action.lower()} time reached")
            result = await run_competition(enable, actor="timer")
            if result.success:
                result_msg = f"**Competition Auto-{done}!**\n\n{result.summary()}"
            else:
                result_msg = f"**Competition Auto-{action} Failed:** {result.error}"
                # A failed run is retried every minute until it works; say so once, not every minute.
                if result_msg == self._last_failure:
                    return
                self._last_failure = result_msg

            await log_to_ops_channel(self.bot, result_msg)
            await update_status_channel(self.bot)

        except Exception as e:
            logger.exception(f"Failed to start/stop competition: {e}")
            message = f"**Error in competition timer:** {e}"
            if message != self._last_failure:
                self._last_failure = message
                with contextlib.suppress(Exception):
                    await log_to_ops_channel(self.bot, message)
