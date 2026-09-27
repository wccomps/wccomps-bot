"""Stored Authentik groups are refreshed by the bot, not only at web login (security review, Medium)."""

from unittest.mock import AsyncMock, patch

import httpx
import pytest

from bot.cogs import authentik_groups
from bot.permissions import _permission_cache
from core.authentik_manager import AuthentikAPIError, AuthentikManager
from core.services.user_groups import GroupRefreshAbortedError, GroupRefreshResult


@pytest.fixture(autouse=True)
def _cache():
    _permission_cache.clear()
    _permission_cache[1] = {"groups": ["WCComps_Discord_Admin"], "expires_at": None}  # type: ignore[typeddict-item]
    yield
    _permission_cache.clear()


async def _run_with(result=None, error=None):
    with patch.object(authentik_groups, "refresh_user_groups", side_effect=error, return_value=result):
        await authentik_groups.refresh_groups_now()


@pytest.mark.asyncio
async def test_changes_clear_the_permission_cache():
    await _run_with(GroupRefreshResult(checked=3, changed=1))
    assert _permission_cache == {}


@pytest.mark.asyncio
async def test_no_changes_keep_the_cache():
    await _run_with(GroupRefreshResult(checked=3, changed=0))
    assert 1 in _permission_cache


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [GroupRefreshAbortedError("no users"), AuthentikAPIError("down"), RuntimeError("x")])
async def test_failures_are_logged_not_raised(error):
    await _run_with(error=error)
    assert 1 in _permission_cache


def _manager(handler):
    return AuthentikManager(
        base_url="https://auth.test", api_token="t", client=httpx.Client(transport=httpx.MockTransport(handler))
    )


def test_list_all_users_follows_pagination():
    def handler(request: httpx.Request) -> httpx.Response:
        page = int(request.url.params["page"])
        return httpx.Response(
            200, json={"results": [{"uid": f"u{page}"}], "pagination": {"next": page + 1 if page < 3 else 0}}
        )

    assert [u["uid"] for u in _manager(handler).list_all_users()] == ["u1", "u2", "u3"]


def test_list_all_raises_on_http_error():
    """A partial listing must never be mistaken for the full user list."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.params["page"] == "2":
            return httpx.Response(503, json={"detail": "busy"})
        return httpx.Response(200, json={"results": [{"uid": "u1"}], "pagination": {"next": 2}})

    with pytest.raises(AuthentikAPIError):
        _manager(handler).list_all_groups()


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("action", ["start_competition", "stop_competition"])
async def test_competition_start_and_stop_refresh_groups_immediately(action, refresh_groups_now: AsyncMock):
    from bot import competition_actions
    from core.models import CompetitionConfig

    await CompetitionConfig.objects.aupdate_or_create(pk=1, defaults={"controlled_applications": ["netbird"]})

    with (
        patch("bot.competition_actions.AuthentikManager"),
        patch("bot.competition_actions.toggle_all_blueteam_accounts", new_callable=AsyncMock, return_value=(50, 0)),
        patch("scoring.quotient_sync.sync_quotient_metadata"),
    ):
        await getattr(competition_actions, action)()

    refresh_groups_now.assert_awaited_once()
