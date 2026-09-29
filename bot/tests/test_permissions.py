"""Bot permission checks read the linked account's stored Authentik groups, as the web portal does."""

from itertools import count
from unittest.mock import AsyncMock, Mock

import discord
import pytest
from django.contrib.auth.models import User

from bot.permissions import has_permission, permission_check
from core.models import UserGroups
from core.permission_constants import PERMISSION_MAP
from team.models import DiscordLink

pytestmark = [pytest.mark.asyncio, pytest.mark.django_db(transaction=True)]

_ids = count(3_000_000_000)


async def _linked(groups: list[str] | None, *, active: bool = True) -> int:
    """A Discord ID linked to a new account holding `groups` (None: no UserGroups row)."""
    discord_id = next(_ids)
    user = await User.objects.acreate(username=f"perm{discord_id}")
    if groups is not None:
        await UserGroups.objects.acreate(user=user, authentik_id=f"uid{discord_id}", groups=groups)
    await DiscordLink.objects.acreate(
        discord_id=discord_id, discord_username=user.username, user=user, is_active=active
    )
    return discord_id


def _interaction(discord_id: int) -> Mock:
    interaction = Mock(spec=discord.Interaction)
    interaction.user = Mock(id=discord_id)
    interaction.response = Mock(send_message=AsyncMock())
    return interaction


async def test_group_changes_apply_on_the_next_check() -> None:
    discord_id = await _linked(["WCComps_Discord_Admin"])
    assert await has_permission(discord_id, "admin")

    await UserGroups.objects.filter(authentik_id=f"uid{discord_id}").aupdate(groups=[])

    assert not await has_permission(discord_id, "admin")


async def test_unlinked_inactive_or_groupless_users_have_no_permissions() -> None:
    assert not await has_permission(next(_ids), "admin")
    assert not await has_permission(await _linked(["WCComps_Discord_Admin"], active=False), "admin")
    assert not await has_permission(await _linked(None), "admin")


@pytest.mark.parametrize(("permission", "group"), [(p, g) for p, groups in PERMISSION_MAP.items() for g in groups])
async def test_check_passes_every_group_that_grants_the_permission(permission: str, group: str) -> None:
    interaction = _interaction(await _linked([group]))

    assert await permission_check(permission)(interaction)
    interaction.response.send_message.assert_not_called()


@pytest.mark.parametrize("permission", list(PERMISSION_MAP))
async def test_denial_names_every_group_that_grants_the_permission(permission: str) -> None:
    interaction = _interaction(await _linked(["WCComps_BlueTeam01"]))

    assert not await permission_check(permission)(interaction)

    message = interaction.response.send_message.await_args.args[0]
    assert all(f"`{group}`" in message for group in PERMISSION_MAP[permission])
    assert interaction.response.send_message.await_args.kwargs["ephemeral"] is True
