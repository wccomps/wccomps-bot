from decimal import ROUND_HALF_UP, Decimal
from typing import TypedDict

from django.db import transaction
from django.db.models import Q, QuerySet, Sum
from registration.models import Event

from team.models import Team

from .models import (
    FinalScore,
    IncidentReport,
    InjectScore,
    OrangeTeamScore,
    RedTeamScore,
    ScoringTemplate,
    ServiceScore,
)


class ScoreBreakdown(TypedDict):
    """Score breakdown returned by calculate_team_score()."""

    service_points: Decimal
    inject_points: Decimal
    orange_points: Decimal
    red_deductions: Decimal
    sla_penalties: Decimal
    point_adjustments: Decimal
    incident_recovery_points: Decimal
    total_score: Decimal


class DetailedScoreBreakdown(ScoreBreakdown):
    """Extended breakdown including raw scores, modifiers, and weights."""

    service_raw: Decimal
    inject_raw: Decimal
    orange_raw: Decimal
    service_modifier: Decimal
    inject_modifier: Decimal
    orange_modifier: Decimal
    service_weight: Decimal
    inject_weight: Decimal
    orange_weight: Decimal


SCORE_COMPONENT_FIELDS = (
    "service_points",
    "inject_points",
    "orange_points",
    "red_deductions",
    "sla_penalties",
    "point_adjustments",
    "incident_recovery_points",
)

RECOVERY_POINT_RATIO = Decimal("0.80")


def _get_modifiers(template: ScoringTemplate) -> tuple[Decimal, Decimal, Decimal]:
    """Get scaling modifiers from template.

    Uses direct modifier fields if set, otherwise derives from category weights:
        total_pool = max(raw_max / (weight/100))
        modifier = (weight/100) × total_pool / raw_max
    """
    # Use direct modifiers when available (exact, no rounding loss)
    if (
        template.service_modifier is not None
        and template.inject_modifier is not None
        and template.orange_modifier is not None
    ):
        return template.service_modifier, template.inject_modifier, template.orange_modifier

    hundred = Decimal("100")
    pairs = [
        (template.service_weight, template.service_max),
        (template.inject_weight, template.inject_max),
        (template.orange_weight, template.orange_max),
    ]
    # Largest raw_max / fractional_weight sets the common scale
    total_pool = max(raw_max / (weight / hundred) for weight, raw_max in pairs if weight > 0 and raw_max > 0)
    modifiers: list[Decimal] = []
    for weight, raw_max in pairs:
        if weight > 0 and raw_max > 0:
            modifiers.append(weight / hundred * total_pool / raw_max)
        else:
            modifiers.append(Decimal("0"))
    return modifiers[0], modifiers[1], modifiers[2]


def get_approved_inject_total(team: Team, event: Event | None = None) -> Decimal:
    filters = {"team": team, "is_approved": True}
    if event:
        filters["event"] = event
    return InjectScore.objects.filter(**filters).aggregate(total=Sum("points_awarded"))["total"] or Decimal("0")


def get_approved_orange_total(team: Team, event: Event | None = None) -> Decimal:
    filters = {"team": team, "is_approved": True}
    if event:
        filters["event"] = event
    return OrangeTeamScore.objects.filter(**filters).aggregate(total=Sum("points_awarded"))["total"] or Decimal("0")


def get_approved_red_deductions(team: Team, event: Event | None = None) -> Decimal:
    filters = {"affected_teams": team, "is_approved": True}
    if event:
        filters["event"] = event
    red_scores = RedTeamScore.objects.filter(**filters)
    total = sum(red_score.points_per_team for red_score in red_scores)
    return Decimal(str(total)) * Decimal("-1")


def calculate_team_score(team: Team) -> ScoreBreakdown:
    """Calculate final score for a team using scaling-factor formula.

    Formula:
        total = (service × service_modifier)
              + (inject × inject_modifier)
              + (orange × orange_modifier)
              + sla_violations + point_adjustments
              + red_deductions + incident_recovery
    """
    detailed = calculate_team_score_detailed(team)
    return {
        "service_points": detailed["service_points"],
        "inject_points": detailed["inject_points"],
        "orange_points": detailed["orange_points"],
        "red_deductions": detailed["red_deductions"],
        "sla_penalties": detailed["sla_penalties"],
        "point_adjustments": detailed["point_adjustments"],
        "incident_recovery_points": detailed["incident_recovery_points"],
        "total_score": detailed["total_score"],
    }


