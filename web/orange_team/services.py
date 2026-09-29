"""Business logic for the orange team app."""

import random

from django.contrib.auth.models import User
from django.db import transaction
from django.utils import timezone
from scoring.models import OrangeTeamScore

from orange_team.forms import CriterionInput
from orange_team.models import (
    OrangeAssignment,
    OrangeAssignmentResult,
    OrangeCheck,
    OrangeCheckCriterion,
)
from team.models import Team


def assign_teams_round_robin(
    check: OrangeCheck,
    checked_in_users: list[User],
    teams: list[Team],
) -> int:
    """Assign teams to checked-in users using round-robin distribution.

    Creates OrangeAssignment and OrangeAssignmentResult records.
    Returns the number of assignments created.
    """
    random.shuffle(teams)
    criteria = list(check.criteria.all())
    count = 0

    with transaction.atomic():
        for i, team in enumerate(teams):
            assigned_user = checked_in_users[i % len(checked_in_users)]
            # Skip if assignment already exists for this check+team
            if OrangeAssignment.objects.filter(orange_check=check, team=team).exists():
                continue
            assignment = OrangeAssignment.objects.create(
                orange_check=check,
                user=assigned_user,
                team=team,
            )
            # Create result rows for each criterion
            for criterion in criteria:
                OrangeAssignmentResult.objects.create(
                    assignment=assignment,
                    criterion=criterion,
                    met=False,
                )
            count += 1

        check.status = "active"
        check.save()

    return count


def update_check_criteria(check: OrangeCheck, criteria: list[CriterionInput]) -> None:
    """Apply an edited rubric in place, so grading already done on kept criteria survives.

    Posted rows with a known id are updated; rows without one are created, with an unmet
    result on every existing assignment so they can be graded; criteria left out are
    deleted along with their results.
    """
    with transaction.atomic():
        existing = {c.pk: c for c in check.criteria.select_for_update()}
        posted_ids = {c["id"] for c in criteria if c["id"] in existing}
        check.criteria.exclude(pk__in=posted_ids).delete()

        assignments = list(check.assignments.all())
        for c in criteria:
            criterion = existing.get(c["id"]) if c["id"] is not None else None
            if criterion is not None:
                criterion.label = c["label"]
                criterion.points = c["points"]
                criterion.sort_order = c["sort_order"]
                criterion.save(update_fields=["label", "points", "sort_order"])
                continue
            criterion = OrangeCheckCriterion.objects.create(
                orange_check=check, label=c["label"], points=c["points"], sort_order=c["sort_order"]
            )
            OrangeAssignmentResult.objects.bulk_create(
                OrangeAssignmentResult(assignment=a, criterion=criterion) for a in assignments
            )


def create_orange_score_from_assignment(
    assignment: OrangeAssignment,
    approver: User,
) -> OrangeTeamScore:
    """Create an OrangeTeamScore record from an approved orange team check.

    Returns the created OrangeTeamScore instance.
    """
    return OrangeTeamScore.objects.create(
        team=assignment.team,
        submitted_by=assignment.user,
        description=f"Check: {assignment.orange_check.title}",
        points_awarded=assignment.score or 0,
        is_approved=True,
        approved_by=approver,
        approved_at=timezone.now(),
        orange_check=assignment.orange_check,
    )
