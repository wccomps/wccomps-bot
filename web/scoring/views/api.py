"""JSON API endpoint views."""

from django.http import HttpRequest, JsonResponse
from django.shortcuts import get_object_or_404

from core.auth_utils import has_permission
from team.models import Team

from ..calculator import calculate_team_score, get_leaderboard
from ..models import RedTeamScore


def _allowed(request: HttpRequest, *permissions: str) -> bool:
    return any(has_permission(request.user, p) for p in permissions)


def api_scores(request: HttpRequest) -> JsonResponse:
    """API endpoint for scores."""
    if not _allowed(request, "gold_team", "white_team", "ticketing_admin"):
        return JsonResponse({"error": "Access denied"}, status=403)
    scores = get_leaderboard()
    data = [
        {
            "rank": score.rank,
            "team": f"Team {score.team.team_number}",
            "team_number": score.team.team_number,
            "total": float(score.total_score),
            "services": float(score.service_points),
            "injects": float(score.inject_points),
            "orange": float(score.orange_points),
            "red": float(score.red_deductions),
            "incidents": float(score.incident_recovery_points),
            "sla": float(score.sla_penalties),
        }
        for score in scores
    ]
    return JsonResponse({"scores": data})


def api_team_detail(request: HttpRequest, team_number: int) -> JsonResponse:
    """API endpoint for team detail."""
    if not _allowed(request, "gold_team", "white_team", "ticketing_admin"):
        return JsonResponse({"error": "Access denied"}, status=403)
    team = get_object_or_404(Team, team_number=team_number)
    scores = calculate_team_score(team)
    return JsonResponse(
        {
            "team": f"Team {team.team_number}",
            "team_number": team.team_number,
            "scores": {k: float(v) for k, v in scores.items()},  # type: ignore[arg-type]
        }
    )


def api_attack_types(request: HttpRequest) -> JsonResponse:
    """API endpoint for attack type suggestions."""
    if not _allowed(request, "red_team", "gold_team"):
        return JsonResponse({"error": "Access denied"}, status=403)
    # Get distinct attack vectors from previous findings
    attack_vectors = (
        RedTeamScore.objects.values_list("attack_vector", flat=True).distinct().order_by("attack_vector")[:50]
    )

    # Extract unique attack types, truncated to 50 chars
    suggestions = []
    seen: set[str] = set()
    for vector in attack_vectors:
        if vector:
            # Truncate to 50 chars max for short attack type names
            attack_type = vector.strip()[:50]
            if attack_type and attack_type.lower() not in seen:
                suggestions.append(attack_type)
                seen.add(attack_type.lower())

    return JsonResponse({"suggestions": sorted(suggestions)})
