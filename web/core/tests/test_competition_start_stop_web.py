"""Starting and stopping from the ops page streams the shared service and tells Discord."""

import json
from unittest.mock import MagicMock, patch

import pytest
from django.test import Client
from django.urls import reverse

from core.models import CompetitionConfig, DiscordTask

# The run streams from its own thread (core.utils.run_detached), which needs committed rows
pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def admin_client(admin_user):
    client = Client()
    client.force_login(admin_user)
    return client


def _post(client, action, **data):
    return client.post(reverse("admin_competition_action"), {"action": action, **data})


def test_start_streams_progress_and_posts_to_ops(admin_client):
    CompetitionConfig.objects.update_or_create(pk=1, defaults={"controlled_applications": ["scoring"]})
    manager = MagicMock()
    manager.enable_application.return_value = (True, None)
    manager.toggle_user.return_value = (True, "")

    with (
        patch("core.authentik_manager.AuthentikManager", return_value=manager),
        patch("core.services.user_groups.refresh_user_groups") as refresh,
        patch("scoring.quotient_sync.sync_quotient_metadata"),
    ):
        response = _post(admin_client, "start_competition")
        lines = [json.loads(line) for line in b"".join(response.streaming_content).decode().splitlines()]

    assert lines[-1]["done"] and lines[-1]["success"]
    refresh.assert_called_once()
    assert CompetitionConfig.get_config().applications_enabled
    assert "Competition Started" in DiscordTask.objects.get(task_type="log_to_channel").payload["message"]


def test_setting_a_start_time_mid_competition_keeps_it_running(admin_client):
    CompetitionConfig.objects.update_or_create(
        pk=1, defaults={"controlled_applications": ["scoring"], "applications_enabled": True}
    )

    _post(admin_client, "set_start_time", datetime="2030-01-01T09:00", timezone="UTC")

    config = CompetitionConfig.get_config()
    assert config.applications_enabled
    assert config.competition_start_time is not None


def test_cleanup_is_queued_for_the_bot(admin_client):
    CompetitionConfig.objects.update_or_create(pk=1, defaults={"applications_enabled": False})

    response = _post(admin_client, "cleanup_competition")

    assert response.json()["success"]
    task = DiscordTask.objects.get(task_type="cleanup_competition")
    assert task.payload["requested_by"]


def test_cleanup_refused_while_running(admin_client):
    CompetitionConfig.objects.update_or_create(pk=1, defaults={"applications_enabled": True})

    assert _post(admin_client, "cleanup_competition").status_code == 400
    assert not DiscordTask.objects.filter(task_type="cleanup_competition").exists()


def _join_detached_runs() -> None:
    import threading

    for thread in threading.enumerate():
        if thread.name == "run-detached":
            thread.join(timeout=10)


def test_start_finishes_after_the_browser_disconnects(admin_client):
    """A closed tab or a proxy timeout used to stop the start partway: some accounts enabled, state not saved."""
    import threading

    from team.models import Team

    for number in (1, 2, 3):
        Team.objects.create(team_number=number, team_name=f"Team {number}", is_active=True)
    CompetitionConfig.objects.update_or_create(pk=1, defaults={"controlled_applications": ["scoring"]})
    browser_gone = threading.Event()
    manager = MagicMock()
    manager.enable_application.return_value = (True, None)

    def toggle_once_the_browser_is_gone(username: str, is_active: bool) -> tuple[bool, str]:
        browser_gone.wait(timeout=10)
        return True, ""

    manager.toggle_user.side_effect = toggle_once_the_browser_is_gone

    with (
        patch("core.authentik_manager.AuthentikManager", return_value=manager),
        patch("core.services.user_groups.refresh_user_groups"),
        patch("scoring.quotient_sync.sync_quotient_metadata"),
    ):
        response = _post(admin_client, "start_competition")
        first = json.loads(next(iter(response.streaming_content)))
        response.close()
        browser_gone.set()
        _join_detached_runs()

    assert first["step"] == "Enabled scoring"
    assert [c.args[0] for c in manager.toggle_user.call_args_list] == ["team01", "team02", "team03"]
    assert CompetitionConfig.get_config().applications_enabled
