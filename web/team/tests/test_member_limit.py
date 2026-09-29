"""A team's member limit is the competition's, not a per-team copy."""

import pytest
from django.contrib.auth.models import User

from core.models import CompetitionConfig
from team.models import DiscordLink, Team

pytestmark = pytest.mark.django_db


def _set_member_limit(limit: int) -> None:
    CompetitionConfig.objects.update_or_create(pk=1, defaults={"max_team_members": limit})


def test_team_follows_the_competition_limit() -> None:
    _set_member_limit(2)
    team = Team.objects.create(team_number=1, team_name="Team 01")
    for i in range(2):
        user = User.objects.create_user(username=f"member{i}")
        DiscordLink.objects.create(discord_id=100 + i, discord_username=f"m{i}", user=user, team=team)

    assert team.max_members == 2
    assert team.is_full()

    _set_member_limit(3)
    team = Team.objects.get(pk=team.pk)
    assert team.max_members == 3
    assert not team.is_full()
