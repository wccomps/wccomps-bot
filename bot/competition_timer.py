"""Competition timer background task to enable/disable applications at scheduled times."""

import asyncio
import contextlib
import logging

import discord
from asgiref.sync import sync_to_async
from django.utils import timezone

from bot.competition_actions import start_competition, stop_competition, update_status_channel
from bot.utils import log_to_ops_channel, recycle_db_connection
from core.models import CompetitionConfig

logger = logging.getLogger(__name__)


class CompetitionTimer:
    """Background task to monitor competition start/end times."""

    def __init__(self, bot: discord.Client) -> None:
        self.bot = bot
        self.task: asyncio.Task[None] | None = None
        self.running = False

    def start(self) -> None:
        """Start the competition timer task."""
        if not self.running:
            self.running = True
            self.task = asyncio.create_task(self._check_loop())
            logger.info("Competition timer started")

    def stop(self) -> None:
        """Stop the competition timer task."""
        self.running = False
        if self.task:
            self.task.cancel()
            logger.info("Competition timer stopped")

    async def _check_loop(self) -> None:
        """Main loop to check competition start/end times."""
        while self.running:
            try:
                await recycle_db_connection()
                await self._check_competition_times()
            except Exception as e:
                logger.exception(f"Error in competition timer check: {e}")

            # Check every minute
            await asyncio.sleep(60)

    async def _check_competition_times(self) -> None:
        """Check if competition should start or stop."""

        @sync_to_async
        def check_and_update() -> tuple[bool, bool, CompetitionConfig | None]:
            try:
                config = CompetitionConfig.get_config()

                # Update last check time
                config.last_check = timezone.now()
                config.save(update_fields=["last_check"])

                should_start = config.should_enable_applications()
                should_stop = config.should_disable_applications()
                return should_start, should_stop, config
            except Exception as e:
                logger.exception(f"Error checking competition config: {e}")
                return False, False, None

        try:
            should_start, should_stop, config = await check_and_update()

            if not config:
                return

            # Handle competition start
            if should_start:
                logger.info("Competition start time reached! Starting competition...")

                result = await start_competition()

                if result["success"]:
                    result_msg = "**Competition Auto-Started!**\n\n"
                    result_msg += (
                        f"Applications enabled: {len(result['apps_enabled'])}/{len(result['controlled_apps'])}\n"
                    )
                    if result["apps_enabled"]:
                        result_msg += f"✓ Enabled: {', '.join(result['apps_enabled'])}\n"
                    if result["apps_failed"]:
                        result_msg += "\n✗ **Failed:**\n"
                        for app, error in result["apps_failed"]:
                            result_msg += f"  • {app}: {error}\n"
                    result_msg += f"\nAccounts enabled: {result['accounts_enabled']}"
                    if result["accounts_failed"] > 0:
                        result_msg += f" ({result['accounts_failed']} failed)"
                else:
                    result_msg = f"**Competition Auto-Start Failed:** {result.get('error', 'Unknown error')}"

                await log_to_ops_channel(self.bot, result_msg)
                await update_status_channel(self.bot)

            # Handle competition stop
            elif should_stop:
                logger.info("Competition end time reached! Stopping competition...")

                stop_result = await stop_competition()

                if stop_result["success"]:
                    result_msg = "**Competition Auto-Stopped!**\n\n"
                    disabled_count = len(stop_result["apps_disabled"])
                    total_count = len(stop_result["controlled_apps"])
                    result_msg += f"Applications disabled: {disabled_count}/{total_count}\n"
                    if stop_result["apps_disabled"]:
                        result_msg += f"✓ Disabled: {', '.join(stop_result['apps_disabled'])}\n"
                    if stop_result["apps_failed"]:
                        result_msg += "\n✗ **Failed:**\n"
                        for app, error in stop_result["apps_failed"]:
                            result_msg += f"  • {app}: {error}\n"
                    result_msg += f"\nAccounts disabled: {stop_result['accounts_disabled']}"
                    if stop_result["accounts_failed"] > 0:
                        result_msg += f" ({stop_result['accounts_failed']} failed)"
                else:
                    result_msg = f"**Competition Auto-Stop Failed:** {stop_result.get('error', 'Unknown error')}"

                await log_to_ops_channel(self.bot, result_msg)
                await update_status_channel(self.bot)

        except Exception as e:
            logger.exception(f"Failed to start/stop competition: {e}")
            with contextlib.suppress(Exception):
                await log_to_ops_channel(
                    self.bot,
                    f"**Error in competition timer:** {e}",
                )
