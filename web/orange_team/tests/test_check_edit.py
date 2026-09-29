"""Editing a check's rubric keeps grading done on the criteria that stay."""

import pytest
from django.contrib.auth.models import User
from django.http import QueryDict
from django.test import Client

from orange_team.forms import extract_criteria
from orange_team.models import OrangeAssignment, OrangeAssignmentResult, OrangeCheck, OrangeCheckCriterion
from team.models import Team

pytestmark = pytest.mark.django_db


@pytest.fixture
def graded_check(gold_team_user: User) -> tuple[OrangeCheck, OrangeCheckCriterion, OrangeCheckCriterion]:
    check = OrangeCheck.objects.create(title="Phones", description="", created_by=gold_team_user)
    keep = OrangeCheckCriterion.objects.create(orange_check=check, label="Polite", points=5, sort_order=0)
    drop = OrangeCheckCriterion.objects.create(orange_check=check, label="Fast", points=3, sort_order=1)
    team = Team.objects.create(team_number=4, team_name="Team 04")
    assignment = OrangeAssignment.objects.create(orange_check=check, user=gold_team_user, team=team)
    OrangeAssignmentResult.objects.create(assignment=assignment, criterion=keep, met=True)
    OrangeAssignmentResult.objects.create(assignment=assignment, criterion=drop, met=True)
    return check, keep, drop


def test_edit_keeps_results_adds_new_and_drops_removed(gold_team_user, graded_check):
    check, keep, drop = graded_check
    client = Client()
    client.force_login(gold_team_user)

    # Row 0 was removed in the browser, so the kept row is still numbered 1.
    response = client.post(
        f"/orange-team/checks/{check.pk}/edit/",
        {
            "title": "Phones (typo fixed)",
            "criterion_id_1": keep.pk,
            "criterion_label_1": "Polite and calm",
            "criterion_points_1": "6",
            "criterion_id_2": "",
            "criterion_label_2": "Escalates",
            "criterion_points_2": "2",
        },
    )

    assert response.status_code == 302
    keep.refresh_from_db()
    assert (keep.label, keep.points, keep.sort_order) == ("Polite and calm", 6, 0)
    assert not OrangeCheckCriterion.objects.filter(pk=drop.pk).exists()
    assignment = OrangeAssignment.objects.get(orange_check=check)
    results = {r.criterion.label: r.met for r in assignment.results.select_related("criterion")}
    assert results == {"Polite and calm": True, "Escalates": False}


def test_extract_criteria_reads_rows_past_gaps():
    post = QueryDict(mutable=True)
    post.update(
        {"criterion_label_2": "B", "criterion_points_2": "3", "criterion_label_0": "A", "criterion_points_0": "1"}
    )
    post.update({"criterion_label_5": "bad", "criterion_points_5": "-1"})

    assert extract_criteria(post) == [
        {"id": None, "label": "A", "points": 1, "sort_order": 0},
        {"id": None, "label": "B", "points": 3, "sort_order": 1},
    ]
