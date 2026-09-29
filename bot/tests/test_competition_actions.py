"""The bot's entry point to starting and stopping the competition."""

from unittest.mock import patch

import pytest

from bot.competition_actions import run_competition
from core.services.competition import CompetitionRunResult


@pytest.mark.asyncio
@pytest.mark.parametrize("enable", [True, False])
async def test_runs_the_shared_service_and_drops_cached_permissions(enable: bool) -> None:
    done = CompetitionRunResult(enable=enable)
    with (
        patch("bot.competition_actions.run_competition_to_completion", return_value=done) as service,
        patch("bot.competition_actions.clear_permission_cache") as clear_cache,
    ):
        result = await run_competition(enable, actor="discord:admin")

    assert result is done
    service.assert_called_once_with(enable, "discord:admin")
    clear_cache.assert_called_once()
