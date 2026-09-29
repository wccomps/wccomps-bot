"""Standings are computed from the scoring inputs on every read; exclusions live on their own rows."""

from decimal import Decimal

import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import Client
from django.urls import reverse

from scoring.calculator import compute_standings, get_leaderboard
from scoring.models import OrangeTeamScore, ScoringExclusion, ScoringTemplate, ServiceScore
from team.models import Team


@pytest.fixture
def unit_modifiers(db):
    return ScoringTemplate.objects.create(
        service_modifier=Decimal("1"), inject_modifier=Decimal("1"), orange_modifier=Decimal("1")
    )


def _team(number, service, is_active=True):
    team = Team.objects.create(team_number=number, team_name=f"Team {number}", is_active=is_active)
    ServiceScore.objects.create(team=team, service_points=Decimal(service))
    return team


@pytest.mark.django_db
def test_leaderboard_reflects_an_approval_without_recalculating(unit_modifiers, gold_team_user):
    team = _team(1, "100")
    client = Client()
    client.force_login(gold_team_user)

    before = client.get(reverse("scoring:api_scores")).json()["scores"]
    OrangeTeamScore.objects.create(team=team, description="x", points_awarded=Decimal("25")).approve(gold_team_user)
    after = client.get(reverse("scoring:api_scores")).json()["scores"]

    assert [s["total"] for s in before] == [100.0]
    assert [s["total"] for s in after] == [125.0]


@pytest.mark.django_db
def test_excluded_team_is_unranked_and_other_ranks_stay_contiguous(unit_modifiers):
    first, middle, last = _team(1, "300"), _team(2, "200"), _team(3, "100")
    ScoringExclusion.objects.create(team=middle)

    ranks = {s.team: s.rank for s in compute_standings()}

    assert ranks == {first: 1, middle: None, last: 2}
    assert [s.team for s in get_leaderboard()] == [first, last]


@pytest.mark.django_db
def test_inactive_and_idle_teams_are_not_ranked(unit_modifiers):
    scored = _team(1, "100")
    _team(2, "500", is_active=False)
    idle = Team.objects.create(team_number=3, team_name="Team 3", is_active=True)

    standings = compute_standings()

    assert [(s.team, s.rank) for s in standings] == [(scored, 1), (idle, None)]


@pytest.mark.django_db(transaction=True)
def test_migration_moves_exclusions_off_final_score():
    executor = MigrationExecutor(connection)
    executor.migrate([("scoring", "0036_approvable_base")])
    old_apps = executor.loader.project_state([("scoring", "0036_approvable_base")]).apps
    old_team_model = old_apps.get_model("team", "Team")
    final_score_model = old_apps.get_model("scoring", "FinalScore")
    excluded = old_team_model.objects.create(team_number=1, team_name="Team 1")
    kept = old_team_model.objects.create(team_number=2, team_name="Team 2")
    final_score_model.objects.create(team=excluded, is_excluded=True)
    final_score_model.objects.create(team=kept, is_excluded=False)

    executor = MigrationExecutor(connection)
    executor.migrate([("scoring", "0037_scoringexclusion_forget_finalscore")])

    assert list(ScoringExclusion.objects.values_list("team_id", flat=True)) == [excluded.pk]
    with connection.cursor() as cursor:
        cursor.execute("DELETE FROM final_score")


@pytest.mark.django_db
def test_deleting_a_team_ignores_the_forgotten_final_score_table():
    team = Team.objects.create(team_number=1, team_name="Team 1")
    with connection.cursor() as cursor:
        cursor.execute(
            "INSERT INTO final_score (team_id, service_points, inject_points, orange_points, red_deductions,"
            " incident_recovery_points, sla_penalties, point_adjustments, total_score, is_excluded, calculated_at)"
            " VALUES (%s, 0, 0, 0, 0, 0, 0, 0, 0, false, now())",
            [team.pk],
        )
        cursor.execute("SET CONSTRAINTS ALL IMMEDIATE")

        team.delete()

        cursor.execute("DELETE FROM final_score")