def calculate_team_score_detailed(team: Team) -> DetailedScoreBreakdown:
    """Like calculate_team_score but also returns raw scores, modifiers, and weights."""
    template = ScoringTemplate.objects.first() or ScoringTemplate()
    service_mod, inject_mod, orange_mod = _get_modifiers(template)

    service_score = ServiceScore.objects.filter(team=team).first()
    if service_score:
        service_raw = service_score.service_points
        sla_raw = service_score.sla_violations
        point_adj = service_score.point_adjustments
    else:
        service_raw = Decimal("0")
        sla_raw = Decimal("0")
        point_adj = Decimal("0")

    inject_raw = get_approved_inject_total(team).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    orange_raw = get_approved_orange_total(team)
    red_raw = get_approved_red_deductions(team)
    recovery_raw = IncidentReport.objects.filter(
        team=team,
        is_approved=True,
    ).aggregate(total=Sum("points_returned"))["total"] or Decimal("0")

    scaled_service = service_raw * service_mod
    scaled_inject = inject_raw * inject_mod
    scaled_orange = orange_raw * orange_mod

    two_places = Decimal("0.01")
    total_score = (
        scaled_service + scaled_inject + scaled_orange + sla_raw + point_adj + red_raw + recovery_raw
    ).quantize(two_places, rounding=ROUND_HALF_UP)

    return {
        # Standard fields (same as calculate_team_score)
        "service_points": scaled_service.quantize(two_places, rounding=ROUND_HALF_UP),
        "inject_points": scaled_inject.quantize(two_places, rounding=ROUND_HALF_UP),
        "orange_points": scaled_orange.quantize(two_places, rounding=ROUND_HALF_UP),
        "red_deductions": red_raw,
        "sla_penalties": sla_raw,
        "point_adjustments": point_adj,
        "incident_recovery_points": recovery_raw,
        "total_score": total_score,
        # Raw scores (before scaling)
        "service_raw": service_raw,
        "inject_raw": inject_raw,
        "orange_raw": orange_raw,
        "service_modifier": service_mod,
        "inject_modifier": inject_mod,
        "orange_modifier": orange_mod,
        "service_weight": template.service_weight,
        "inject_weight": template.inject_weight,
        "orange_weight": template.orange_weight,
    }


def _has_scoring_activity(scores: ScoreBreakdown) -> bool:
    return any(scores[key] != 0 for key in SCORE_COMPONENT_FIELDS)  # type: ignore[literal-required]


def _score_defaults(scores: ScoreBreakdown, *, rank: int | None) -> dict[str, Decimal | int | None]:
    """Build FinalScore defaults dict from a score breakdown."""
    defaults: dict[str, Decimal | int | None] = {field: scores[field] for field in SCORE_COMPONENT_FIELDS}  # type: ignore[literal-required]
    defaults["total_score"] = scores["total_score"]
    defaults["rank"] = rank
    return defaults


@transaction.atomic
def recalculate_all_scores() -> None:
    """Recalculate scores for all teams and update rankings; only teams with scoring activity are ranked."""
    teams = Team.objects.filter(is_active=True)

    score_data = []
    for team in teams:
        scores = calculate_team_score(team)
        score_data.append((team, scores))

    active_teams = [(t, s) for t, s in score_data if _has_scoring_activity(s)]
    inactive_teams = [(t, s) for t, s in score_data if not _has_scoring_activity(s)]

    active_teams.sort(key=lambda x: x[1]["total_score"], reverse=True)

    excluded_team_ids = set(FinalScore.objects.filter(is_excluded=True).values_list("team_id", flat=True))

    # Update or create FinalScore records; only non-excluded teams get a rank
    rank = 0
    for team, scores in active_teams:
        is_excluded = team.pk in excluded_team_ids
        if not is_excluded:
            rank += 1
        FinalScore.objects.update_or_create(
            team=team,
            defaults=_score_defaults(scores, rank=rank if not is_excluded else None),
        )

    for team, scores in inactive_teams:
        FinalScore.objects.update_or_create(
            team=team,
            defaults=_score_defaults(scores, rank=None),
        )


def get_leaderboard() -> list[FinalScore]:
    """FinalScores ordered by rank, excluding excluded teams and teams with no scoring activity."""
    exclude_kwargs = dict.fromkeys(SCORE_COMPONENT_FIELDS, 0)
    return list(
        FinalScore.objects.filter(is_excluded=False).exclude(**exclude_kwargs).select_related("team").order_by("rank")
    )


def suggest_red_score_matches(incident: IncidentReport) -> QuerySet[RedTeamScore]:
    """Red team scores against the incident's team that share its source IP (or an IP pool
    containing it), one of its boxes, or its service. Newest first, at most 10.
    """
    from .models import RedTeamIPPool

    query = Q(affected_teams=incident.team)

    filters = Q()
    if incident.source_ip:
        filters |= Q(source_ip=incident.source_ip)
        pool_ids = [pool.id for pool in RedTeamIPPool.objects.all() if pool.contains_ip(str(incident.source_ip))]
        if pool_ids:
            filters |= Q(source_ip_pool_id__in=pool_ids)
    if incident.affected_boxes:
        for box in incident.affected_boxes:
            filters |= Q(affected_boxes__contains=[box])
    if incident.affected_service:
        filters |= Q(affected_service=incident.affected_service)

    if filters:
        query &= filters

    scores = RedTeamScore.objects.filter(query).distinct().order_by("-created_at")

    return scores[:10]


def calculate_suggested_recovery_points(incident: IncidentReport, red_score: RedTeamScore) -> Decimal:
    """Suggested recovery points: RECOVERY_POINT_RATIO of the red team deduction, as a positive value."""
    deduction_amount = abs(red_score.points_per_team)
    suggested_return = deduction_amount * RECOVERY_POINT_RATIO
    return suggested_return
