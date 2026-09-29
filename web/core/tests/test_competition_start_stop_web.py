"""Starting and stopping from the ops page streams the shared service and tells Discord."""

import json
from unittest.mock import MagicMock, patch

import pytest
from django.test import Client
from django.urls import reverse

from core.models import CompetitionConfig, DiscordTask

pytestmark = pytest.mark.django_db


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
