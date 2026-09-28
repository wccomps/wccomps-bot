"""prepare_database: every web container runs it at start, so concurrent starts must take turns."""

import threading

import psycopg2
import pytest
from django.core.management import call_command
from django.db import connection

from core.management.commands.prepare_database import LOCK_ID
from team.models import MAX_TEAMS, Team


@pytest.mark.django_db
def test_seeds_teams_when_there_are_none():
    Team.objects.all().delete()

    call_command("prepare_database", verbosity=0)

    assert Team.objects.count() == MAX_TEAMS


@pytest.mark.django_db
def test_leaves_existing_teams_alone():
    Team.objects.all().delete()
    Team.objects.create(team_number=1, team_name="Renamed", max_members=10)

    call_command("prepare_database", verbosity=0)

    assert list(Team.objects.values_list("team_name", flat=True)) == ["Renamed"]


@pytest.mark.django_db(transaction=True)
def test_waits_while_another_container_holds_the_lock():
    db = connection.settings_dict
    other = psycopg2.connect(
        host=db["HOST"], port=db["PORT"], user=db["USER"], password=db["PASSWORD"], dbname=db["NAME"]
    )
    other.autocommit = True
    other.cursor().execute("SELECT pg_advisory_lock(%s)", [LOCK_ID])

    def run() -> None:
        try:
            call_command("prepare_database", verbosity=0)
        finally:
            connection.close()

    worker = threading.Thread(target=run)
    worker.start()
    try:
        worker.join(timeout=2)
        assert worker.is_alive(), "prepare_database ran without waiting for the advisory lock"
    finally:
        other.cursor().execute("SELECT pg_advisory_unlock(%s)", [LOCK_ID])
        other.close()
    worker.join(timeout=60)
    assert not worker.is_alive()
