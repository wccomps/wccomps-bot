"""Team password resets, and what unlinking a team member tells Discord."""

from datetime import date
from unittest.mock import MagicMock, patch

import pytest
from django.contrib.auth.models import User
from django.test import Client
from django.urls import reverse
from registration.models import Event, EventTeamAssignment, Season, TeamRegistration

from core.authentik_manager import AuthentikManager
from core.authentik_utils import reset_team_password
from core.models import DiscordTask
from team.models import DiscordLink, Team

pytestmark = pytest.mark.django_db


@pytest.fixture
def assignments() -> tuple[EventTeamAssignment, EventTeamAssignment]:
    team = Team.objects.create(team_number=7, team_name="Team 07")
    season = Season.objects.create(name="2026", year=2026)
    registration = TeamRegistration.objects.create(school_name="School")

    def assign(name: str, finalized: bool) -> EventTeamAssignment:
        event = Event.objects.create(
            season=season, name=name, event_type="invitational", date=date(2026, 10, 3), is_finalized=finalized
        )
        return EventTeamAssignment.objects.create(
            event=event, registration=registration, team=team, password_generated="old"
        )

    return assign("Upcoming", finalized=False), assign("Past", finalized=True)


def test_reset_records_the_new_password_for_unfinished_events(assignments):
    upcoming, past = assignments
    with patch("core.authentik_manager.AuthentikManager") as manager:
        manager.return_value.reset_blueteam_password.return_value = (True, "")
        password, error = reset_team_password(7)

    assert password and error == ""
    manager.return_value.reset_blueteam_password.assert_called_once_with(7, password)
    upcoming.refresh_from_db()
    past.refresh_from_db()
    assert upcoming.password_generated == password
    assert past.password_generated == "old"


def test_failed_reset_records_nothing(assignments):
    upcoming, _ = assignments
    with patch("core.authentik_manager.AuthentikManager") as manager:
        manager.return_value.reset_blueteam_password.return_value = (False, "HTTP 500")
        assert reset_team_password(7) == (None, "HTTP 500")

    upcoming.refresh_from_db()
    assert upcoming.password_generated == "old"


def test_authentik_reset_leaves_the_account_disabled():
    """Outside the competition team accounts stay disabled; a reset must not open them up."""
    manager = AuthentikManager.__new__(AuthentikManager)
    manager.base_url = "https://auth.example"
    manager.client = MagicMock()
    manager.client.get.return_value.json.return_value = {"results": [{"pk": 5, "username": "team07"}]}

    ok, _ = manager.reset_blueteam_password(7, "new-pass")

    assert ok
    manager.client.patch.assert_not_called()


@pytest.mark.parametrize("action", ["unlink_user", "reset"])
def test_web_unlink_queues_a_role_sync_for_the_member(admin_user, action):
    team = Team.objects.create(team_number=8, team_name="Team 08")
    member = User.objects.create(username="member08")
    DiscordLink.objects.create(user=member, discord_id=4242, discord_username="m", team=team, is_active=True)
    client = Client()
    client.force_login(admin_user)

    with (
        patch("core.admin_views.teams.reset_team_password", return_value=("pw", "")),
        patch("core.admin_views.teams.AuthentikManager") as manager,
    ):
        manager.return_value.revoke_user_sessions.return_value = (True, "", 0)
        client.post(reverse("admin_team_action", args=[8]), {"action": action, "discord_id": 4242})

    task = DiscordTask.objects.get(task_type="sync_member_roles")
    assert task.payload == {"discord_id": 4242}
    assert not DiscordLink.objects.filter(discord_id=4242, is_active=True).exists()
